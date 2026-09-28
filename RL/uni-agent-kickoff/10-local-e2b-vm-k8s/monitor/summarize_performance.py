"""Summarize passive recordings. Findings are bottleneck clues, not causal claims."""
import argparse
from collections import Counter,defaultdict
import csv
import fcntl
from datetime import datetime,timedelta,timezone
import gzip
import json
from pathlib import Path
import re
import statistics

ROOT=Path('/data2/yuhanya/e2b-local-20260916/monitoring')


def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00'))


def rows(directory,stem,since):
    seen=set()
    for path in directory.glob(stem+'.jsonl*'):
        if path.stat().st_mtime < since.timestamp()-120:continue
        opener=gzip.open if path.suffix=='.gz' else open
        with opener(path,'rt') as handle:
            for line in handle:
                try:r=json.loads(line)
                except ValueError:continue  # writer may be appending the final line
                stamp=r.get('utc') or r.get('timestamp') or r.get('guest',{}).get('utc')
                if not stamp:continue
                try:when=dt(stamp)
                except ValueError:continue
                if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
                if when<since:continue
                ident=r.get('event_id')
                if ident:
                    if ident in seen:continue
                    seen.add(ident)
                yield when,r


def percentile(values,p):
    if not values:return None
    v=sorted(values);x=(len(v)-1)*p;i=int(x)
    return round(v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i),3)


def stats(values):
    return {'n':len(values),'mean':round(statistics.mean(values),3) if values else None,
            'p50':percentile(values,.5),'p95':percentile(values,.95) if len(values)>=20 else None,
            'p99':percentile(values,.99) if len(values)>=100 else None,
            'max':round(max(values),3) if values else None}


