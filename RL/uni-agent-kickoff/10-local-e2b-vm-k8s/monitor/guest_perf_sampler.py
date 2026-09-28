"""Read-only VM sampling. CPU percentages use local monotonic counter deltas."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

HZ = os.sysconf('SC_CLK_TCK')
PAGE = os.sysconf('SC_PAGE_SIZE')


def cpus():
    result = {}
    for line in Path('/proc/stat').read_text().splitlines():
        if line.startswith('cpu'):
            values = line.split(); result[values[0]] = list(map(int, values[1:9]))
    return result


def processes():
    result = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            data = (p/'stat').read_text().rsplit(')', 1)[1].split()
            io = {}
            try:io = {k: int(v) for k, v in (line.split(':') for line in (p/'io').read_text().splitlines())}
            except (OSError, ValueError):pass
            result[p.name] = {'pid':int(p.name),'name':(p/'comm').read_text().strip(),
                'start':data[19],'ticks':int(data[11])+int(data[12]),'rss_bytes':int(data[21])*PAGE,
                'read_bytes':io.get('read_bytes',0),'write_bytes':io.get('write_bytes',0)}
        except (OSError, ValueError, IndexError):pass
    return result


def disks():
    result = {}
    for line in Path('/proc/diskstats').read_text().splitlines():
        parts = line.split()
        if parts[2] == 'vda' or parts[2].startswith('nbd'):
            result[parts[2]] = list(map(int, parts[3:]))
    return result


def network():
    result = {}
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name, values = line.split(':'); result[name.strip()] = list(map(int, values.split()))
    return result


def tcp():
    result = {}
    for file in ['/proc/net/snmp','/proc/net/netstat']:
        lines = Path(file).read_text().splitlines()
        for i in range(0,len(lines)-1,2):
            keys=lines[i].split();values=lines[i+1].split()
            if keys[0] not in ['Tcp:','TcpExt:']:continue
            for k,v in zip(keys[1:],values[1:]):
                if k in ['InSegs','OutSegs','RetransSegs','CurrEstab','AttemptFails','EstabResets','TCPSynRetrans','TCPTimeouts','ListenOverflows','ListenDrops']:
                    result[k]=int(v)
    return result


def cgroups():
    result={}
    for p in Path('/sys/fs/cgroup/e2b').glob('sbx-*'):
        try:
            cpu={k:int(v) for k,v in (l.split() for l in (p/'cpu.stat').read_text().splitlines())}
            quota,period=(p/'cpu.max').read_text().split()
            result[p.name]={'cgroup':p.name,'usage_usec':cpu['usage_usec'],
                'throttled_usec':cpu.get('throttled_usec',0),'nr_throttled':cpu.get('nr_throttled',0),
                'cpu_quota_cores':None if quota=='max' else int(quota)/int(period),
                'memory_current_bytes':int((p/'memory.current').read_text()),
                'memory_peak_bytes':int((p/'memory.peak').read_text())}
        except (OSError,ValueError):pass
    return result


def snapshot():
    return {'monotonic':time.monotonic(),'cpu':cpus(),'processes':processes(),
            'disk':disks(),'network':network(),'tcp':tcp(),'cgroups':cgroups()}


def positive(new,old):return max(0,new-old)


def sample(before,after):
    dt=after['monotonic']-before['monotonic'];cpu=[]
    for name,values in after['cpu'].items():
        if name not in before['cpu']:continue
        d=[positive(n,o) for n,o in zip(values,before['cpu'][name])];total=sum(d)
        if not total:continue
        cpu.append({'cpu':name,'busy_pct':round(100*(sum(d[:3])+d[5]+d[6])/total,2),
                    'iowait_pct':round(100*d[4]/total,2),'steal_pct':round(100*d[7]/total,2)})
    procs=[]
    for pid,row in after['processes'].items():
        old=before['processes'].get(pid)
        if old and old['start']==row['start']:
            procs.append({**row,'cpu_cores':round(positive(row['ticks'],old['ticks'])/HZ/dt,3),
                'read_bytes_per_s':round(positive(row['read_bytes'],old['read_bytes'])/dt),
                'write_bytes_per_s':round(positive(row['write_bytes'],old['write_bytes'])/dt)})
    procs.sort(key=lambda x:x['cpu_cores'],reverse=True)
    disk=[]
    for name,v in after['disk'].items():
        if name not in before['disk']:continue
        old=before['disk'][name];d=[positive(n,o) for n,o in zip(v,old)]
        if name!='vda' and not (d[0] or d[4] or v[8]):continue
        ios=d[0]+d[4]
        disk.append({'device':name,'read_bytes_per_s':round(d[2]*512/dt),
            'write_bytes_per_s':round(d[6]*512/dt),'iops':round(ios/dt,2),
            'await_ms':round((d[3]+d[7])/ios,2) if ios else 0,
            'busy_pct':round(d[9]/dt/10,2),'avg_queue':round(d[10]/dt/1000,3),'in_flight':v[8]})
    net=[]
    for name,v in after['network'].items():
        if name not in before['network'] or not (name.startswith(('en','eth'))):continue
        d=[positive(n,o) for n,o in zip(v,before['network'][name])]
        net.append({'interface':name,'rx_bytes_per_s':round(d[0]/dt),'tx_bytes_per_s':round(d[8]/dt),
                    'rx_errors':d[2],'rx_drops':d[3],'tx_errors':d[10],'tx_drops':d[11]})
    cg=[]
    for name,v in after['cgroups'].items():
        old=before['cgroups'].get(name);row=dict(v)
        if old:
            row['cpu_cores']=round(positive(v['usage_usec'],old['usage_usec'])/1e6/dt,3)
            row['throttled_usec_delta']=positive(v['throttled_usec'],old['throttled_usec'])
        else:row['cpu_cores']=None
        cg.append(row)
    mem={k:v.strip() for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}
    mem={k:int(v.split()[0])*1024 for k,v in mem.items() if k in ['MemTotal','MemAvailable','Cached','Buffers','SReclaimable','SwapTotal','SwapFree']}
    pressure={k:Path('/proc/pressure/'+k).read_text().strip() for k in ['cpu','memory','io']}
    tc={k:positive(v,before['tcp'].get(k,v)) for k,v in after['tcp'].items() if k!='CurrEstab'}
    tc['connections_established']=after['tcp'].get('CurrEstab',0)
    agg=next(x for x in cpu if x['cpu']=='cpu')
    return {'utc':datetime.now(timezone.utc).isoformat(),'interval_seconds':round(dt,3),
        'vcpus':os.cpu_count(),'aggregate_cpu':agg,
        'busy_vcpu_equivalents':round(agg['busy_pct']*os.cpu_count()/100,2),
        'per_cpu':cpu[1:],'top_processes':procs[:15],'disk':disk,'network':net,'tcp':tc,
        'memory_bytes':mem,'load_average':os.getloadavg(),'pressure':pressure,
        'sandbox_cgroups':cg,'cgroup_count':len(cg),
        'memory_peak_semantics':'read-only lifetime peak; not reset or an interval peak'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--interval',type=float,default=5);p.add_argument('--samples',type=int,default=0);a=p.parse_args()
    old=snapshot();count=0
    while a.samples==0 or count<a.samples:
        time.sleep(a.interval);new=snapshot()
        print(json.dumps(sample(old,new),separators=(',',':')),flush=True)
        old=new;count+=1


if __name__=='__main__':
    try:main()
    except BrokenPipeError:pass
