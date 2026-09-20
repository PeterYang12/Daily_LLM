"""Create reproducible vector/raster figures from the recorded measurements."""
import argparse,json,statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':180})

def save(fig,name):
    out=ROOT/'figures';out.mkdir(exist_ok=True)
    for ext in ['svg','png']:fig.savefig(out/(name+'.'+ext),bbox_inches='tight',facecolor='white')
    plt.close(fig)

def architecture():
    fig,ax=plt.subplots(figsize=(14,7));ax.set(xlim=(0,14),ylim=(0,7));ax.axis('off')
    ax.add_patch(FancyBboxPatch((.3,.8),9.1,5.65,boxstyle='round,pad=.05',facecolor='#f1f5fb',edgecolor='#93a9c6'))
    ax.text(.5,6.18,'Docker ua-rl-gpu  |  8 AMD MI350X GPUs',fontsize=14,weight='bold')
    ax.text(.45,6.83,'Uni-Agent: full RL with remote E2B sandboxes',fontsize=19,weight='bold')
    def box(x,y,w,h,label,color='#ffffff'):
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=.04',facecolor=color,edgecolor='#64748b'))
        ax.text(x+w/2,y+h/2,label,ha='center',va='center',fontsize=10)
    def arrow(points,color='#334155',style='->'):
        for a,b in zip(points[:-2],points[1:-1]):ax.plot([a[0],b[0]],[a[1],b[1]],color=color,lw=1.4)
        ax.annotate('',xy=points[-1],xytext=points[-2],arrowprops={'arrowstyle':style,'color':color,'lw':1.4})
    box(.6,5.05,2.05,.8,'Frozen tasks\n62 training tasks\n16 evaluation tasks')
    box(3.05,5.05,5.85,.8,'verl / Ray trainer + Uni-Agent framework\n300 updates; 4 prompts × 4 rollouts')
    box(.6,3.3,2.05,1.0,'Task + ReAct workers\nCPU; concurrency 16')
    box(3.25,3.3,2.1,1.0,'Gateway (CPU)\n4 FastAPI actors\nPer-rollout sessions')
    box(6.2,3.3,2.7,1.0,'vLLM rollout (GPU)\n4 replicas × TP 2','#dcfce7')
    box(.6,1.45,2.25,1.0,'SWE verifier controller\nBinary test reward')
    box(3.3,1.45,2.25,1.0,'Trajectories\nToken IDs / masks\nLogprobs / rewards')
    box(6.2,1.45,2.7,1.0,'Megatron GSPO (GPU)\nEP 8; full parameters','#dcfce7')
    box(10.05,4.5,3.55,.9,'User-hosted E2B API\n10.145.64.67:8088','#fff7ed')
    box(10.05,3.25,3.55,.85,'Agent sandbox\n4 CPU / 8 GiB','#fff7ed')
    box(10.05,2.0,3.55,.85,'Fresh verifier sandbox\nSame task template','#fff7ed')
    box(10.05,.4,3.55,.9,'W&B\nMetrics, curves, and report','#fef3c7')
    arrow([(2.65,5.45),(3.05,5.45)])
    arrow([(3.15,5.05),(3.15,4.9),(1.0,4.9),(1.0,4.3)])
    ax.text(2.1,4.96,'Launch tasks',fontsize=8,ha='center')
    arrow([(4.3,5.05),(4.3,4.3)])
    arrow([(2.65,3.8),(3.25,3.8)],style='<->')
    arrow([(5.35,3.8),(6.2,3.8)],style='<->')
    ax.text(5.77,4.02,'Model API',fontsize=8,ha='center')
    arrow([(1.6,4.3),(1.6,4.68),(9.65,4.68),(10.05,4.93)],color='#c2410c')
    ax.text(7.1,4.73,'Lifecycle / command / file RPC',fontsize=9,color='#9a3412',ha='center')
    arrow([(11.82,4.5),(11.82,4.1)],color='#c2410c')
    arrow([(11.82,3.25),(11.82,2.85)],color='#c2410c')
    ax.text(12.05,3.02,'Destroy, then create',fontsize=8,va='center')
    arrow([(1.7,3.3),(1.7,2.45)])
    ax.text(1.85,2.82,'Candidate patch',fontsize=8,ha='left')
    arrow([(4.3,3.3),(4.3,2.45)])
    ax.text(4.43,2.82,'Finalize session',fontsize=8,ha='left')
    arrow([(2.85,1.95),(3.3,1.95)])
    arrow([(5.55,1.95),(6.2,1.95)])
    arrow([(7.55,2.45),(7.55,3.3)],color='#15803d')
    ax.text(7.7,2.82,'Weight sync\neach step',fontsize=8,va='center',color='#166534')
    arrow([(1.7,1.45),(1.7,1.08),(9.65,1.08),(9.65,2.42),(10.05,2.42)],color='#c2410c')
    ax.text(5.0,1.13,'Independent verification RPC',fontsize=8,ha='center',color='#9a3412')
    arrow([(8.9,1.65),(9.35,1.65),(9.35,.85),(10.05,.85)],color='#92400e')
    ax.text(.6,.33,'GPU training and rollout alternate. In-flight agent sandboxes retain state across policy updates.',fontsize=10)
    save(fig,'architecture')
    (ROOT/'figures/architecture.mmd').write_text('''flowchart LR
  subgraph D["Docker: 8 AMD MI350X GPUs"]
    Data["62 SWE-reBench train / 16 SWE-bench evaluation"] --> F["verl + Uni-Agent framework"]
    F --> A["ReAct CPU workers: concurrency 16"]
    A <--> G["Uni-Agent Gateway: 4 FastAPI actors"]
    G <--> L["vLLM: 4 TP2 replicas"]
    G --> T["Trajectory IDs, masks, logprobs"]
    A -->|"candidate patch"| V
    V["Official SWE verifier controller"] --> T
    T --> O["Megatron GSPO: EP8, full parameters"]
    O -->|"weight sync each step"| L
  end
  A -->|"create / exec / files"| E["User E2B API: 10.145.64.67:8088"]
  E --> S["Agent sandbox: 4 CPU / 8 GiB"]
  S -->|"destroy; create fresh verifier"| R["Verification sandbox"]
  V <--> R
  O --> W["W&B metrics and profiles"]
''')

