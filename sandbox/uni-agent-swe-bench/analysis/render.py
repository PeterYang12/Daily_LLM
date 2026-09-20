"""Build report figures and additional descriptive statistics from extracted CSVs."""
import collections
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.colors import LogNorm
from extract import stats, concurrency, integrate_gpu, read_jsonl

OUT=Path(__file__).resolve().parents[1]
COLORS={'create':'#8b5fbf','setup':'#9aa1aa','model':'#2878b5','tools':'#e69f00',
        'verifier':'#299d72','destroy':'#555555','other':'#cccccc'}
plt.rcParams.update({'font.size':10,'axes.titlesize':12,'axes.labelsize':10,'figure.facecolor':'white',
                     'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none'})


def savefig(fig,name):
    fig.savefig(OUT/'figures'/f'{name}.png',dpi=180,bbox_inches='tight')
    fig.savefig(OUT/'figures'/f'{name}.svg',bbox_inches='tight')
    plt.close(fig)


def gantt_group(ep,req,tool,iid,batch,name,title):
    g=ep[(ep.instance_id==iid)&(ep.batch==batch)].sort_values('rollout_index')
    origin=g.task_start.min()
    fig,ax=plt.subplots(figsize=(11,5.4))
    for _,r in g.iterrows():
        y=r.rollout_index
        def bar(a,b,color):
            if b>a:ax.broken_barh([((a-origin)/60,(b-a)/60)],(y-.32,.64),facecolors=color,edgecolors='none')
        bar(r.task_start,r.created_at,COLORS['create'])
        bar(r.created_at,r.agent_start,COLORS['setup'])
        for q in req[req.session_id==r.session_id].itertuples():bar(q.start,q.end,COLORS['model'])
        for t in tool[tool.session_id==r.session_id].itertuples():bar(t.start,t.end,COLORS['tools'])
        bar(r.verifier_start,r.verifier_end,COLORS['verifier'])
        ax.text((r.task_end-origin)/60+.12,y,f"{r.episode_s/60:.1f}m | {int(r.turns)} turns | R={int(r.reward)}",va='center',fontsize=8)
    ax.set_yticks(range(8),[f'Sample {i}' for i in range(8)]);ax.invert_yaxis()
    ax.set_xlabel('Minutes since the first runner in this task group entered')
    ax.set_title(title+'\n'+iid)
    ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
    ax.set_xlim(0,(g.task_end.max()-origin)/60+6)
    ax.legend(handles=[Patch(color=COLORS[k],label=l) for k,l in
        [('create','Creation wait + API'),('setup','Post-create setup'),('model','Model roundtrip'),
         ('tools','Logical tools'),('verifier','Verifier')]],loc='upper center',bbox_to_anchor=(.5,-.13),ncol=3,fontsize=8)
    fig.tight_layout();savefig(fig,name)