def psi(text):
    m=re.search(r'^some avg10=([\d.]+)',text)
    return float(m[1]) if m else 0


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--hours',type=float,default=1)
    parser.add_argument('--since');parser.add_argument('--output-dir',type=Path,default=ROOT/'reports')
    a=parser.parse_args();since=dt(a.since) if a.since else datetime.now(timezone.utc)-timedelta(hours=a.hours)
    if since.tzinfo is None:since=since.replace(tzinfo=timezone.utc)
    a.output_dir.mkdir(parents=True,exist_ok=True)
    lock=(a.output_dir/'.summary.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('A summary for this output directory is already running');return
    samples=[];minutes=defaultdict(list);rtts=[];tcp_seen=set();warnings=[]
    for when,r in rows(ROOT/'performance','samples',since):
        g=r['guest'];h=r['host'];cpu=g['aggregate_cpu'];disk=next((d for d in g['disk'] if d['device']=='vda'),{})
        v={'utc':when.isoformat(),'cpu_pct':cpu['busy_pct'],'busy_vcpus':g['busy_vcpu_equivalents'],
            'iowait_pct':cpu['iowait_pct'],'steal_pct':cpu['steal_pct'],
            'hot_core_pct':max((c['busy_pct'] for c in g['per_cpu']),default=0),
            'qemu_top_thread_pct':max((t['cpu_cores']*100 for t in h['qemu_top_threads']),default=0),
            'io_psi_some_avg10':psi(g['pressure']['io']),
            'disk_read_mib_s':disk.get('read_bytes_per_s',0)/2**20,
            'disk_write_mib_s':disk.get('write_bytes_per_s',0)/2**20,
            'disk_await_ms':disk.get('await_ms',0),'disk_queue':disk.get('avg_queue',0),
            'disk_iops':disk.get('iops',0),'disk_inflight':disk.get('in_flight',0),'disk_busy_pct':disk.get('busy_pct',0),
            'net_rx_mib_s':sum(n['rx_bytes_per_s'] for n in g['network'])/2**20,
            'net_tx_mib_s':sum(n['tx_bytes_per_s'] for n in g['network'])/2**20,
            'tcp_retrans':g['tcp'].get('RetransSegs',0),'tcp_out':g['tcp'].get('OutSegs',0),
            'active_cgroups':g['cgroup_count']}
        samples.append(v);minutes[when.replace(second=0,microsecond=0).isoformat()].append(v)
        tcp=r.get('front_tcp',{})
        if tcp.get('utc') not in tcp_seen:
            tcp_seen.add(tcp.get('utc'));rtts.extend(tcp.get('rtt_ms',[]))
    samples.sort(key=lambda x:x['utc'])
    with (a.output_dir/'minute-timeseries.csv').open('w') as f:
        columns=['utc']+[k for k in samples[0] if k!='utc'] if samples else ['utc']
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for stamp,values in sorted(minutes.items()):
            row={'utc':stamp}
            for key in columns[1:]:
                row[key]=round(sum(v[key] for v in values) if key in ['tcp_retrans','tcp_out'] else statistics.mean(v[key] for v in values),4)
            writer.writerow(row)
    api=defaultdict(list);codes=defaultdict(Counter);exports=0
    for when,r in rows(ROOT/'events','api',since):
        if r.get('event')=='metrics_export_failure':exports+=1
        if r.get('event')!='api_request':continue
        if (r.get('user_agent') or '').startswith('e2b-service-monitor'):continue
        key=r['method']+' '+r['route'];codes[key][str(r['status'])]+=1
        if r.get('handler_ms') is not None:api[key].append(r['handler_ms'])
    proxy=defaultdict(list);proxy_codes=defaultdict(Counter)
    for when,r in rows(ROOT/'events','gateway',since):
        if r.get('event')!='http_proxy_request':continue
        key=r['method']+' '+r['route'];proxy[key].append(r['request_ms']);proxy_codes[key][str(r['status'])]+=1
    builds={};download_events=0
    with (a.output_dir/'build-event-timeline.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['utc','template_id','build_id','message'])
        for when,r in rows(ROOT/'events','build_events',since):
            message=r.get('message','');writer.writerow([when.isoformat(),r.get('template_id'),r.get('build_id'),message])
            if re.search(r'download|pull|extract|unpack',message,re.I):download_events+=1
            match=re.search(r'Build finished, took ([\w.µμ]+)',message)
            if match:
                factors={'ns':1e-6,'us':.001,'µs':.001,'μs':.001,'ms':1,'s':1000,'m':60000,'h':3600000}
                parts=re.findall(r'(\d+(?:\.\d+)?)(ns|µs|μs|us|ms|s|m|h)',match[1])
                builds[r.get('build_id') or r['event_id']]=sum(float(n)*factors[u] for n,u in parts)
    sb={}
    for when,r in rows(ROOT/'events','sandbox_host_stats',since):
        key=(r['sandbox_id'],r['sandbox_execution_id']);v=sb.setdefault(key,{'cpu_usec':0,'interval_us':0,'max_current_memory_bytes':0,'vcpu_count':int(r['sandbox_vcpu_count'])})
        if int(r['interval_us'])>0:
            v['cpu_usec']+=int(r['delta_cgroup_cpu_usage_usec']);v['interval_us']+=int(r['interval_us'])
        v['max_current_memory_bytes']=max(v['max_current_memory_bytes'],int(r['cgroup_memory_usage_bytes']))
    cpu_means=[v['cpu_usec']/v['interval_us'] for v in sb.values() if v['interval_us']]
    gaps=[(dt(b['utc'])-dt(a['utc'])).total_seconds() for a,b in zip(samples,samples[1:])]
    hot=sum(s['hot_core_pct']>=95 and s['cpu_pct']<30 for s in samples)
    qemu=sum(s['qemu_top_thread_pct']>=90 for s in samples)
    io=sum(s['io_psi_some_avg10']>=10 and (s['disk_await_ms']>=20 or
           (s['disk_inflight']>0 and s['disk_iops']==0)) for s in samples)
    if samples and max(s['cpu_pct'] for s in samples)<50:warnings.append('Observed VM CPU capacity was not saturated; low CPU alone does not identify the limiting stage.')
    if hot:warnings.append(f'{hot} samples had a hot individual vCPU despite low aggregate CPU; inspect serial work and per-process activity.')
    if qemu:warnings.append(f'{qemu} samples had a near-full QEMU thread; correlate with network and block-I/O before attributing a forwarding bottleneck.')
    if io:warnings.append(f'{io} samples combined elevated IO pressure and disk await; storage is a candidate during those windows.')
    report={'generated_utc':datetime.now(timezone.utc).isoformat(),'since_utc':since.isoformat(),
        'vm_samples':len(samples),'first_vm_sample':samples[0]['utc'] if samples else None,
        'last_vm_sample':samples[-1]['utc'] if samples else None,'max_sampling_gap_seconds':max(gaps,default=0),
        'sampling_gaps_over_15s':sum(x>15 for x in gaps),
        'vm_metrics':{k:stats([v[k] for v in samples]) for k in ['cpu_pct','busy_vcpus','iowait_pct','steal_pct','net_rx_mib_s','net_tx_mib_s','disk_await_ms','io_psi_some_avg10']},
        'front_tcp_rtt_ms':stats(rtts),'tcp_retrans_segments':sum(s['tcp_retrans'] for s in samples),
        'tcp_out_segments':sum(s['tcp_out'] for s in samples),
        'api_handler_latency_ms':{k:{**stats(v),'status_counts':dict(codes[k])} for k,v in api.items()},
        'gateway_request_latency_ms':{k:{**stats(v),'status_counts':dict(proxy_codes[k])} for k,v in proxy.items()},
        'native_sandbox_executions':len(sb),'native_mean_cpu_cores_per_execution':stats(cpu_means),
        'reported_build_duration_ms':stats(list(builds.values())),'download_related_event_count':download_events,
        'metrics_export_failure_events':exports,'candidate_findings':warnings,
        'limitations':['No remote Uni-Agent LLM/SDK spans are collected here.',
            'TCP RTT is sampled socket RTT, not per-request application latency.',
            'Gateway /health can mean sandbox health; 502 after deletion is not necessarily an API outage.',
            '5-second CPU sampling misses very short peaks; native cgroup records supplement it.',
            'Native memory.peak reset is unsupported in this kernel; use current memory, not interval peak.',
            'Exact OCI download duration requires matched phase events/spans; event counts are not image counts.',
            'Current timer report covers the last hour; use --hours 24 for overnight analysis.']}
    (a.output_dir/'latest-summary.json').write_text(json.dumps(report,indent=2))
    text=['# E2B performance recording summary',f"Window starts: {since.isoformat()}",f"VM samples: {len(samples)}; maximum gap: {max(gaps,default=0):.1f}s",'']
    text.extend('- '+w for w in warnings)
    text.extend(['','See latest-summary.json, minute-timeseries.csv and build-event-timeline.csv for measurements.','Findings are candidates; causality requires matching remote Uni-Agent spans.'])
    (a.output_dir/'latest-findings.md').write_text('\n'.join(text)+'\n')
    print(json.dumps({'status':'written','vm_samples':len(samples),'api_routes':len(api),'gateway_routes':len(proxy),'directory':str(a.output_dir)}))


if __name__=='__main__':main()