def measurements(summary_path):
    summary=json.loads(summary_path.read_text());rows=summary['training_updates']
    x=np.array([r['step'] for r in rows]);reward=np.array([r.get('critic/rewards/mean',np.nan) for r in rows])
    fig,axes=plt.subplots(2,2,figsize=(12,7.4))
    ax=axes[0,0];ax.plot(x,reward,color='#94a3b8',alpha=.55,lw=1,label='Train batch reward')
    width=min(20,len(x))
    if width:ax.plot(x[width-1:],np.convolve(reward,np.ones(width)/width,mode='valid'),color='#2563eb',lw=2,label=f'{width}-step moving mean')
    val=summary['validation']
    if val:
        vx=[v['step'] for v in val];vy=[v['accuracy'] for v in val]
        error=np.array([[v['accuracy']-v['wilson_95'][0] for v in val],[v['wilson_95'][1]-v['accuracy'] for v in val]])
        ax.errorbar(vx,vy,yerr=error,fmt='o',color='#ea580c',capsize=4,label='Fixed 16-task evaluation; Wilson 95%')
    ax.set(ylabel='Reward / resolved fraction',ylim=(-.03,1.03));ax.legend(fontsize=8)
    axes[0,1].plot(x,[r.get('timing_s/step',np.nan) for r in rows],color='#2563eb',lw=1)
    axes[0,1].set(ylabel='Training step wall time (s)')
    axes[1,0].plot(x,[r.get('actor/grad_norm',np.nan) for r in rows],color='#15803d')
    axes[1,0].set(ylabel='Actor gradient norm before clipping',xlabel='Optimizer step')
    batches=summary['consumed_batches']
    axes[1,1].plot([b['step'] for b in batches],[b['mixed_reward_groups']/max(1,b['prompt_groups']) for b in batches],color='#7c3aed',alpha=.65)
    axes[1,1].set(ylabel='Groups with nonconstant reward',ylim=(-.05,1.05),xlabel='Optimizer step')
    for ax in axes.flat:ax.grid(alpha=.18)
    fig.suptitle('Full parameter ReAct RL: learning signals and update cost',fontsize=14)
    fig.tight_layout();save(fig,'training-curves')

    names=['gen','old_log_prob','update_actor','update_weights'];labels=['Rollout wait','Old policy logprob','Actor update','Weight synchronization']
    ordinary=[r for r in rows if r['step']%100]
    values=[statistics.mean(r.get('timing_s/'+k,0) for r in ordinary) if ordinary else 0 for k in names]
    fig,ax=plt.subplots(figsize=(9,3.5));bars=ax.barh(labels,values,color=['#2563eb','#7c3aed','#15803d','#d97706'])
    for bar,value in zip(bars,values):ax.text(value+.3,bar.get_y()+bar.get_height()/2,f'{value:.1f} s',va='center')
    ax.set_xlabel('Mean wall time per ordinary update (s)');ax.set_title('Serial trainer phases; checkpoint steps excluded');ax.invert_yaxis();ax.grid(axis='x',alpha=.2)
    fig.tight_layout();save(fig,'trainer-phase-times')

    native=json.loads((ROOT/'results/native-exec-acceptance/summary.json').read_text())['latency_summary']
    fig,ax=plt.subplots(figsize=(7,3.5));vals=[native[m]['mean_ms'] for m in ['sdk','native']]
    bars=ax.bar(['SDK login-shell wrapper','Direct envd argv RPC'],vals,color=['#94a3b8','#2563eb'])
    for bar,value in zip(bars,vals):ax.text(bar.get_x()+bar.get_width()/2,value+4,f'{value:.2f} ms',ha='center')
    ax.set(ylabel='Mean command round trip (ms)',title='Controlled crossover: same sandbox, 20 samples per mode')
    ax.set_ylim(0,max(vals)*1.22);ax.grid(axis='y',alpha=.2);fig.tight_layout();save(fig,'sandbox-command-latency')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--architecture-only',action='store_true');p.add_argument('--preview',action='store_true');a=p.parse_args()
    architecture()
    if not a.architecture_only:
        measurements(ROOT/'results'/('training-progress-summary.json' if a.preview else 'training-summary.json'))