def main():
    (OUT/'figures').mkdir(exist_ok=True)
    ep=pd.read_csv(OUT/'data/episodes.csv');groups=pd.read_csv(OUT/'data/groups.csv')
    req=pd.read_csv(OUT/'data/model_requests.csv.gz');tool=pd.read_csv(OUT/'data/tool_actions.csv.gz')
    rpc=pd.read_csv(OUT/'data/sandbox_rpc.csv.gz');summary=json.loads((OUT/'data/summary.json').read_text())
    raw=Path(summary['runtime'])
    def evidence_file(name):
        copied=OUT/'evidence'/name
        return copied if copied.is_file() else raw/'results'/name
    telemetry=read_jsonl(evidence_file('telemetry.jsonl'))
    metrics=summary['step1_metrics'];facts={}
    phase_names=[('gen','Rollout collection'),('old_log_prob','Actor logprob recomputation'),
                 ('update_actor','Actor update'),('update_weights','Weight synchronization'),('adv','Advantages')]
    phases=[{'phase':label,'seconds':metrics['timing_s/'+key]} for key,label in phase_names]
    phases.append({'phase':'Unattributed timer remainder','seconds':metrics['timing_s/step']-sum(p['seconds'] for p in phases)})
    for p in phases:p['percent']=100*p['seconds']/metrics['timing_s/step']
    facts['step1_wall_phases']=phases
    pd.DataFrame(phases).to_csv(OUT/'data/step1_wall_phases.csv',index=False)
    fig,ax=plt.subplots(figsize=(10,4.6))
    ax.barh([p['phase'] for p in phases],[p['seconds']/60 for p in phases],color=[COLORS['model'],'#6f9bc4','#bd4a4a','#7a9259','#ba9dca','#bbbbbb'])
    for y,p in enumerate(phases):ax.text(p['seconds']/60+.15,y,f"{p['seconds']:.2f}s  ({p['percent']:.3f}%)",va='center',fontsize=9)
    ax.invert_yaxis();ax.set_xlabel('Minutes of completed trainer step 1');ax.set_xlim(0,37)
    ax.set_title('Step 1: authoritative, non-overlapping wall-clock timers');ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
    fig.tight_layout();savefig(fig,'01_step1_wall_phases')

    launch=summary['launch']['started_unix'];exited=summary['state']['exited_unix']
    fault_line=next(line for line in evidence_file('kernel-fault-summary.txt').read_text().splitlines()
                    if 'uncorrectable hardware error' in line)
    fault=dt.datetime.fromisoformat(fault_line.split()[0].replace(',','.')).timestamp()
    facts['timeline']={'launch_unix':launch,'first_uncorrectable_event_unix':fault,'process_exit_unix':exited}
    samples=[]
    for r in telemetry:
        if not launch<=r['time_unix']<=exited:continue
        vals=[float(x['GPU use (%)']) for x in r.get('gpu',{}).values() if isinstance(x,dict) and 'GPU use (%)' in x]
        samples.append({'minutes':(r['time_unix']-launch)/60,'gpu':np.mean(vals) if len(vals)==8 else np.nan,
                        'active':r.get('active_training_sandboxes',np.nan)})
    tm=pd.DataFrame(samples)
    fig,axs=plt.subplots(2,1,figsize=(11,6.5),sharex=True)
    axs[0].plot(tm.minutes,tm.active,color=COLORS['model'],label='API-sampled live sandboxes')
    axs[0].axhline(128,color='gray',ls='--',lw=1,label='Configured session cap 128')
    axs[0].set_ylabel('Live sandbox count');axs[0].legend(loc='upper right',fontsize=8)
    axs[1].plot(tm.minutes,tm.gpu,color='#d27a13');axs[1].set_ylabel('Mean GPU busy (%)');axs[1].set_ylim(0,105)
    axs[1].set_xlabel('Minutes from attempt 3 launch')
    for b in [1,2]:
        x=summary['batches'][str(b)];a=(x['framework_start']-launch)/60;z=(x['framework_end']-launch)/60
        for ax in axs:ax.axvspan(a,z,color=COLORS['model'],alpha=.05)
        axs[0].text((a+z)/2,132,f'Rollout batch {b}',ha='center',fontsize=9)
    opt=read_jsonl(evidence_file('optimizer/rank-0.jsonl'))[0]['time_unix']
    for ax in axs:
        ax.axvline((opt-launch)/60,color=COLORS['verifier'],ls=':',label='Update 1')
        ax.axvline((fault-launch)/60,color='#c83333',ls='--');ax.grid(alpha=.2)
    axs[1].text((opt-launch)/60+.3,6,'Update 1',color=COLORS['verifier'],fontsize=8)
    axs[1].text((fault-launch)/60-1,8,'GPU fault',color='#c83333',ha='right',fontsize=8)
    fig.tight_layout();savefig(fig,'02_attempt_timeline')

    fig,axs=plt.subplots(1,2,figsize=(11,4.5))
    startup={}
    for b,g in ep.groupby('batch'):
        base=summary['batches'][str(b)]['framework_start']; created=np.sort(g.created_at.to_numpy())
        axs[0].step((created-base)/60,np.arange(1,129),where='post',label=f'Batch {b}')
        durations=np.sort(g.create_wait_and_api_s.to_numpy())
        axs[1].plot(durations,np.arange(1,129)/128,label=f'Batch {b}')
        first,last=created[0],created[-1]
        hold=sum(max(0,min(r.tools_provisioned_at,last)-max(r.created_at,first)) for r in g.itertuples())
        startup[str(b)]={'first3_creation_seconds':g.sort_values('created_at').head(3).create_wait_and_api_s.tolist(),
            'post_create_gate_occupancy_lower_fraction':hold/(3*(last-first)),
            'post_create_gate_hold_mean_s':g.post_create_gate_hold_lower_s.mean(),
            'cached_packages_mean_s':g.tools_provision_s.mean(),
            'readiness_and_other_in_gate_mean_s':(g.post_create_gate_hold_lower_s-g.tools_provision_s).mean(),
            'dispatch_log_upper_bound_stats':stats(g.framework_dispatch_s)}
    axs[0].set(xlabel='Minutes from first framework session',ylabel='Cumulative successful creates',title='Sandbox creation ramp')
    axs[1].set(xlabel='Creation wait + API seconds',ylabel='Fraction of runners',title='This counter includes client queuing')
    for ax in axs:ax.legend();ax.grid(alpha=.2)
    fig.tight_layout();savefig(fig,'03_startup_ramp_and_wait')
    facts['startup']=startup

    component_columns=[('create_wait_and_api_s','Creation wait + API','create'),('post_create_setup_s','Post-create prep','setup'),
       ('model_roundtrip_s','Model roundtrips','model'),('logical_tools_s','Logical tools','tools'),
       ('verifier_s','Verifier','verifier'),('destroy_s','Destroy','destroy'),
       ('agent_local_residual_s','Agent local/resolution residue','other'),('other_episode_s','Other runner/resolution residue','other')]
    parts=[]
    for col,label,color in component_columns:
        value=ep[col].sum();parts.append({'phase':label,'seconds':float(value),'runner_lifetime_percent':100*value/ep.episode_s.sum()})
    facts['aggregate_runner_phases']=parts;pd.DataFrame(parts).to_csv(OUT/'data/aggregate_runner_phases.csv',index=False)
    fig,ax=plt.subplots(figsize=(10,4))
    for j,(batch,g) in enumerate(ep.groupby('batch')):
        offset=0
        for col,label,color in component_columns:
            value=100*g[col].sum()/g.episode_s.sum();ax.barh(j,value,left=offset,color=COLORS[color],label=label if j==0 else None)
            if value>3:ax.text(offset+value/2,j,f'{value:.1f}%',ha='center',va='center',color='white' if color=='model' else 'black',fontsize=9)
            offset+=value
    ax.set_yticks([0,1],['Batch 1','Batch 2']);ax.invert_yaxis();ax.set_xlim(0,100)
    ax.set_xlabel('Share of summed runner lifetimes (%) — NOT batch wall time')
    ax.set_title('What each active runner spends its lifetime waiting for')
    ax.legend(loc='upper center',bbox_to_anchor=(.5,-.22),ncol=3,fontsize=8)
    fig.tight_layout();savefig(fig,'04_runner_time_composition')

    fig,axs=plt.subplots(1,2,figsize=(11,4.5))
    for b,g in ep.groupby('batch'):
        durations=np.sort(g.episode_s.to_numpy()/60)
        axs[0].plot(durations,np.arange(1,129)/128,label=f'Batch {b}')
        base=summary['batches'][str(b)]['framework_start']
        completions=np.sort((g.task_end.to_numpy()-base)/60)
        axs[1].plot(np.arange(1,129)/128*100,completions,label=f'Batch {b}')
    axs[0].set(xlabel='Runner duration (minutes)',ylabel='Fraction complete',title='Episode duration CDF')
    axs[1].set(xlabel='Completed rollouts (%)',ylabel='Minutes from first framework session',title='Last samples dominate the barrier')
    axs[1].axvline(90,color='gray',ls=':',lw=1)
    for ax in axs:ax.legend();ax.grid(alpha=.2)
    fig.tight_layout();savefig(fig,'05_duration_and_completion_tail')

    order=groups.sort_values(['batch','sample_index'])
    matrix=np.array([[float(ep[(ep.batch==g.batch)&(ep.instance_id==g.instance_id)&(ep.rollout_index==n)].episode_s.iloc[0])/60
                     for n in range(8)] for g in order.itertuples()])
    fig,ax=plt.subplots(figsize=(12,13))
    im=ax.imshow(matrix,aspect='auto',cmap='YlOrRd',vmin=0,vmax=30)
    ax.set_yticks(range(len(order)),[f'B{int(r.batch)} {r.instance_id}' for r in order.itertuples()],fontsize=8)
    ax.set_xticks(range(8),[f'Sample {i}' for i in range(8)])
    for i,g in enumerate(order.itertuples()):
        for j in range(8):
            reward=int(ep[(ep.batch==g.batch)&(ep.instance_id==g.instance_id)&(ep.rollout_index==j)].reward.iloc[0])
            ax.text(j,i,f'{matrix[i,j]:.1f}'+('*' if reward else ''),ha='center',va='center',fontsize=8,
                    color='white' if matrix[i,j]>19 else 'black')
    ax.axhline(15.5,color='black',lw=1.5)
    ax.set_title('All 32 tasks × 8 samples: runner minutes\n* = verifier resolved; includes creation queuing')
    fig.colorbar(im,ax=ax,fraction=.025,pad=.02,label='Minutes');fig.tight_layout();savefig(fig,'06_all_8_sample_groups')
    gantt_group(ep,req,tool,'agronholm__anyio-639',1,'07_anyio_eight_samples','Batch 1 critical group')
    gantt_group(ep,req,tool,'hylang__hy-2522',2,'08_hy_eight_samples','Batch 2 critical group')
    gantt_group(ep,req,tool,'diogommartins__simple_json_logger-14',2,'09_largest_sample_ratio','Largest fastest/slowest duration ratio')

    fig,axs=plt.subplots(1,2,figsize=(11,4.5))
    for b,g in ep.groupby('batch'):
        axs[0].scatter(g.api_completion_tokens_sum/1000,g.agent_s/60,s=16,alpha=.6,label=f'Batch {b}')
        qs=req[req.batch==b]
        axs[1].scatter(qs.completion_tokens,qs.duration_s,s=5,alpha=.18,label=f'Batch {b}')
    axs[0].set(xlabel='Generated model tokens (thousands)',ylabel='Agent-loop minutes',title='Longer generated traces correlate with longer episodes')
    axs[1].set(xlabel='Completion tokens per model request',ylabel='Model roundtrip seconds',title='Roundtrip includes queue + inference + transport')
    axs[1].set_xscale('symlog',linthresh=10);axs[1].set_yscale('symlog',linthresh=1)
    for ax in axs:ax.legend();ax.grid(alpha=.2)
    fig.tight_layout();savefig(fig,'10_model_work_and_latency')

    fig,axs=plt.subplots(1,2,figsize=(11,4.5))
    axs[0].hist(ep.verifier_s,bins=25,color=COLORS['verifier'],alpha=.8)
    axs[0].set(xlabel='Verifier call seconds',ylabel='Rollout count',title='All 256 verifier calls')
    command=rpc[rpc.event=='exec'].groupby('executable').agg(n=('duration_s','size'),seconds=('duration_s','sum')).sort_values('n')
    axs[1].barh(command.index,command.n,color=COLORS['tools']);axs[1].set(xlabel='Instrumented exec RPC count',title='Control operations dominate call counts')
    for ax in axs:ax.grid(alpha=.2);ax.set_axisbelow(True)
    fig.tight_layout();savefig(fig,'11_verifier_and_rpc_calls')

    tails={}
    for b in [1,2]:
        x=summary['batches'][str(b)];cut=x['framework_start']+x['completion_quantiles_s']['0.9']
        gi=integrate_gpu(telemetry,cut,x['framework_end'])
        tails[str(b)]={'after90_mean_gpu_busy_pct':gi['mean_busy_pct'],
                      'after90_wall_fraction':x['tail_after_90pct_s']/x['observed_wall_s'],
                      'after95_wall_fraction':x['tail_after_95pct_s']/x['observed_wall_s'],
                      'last_sample_wall_fraction':x['last_sample_penalty_s']/x['observed_wall_s']}
    facts['tails']=tails
    exec_agent=rpc[(rpc.event=='exec')&(rpc.stage=='agent')]
    facts['rpc']={'exec_count':int((rpc.event=='exec').sum()),'agent_exec_count':len(exec_agent),
                  'agent_total_rpc_count':int((rpc.stage=='agent').sum()),
                  'agent_rpc_per_logical_tool':float((rpc.stage=='agent').sum()/len(tool)),
                  'agent_exec_per_logical_tool':len(exec_agent)/len(tool),
                  'agent_exec_under_1s_fraction':float((exec_agent.duration_s<1).mean()),
                  'nonzero_exec_exits':int(((rpc.event=='exec')&(rpc.exit_code!=0)).sum()),
                  'command_counts':command.to_dict('index')}
    for b in [1,2]:
        x=summary['batches'][str(b)]['instrumented_exec_concurrency'];dur=summary['batches'][str(b)]['observed_wall_s']
        facts['rpc'][f'batch{b}_time_at_16_s']=sum(float(t) for k,t in x['time_at'].items() if int(k)>=16)
        facts['rpc'][f'batch{b}_gate_capacity_utilization']=x['slot_seconds']/(16*dur)
    facts['model']={'generated_tokens':int(ep.api_completion_tokens_sum.sum()),
                    'summed_api_prompt_tokens':int(ep.api_prompt_tokens_sum.sum()),
                    'training_sequence_tokens':int((ep.training_prompt_tokens+ep.training_response_tokens).sum()),
                    'training_model_tokens':int(ep.training_model_tokens.sum()),
                    'model_roundtrip_share_of_sandbox_lifetime':float(ep.model_roundtrip_s.sum()/ep.sandbox_lifetime_s.sum()),
                    'model_roundtrip_share_of_agent_loop':float(ep.model_roundtrip_s.sum()/ep.agent_s.sum()),
                    'max_context':int(ep.last_context_tokens.max()),'max_turns':int(ep.turns.max())}
    facts['accounting']={'runner_s':float(ep.episode_s.sum()),'sandbox_s':float(ep.sandbox_lifetime_s.sum()),
                         'dispatch_log_delta_negative':int((ep.framework_dispatch_s<0).sum()),
                         'phase_residual_min_s':float(ep.other_episode_s.min()),
                         'phase_residual_max_s':float(ep.other_episode_s.max())}
    facts['group_statistics']=groups.groupby('batch').agg(groups=('instance_id','size'),zero_variance=('zero_reward_variance','sum'),
        median_max_min=('max_min_ratio','median'),max_max_min=('max_min_ratio','max'),
        mean_max_median=('max_median_ratio','mean')).to_dict('index')
    facts['critical_samples']=[]
    for b in [1,2]:
        sid=summary['batches'][str(b)]['last_session_id']
        facts['critical_samples'].append(ep[ep.session_id==sid].iloc[0].to_dict())
    (OUT/'data/additional-facts.json').write_text(json.dumps(facts,indent=2)+'\n')
    print(json.dumps({'figures':len(list((OUT/'figures').glob('*.png'))),'tail_metrics':tails,
                      'runner_phase_shares':parts,'model':facts['model']},indent=2))


if __name__=='__main__':main()
