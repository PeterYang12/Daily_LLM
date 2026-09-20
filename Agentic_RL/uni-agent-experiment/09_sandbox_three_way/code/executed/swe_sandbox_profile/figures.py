"""Standalone scientific figures from saved profiling evidence."""
import argparse,json,statistics
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

COLORS={'docker':'#2869b2','e2b':'#df8933'}

def main(root):
    analysis=root/'analysis';out=analysis/'figures';out.mkdir(exist_ok=True)
    summary=json.loads((analysis/'summary.json').read_text());cases=json.loads((analysis/'cases.json').read_text())
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none'})
    def save(fig,name):
        fig.savefig(out/(name+'.svg'),bbox_inches='tight');plt.close(fig)

    providers=[p for p in ['docker','e2b'] if p in summary['providers']]
    fig,ax=plt.subplots(figsize=(10.4,4.8));bottom=np.zeros(len(providers))
    components=[('model_api_s','Model API','#356fad'),('gateway_client_s','Gateway / client','#8eb8db'),
                ('tools_s','Tools','#e39042'),('tool_setup_s','Tool initialization','#edbf88'),
                ('provision_s','Environment preparation','#58a18a'),('verifier_s','Verifier','#a178b3')]
    used=np.zeros(len(providers))
    for key,label,color in components:
        values=np.array([summary['providers'][p]['mean_components_s'][key] for p in providers])
        ax.bar(providers,values,bottom=bottom,label=label,color=color,width=.5);bottom+=values;used+=values
    totals=np.array([summary['providers'][p]['mean_wall_s'] for p in providers])
    ax.bar(providers,np.maximum(0,totals-used),bottom=bottom,label='Export / cleanup / other',color='#b8bcc5',width=.5)
    for x,p,total in zip(range(len(providers)),providers,totals):ax.text(x,total+4,f'{total:.1f}s; n={summary["providers"][p]["n"]}',ha='center',fontweight='bold')
    ax.set_ylim(0,max(totals)*1.14);ax.set_ylabel('Mean seconds per task');ax.set_title('Where rollout time is spent',loc='left',fontweight='bold')
    ax.legend(loc='upper left',bbox_to_anchor=(1,1),frameon=False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.text(.12,-.035,'Independent sampled trajectories can contain different token/tool counts. These are observed task times, not a pure platform speed ratio.',fontsize=8)
    save(fig,'rollout-stages')

    micro={p:{r['operation']:r for r in json.loads((root/'microbench'/p/'summary.json').read_text())} for p in providers}
    names=['exec_true','read_1024','write_1024','stateful_shell_true'];labels=['Execute true','Read 1 KiB','Write 1 KiB','Stateful shell: true']
    fig,ax=plt.subplots(figsize=(10.5,4.2));xs=np.arange(len(names))
    for j,p in enumerate(providers):
        values=[micro[p][n]['median_s']*1000 for n in names]
        ax.bar(xs+(j-.5)*.34,values,width=.32,label=p,color=COLORS[p])
        for x,v in zip(xs+(j-.5)*.34,values):ax.text(x,v*1.06,f'{v:.0f}',ha='center',fontsize=9)
    ax.set_yscale('log');ax.set_ylim(20,2300);ax.set_xticks(xs,labels);ax.set_ylabel('Median client latency (ms, log scale)')
    ax.set_title('Fixed-operation comparison on matched Flask environments',loc='left',fontweight='bold');ax.legend(frameon=False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.text(.12,-.035,'10–30 repeats per operation. Stateful shell uses tmux; the E2B no-op expands into four exec operations plus one file write.',fontsize=8)
    save(fig,'operation-latency')

    fig,axs=plt.subplots(1,2,figsize=(10.8,4.2))
    for ax,field,title in [(axs[0],'gpu_busy_mean','Per-task mean serving-GPU activity'),(axs[1],'tool_fraction','Tools as a share of agent time')]:
        for index,p in enumerate(providers):
            group=[r[field] for r in cases if r['provider']==p and r[field] is not None]
            if field=='tool_fraction':group=[100*v for v in group]
            ax.scatter([index+(i-len(group)/2)*.012 for i in range(len(group))],group,color=COLORS[p],s=27,alpha=.8)
            if group:ax.plot([index-.2,index+.2],[statistics.median(group)]*2,color=COLORS[p],lw=3)
        ax.set_xticks(range(len(providers)),providers);ax.set_ylim(0,105);ax.set_ylabel('Percent');ax.set_title(title,loc='left',fontweight='bold');ax.grid(axis='y',alpha=.15)
    fig.tight_layout();fig.text(.065,-.035,'Dots: individual primary tasks; line: median. GPU data are 1 Hz samples on PCI 0000:66:00.0, not eight-GPU fleet utilization.',fontsize=8)
    save(fig,'gpu-and-tools')

    iid='django__django-14373';fig,ax=plt.subplots(figsize=(11,3.5));max_time=0
    for index,p in enumerate(providers):
        path=root/'main-optimized'/p/iid/'spans.json'
        if not path.exists():continue
        events=json.loads(path.read_text())['events'];agent=next(e for e in events if e['category']=='phase' and e['name']=='agent');origin=agent['start_ns'];duration=agent['duration_ns']/1e9;max_time=max(max_time,duration)
        ax.broken_barh([(0,duration)],(index-.25,.5),facecolors='#e3e6eb')
        for category,color in [('model_api','#356fad'),('tool_call','#e39042')]:
            segments=[((e['start_ns']-origin)/1e9,e['duration_ns']/1e9) for e in events if e['category']==category and e['phase']=='agent']
            ax.broken_barh(segments,(index-.25,.5),facecolors=color)
    ax.set_xlim(0,max_time*1.02);ax.set_yticks(range(len(providers)),providers);ax.set_xlabel('Seconds from agent start');ax.set_title('Django 14373: model requests and tool waits',loc='left',fontweight='bold')
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color='#356fad',label='Model API'),Patch(color='#e39042',label='Tool call'),Patch(color='#e3e6eb',label='Setup / client / other')],loc='upper center',bbox_to_anchor=(.5,-.2),ncol=3,frameon=False)
    save(fig,'django-timeline')

    scheduling=summary.get('scheduling',[])
    if len(scheduling)==4:
        fig,axs=plt.subplots(1,2,figsize=(10.8,4.2))
        for p in providers:
            rows=sorted((r for r in scheduling if r['provider']==p),key=lambda r:r['concurrency'])
            axs[0].plot([r['concurrency'] for r in rows],[r['rollouts_per_minute'] for r in rows],'o-',label=p,color=COLORS[p],lw=2)
            axs[1].plot([r['concurrency'] for r in rows],[r['generated_tokens_per_wall_second'] for r in rows],'o-',label=p,color=COLORS[p],lw=2)
        for ax in axs:ax.set_xticks([1,4]);ax.set_xlabel('Concurrent tasks');ax.grid(alpha=.15);ax.legend(frameon=False)
        axs[0].set_ylabel('Scored trajectories / minute');axs[0].set_title('Scheduling throughput',loc='left',fontweight='bold')
        axs[1].set_ylabel('Generated tokens / batch wall second');axs[1].set_title('Token-normalized throughput',loc='left',fontweight='bold')
        fig.tight_layout();fig.text(.07,-.045,'Four independent Flask rollouts per setting on one serving GPU. Sampling changes the amount of work; this is an exploratory scheduling study.',fontsize=8)
        save(fig,'scheduling')

    path=root/'inference-benchmark/summary.json'
    if path.exists():
        rows=json.loads(path.read_text())
        for field,ylabel,name in [('output_tokens_per_s','Output tokens / second','serving-throughput'),('mean_ttft_s','Client time to first token (s)','serving-ttft')]:
            fig,axs=plt.subplots(1,3,figsize=(12,3.7))
            for ax,length in zip(axs,[2048,16384,65536]):
                for backend,color in [('eager','#8059a4'),('aiter_graph','#287a71')]:
                    group=sorted((r for r in rows if r['backend']==backend and r['input_length']==length),key=lambda r:r['concurrency'])
                    ax.plot([r['concurrency'] for r in group],[r[field] for r in group],'o-',label=backend,color=color,lw=2)
                    if field=='output_tokens_per_s':ax.fill_between([r['concurrency'] for r in group],[r['throughput_min'] for r in group],[r['throughput_max'] for r in group],alpha=.12,color=color)
                ax.set_title(f'{length//1024}K input tokens',loc='left',fontweight='bold');ax.set_xticks([1,4,8]);ax.set_xlabel('Concurrent requests');ax.grid(alpha=.15)
            axs[0].set_ylabel(ylabel);axs[0].legend(frameon=False,fontsize=8);fig.tight_layout()
            fig.text(.065,-.045,'256 forced output tokens; logprobs and token IDs enabled; unique prompt salts; three repeats. Same BF16 model and 128K serving capacity.',fontsize=8)
            save(fig,name)
    print('Generated SVG figures:',[p.name for p in out.glob('*.svg')])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);main(p.parse_args().root)
