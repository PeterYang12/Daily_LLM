"""Three English slides: phase accounting, two cases, eight-sample tails/tool stalls."""
import json
from pathlib import Path

import pandas as pd
from build_slides import ROOT, OUT, W, H, C, Scene, render, pptx

STEM='Uni-Agent_Time_Breakdown_EN'


def header(s,title,subtitle,page):
    s.rect(0,0,W,H,C['white'])
    s.rect(40,26,5,29,C['blue'],2)
    s.text(title,55,25,870,25,bold=True)
    s.text(subtitle,55,69,865,12,C['muted'])
    s.text(f'{page} / 3',878,517,38,9,C['muted'],align='right')


def percent(n):
    return '<0.01%' if n<.01 else f'{n:.2f}%'


def phase_row(s,x,y,w,label,seconds,share,color,height=7,label_size=14):
    s.text(label,x,y,w*.52,label_size,C['ink'],bold=bool(share>50))
    seconds_label=f'{seconds:,.1f}s' if seconds>=.1 else f'{seconds:.3f}s'
    s.text(f'{seconds_label}  |  {percent(share)}',x+w*.47,y+1,w*.53,12,color,bold=True,align='right')
    s.rect(x,y+23,w,height,'E5EBF3')
    s.rect(x,y+23,w*share/100,height,color)


