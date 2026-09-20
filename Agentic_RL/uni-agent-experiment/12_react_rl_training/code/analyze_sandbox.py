"""Measure sandbox occupancy, RPC time, and overlap with paused generation."""
import bisect,collections,json,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def stats(values):
    v=sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not v:return {'n':0}
    return {'n':len(v),'mean':statistics.mean(v),'median':statistics.median(v),
            'p95':v[min(len(v)-1,math.ceil(.95*len(v))-1)],'max':v[-1],'sum':sum(v)}

def overlap(start,end,intervals,starts):
    i=max(0,bisect.bisect_right(starts,start)-1);total=0.0
    while i<len(intervals) and intervals[i][0]<end:
        lo,hi=intervals[i];total+=max(0,min(end,hi)-max(start,lo));i+=1
    return total

def main():
    phases=[]
    for p in (ROOT/'results/main').glob('trainer-phases-*.jsonl'):
        for line in p.read_text().splitlines():
            try:phases.append(json.loads(line))
            except json.JSONDecodeError:pass
    phases.sort(key=lambda r:r['start_unix'])
    # In colocate_async, on_sample_end pauses/sleeps inference before gen ends;
    # update_weights ends immediately before generation resumes. This interval
    # conservatively captures the period where model execution is unavailable.
    pauses=[]
    for step in [p for p in phases if p['name']=='step']:
        inside=[p for p in phases if step['start_unix']<=p['start_unix'] and p['end_unix']<=step['end_unix']]
        gen=[p for p in inside if p['name']=='gen'];sync=[p for p in inside if p['name']=='update_weights']
        if gen and sync and sync[-1]['end_unix']>gen[-1]['end_unix']:
            pauses.append((gen[-1]['end_unix'],sync[-1]['end_unix']))
    pauses.sort();starts=[p[0] for p in pauses]
    task_rows=[];lives=[];op_times=collections.defaultdict(list);op_bytes=collections.Counter();errors=collections.Counter()
    all_main_ids=set();frozen=json.loads((ROOT/'data/frozen-manifest.json').read_text());train_ids=set(frozen['train']['instance_ids'])
    resource_files=resource_nonempty=0
    for identity_path in (ROOT/'results/sessions').glob('*/identity.json'):
        identity=json.loads(identity_path.read_text())
        if identity.get('run_phase')!='main':continue
        sid=identity['session_id'];all_main_ids.add(sid);folder=identity_path.parent
        spans_path=folder/'spans.json'
        if not spans_path.exists():continue
        events=json.loads(spans_path.read_text())['events']
        partition='train' if identity['instance_id'] in train_ids else 'validation'
        row={'session_id':sid,'instance_id':identity['instance_id'],'partition':partition,'start_unix':identity['started_unix'],'operations':{},'phases_s':{},'model_wait_during_pause_s':0}
        result_path=folder/'result.json'
        if result_path.exists():
            result=json.loads(result_path.read_text());row.update(reward=result['reward'],finished=result['finished'],eval_completed=result['extra_info'].get('eval_completed'))
        if (folder/'error.json').exists():row['error']=json.loads((folder/'error.json').read_text())['error_type']
        created={};destroyed={}
        for e in events:
            category=e['category'];duration=e.get('duration_s',0);key=category
            if category=='sandbox_exec':
                argv=e.get('argv') or [];program=Path(argv[0]).name if argv else 'unknown'
                key+=':'+program+(':'+argv[1] if program=='tmux' and len(argv)>1 else '')
            if category=='phase':row['phases_s'][e['name']]=row['phases_s'].get(e['name'],0)+duration
            else:
                op_times[key].append(duration);op_bytes[key]+=e.get('stdout_bytes',0)+e.get('stderr_bytes',0)+e.get('bytes',0)
                count=row['operations'].setdefault(category,{'count':0,'seconds':0});count['count']+=1;count['seconds']+=duration
            if 'error_type' in e:errors[category+':'+e['error_type']]+=1
            if category=='sandbox_create' and e.get('sandbox_id'):
                created[e['sandbox_id']]=e['start_unix']+duration
            if category=='sandbox_destroy' and e.get('sandbox_id'):
                destroyed[e['sandbox_id']]=e['start_unix']+duration
            if category=='model_roundtrip':
                row['model_wait_during_pause_s']+=overlap(e['start_unix'],e['start_unix']+duration,pauses,starts)
        for sid_box,begin in created.items():
            if sid_box in destroyed:
                end=destroyed[sid_box];lives.append({'sandbox_id':sid_box,'session_id':sid,'partition':partition,'start_unix':begin,'end_unix':end,'lifetime_s':end-begin,'allocated_during_generation_pause_s':overlap(begin,end,pauses,starts)})
        for p in folder.glob('resources-*.json'):
            resource_files+=1;resource_nonempty+=bool(json.loads(p.read_text()).get('samples'))
        row['total_phase_s']=sum(row['phases_s'].values());task_rows.append(row)
    transitions=sorted([(r['start_unix'],1) for r in lives]+[(r['end_unix'],-1) for r in lives]);active=peak=0
    for _,delta in transitions:active+=delta;peak=max(peak,active)
    telemetry=[]
    for line in (ROOT/'results/main/telemetry.jsonl').read_text().splitlines():
        try:telemetry.append(json.loads(line))
        except json.JSONDecodeError:pass
    inventory_peak=max((r.get('rollout_sandbox_count',0) for r in telemetry),default=0)
    active_windows=[]
    for launch_path in (ROOT/'results/attempts').glob('attempt-*/main-launch.json'):
        launch=json.loads(launch_path.read_text());exit_path=launch_path.with_name('main-exit.json')
        if exit_path.exists():end=json.loads(exit_path.read_text())['time_unix']
        else:
            final=ROOT/'results/main-exit.json'
            end=json.loads(final.read_text())['time_unix'] if final.exists() else float('inf')
        active_windows.append((launch['launched_unix'],end))
    by_phase={name:sorted((r['start_unix'],r['end_unix']) for r in phases if r['name']==name)
              for name in ['testing','save_checkpoint','update_actor','old_log_prob','update_weights','gen']}
    phase_starts={name:[r[0] for r in spans] for name,spans in by_phase.items()}
    utilization=collections.defaultdict(lambda:{'sample_seconds':0.0,'gpu_pct_seconds':0.0,'sandbox_seconds':0.0,'samples':0})
    for i,sample in enumerate(telemetry[:-1]):
        t=sample['time_unix'];duration=telemetry[i+1]['time_unix']-t
        if not 0<duration<=30 or not any(lo<=t<hi for lo,hi in active_windows):continue
        gpu=[float(v['GPU use (%)']) for k,v in sample.get('gpu',{}).items() if k.startswith('card') and 'GPU use (%)' in v]
        if len(gpu)!=8:continue
        label='initialization_or_other'
        for name,spans in by_phase.items():
            j=bisect.bisect_right(phase_starts[name],t)-1
            if j>=0 and spans[j][0]<=t<spans[j][1]:label=name;break
        row=utilization[label];row['samples']+=1;row['sample_seconds']+=duration
        row['gpu_pct_seconds']+=statistics.mean(gpu)*duration
        row['sandbox_seconds']+=sample.get('rollout_sandbox_count',0)*duration
    for row in utilization.values():
        row['mean_gpu_utilization_pct']=row['gpu_pct_seconds']/row['sample_seconds']
        row['mean_active_sandboxes']=row['sandbox_seconds']/row['sample_seconds']
    report={'tasks':task_rows,'sandbox_lifetimes':lives,'generation_pause_intervals':pauses,
            'completed_task_count':len(task_rows),'completed_sandbox_lifetimes':len(lives),'peak_from_lifetimes':peak,'peak_from_api_inventory':inventory_peak,
            'lifetime_s':stats(r['lifetime_s'] for r in lives),'allocated_during_generation_pause_s':sum(r['allocated_during_generation_pause_s'] for r in lives),
            'model_wait_during_generation_pause_s':sum(r['model_wait_during_pause_s'] for r in task_rows),
            'operations':{k:{**stats(v),'output_or_transfer_bytes':op_bytes[k]} for k,v in op_times.items()},
            'operation_errors':dict(errors),'resource_metrics_files':resource_files,'resource_metrics_nonempty':resource_nonempty,
            'telemetry_by_trainer_phase':dict(utilization),
            'scope':'Main-run sessions with saved spans, including failed/cancelled, evaluation and rolled-back work. Aggregate times across concurrent tasks are not wall time. Allocated during generation pause does not imply the sandbox CPU was idle.'}
    (ROOT/'results/sandbox-analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['completed_task_count','completed_sandbox_lifetimes','peak_from_lifetimes','peak_from_api_inventory','resource_metrics_nonempty']}))

if __name__=='__main__':main()
