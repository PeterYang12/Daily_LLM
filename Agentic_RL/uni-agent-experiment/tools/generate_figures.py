import json,statistics
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'assets/figures';OUT.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':150})
D=json.loads((ROOT/'evidence/summary/final-summary.json').read_text());order=['react','claude','mini'];labels=['ReAct','Claude Code','mini-swe-agent'];colors=['#2468ce','#8055ab','#12846b'];outputs=[]
def save(fig,name):
 for suffix in ['svg','png']:fig.savefig(OUT/(name+'.'+suffix),bbox_inches='tight')
 plt.close(fig);outputs.append(name)
fig,ax=plt.subplots(figsize=(9,4.3));values=[D['agents'][a]['pass_fraction']*100 for a in order];bars=ax.bar(labels,values,color=colors,width=.55)
for b,a in zip(bars,order):
 s=D['agents'][a];ax.text(b.get_x()+b.get_width()/2,b.get_height()+2,f"{s['resolved']}/28 ({s['pass_fraction']:.1%})",ha='center',fontweight='bold')
ax.set_ylim(0,100);ax.set_ylabel('Resolved cases (%)');ax.set_title('Same 30B weights, 28 validated SWE-bench tasks',loc='left',fontweight='bold');ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
fig.text(.125,-.02,'One attempt per harness. Planned stratified sample: 30; environment-qualified subset: 28.',fontsize=9,color='#586b82');save(fig,'agent-results')
fig,axs=plt.subplots(1,2,figsize=(11,4.1))
for ax,values,title,ylabel in [(axs[0],[D['agents'][a]['model_generated_tokens']/28/1000 for a in order],'Retained generation per task','Mask=1 tokens (thousands)'),(axs[1],[D['agents'][a]['median_agent_seconds']/60 for a in order],'Median agent execution time','Minutes')]:
 bars=ax.bar(labels,values,color=colors,width=.6);ax.set_title(title,loc='left',fontweight='bold');ax.set_ylabel(ylabel);ax.set_ylim(0,max(values)*1.22);ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
 for b,v in zip(bars,values):ax.text(b.get_x()+b.get_width()/2,b.get_height()+max(values)*.035,f'{v:.2f}',ha='center')
fig.tight_layout();fig.text(.07,-.035,'Token counts come from retained trajectories. Timing reflects concurrent jobs on a shared model service.',fontsize=9,color='#586b82');save(fig,'agent-cost')
fig,axs=plt.subplots(1,2,figsize=(11.8,4.4))
for ax,folder,curves,title in [(axs[0],'serving-benchmark',[('tp1','TP1 eager','#2468ce'),('tp4','TP4 eager','#8055ab')],'Tensor parallelism (eager)'),(axs[1],'serving-aiter-benchmark',[('tp1_eager','TP1 eager','#2468ce'),('tp1_aiter_graph','TP1 AITER + graphs','#12846b')],'Single-GPU execution config')]:
 base=ROOT/'evidence/performance'/folder;summary=json.loads((base/'summary.json').read_text())['results'];raw=json.loads((base/'raw.json').read_text())
 for key,label,color in curves:
  rs=[r for r in summary if r['backend']==key];xs=[r['concurrency'] for r in rs];ys=[r['aggregate_output_tok_s'] for r in rs]
  low=[];high=[]
  for r in rs:
   vs=[q['aggregate_output_tok_s'] for q in raw if q['backend']==key and q['concurrency']==r['concurrency']];low.append(min(vs));high.append(max(vs))
  ax.plot(xs,ys,'o-',lw=2,label=label,color=color);ax.fill_between(xs,low,high,color=color,alpha=.15)
 ax.set_title(title,loc='left',fontweight='bold');ax.set_xticks([1,4,8]);ax.set_xlabel('Concurrent requests');ax.set_ylabel('Output tokens / second');ax.grid(alpha=.18);ax.legend(frameon=False,fontsize=9)
fig.tight_layout();fig.text(.055,-.045,'2,048 input / 256 forced output tokens; 3 repeats. Shading: observed min-max. Panels are separate experiments.',fontsize=9,color='#586b82');save(fig,'serving-throughput')
base=ROOT/'evidence/performance/serving-replicas-benchmark-v2';summary=json.loads((base/'summary.json').read_text())['results'];raw=json.loads((base/'raw.json').read_text());fig,axs=plt.subplots(1,2,figsize=(11.5,4.3))
for count,color in [(1,'#2468ce'),(2,'#12846b')]:
 rs=[r for r in summary if r['replicas']==count];xs=[r['concurrency'] for r in rs];ys=[r['aggregate_output_tok_s'] for r in rs];low=[];high=[]
 for r in rs:
  vs=[q['aggregate_output_tok_s'] for q in raw if q['replicas']==count and q['concurrency']==r['concurrency']];low.append(min(vs));high.append(max(vs))
 label=f'{count} replica'+('s' if count>1 else '');axs[0].plot(xs,ys,'o-',label=label,color=color,lw=2);axs[0].fill_between(xs,low,high,color=color,alpha=.15);axs[1].plot(xs,[r['gpu_seconds_per_1000_output_tokens'] for r in rs],'o-',label=label,color=color,lw=2)
for ax in axs:ax.set_xticks([8,16,32]);ax.set_xlabel('Concurrent requests');ax.grid(alpha=.18);ax.legend(frameon=False)
axs[0].set_title('Single-GPU replicas, static round robin',loc='left',fontweight='bold');axs[0].set_ylabel('Output tokens / second');axs[1].set_title('Allocated GPU time per output',loc='left',fontweight='bold');axs[1].set_ylabel('GPU-seconds / 1,000 output tokens')
fig.tight_layout();fig.text(.065,-.04,'Same AITER + graph configuration. 3 repeats; shaded throughput ranges. No 8-replica scaling measurement.',fontsize=9,color='#586b82');save(fig,'replica-scaling')
(OUT/'manifest.json').write_text(json.dumps({'generator':'matplotlib','version':matplotlib.__version__,'figures':outputs,'source':'evidence/summary and evidence/performance; no new model runs'},indent=2));print(outputs)