def main():
    summary=json.loads((ROOT/'data/summary.json').read_text())
    facts=json.loads((ROOT/'data/additional-facts.json').read_text())
    ep=pd.read_csv(ROOT/'data/episodes.csv')
    queries=pd.read_csv(ROOT/'data/model_requests.csv.gz')
    tools=pd.read_csv(ROOT/'data/tool_actions.csv.gz')
    api=pd.read_csv(ROOT/'data/sandbox_rpc.csv.gz')
    batch_ep=ep[ep.batch==1]
    group=batch_ep[batch_ep.instance_id=='agronholm__anyio-639'].sort_values('rollout_index')
    typical=group[group.rollout_index==4].iloc[0]
    sid=typical.session_id
    case_q=queries[queries.session_id==sid]
    case_t=tools[tools.session_id==sid]
    case_api=api[api.session_id==sid]
    m=summary['step1_metrics'];b=summary['batches']['1']
    step=m['timing_s/step']
    step_phases=[('Rollout collection',m['timing_s/gen'],C['blue']),
                 ('Actor update',m['timing_s/update_actor'],C['purple']),
                 ('Log-prob recompute',m['timing_s/old_log_prob'],'9A82C4'),
                 ('Weight sync',m['timing_s/update_weights'],C['green']),
                 ('Advantage compute',m['timing_s/adv'],C['orange'])]
    step_phases.append(('Other',step-sum(r[1] for r in step_phases),C['muted']))
    attempt_parts=[('LLM calls*',batch_ep.model_roundtrip_s.mean(),C['blue']),
                   ('Create queue + API',batch_ep.create_wait_and_api_s.mean(),C['purple']),
                   ('Tool actions',batch_ep.logical_tools_s.mean(),C['orange']),
                   ('Sandbox setup',batch_ep.post_create_setup_s.mean(),'8C9BAD'),
                   ('Verifier',batch_ep.verifier_s.mean(),C['green']),
                   ('Local / other',(batch_ep.agent_local_residual_s+batch_ep.other_episode_s).mean(),C['muted']),
                   ('Delete API ack',batch_ep.destroy_s.mean(),C['muted'])]
    avg=batch_ep.episode_s.mean()

    a=Scene()
    header(a,'Rollout dominates; LLM calls and startup drive its cost',
           'STEP 1  |  16 tasks × 8 attempts = 128 rollouts  |  Qwen3-Coder-30B-A3B-Instruct',1)
    a.rect(40,108,430,358,C['bg'],6)
    a.rect(494,108,426,358,C['bg'],6)
    a.text('1  FULL STEP',56,121,350,16,bold=True)
    a.text('35.32',56,151,150,32,C['blue'],bold=True)
    a.text('min wall time',201,165,232,14,C['muted'])
    a.text('Seconds and share of the complete step',56,193,395,11,C['muted'])
    a.text('2  INSIDE ROLLOUT',510,121,350,16,bold=True)
    a.text(f'{avg/60:.2f}',510,151,152,32,C['blue'],bold=True)
    a.text('min / attempt, on average',652,166,252,13,C['muted'])
    a.text('Mean seconds and share across 128 parallel attempts',510,193,394,10.5,C['muted'])
    a.text('›',473,157,18,29,C['blue'],align='center')
    for i,(label,seconds,color) in enumerate(step_phases):
        phase_row(a,56,219+i*39,398,label,seconds,100*seconds/step,color,6,13.5)
    for i,(label,seconds,color) in enumerate(attempt_parts):
        phase_row(a,510,216+i*33.8,394,label,seconds,100*seconds/avg,color,5,13)
    a.text('Different denominators: step wall time vs. average parallel attempt time.',44,476,870,11,C['muted'])
    a.text('*LLM time includes serving queue, inference and network; model/tool timings use 1-second log markers.',44,494,870,10,C['muted'])
    a.text('Rollout includes dispatch, environment setup, agent execution and verification.',44,517,810,9,C['muted'])

    # Compare two attempts on the same task; preserve a common elapsed-time axis.
    c=Scene()
    header(c,"Two cases: a typical attempt and the batch's long tail",
           'Same task: AnyIO #639 · Same batch and model · Neither case had a tool timeout',2)
    startup=typical.agent_start-typical.task_start
    origin=float(typical.task_start);duration=float(typical.episode_s)
    counts=[('Execute',int((case_api.event=='exec').sum()),C['orange']),
            ('File write',int((case_api.event=='file_write').sum()),C['purple']),
            ('File read',int((case_api.event=='file_read').sum()),C['green'])]
    long_tail=group[group.rollout_index==2].iloc[0]
    case_records=[]
    for x,row,label,accent,detail in [
        (40,typical,'CASE A  |  TYPICAL · Sample 4',C['blue'],'Near batch median: 11.56 min'),
        (494,long_tail,'CASE B  |  LONG TAIL · Sample 2',C['red'],'Last of all 128 attempts to finish')]:
        q=queries[queries.session_id==row.session_id]
        t=tools[tools.session_id==row.session_id]
        rpc=api[api.session_id==row.session_id]
        width=426;inside=394;left=x+16
        c.rect(x,106,width,348,C['bg'],6)
        c.text(label,left,119,inside,13,accent,bold=True)
        c.text(f'{row.episode_s/60:.2f}',left,145,194,38,accent,bold=True)
        c.text('min',left+157,164,78,18,accent)
        c.text(detail,left,194,inside,12,C['muted'])
        gx=left;gy=223;axis_s=30*60;start=float(row.task_start)
        c.rect(gx,gy,inside,17,'E3EAF3')
        def span(a,z,color):
            if z>a:c.rect(gx+(a-start)/axis_s*inside,gy,(z-a)/axis_s*inside,17,color)
        span(start,row.agent_start,C['purple'])
        for request in q.itertuples():span(request.start,request.end,C['blue'])
        for action in t.itertuples():span(action.start,action.end,C['orange'])
        span(row.verifier_start,row.verifier_end,C['green'])
        for minute in [0,10,20,30]:
            at=gx+inside*minute/30
            c.line(at,242,at,246,C['muted'],.5)
            c.text(str(minute)+(' min' if minute==30 else ''),at-18,248,52,8.5,C['muted'],align='center')
        metrics=[('Sandbox startup',f'{row.agent_start-row.task_start:.0f}s',C['purple']),
                 ('LLM calls*',f'{row.model_roundtrip_s:,.0f}s · {len(q)} calls',C['blue']),
                 ('Tool actions',f'{row.logical_tools_s:.0f}s · {len(t)} actions',C['orange']),
                 ('Verifier',f'{row.verifier_s:.1f}s',C['green']),
                 ('Sandbox API calls',f'{len(rpc)} logged',C['ink'])]
        for i,(name,value,color) in enumerate(metrics):
            yy=273+i*27
            c.rect(left,yy+4,5,11,color,1)
            c.text(name,left+12,yy,186,12.5,C['ink'])
            c.text(value,left+183,yy,inside-183,13,color,bold=True,align='right')
        api_counts={event:int((rpc.event==event).sum()) for event in ['exec','file_write','file_read']}
        c.text(f"{api_counts['exec']} exec · {api_counts['file_write']} file writes · {api_counts['file_read']} file reads",left+12,405,inside-12,10,C['muted'])
        c.text('Generated tokens',left+12,429,206,12.5,C['ink'])
        c.text(f'{row.api_completion_tokens_sum/1000:.1f}K',left+223,427,inside-223,17,accent,bold=True,align='right')
        case_records.append({'session_id':row.session_id,'sample':int(row.rollout_index),
                             'runtime_s':float(row.episode_s),'startup_s':float(row.agent_start-row.task_start),
                             'llm_s':float(row.model_roundtrip_s),'llm_calls':len(q),
                             'tool_s':float(row.logical_tools_s),'tool_actions':len(t),
                             'verifier_s':float(row.verifier_s),'sandbox_logged_api_calls':len(rpc),'api_counts':api_counts,
                             'generated_tokens':int(row.api_completion_tokens_sum),'tool_timeouts':int(row.tool_timeouts)})
    runtime_gap=long_tail.episode_s-typical.episode_s
    llm_gap=long_tail.model_roundtrip_s-typical.model_roundtrip_s
    c.rect(40,470,426,32,C['bg'],4)
    c.text(f'{100*llm_gap/runtime_gap:.0f}% of the runtime gap is extra LLM time',52,479,404,13,C['blue'],bold=True)
    c.rect(494,470,426,32,'FFF1EB',4)
    c.text(f'+{b["last_sample_penalty_s"]/60:.2f} min waiting for this last sample',506,479,402,13,C['red'],bold=True)
    c.text('Startup includes queue. *LLM = queue + inference + network. API counts cover logged exec/file calls.',44,517,824,9,C['muted'])

    # Eight attempts per task: actual intervals, a shared 0–30 minute axis,
    # final test outcomes, and a separately marked observed tool timeout.
    d=Scene()
    header(d,'Eight samples reveal long tails and tool stalls',
           'Batch 1 · Two tasks, 8 independent attempts each · Common 0–30 min time scale',3)
    legend=[('Startup',C['purple']),('LLM calls',C['blue']),('Tools',C['orange']),
            ('Verifier',C['green']),('Tool timeout',C['red'])]
    for x,(name,color) in zip([44,183,336,465,610],legend):
        d.rect(x,107,9,9,color,1)
        d.text(name,x+15,103,151,11,C['muted'])
    timeout_events=tools[tools.status=='timeout']
    assert len(timeout_events)==1
    timeout=timeout_events.iloc[0]
    timeout_episode=ep[ep.session_id==timeout.session_id].iloc[0]
    chart_groups=[]
    for panel_x,iid,title,subtitle in [
        (40,'agronholm__anyio-639','AnyIO #639  |  long tail','No tool errors; 1 of 8 repairs passed final tests'),
        (494,'Shopify__shopify_python_api-363','Shopify #363  |  recovered timeout','Sample 1: one shell command stalled')]:
        selected=batch_ep[batch_ep.instance_id==iid].sort_values('rollout_index')
        assert len(selected)==8
        d.rect(panel_x,137,426,305,C['bg'],6)
        d.text(title,panel_x+16,149,400,14,bold=True)
        d.text(subtitle,panel_x+16,174,400,10,C['muted'])
        barx=panel_x+44;barw=287
        d.text('min',panel_x+337,191,43,8.5,C['muted'],align='right')
        d.text('Test',panel_x+391,191,28,8.5,C['muted'],align='right')
        plotted=[]
        for j,row in enumerate(selected.itertuples()):
            y=211+j*19.1
            q=queries[queries.session_id==row.session_id]
            t=tools[tools.session_id==row.session_id]
            start=float(row.task_start)
            is_timeout=bool(row.tool_timeouts)
            is_critical=row.session_id==b['last_session_id']
            label_color=C['red'] if is_timeout or is_critical else C['muted']
            d.text(f'S{row.rollout_index}',panel_x+16,y-2,28,10,label_color,bold=is_timeout or is_critical)
            d.rect(barx,y,barw,10,'E3EAF3')
            def segment(lo,hi,color):
                if hi>lo:d.rect(barx+(lo-start)/1800*barw,y,(hi-lo)/1800*barw,10,color)
            segment(start,row.agent_start,C['purple'])
            for request in q.itertuples():segment(request.start,request.end,C['blue'])
            for action in t.itertuples():
                segment(action.start,action.end,C['red'] if action.status=='timeout' else C['orange'])
            segment(row.verifier_start,row.verifier_end,C['green'])
            d.text(f'{row.episode_s/60:.2f}',panel_x+335,y-2,45,10,label_color,bold=is_timeout or is_critical,align='right')
            d.text('PASS' if row.reward else 'FAIL',panel_x+388,y-1,31,8.5,C['green'] if row.reward else C['muted'],bold=bool(row.reward),align='right')
            plotted.append({'session_id':row.session_id,'sample':int(row.rollout_index),
                            'duration_s':float(row.episode_s),'test_passed':bool(row.reward),
                            'tool_timeouts':int(row.tool_timeouts),'tool_errors':int(row.tool_errors)})
        for minute in [0,10,20,30]:
            at=barx+barw*minute/30
            d.line(at,359,at,363,C['muted'],.5)
            d.text(str(minute),at-11,366,25,9,C['muted'],align='center')
        if iid=='agronholm__anyio-639':
            d.text(f'+{b["tail_after_90pct_s"]/60:.2f} min',panel_x+16,390,183,25,C['red'],bold=True)
            d.text('after 90% finish',panel_x+207,403,202,13,C['red'])
            d.text('32% of batch time remains; last sample alone adds 5.53 min',panel_x+16,426,403,9,C['muted'])
        else:
            d.text(f'{timeout.duration_s:.0f}s timeout',panel_x+16,390,201,25,C['red'],bold=True)
            d.text(f'{100*timeout.duration_s/timeout_episode.episode_s:.0f}% of this sample',panel_x+221,403,190,12,C['red'])
            d.text('Malformed search command; recovered before the batch ended',panel_x+16,426,403,9,C['muted'])
        chart_groups.append({'instance_id':iid,'axis_max_s':1800,'samples':plotted})
    b2=summary['batches']['2']
    d.rect(40,455,426,46,'FFF1EB',4)
    d.text('The tail repeats in batch 2',52,464,403,12,C['red'],bold=True)
    d.text(f'{b2["tail_after_90pct_s"]/60:.2f} min / 45% remains after 90% finish',52,483,403,11,C['red'])
    d.rect(494,455,426,46,C['bg'],4)
    d.text('Recorded tool errors across 256 rollouts',506,464,400,12,C['ink'],bold=True)
    d.text('1 timeout + 1 argument error; both recovered',506,483,400,11,C['muted'])
    d.text('PASS/FAIL = final repair tests, not tool-call status. AnyIO had no tool timeout. Bars use per-attempt start times.',44,517,824,9,C['muted'])

    scenes=[a,c,d]
    evidence={'scope':'Detailed phase/case charts use completed step 1 and batch 1; the third slide also reports batch-2 tail duration and tool-error totals across both completed batches.',
              'step_total_s':step,'step_phases':[{'phase':l,'seconds':s,'pct':100*s/step} for l,s,_ in step_phases],
              'attempt_mean_s':avg,'attempt_phases':[{'phase':l,'mean_seconds':s,'pct':100*s/avg} for l,s,_ in attempt_parts],
              'typical_case':{'session_id':sid,'instance_id':typical.instance_id,'sample':4,
                              'selection':'Near batch runtime median and exactly the median Agent call count of 40',
                              'episode_s':duration,'batch_median_s':batch_ep.episode_s.median(),'startup_s':startup,
                              'llm_calls':len(case_q),'llm_s':typical.model_roundtrip_s,'generated_tokens':int(typical.api_completion_tokens_sum),
                              'tool_actions':len(case_t),'tool_s':typical.logical_tools_s,'verifier_s':typical.verifier_s,
                              'recorded_exec_file_calls':len(case_api),'api_counts':{l:n for l,n,_ in counts},
                              'api_scope':'Includes instrumented exec/file APIs across setup, agent and verifier. Excludes create/delete (shown separately) and uninstrumented readiness/provision calls.'},
              'tails':{'after90_s':b['tail_after_90pct_s'],'batch_span_s':b['observed_wall_s'],'creation_ramp_s':b['creation_ramp_s']},
              'two_cases':case_records,
              'case_comparison':{'timeline_axis_max_s':1800,'runtime_gap_s':float(runtime_gap),
                                 'llm_gap_s':float(llm_gap),'llm_fraction_of_runtime_gap':float(llm_gap/runtime_gap),
                                 'last_sample_penalty_s':b['last_sample_penalty_s']},
              'eight_sample_comparison':chart_groups,
              'tool_failure_example':{'session_id':timeout.session_id,'duration_s':float(timeout.duration_s),
                                      'sample_duration_s':float(timeout_episode.episode_s),
                                      'fraction_of_sample_time':float(timeout.duration_s/timeout_episode.episode_s),
                                      'recovered':bool(timeout_episode.finished),
                                      'finished_before_critical_sample':bool(timeout_episode.task_end<long_tail.task_end)},
              'all_attempt_tool_events':{'episodes':len(ep),'timeouts':int(ep.tool_timeouts.sum()),
                                         'argument_errors':int(ep.tool_errors.sum())},
              'accounting':'Step wall time is not the sum of parallel attempt durations. Rollout internals use batch-1 cumulative component time / cumulative attempt lifetime, equivalent to mean component seconds / mean attempt seconds. LLM roundtrip includes serving queue + inference + network.'}
    render(scenes,STEM,'time-breakdown-en')
    pptx(scenes,STEM,'en-US')
    (OUT/'time-breakdown-en-data.json').write_text(json.dumps(evidence,indent=2)+'\n')
    (OUT/'time-breakdown-en-scene.json').write_text(json.dumps([s.items for s in scenes],ensure_ascii=False)+'\n')
    print(json.dumps({'slides':len(scenes),'pptx':str(OUT/(STEM+'.pptx')),
                      'words_per_slide':[len(' '.join(o['text'] for o in s.items if o['kind']=='text').split()) for s in scenes]},indent=2))


if __name__=='__main__':main()
