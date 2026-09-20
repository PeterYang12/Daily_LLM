"""Render the three-deployment comparison from measured evidence."""
import argparse,json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main(root):
    data=json.loads((root/'analysis/summary.json').read_text());out=root/'analysis/figures';out.mkdir(exist_ok=True)
    names=['docker','local_e2b','remote_e2b'];labels=['Local Docker','Local E2B','Remote E2B-compatible']
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'path'})
    fig,ax=plt.subplots(figsize=(10.4,4.8));bottom=np.zeros(3)
    components=[('model_api_s','Model API','#356fad'),('gateway_client_s','Gateway / client','#8eb8db'),('tools_s','Tools','#e39042'),('tool_setup_s','Tool setup','#edbf88'),('provision_s','Prepare both sandboxes','#58a18a'),('verifier_s','Verifier','#a178b3')]
    for key,label,color in components:
        values=np.array([data['groups'][n]['means'][key] for n in names]);ax.bar(labels,values,bottom=bottom,label=label,color=color,width=.55);bottom+=values
    totals=np.array([data['groups'][n]['means']['wall_seconds'] for n in names])
    ax.bar(labels,np.maximum(0,totals-bottom),bottom=bottom,label='Export / cleanup / other',color='#b8bcc5',width=.55)
    for i,n in enumerate(names):
        g=data['groups'][n];value=totals[i]
        ax.errorbar(i,value,yerr=[[value-g['wall_min_s']],[g['wall_max_s']-value]],fmt='none',ecolor='#333333',capsize=5)
        ax.text(i,g['wall_max_s']+4,f'{value:.1f}s mean',ha='center',fontweight='bold')
    ax.set_ylim(0,max(data['groups'][n]['wall_max_s'] for n in names)*1.2);ax.set_ylabel('Seconds per rollout');ax.set_title('Same Flask issue, same model service, three samples per deployment',loc='left',fontweight='bold');ax.legend(frameon=False,bbox_to_anchor=(1,1),loc='upper left');ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.text(.12,-.035,'Whiskers: observed min/max, not confidence intervals. Sampled token and tool counts differ.',fontsize=8)
    fig.savefig(out/'rollout-comparison.svg',bbox_inches='tight');plt.close(fig)
    micro={(r['variant'],r['operation']):r for r in data['microbench']}
    ops=['exec_true','read_1024','write_1024','stateful_shell_true'];oplabels=['Execute true','Read 1 KiB','Write 1 KiB','Stateful shell: true'];xs=np.arange(len(ops))
    fig,ax=plt.subplots(figsize=(10.4,4.3))
    for j,(n,label,color) in enumerate(zip(names,labels,['#356fad','#3d967a','#e39042'])):
        values=[micro[n,k]['median_s']*1000 for k in ops];pos=xs+(j-1)*.25
        ax.bar(pos,values,.23,label=label,color=color)
        for x,value in zip(pos,values):ax.text(x,value*1.07,f'{value:.1f}',ha='center',fontsize=8)
    ax.set_xticks(xs,oplabels);ax.set_yscale('log');ax.set_ylim(1,2500);ax.set_ylabel('Median client-observed ms (log scale)');ax.set_title('Fixed-operation comparison through the current adapters',loc='left',fontweight='bold');ax.legend(frameon=False,loc='upper left');ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.text(.12,-.035,'Same source SWE image; active sandbox, excluding startup. Exec includes the provider\'s shell/process semantics.',fontsize=8)
    fig.savefig(out/'operation-comparison.svg',bbox_inches='tight');plt.close(fig)
    print('Rendered two comparison figures')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);main(p.parse_args().root)
