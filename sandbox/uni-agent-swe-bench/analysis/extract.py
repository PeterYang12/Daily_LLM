"""Reconstruct measured intervals from the stopped 2026-09-20 experiment.

Reads original evidence only; never launches a model, sandbox, or training job.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

STAMP = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*\|\s*(.*?)\s*\|\s*(INFO|WARNING|ERROR|DEBUG)\s*\|\s*(.*)$')


def read_json(path):
    return json.loads(path.read_text())


def read_jsonl(path):
    with path.open() as f:
        return [json.loads(s) for s in f if s.strip()]


def timestamp(text):
    return dt.datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc).timestamp()


def stats(values):
    a=np.asarray(list(values),dtype=float)
    a=a[np.isfinite(a)]
    if not len(a): return {'n':0}
    return {'n':len(a),'sum':float(a.sum()),'mean':float(a.mean()),'min':float(a.min()),
            'p50':float(np.quantile(a,.5)),'p90':float(np.quantile(a,.9)),
            'p95':float(np.quantile(a,.95)),'p99':float(np.quantile(a,.99)), 'max':float(a.max())}


def concurrency(intervals, lo, hi):
    changes=collections.defaultdict(int)
    for a,b in intervals:
        a=max(a,lo); b=min(b,hi)
        if b>a: changes[a]+=1;changes[b]-=1
    changes[lo]+=0;changes[hi]+=0
    active=0;last=lo;area=0.;maximum=0;time_at=collections.defaultdict(float);segments=[]
    for t,change in sorted(changes.items()):
        if t>last:
            area+=active*(t-last);time_at[active]+=t-last;segments.append((last,t,active))
        active+=change;maximum=max(maximum,active);last=t
    return {'max':maximum,'mean':area/(hi-lo),'slot_seconds':area,
            'time_at':dict(time_at),'segments':segments}


def integrate_gpu(telemetry,lo,hi):
    total=0.;busy=0.;low=0.;gaps=0.;peak=0.;samples=[]
    for i,row in enumerate(telemetry[:-1]):
        a=max(lo,row['time_unix']);b=min(hi,telemetry[i+1]['time_unix'])
        if b<=a:continue
        vals=[float(v['GPU use (%)']) for v in row.get('gpu',{}).values()
              if isinstance(v,dict) and 'GPU use (%)' in v]
        if len(vals)!=8:continue
        duration=b-a;util=float(np.mean(vals));total+=duration;busy+=duration*util/100
        if util<50:low+=duration
        if telemetry[i+1]['time_unix']-row['time_unix']>30:gaps+=duration
        peak=max(peak,row.get('active_training_sandboxes',0))
        samples.append({'time_unix':row['time_unix'],'gpu_mean_pct':util,
                        'sandboxes_sampled':row.get('active_training_sandboxes')})
    return {'coverage_s':total,'mean_busy_pct':100*busy/total if total else None,
            'busy_gpu_seconds':8*busy,'under_50pct_s':low,'sampling_gaps_over_30s':gaps,
            'sampled_peak_sandboxes':peak,'samples':samples}


def parse_agent(path,identity):
    markers=[];queries=[];tools=[];current=None;pending=None;info={};issues=[]
    for line_number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
        match=STAMP.match(line)
        if not match:continue
        stamp,logger,level,message=match.groups(); t=timestamp(stamp)
        if logger.strip()=='agents.react.agent':
            st=re.search(r'=+ STEP (\d+) =+',message)
            tok=re.search(r'Prompt Tokens: (\d+), Completion Tokens: (\d+) \(total (\d+)\)',message)
            act=re.search(r'ACTION \(([^)]+)\):',message)
            terminal=re.search(r'OBSERVATION \(([^)]+)\):',message)
            timeout=re.search(r'TIMEOUT \(([^)]+)\):',message)
            err=re.search(r'TOOL ([A-Z_]+) \(([^)]+)\):',message)
            done=re.search(r'Episode done: termination_reason=(\S+) steps=(\d+) tool_calls=(\d+) timeouts=(\d+) errors=(\d+) total_tokens=(\d+)',message)
            if st:
                current={'turn':int(st.group(1)),'start':t,'line_start':line_number}
                info.setdefault('agent_start',t)
                markers.append({'time':t,'kind':'turn_start','turn':current['turn'],'line':line_number})
            elif tok:
                assert current is not None,(path,line_number)
                q={**identity,**current,'end':t,'duration_s':t-current['start'],
                   'prompt_tokens':int(tok.group(1)),'completion_tokens':int(tok.group(2)),
                   'context_plus_completion_tokens':int(tok.group(3)),'line_end':line_number}
                queries.append(q);markers.append({'time':t,'kind':'model_return','turn':current['turn'],'line':line_number})
            elif act:
                if pending:issues.append({'type':'unclosed_tool','tool':pending})
                pending={**identity,'turn':current['turn'] if current else 0,'tool':act.group(1),
                         'start':t,'line_start':line_number}
                markers.append({'time':t,'kind':'tool_start','tool':act.group(1),'line':line_number})
            elif terminal or timeout or err:
                if pending:
                    name=(terminal or timeout).group(1) if (terminal or timeout) else err.group(2)
                    assert name==pending['tool'],(path,line_number,pending,name)
                    status='ok' if terminal else 'timeout' if timeout else err.group(1).lower()
                    tools.append({**pending,'end':t,'duration_s':t-pending['start'],
                                  'status':status,'line_end':line_number})
                    markers.append({'time':t,'kind':'tool_end','tool':name,'status':status,'line':line_number});pending=None
            elif done:
                reason,steps,calls,timeouts,errors,total=done.groups()
                info.update(agent_end=t,termination_reason=reason,agent_steps=int(steps),
                            reported_tool_calls=int(calls),reported_timeouts=int(timeouts),
                            reported_tool_errors=int(errors),last_context_tokens=int(total))
                markers.append({'time':t,'kind':'agent_end','line':line_number})
        if 'running eval for ' in message:info['eval_start_log']=t;markers.append({'time':t,'kind':'eval_start','line':line_number})
        if 'eval finished in ' in message:info['eval_end_log']=t;markers.append({'time':t,'kind':'eval_end','line':line_number})
    if pending:issues.append({'type':'unclosed_tool_at_end','tool':pending})
    assert info['reported_tool_calls']==len(tools),(path,info,len(tools))
    assert info['agent_steps']==len(queries),(path,info,len(queries))
    assert info['reported_timeouts']==sum(t['status']=='timeout' for t in tools),(path,info)
    assert info['reported_tool_errors']==sum(t['status'] not in ['ok','timeout'] for t in tools),(path,info)
    assert not issues,(path,issues)
    return info,queries,tools,markers


def main(args):
    root=Path(args.runtime);out=Path(args.output);(out/'data').mkdir(parents=True,exist_ok=True)
    launch=read_json(root/'results/launch.json');state=read_json(root/'state.json')
    start=launch['started_unix'];end=state['exited_unix']
    results=[(p,read_json(p)) for p in sorted((root/'results/sessions').glob('*/result.json'))]
    results=[(p,r) for p,r in results if r['started_unix']>=start]
    assert len(results)==256
    selected={r['session_id'] for _,r in results}
    all_events=read_jsonl(root/'results/sandbox-events.jsonl')
    events=[e for e in all_events if e.get('phase')=='train' and e.get('session_id') in selected]
    per_session=collections.defaultdict(list)
    for e in events:per_session[e['session_id']].append(e)
    episodes=[];requests=[];tools=[];marker_rows=[];all_rpc=[];source_files=[]
    for result_path,r in results:
        sid=r['session_id'];batch=next(i for i in [1,2] if (root/f'results/trajectories/step_{i}'/sid).exists())
        directory=root/f'results/trajectories/step_{batch}'/sid
        sampled=re.search(r'session-sample-(\d+)-rollout-(\d+)-',sid)
        identity={'batch':batch,'instance_id':r['instance_id'],'sample_index':int(sampled.group(1)),
                  'rollout_index':int(sampled.group(2)),'session_id':sid}
        info,qs,ts,markers=parse_agent(directory/'task.log',identity)
        requests.extend(qs);tools.extend(ts);marker_rows.append({**identity,'markers':markers})
        framework=(directory/'framework.log').read_text().splitlines()
        framework_stamps=[timestamp(m.group(1)) for s in framework if (m:=STAMP.match(s))]
        es=sorted(per_session[sid],key=lambda e:e['time_unix'])
        creates=[e for e in es if e['event']=='created'];deletes=[e for e in es if e['event']=='destroyed']
        provisions=[e for e in es if e['event']=='tools_provisioned'];execs=[e for e in es if e['event']=='exec']
        assert len(creates)==len(deletes)==len(provisions)==1,(sid,creates,deletes,provisions)
        c,death,provision=creates[0],deletes[0],provisions[0]
        evaluator=execs[-1]; eval_duration=r['extra_info']['eval_execution_time']
        eval_end=evaluator['time_unix'];eval_start=eval_end-eval_duration
        assert abs(eval_end-info['eval_end_log'])<2,(sid,eval_end,info)
        assert eval_duration+0.2>=evaluator['seconds']
        assert evaluator['time_unix']>info['agent_end']-1
        create_start=c['time_unix']-c['seconds'];death_start=death['time_unix']-death['seconds']
        t0=r['started_unix'];t1=t0+r['elapsed_s']
        query_time=sum(q['duration_s'] for q in qs);tool_time=sum(t['duration_s'] for t in ts)
        agent_time=info['agent_end']-info['agent_start']
        assert query_time+tool_time<=agent_time+1,(sid,query_time,tool_time,agent_time)
        meta=read_json(directory/'trajectory.json');assert meta['num_trajectories']==1
        traj=meta['trajectories'][0]
        row={**identity,'reward':r['reward'],'finished':r['finished'],'task_start':t0,'task_end':t1,
             'framework_start':min(framework_stamps),'framework_end':max(framework_stamps),
             'framework_dispatch_s':t0-min(framework_stamps),
             'framework_finalize_log_delta_s':max(framework_stamps)-t1,
             'framework_finalize_lower_s':max(0,max(framework_stamps)-t1),
             'framework_finalize_upper_s':max(0,max(framework_stamps)+1-t1),
             'episode_s':r['elapsed_s'],'pre_create_local_s':create_start-t0,
             'create_wait_and_api_s':c['seconds'],'create_start':create_start,'created_at':c['time_unix'],
             'tools_provisioned_at':provision['time_unix'],'tools_provision_s':provision['seconds'],
             'post_create_gate_hold_lower_s':provision['time_unix']-c['time_unix'],
             'post_create_setup_s':info['agent_start']-c['time_unix'],
             'agent_start':info['agent_start'],'agent_end':info['agent_end'],'agent_s':agent_time,
             'model_roundtrip_s':query_time,'logical_tools_s':tool_time,
             'agent_local_residual_s':agent_time-query_time-tool_time,
             'verifier_start':eval_start,'verifier_end':eval_end,'verifier_s':eval_duration,
             'verifier_rpc_s':evaluator['seconds'],'verifier_gate_wrapper_s':eval_duration-evaluator['seconds'],
             'destroy_s':death['seconds'],'destroyed_at':death['time_unix'],
             'sandbox_lifetime_s':death['time_unix']-c['time_unix'],
             'eval_completed':r['extra_info']['eval_completed'],
             'found_eval_status':r['extra_info']['eval_report']['found_eval_status'],
             'llm_requests':len(qs),'logical_tool_calls':len(ts),
             'tool_timeouts':info['reported_timeouts'],'tool_errors':info['reported_tool_errors'],
             'turns':info['agent_steps'],'termination_reason':info['termination_reason'],
             'api_prompt_tokens_sum':sum(q['prompt_tokens'] for q in qs),
             'api_completion_tokens_sum':sum(q['completion_tokens'] for q in qs),
             'last_context_tokens':info['last_context_tokens'],
             'training_prompt_tokens':traj['prompt_len'],'training_response_tokens':traj['response_len'],
             'training_model_tokens':traj['model_token_count'],'trajectory_turns':traj['num_turns'],
             'exec_calls':len(execs),'exec_s':sum(e['seconds'] for e in execs),
             'file_read_calls':sum(e['event']=='file_read' for e in es),
             'file_write_calls':sum(e['event']=='file_write' for e in es),
             'file_read_s':sum(e.get('seconds',0) for e in es if e['event']=='file_read'),
             'file_write_s':sum(e.get('seconds',0) for e in es if e['event']=='file_write')}
        parts=['create_wait_and_api_s','post_create_setup_s','agent_s','verifier_s','destroy_s']
        row['other_episode_s']=row['episode_s']-sum(row[k] for k in parts)
        assert row['other_episode_s']>=-2,(sid,row['other_episode_s'])
        agent_exec=[e for e in execs if info['agent_start']<=e['time_unix']<=info['agent_end']]
        row['agent_exec_calls']=len(agent_exec);row['agent_exec_s']=sum(e['seconds'] for e in agent_exec)
        episodes.append(row)
        for e in es:
            if e['event'] in ['exec','file_read','file_write']:
                a=e['time_unix']-e['seconds'];b=e['time_unix']
                stage='verifier' if e is evaluator else 'agent' if info['agent_start']<=b<=info['agent_end'] else 'setup_or_cleanup'
                all_rpc.append({**identity,'event':e['event'],'executable':e.get('executable'),
                                'start':a,'end':b,'duration_s':e['seconds'],'stage':stage,
                                'exit_code':e.get('exit_code'),'bytes':e.get('size'),
                                'stdout_bytes':e.get('stdout_bytes'),'stderr_bytes':e.get('stderr_bytes')})
        source_files.extend([result_path,directory/'task.log',directory/'framework.log',directory/'trajectory.json'])
    ep=pd.DataFrame(episodes).sort_values(['batch','sample_index','rollout_index'])
    req=pd.DataFrame(requests);tool=pd.DataFrame(tools);rpc=pd.DataFrame(all_rpc)
    ep.to_csv(out/'data/episodes.csv',index=False);req.to_csv(out/'data/model_requests.csv.gz',index=False)
    tool.to_csv(out/'data/tool_actions.csv.gz',index=False);rpc.to_csv(out/'data/sandbox_rpc.csv.gz',index=False)
    with (out/'data/timing_markers.jsonl').open('w') as f:
        for row in marker_rows:f.write(json.dumps(row)+'\n')
    telemetry=read_jsonl(root/'results/telemetry.jsonl')
    batches={};groups=[]
    for batch,g in ep.groupby('batch'):
        assert len(g)==128 and g.instance_id.nunique()==16
        lo=float(g.framework_start.min());hi=float(max(g.framework_end.max(),g.task_end.max()))
        window=hi-lo;end_times=np.sort(g.task_end.to_numpy())
        occ=concurrency(zip(g.created_at,g.destroyed_at),lo,hi)
        exec_events=rpc[(rpc.batch==batch)&(rpc.event=='exec')]
        rpc_occ=concurrency(zip(exec_events.start,exec_events.end),lo,hi)
        gpu=integrate_gpu(telemetry,lo,hi)
        quantiles={str(k):float(end_times[int(np.ceil(k*128))-1]-lo) for k in [.5,.9,.95,.99,1.]}
        last=g.loc[g.task_end.idxmax()]
        batches[int(batch)]={'framework_start':lo,'framework_end':hi,'observed_wall_s':window,
            'runner_observed_wall_s':float(g.task_end.max()-g.task_start.min()),
            'completed':len(g),'resolved':int(g.reward.sum()),'completion_quantiles_s':quantiles,
            'tail_after_90pct_s':float(g.task_end.max()-end_times[int(np.ceil(.9*128))-1]),
            'tail_after_95pct_s':float(g.task_end.max()-end_times[int(np.ceil(.95*128))-1]),
            'last_sample_penalty_s':float(end_times[-1]-end_times[-2]),
            'last_session_id':last.session_id,'last_instance_id':last.instance_id,
            'first_created_offset_s':float(g.created_at.min()-lo),
            'last_created_offset_s':float(g.created_at.max()-lo),
            'creation_ramp_s':float(g.created_at.max()-g.created_at.min()),
            'created_per_s_during_ramp':127/float(g.created_at.max()-g.created_at.min()),
            'cum_episode_s':float(g.episode_s.sum()),'cum_sandbox_s':float(g.sandbox_lifetime_s.sum()),
            'cum_model_roundtrip_s':float(g.model_roundtrip_s.sum()),'cum_verifier_s':float(g.verifier_s.sum()),
            'cum_tokens_generated':int(g.api_completion_tokens_sum.sum()),
            'generated_tokens_per_observed_wall_s':float(g.api_completion_tokens_sum.sum()/window),
            'client_live':{k:v for k,v in occ.items() if k!='segments'},
            'instrumented_exec_concurrency':{k:v for k,v in rpc_occ.items() if k!='segments'},
            'gpu':{k:v for k,v in gpu.items() if k!='samples'},
            'critical_verifier_s':float(last.verifier_s),
            'fixed_trace_zero_verifier_reduction_s':float(g.task_end.max()-(g.task_end-g.verifier_s).max()),
            'statistics':{c:stats(g[c]) for c in ['episode_s','framework_dispatch_s','create_wait_and_api_s',
                'post_create_gate_hold_lower_s','tools_provision_s','post_create_setup_s','agent_s',
                'model_roundtrip_s','logical_tools_s','verifier_s','destroy_s','turns','api_completion_tokens_sum']}}
        pd.DataFrame(occ['segments'],columns=['start','end','active']).to_csv(out/f'data/batch{batch}_occupancy.csv',index=False)
        pd.DataFrame(gpu['samples']).to_csv(out/f'data/batch{batch}_telemetry.csv',index=False)
        for iid,h in g.groupby('instance_id',sort=False):
            assert len(h)==8 and set(h.rollout_index)==set(range(8))
            fast=h.loc[h.episode_s.idxmin()];slow=h.loc[h.episode_s.idxmax()]
            groups.append({'batch':int(batch),'instance_id':iid,'sample_index':int(h.sample_index.iloc[0]),
                'resolved':int(h.reward.sum()),'min_s':float(h.episode_s.min()),'median_s':float(h.episode_s.median()),
                'mean_s':float(h.episode_s.mean()),'max_s':float(h.episode_s.max()),
                'max_min_ratio':float(h.episode_s.max()/h.episode_s.min()),
                'max_median_ratio':float(h.episode_s.max()/h.episode_s.median()),
                'cv':float(h.episode_s.std(ddof=0)/h.episode_s.mean()),
                'completion_spread_s':float(h.task_end.max()-h.task_end.min()),
                'post_finish_group_wait_slot_s':float((h.task_end.max()-h.task_end).sum()),
                'slowest_session':slow.session_id,'fastest_session':fast.session_id,
                'slowest_turns':int(slow.turns),'fastest_turns':int(fast.turns),
                'slowest_model_tokens':int(slow.api_completion_tokens_sum),
                'fastest_model_tokens':int(fast.api_completion_tokens_sum),
                'zero_reward_variance':bool(h.reward.nunique()==1),
                'cum_episode_s':float(h.episode_s.sum()),'cum_generated_tokens':int(h.api_completion_tokens_sum.sum())})
    group=pd.DataFrame(groups).sort_values(['batch','sample_index']);group.to_csv(out/'data/groups.csv',index=False)
    numeric=['episode_s','agent_s','model_roundtrip_s','logical_tools_s','verifier_s','turns',
             'api_completion_tokens_sum','api_prompt_tokens_sum','last_context_tokens','create_wait_and_api_s']
    corrs=ep[numeric].corr(method='spearman').to_dict()
    demeaned=ep[numeric]-ep.groupby(['batch','instance_id'])[numeric].transform('mean')
    summary={'runtime':str(root),'launch':launch,'state':state,'batches':batches,
             'episode_statistics':{c:stats(ep[c]) for c in ep.select_dtypes(include='number').columns
                                    if c.endswith('_s') or c in ['turns','llm_requests','logical_tool_calls','api_completion_tokens_sum']},
             'request_statistics':{'latency_s':stats(req.duration_s),'prompt_tokens':stats(req.prompt_tokens),
                                   'completion_tokens':stats(req.completion_tokens)},
             'tool_statistics':{name:{'n':len(g),'duration_s':stats(g.duration_s),'statuses':g.status.value_counts().to_dict()}
                                for name,g in tool.groupby('tool')},
             'rpc_statistics':{name:{'n':len(g),'duration_s':stats(g.duration_s)}
                               for name,g in rpc.groupby(['event','stage'])},
             'event_counts':dict(collections.Counter(e['event'] for e in events)),
             'spearman':corrs,'within_group_demeaned_pearson':demeaned.corr().to_dict(),
             'tool_timeout_count':int(ep.tool_timeouts.sum()),'tool_error_count':int(ep.tool_errors.sum()),
             'termination_counts':ep.termination_reason.value_counts().to_dict(),
             'eval_completed_count':int(ep.eval_completed.sum()),'eval_parseable_count':int(ep.found_eval_status.sum()),
             'model_tokens_match_log_completion':bool((ep.training_model_tokens==ep.api_completion_tokens_sum).all()),
             'token_deltas':stats(ep.training_model_tokens-ep.api_completion_tokens_sum),
             'group_max_min_ratio':stats(group.max_min_ratio),'group_max_median_ratio':stats(group.max_median_ratio),
             'zero_variance_groups':int(group.zero_reward_variance.sum()),
             'zero_variance_episode_time_fraction':float(group.loc[group.zero_reward_variance,'cum_episode_s'].sum()/group.cum_episode_s.sum()),
             'zero_variance_generated_token_fraction':float(group.loc[group.zero_reward_variance,'cum_generated_tokens'].sum()/group.cum_generated_tokens.sum()),
             'step1_metrics':read_jsonl(root/'results/train-metrics.jsonl')[0]['data']}
    summary['rpc_statistics']={'/'.join(k):v for k,v in summary['rpc_statistics'].items()}
    (out/'data/summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    direct=['results/train-metrics.jsonl','results/telemetry.jsonl','results/sandbox-events.jsonl',
            'results/failure-summary.json','results/capacity-128.json','results/capacity-128-attempt-1.json',
            'results/kernel-fault-summary.txt','configs/train.yaml','configs/task_config.yaml']
    source_files.extend(root/p for p in direct)
    manifest={str(p):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in source_files}
    (out/'data/source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'episodes':len(ep),'groups':len(group),'requests':len(req),'tools':len(tool),
                      'rpc_records':len(rpc),'tool_timeouts':summary['tool_timeout_count'],
                      'tool_errors':summary['tool_error_count'],'zero_variance_groups':summary['zero_variance_groups']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--runtime',default='/home/yuhanya/yuhan-uniAgent/experiments/colocate-e2b-3step-20260920')
    p.add_argument('--output',default=str(Path(__file__).resolve().parents[1]))
    main(p.parse_args())
