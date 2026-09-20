"""Recompute rollout bottleneck tables, telemetry summaries and traces from saved data."""
import argparse,bisect,collections,csv,json,math,statistics
from pathlib import Path

PCI='0000:66:00.0'

def read(path):return json.loads(path.read_text())
def total(events,category,phase=None):return sum(e['duration_ns']/1e9 for e in events if e['category']==category and (phase is None or e['phase']==phase))
def percentile(values,q):
    values=sorted(values)
    if not values:return None
    index=(len(values)-1)*q;lower=int(index);upper=min(lower+1,len(values)-1)
    return values[lower]+(values[upper]-values[lower])*(index-lower)

def prom(text):
    values={}
    for line in text.splitlines():
        if not line or line.startswith('#'):continue
        name=line.split('{',1)[0].split()[0]
        if not name.startswith('vllm:'):continue
        try:value=float(line.split()[-1])
        except ValueError:continue
        values[name]=values.get(name,0)+value
    return values

def interval_index(events,category):
    pairs=sorted((e['start_ns'],e['start_ns']+e['duration_ns']) for e in events if e['category']==category and e['phase']=='agent')
    return [x[0] for x in pairs],[x[1] for x in pairs]

def includes(index,t):
    starts,ends=index;i=bisect.bisect_right(starts,t)-1
    return i>=0 and t<ends[i]

def case_data(path,telemetry):
    result=read(path);events=read(path.with_name('spans.json'))['events']
    agent=next((e for e in events if e['category']=='phase' and e['name']=='agent'),None)
    agent_s=agent['duration_ns']/1e9 if agent else 0
    model=total(events,'model_api','agent');roundtrip=total(events,'model_roundtrip','agent')
    tools=total(events,'tool_call','agent');init=total(events,'tool_init','agent')+total(events,'tool_close','agent')
    residual=agent_s-roundtrip-tools-init
    row={k:result.get(k) for k in ['instance_id','trial_id','provider','tag','resolved','finished','eval_completed','trajectory_valid','wall_seconds','error']}
    row.update(agent_s=agent_s,model_api_s=model,gateway_client_s=max(0,roundtrip-model),tools_s=tools,
               tool_setup_s=init,agent_other_s=max(0,residual),unclamped_residual_s=residual,
               provision_s=sum(v for k,v in result['timings_s'].items() if k in ['provision','verifier_provision']),
               verifier_s=result['timings_s'].get('verify',0),export_s=result['timings_s'].get('trajectory_export',0),
               cleanup_s=sum(v for k,v in result['timings_s'].items() if k in ['cleanup','agent_cleanup']),
               generated_tokens=result['model_tokens']['generated'],prompt_tokens_sum=result['model_tokens']['prompt'],
               model_calls=sum(e['category']=='model_api' for e in events),
               tool_calls=sum(e['category']=='tool_call' and e['phase']=='agent' for e in events),
               codec_s=total(events,'codec','agent'))
    row['tool_fraction']=tools/agent_s if agent_s else None
    row['model_fraction']=model/agent_s if agent_s else None
    row['verifier_body_s']=sum(e.get('body_seconds',0) for e in events if e['category']=='verifier_exec')
    row['verifier_client_s']=total(events,'verifier_exec')
    row['verifier_dispatch_remainder_s']=max(0,row['verifier_client_s']-row['verifier_body_s'])
    before=prom(path.with_name('metrics-before.txt').read_text());after=prom(path.with_name('metrics-after.txt').read_text())
    delta={key:after.get(key,0)-before.get(key,0) for key in set(before)|set(after)}
    row['server_completed_requests']=delta.get('vllm:request_generation_tokens_count',0)
    row['server_metric_boundary_matches']=row['server_completed_requests']==row['model_calls']
    for metric,name in [('request_queue_time_seconds_sum','server_queue_s'),('request_prefill_time_seconds_sum','server_prefill_s'),('request_decode_time_seconds_sum','server_decode_s'),('request_inference_time_seconds_sum','server_inference_s'),('request_prefill_kv_cached_tokens_sum','cached_prefill_tokens'),('request_prefill_kv_computed_tokens_sum','computed_prefill_tokens')]:
        row[name]=delta.get('vllm:'+metric)
    row['cached_prefill_tokens']=delta.get('vllm:prompt_tokens_cached_total')
    row['prefix_cache_queried_tokens']=delta.get('vllm:prefix_cache_queries_total')
    row['prefix_cache_hit_tokens']=delta.get('vllm:prefix_cache_hits_total')
    row['prefix_cache_hit_fraction']=row['prefix_cache_hit_tokens']/row['prefix_cache_queried_tokens'] if row['prefix_cache_queried_tokens'] else None
    samples=[];in_model=[];in_tools=[];model_intervals=interval_index(events,'model_api');tool_intervals=interval_index(events,'tool_call')
    if agent:
        start=agent['start_ns'];end=start+agent['duration_ns']
        for sample in telemetry:
            if not start<=sample['time_ns']<=end:continue
            value=next((g['gfx_busy_percent'] for g in sample['gpus'] if g['pci']==PCI),None)
            if value is None:continue
            samples.append(value)
            if includes(model_intervals,sample['time_ns']):in_model.append(value)
            if includes(tool_intervals,sample['time_ns']):in_tools.append(value)
    row.update(gpu_samples=len(samples),gpu_busy_mean=statistics.mean(samples) if samples else None,
               gpu_busy_during_model=statistics.mean(in_model) if in_model else None,
               gpu_busy_during_tools=statistics.mean(in_tools) if in_tools else None,
               gpu_near_idle_sample_fraction=sum(v<=5 for v in samples)/len(samples) if samples else None)
    calls=[]
    for event in events:
        if event['category']!='tool_call' or event['phase']!='agent':continue
        start=event['start_ns'];end=start+event['duration_ns']
        inside=[e for e in events if e['start_ns']>=start and e['start_ns']+e['duration_ns']<=end]
        calls.append({'provider':row['provider'],'instance_id':row['instance_id'],'tool':event.get('tool'),
                      'elapsed_s':event['duration_ns']/1e9,'status':event.get('status'),
                      'sdk_exec_calls':sum(e['category']=='sandbox_exec' for e in inside),
                      'file_reads':sum(e['category']=='file_read' for e in inside),
                      'file_writes':sum(e['category']=='file_write' for e in inside),
                      'docker_cli_calls':sum(e['category']=='docker_cli' for e in inside),
                      'httpx_sends':sum(e['category']=='http_send' for e in inside)})
    return row,calls,events

def csv_file(path,rows):
    if not rows:return
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def main(root):
    output=root/'analysis';output.mkdir(exist_ok=True)
    telemetry=[json.loads(line) for line in (root/'telemetry-optimized.jsonl').read_text().splitlines()]
    rows=[];calls=[];traces=[];all_events=[]
    paths=sorted((root/'main-optimized').glob('*/*/result.json'))
    for index,path in enumerate(paths):
        row,tool_calls,events=case_data(path,telemetry);rows.append(row);calls.extend(tool_calls)
        all_events.extend((index,e) for e in events)
    groups={}
    for provider in ['docker','e2b']:
        group=[r for r in rows if r['provider']==provider]
        if not group:continue
        groups[provider]={'n':len(group),'resolved':sum(bool(r['resolved']) for r in group),
            'finished':sum(bool(r['finished']) for r in group),'valid_trajectories':sum(bool(r['trajectory_valid']) for r in group),
            'mean_wall_s':statistics.mean(r['wall_seconds'] for r in group),'median_wall_s':statistics.median(r['wall_seconds'] for r in group),
            'p90_wall_s':percentile([r['wall_seconds'] for r in group],.9),
            'mean_components_s':{key:statistics.mean(r[key] for r in group) for key in ['agent_s','model_api_s','gateway_client_s','tools_s','tool_setup_s','agent_other_s','provision_s','verifier_s','cleanup_s','export_s']},
            'total_generated_tokens':sum(r['generated_tokens'] for r in group),'total_tool_calls':sum(r['tool_calls'] for r in group),
            'gpu_busy_mean_by_task':statistics.mean(r['gpu_busy_mean'] for r in group if r['gpu_busy_mean'] is not None),
            'median_tool_fraction':statistics.median(r['tool_fraction'] for r in group if r['tool_fraction'] is not None)}
    tool_stats=[]
    for (provider,tool),group in __import__('itertools').groupby(sorted(calls,key=lambda r:(r['provider'],r['tool'])),key=lambda r:(r['provider'],r['tool'])):
        group=list(group);durations=[r['elapsed_s'] for r in group]
        tool_stats.append({'provider':provider,'tool':tool,'n':len(group),'median_s':statistics.median(durations),
                           'p95_s':percentile(durations,.95),'mean_s':statistics.mean(durations),
                           'mean_sdk_exec_calls':statistics.mean(r['sdk_exec_calls'] for r in group),
                           'mean_file_reads':statistics.mean(r['file_reads'] for r in group),
                           'mean_file_writes':statistics.mean(r['file_writes'] for r in group),
                           'mean_docker_cli_calls':statistics.mean(r['docker_cli_calls'] for r in group)})
    scheduling=[]
    for path in sorted(root.glob('scheduling-*-summary.json')):
        data=read(path);batch=data['results'];generated=sum(r['model_tokens']['generated'] for r in batch)
        scored=sum(bool(r.get('trajectory_valid')) and bool(r.get('eval_completed')) and 'error' not in r for r in batch)
        window=(min(r['started_unix'] for r in batch)*1e9,max(r['ended_unix'] for r in batch)*1e9)
        gpu=[g['gfx_busy_percent'] for sample in telemetry if window[0]<=sample['time_ns']<=window[1]
             for g in sample['gpus'] if g['pci']==PCI and g['gfx_busy_percent'] is not None]
        scheduling.append({'tag':data['tag'],'provider':batch[0]['provider'],'concurrency':data['concurrency'],
            'jobs':len(batch),'resolved':sum(bool(r.get('resolved')) for r in batch),
            'valid_trajectories':sum(bool(r.get('trajectory_valid')) for r in batch),
            'scored_trajectories':scored,'gpu_busy_mean':statistics.mean(gpu) if gpu else None,
            'wall_s':data['wall_seconds'],'rollouts_per_minute':scored/data['wall_seconds']*60,
            'mean_task_wall_s':statistics.mean(r['wall_seconds'] for r in batch),
            'p95_task_wall_s':percentile([r['wall_seconds'] for r in batch],.95),
            'generated_tokens':generated,'generated_tokens_per_wall_second':generated/data['wall_seconds']})
    summary={'status':'complete' if len(rows)==24 else 'running','primary_jobs':len(rows),'expected_primary_jobs':24,
             'providers':groups,'tool_stats':tool_stats,'scheduling':scheduling,
             'notes':['Only primary main-optimized runs enter task comparisons.','Wall times include differing sampled trajectories; use fixed-operation microbenchmarks for causal transport comparisons.','httpx_sends excludes Connect RPC sends; sdk_exec_calls measures E2B command operations.','GPU utilization is 1 Hz observed activity on PCI '+PCI+', not fleet utilization or a kernel trace.','Server timer deltas are per-case only when server_metric_boundary_matches is true.']}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (output/'cases.json').write_text(json.dumps(rows,indent=2)+'\n');csv_file(output/'cases.csv',rows)
    csv_file(output/'tool-calls.csv',calls);csv_file(output/'tool-stats.csv',tool_stats);csv_file(output/'scheduling.csv',scheduling)
    if all_events:
        origin=min(e['start_ns'] for _,e in all_events)
        keep={'phase','model_roundtrip','model_api','tool_call','tool_init','tool_close','codec','sandbox_create','sandbox_destroy','verifier_exec'}
        tids={name:index for index,name in enumerate(sorted(keep))}
        for index,event in all_events:
            if event['category'] not in keep:continue
            detail={k:v for k,v in event.items() if k not in ['start_ns','duration_ns','parent','id']}
            traces.append({'name':event.get('name') or event.get('tool') or event.get('operation') or event['category'],
                'cat':event['category'],'ph':'X','ts':(event['start_ns']-origin)/1000,'dur':event['duration_ns']/1000,
                'pid':index,'tid':tids[event['category']],'args':detail})
        for index,row in enumerate(rows):
            traces.append({'ph':'M','name':'process_name','pid':index,'args':{'name':row['provider']+' / '+row['instance_id']}})
            for name,tid in tids.items():traces.append({'ph':'M','name':'thread_name','pid':index,'tid':tid,'args':{'name':name}})
        finish=max(e['start_ns']+e['duration_ns'] for _,e in all_events)
        traces.append({'ph':'M','name':'process_name','pid':1000,'args':{'name':'Serving GPU / vLLM (1 Hz)'}})
        for sample in telemetry:
            if not origin<=sample['time_ns']<=finish:continue
            value=next((g['gfx_busy_percent'] for g in sample['gpus'] if g['pci']==PCI),None)
            if value is not None:traces.append({'ph':'C','pid':1000,'tid':0,'name':'GPU busy percent','ts':(sample['time_ns']-origin)/1000,'args':{'percent':value}})
        (output/'timeline.json').write_text(json.dumps({'traceEvents':traces,'displayTimeUnit':'ms'},separators=(',',':'))+'\n')
    print(json.dumps({'status':summary['status'],'jobs':len(rows),'providers':{p:{k:v for k,v in x.items() if k in ['n','resolved','mean_wall_s','median_tool_fraction','gpu_busy_mean_by_task']} for p,x in groups.items()},'scheduling_groups':len(scheduling)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);main(p.parse_args().root)
