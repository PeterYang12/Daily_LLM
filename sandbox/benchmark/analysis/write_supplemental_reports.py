#!/usr/bin/env python3
"""Frozen supplemental comparisons; never rewrites main-matrix artifacts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

from summarize import save_json, write_csv

ROOT = Path('/home/yuhanya/Daily_LLM/sandbox/benchmark')


def read(path):
    return json.loads(path.read_text())


def load():
    data={}
    for phase in ('phase1-service','phase2-sdk'):
        for kind in ('main-matrix','supplemental'):
            folder=ROOT/phase/'results'/kind
            data[(phase,kind)]={'folder':folder, **{name:read(folder/(name+'.json')) for name in ('summary','per_run','runs','INPUTS-FROZEN')}}
    comparison=read(ROOT/'phase1-service/results/supplemental/COMPARISON-INPUTS-FROZEN.json')
    allowed_main={e['run_id'] for e in comparison['main_baselines']}
    for item in data.values():
        item['allowed_main']=allowed_main
    main_hashes={}
    for phase in ('phase1-service','phase2-sdk'):
        folder=data[(phase,'main-matrix')]['folder']
        for p in [*(p for p in folder.rglob('*') if p.is_file()),ROOT/phase/'MAIN-RESULTS.md']:
            main_hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    data['main_artifact_hashes_before']=main_hashes
    return data,comparison


def point(data,phase,kind,scenario,c,n=None,mode='closed_loop',rate=None):
    item=data[(phase,kind)]
    found=[r for r in item['summary'] if r['scenario']==scenario and r['concurrency']==c and r['resident_count']==n
           and r['mode']==mode and r.get('arrival_rate_ops_s')==rate and r['instrumentation']!='buffered-events']
    assert len(found)==1,(phase,kind,scenario,c,n,rate)
    row=dict(found[0],source_phase=phase,source_set=kind,adapter='HTTPX' if phase=='phase1-service' else 'SDK')
    if kind=='main-matrix':assert set(row['run_ids'])<=item['allowed_main']
    selected=[r for r in item['runs'] if r['run_id'] in row['run_ids']]
    row['scheduled_count']=sum(r['scheduled_count'] for r in selected)
    row['labelled_count']=sum(r.get('measurement_labeled_operation_count',r['reported_dispatch_count']) for r in selected)
    row['undispatched_count']=row['scheduled_count']-row['labelled_count']
    return row


def comparisons(data):
    rows=[]
    for cap,kind in ((16,'main-matrix'),(64,'supplemental')):
        for rate in (8,20):
            rows.append(dict(point(data,'phase1-service',kind,'create_delete_churn',cap,None,'open_loop',float(rate)),
                             comparison='client_cap',condition=f'Client cap {cap}'))
    for phase,kind in (('phase1-service','main-matrix'),('phase2-sdk','supplemental')):
        for c in (1,2,3,8):
            rows.append(dict(point(data,phase,kind,'create_delete_churn',c),comparison='create_adapter',
                             condition='Raw HTTP create' if phase=='phase1-service' else 'Public SDK create'))
    for phase in ('phase1-service','phase2-sdk'):
        for kind,scenario,condition in (('main-matrix','file_read_1m','Repeated bytes / default gzip'),
                                        ('supplemental','file_read_1m_identity_random','Random bytes / identity')):
            rows.append(dict(point(data,phase,kind,scenario,8,8),comparison='payload_encoding',condition=condition))
    for kind,scenario,condition in (('main-matrix','exec_true','Slot 0 warmup'),
                                    ('supplemental','exec_true_all_connections_warm','64 slots exercised at C8')):
        rows.append(dict(point(data,'phase2-sdk',kind,scenario,64,64),comparison='warmup',condition=condition))
    return rows


def figures(rows,data):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'savefig.dpi':200,'svg.fonttype':'none',
                         'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.2,'legend.frameon':False})
    index=[]
    def finish(fig,phase,name,title,caption):
        folder=ROOT/phase/'results/supplemental/plots';folder.mkdir(exist_ok=True)
        fig.suptitle(title,fontsize=12)
        fig.text(.01,.012,caption,fontsize=8,ha='left')
        fig.tight_layout(rect=(0,.10,1,.91))
        for ext in ('png','svg'):fig.savefig(folder/(name+'.'+ext),bbox_inches='tight',facecolor='white')
        plt.close(fig)
        index.append({'phase':phase,'name':name,'png':'plots/'+name+'.png','svg':'plots/'+name+'.svg','caption':caption})
    def line(ax,points,xkey,metric,label,color):
        points=sorted(points,key=lambda r:r[xkey]);x=[r[xkey] for r in points];y=[r[metric] for r in points]
        ax.plot(x,y,'o-',label=label,color=color)
        ax.fill_between(x,[r[metric+'_min'] for r in points],[r[metric+'_max'] for r in points],color=color,alpha=.13)
        ax.set_xticks(x)

    fig,axes=plt.subplots(3,1,figsize=(9,8.2),sharex=True)
    for cap,color in ((16,'#0072B2'),(64,'#D55E00')):
        selected=[r for r in rows if r['comparison']=='client_cap' and r['concurrency']==cap]
        for ax,metric,label in zip(axes,['successful_throughput_ops_s','p95_ms','undispatched_rate_pct'],['Successful cycles / s','Create P95 (ms)','Not dispatched (%)']):
            line(ax,selected,'arrival_rate_ops_s',metric,f'Client cap {cap}',color);ax.set_ylabel(label);ax.legend()
    axes[2].set_ylim(bottom=0);axes[2].set_xlabel('Configured arrival rate (cycles/s)')
    finish(fig,'phase1-service','client-cap16-vs64','Lifecycle arrival batches: client cap 16 versus 64',
           'Points: median of 3 run statistics; bands: observed run min–max. Server startup admission remains unchanged.\nRates use complete finite-batch windows, including drain. Not-dispatched denominator: scheduled arrivals. Latency is create only.')

    fig,axes=plt.subplots(2,1,figsize=(9,6.3),sharex=True)
    for adapter,color in (('HTTPX','#0072B2'),('SDK','#D55E00')):
        selected=[r for r in rows if r['comparison']=='create_adapter' and r['adapter']==adapter]
        line(axes[0],selected,'concurrency','successful_throughput_ops_s',adapter+' create',color)
        line(axes[1],selected,'concurrency','p95_ms',adapter+' create',color)
    axes[0].set_ylabel('Successful cycles / s');axes[1].set_ylabel('Create P95 (ms)');axes[1].set_xlabel('Create/delete cycle workers')
    for ax in axes:ax.legend()
    finish(fig,'phase2-sdk','create-sdk-vs-raw','Public SDK create versus raw HTTP create',
           'Both adapters use the same raw HTTPX delete for cleanup. Cycle throughput is not full SDK create+kill throughput.\nPoints: per-run median; bands: run min–max; 3 runs per point. Sequential execution does not isolate SDK overhead.')

    fig,axes=plt.subplots(1,2,figsize=(11,5.0))
    conditions=['Repeated bytes / default gzip','Random bytes / identity']
    for i,adapter in enumerate(('HTTPX','SDK')):
        selected=[next(r for r in rows if r['comparison']=='payload_encoding' and r['adapter']==adapter and r['condition']==condition) for condition in conditions]
        x=[j+(i-.5)*.34 for j in range(2)]
        for ax,metric,ylabel in ((axes[0],'successful_throughput_ops_s','Successful reads / second'),(axes[1],'p95_ms','Read P95 (ms)')):
            ys=[r[metric] for r in selected]
            ax.bar(x,ys,width=.3,label=adapter,color=('#0072B2','#D55E00')[i],
                   yerr=[[r[metric]-r[metric+'_min'] for r in selected],[r[metric+'_max']-r[metric] for r in selected]],capsize=3)
            ax.set_xticks([0,1],['Repeated bytes\ndefault gzip','Random bytes\nidentity']);ax.set_ylabel(ylabel);ax.legend()
    finish(fig,'phase1-service','payload-encoding-c8','1 MiB logical file reads at C=N=8: workload variants',
           'Payload entropy and content encoding both change. These observations do not isolate a compression effect.\nRates count logical successful payloads, not network bandwidth. Bars: median of 3 runs; whiskers: run min–max.')

    fig,axes=plt.subplots(1,2,figsize=(11,4.9))
    warm=[r for r in rows if r['comparison']=='warmup']
    names=['Slot 0\nwarmup','64 slots\nexercised at C8']
    ys=[r['successful_throughput_ops_s'] for r in warm]
    axes[0].bar(names,ys,color=['#0072B2','#D55E00'],yerr=[[r['successful_throughput_ops_s']-r['successful_throughput_ops_s_min'] for r in warm],[r['successful_throughput_ops_s_max']-r['successful_throughput_ops_s'] for r in warm]],capsize=4)
    axes[0].set_ylabel('Successful operations / s')
    for x,kind,color in ((0,'main-matrix','#0072B2'),(1,'supplemental','#D55E00')):
        folder=data[('phase2-sdk',kind)]['folder'];cohorts=read(folder/'first_call_per_run.json')
        selected=sorted((r for r in cohorts if r['cohort']=='first_measured_per_worker'),key=lambda r:r['repeat'])
        rate=100*sum(r['failures'] for r in selected)/sum(r['calls'] for r in selected)
        axes[1].scatter([x-.08,x,x+.08],[r['failure_rate_pct'] for r in selected],color=color,s=35)
        axes[1].hlines(rate,x-.22,x+.22,color=color,linewidth=2.5)
        axes[1].annotate(f"{sum(r['failures'] for r in selected)}/{sum(r['calls'] for r in selected)}",(x,rate),xytext=(0,8),textcoords='offset points',ha='center')
    axes[1].set_xticks([0,1],names);axes[1].set_xlim(-.5,1.5);axes[1].set_ylim(bottom=0);axes[1].set_ylabel('First-measured failure rate (%)')
    finish(fig,'phase2-sdk','warmup-c8-to-c64','SDK measurement C=N=64 after two preparation procedures',
           'Left: median throughput and run min–max. Right: per-run rates (dots) and pooled first-call counts (horizontal lines).\nEvery slot running once at C8 does not prove that all connections needed at C64 are pre-established. The conditions were not randomized.')
    return index


def q(row):
    return f"{row['successful_throughput_ops_s']:.2f} [{row['successful_throughput_ops_s_min']:.2f}, {row['successful_throughput_ops_s_max']:.2f}]"


def reports(rows,data,index):
    base='results/supplemental/'
    svc=["# Phase 1 补充对照结果草稿","",
         "输入来自冻结的42项补充矩阵；按记录phase归属，本页数据表包含15个run，全部完成并清理成功。主矩阵保持原样，对照仅引用另行列出的27份主矩阵原始文件。多发生器和idle-pool不进入此集合。", "",
         "**提高客户端在途上限减少丢弃，同时增加排队。** 下表中服务端启动准入配置未变；两档客户端cap比较的都是100次计划到达的有限批次，吞吐分母含排空。", "",
         "| λ /s | 客户端cap | 实发/计划（三轮合计） | 未发出 | 成功 cycles/s，中位数 [min,max] | create P95 ms |",
         "|---:|---:|---:|---:|---:|---:|"]
    for rate in (8,20):
        for cap in (16,64):
            r=next(r for r in rows if r['comparison']=='client_cap' and r['concurrency']==cap and r['arrival_rate_ops_s']==rate)
            svc.append(f"| {rate} | {cap} | {int(r['labelled_count'])}/{r['scheduled_count']} | {int(r['undispatched_count'])} | {q(r)} | {r['p95_ms']:.2f} |")
    svc += ["",f"[cap16/cap64曲线]({base}plots/client-cap16-vs64.png)显示更多计划请求进入系统后，成功吞吐仍约5 cycles/s，P95显著延长。不能把较少的发生器丢弃解释为服务容量同倍增加；这些有限批次也不是稳态上限认证。图按逐轮中位数显示比例，表中计划/实发为合并计数。", "",
            "**1 MiB随机内容 + identity编码属于另一个工作负载。** 每个条件有3轮，C=N=8。", "",
            "| 客户端 | 内容 / 编码 | 成功 reads/s，中位数 [min,max] | P95 ms |", "|---|---|---:|---:|"]
    for r in (r for r in rows if r['comparison']=='payload_encoding'):
        svc.append(f"| {r['adapter']} | {r['condition']} | {q(r)} | {r['p95_ms']:.2f} |")
    svc += ["",f"[内容与编码对照图]({base}plots/payload-encoding-c8.png)。重复字节的默认gzip实体仅1055字节；随机identity场景使用固定SHA的1048576字节内容。内容熵和编码同时改变，不能把全部差异归因于压缩或宣称网络带宽。新场景6轮均通过内容校验。", "",
            "SDK/raw create对照见[Phase 2补充报告](../phase2-sdk/SUPPLEMENTAL-RESULTS.md)；两者删除均走相同的raw HTTPX路径。暂停/恢复见[生命周期专报](LIFECYCLE-RESULTS.md)。记录开销见[Phase 0报告](../phase0-environment/REPORT.md)：本页保留其3个service run，并保留其中1个InvalidResult；详细RPC解释以专报为准。"]

    sdk=["# Phase 2 补充对照结果草稿","",
         "冻结矩阵按记录phase归属包含27个SDK run，全部完成并清理成功。这里将create路径、文件内容/编码、预热过程分别比较，未改动主矩阵或混入多发生器、idle-pool。", "",
         "**公开SDK create与raw create。** 各点3轮；吞吐包含create/delete循环，但删除统一由raw HTTPX执行，不能称为完整SDK create+kill吞吐。", "",
         "| 循环C | raw cycles/s，中位数 [min,max] | SDK cycles/s，中位数 [min,max] | raw / SDK create P95 ms |", "|---:|---:|---:|---:|"]
    for c in (1,2,3,8):
        raw=next(r for r in rows if r['comparison']=='create_adapter' and r['adapter']=='HTTPX' and r['concurrency']==c)
        public=next(r for r in rows if r['comparison']=='create_adapter' and r['adapter']=='SDK' and r['concurrency']==c)
        sdk.append(f"| {c} | {q(raw)} | {q(public)} | {raw['p95_ms']:.2f} / {public['p95_ms']:.2f} |")
    sdk += ["",f"[create路径对照图]({base}plots/create-sdk-vs-raw.png)。本地配置下两条路径的曲线形态接近；测试按顺序执行，差值包含时段、客户端和启动路径差异，不能直接命名为SDK额外成本。N不适用于没有预建驻留池的churn。", "",
            "**以C8遍历64个slot后，C64第一条测量调用仍有错误。** 新过程在继承的slot0预热后，额外对全部64个slot各执行1条命令，预热并发8，再切到测量并发64。原始scenario名称保留在CSV，但它不能证明测量所需transport连接均已建立。", "",
            "| 准备过程 | 成功 ops/s，中位数 [min,max] | 测量失败/请求 | 首条失败/192 | 后续失败/请求 |", "|---|---:|---:|---:|---:|"]
    for r in (r for r in rows if r['comparison']=='warmup'):
        folder=data[('phase2-sdk',r['source_set'])]['folder'];cohorts=read(folder/'first_call_summary.json')
        first=next(v for v in cohorts if v['cohort']=='first_measured_per_worker');later=next(v for v in cohorts if v['cohort']=='later_measured_calls')
        sdk.append(f"| {r['condition']} | {q(r)} | {r['observed_failure_count']}/{r['observed_dispatch_count']:,} | {first['failures']}/{first['calls']} ({first['failure_rate_pct']:.4f}%) | {later['failures']}/{later['calls']:,} |")
    first_graph=read(data[('phase2-sdk','supplemental')]['folder']/'first_call_plot_index.json')[0]['png']
    sdk += ["",f"[预热过程比较]({base}plots/warmup-c8-to-c64.png)与[新过程逐轮first/later图]({base}{first_graph})保留全部错误。新过程三轮首条错误分别为4、3、2个，均为ConnectError/RPC unavailable；其余187717条后续调用未记录错误。此分类按每worker最早measurement时间确定，setup可能更早调用过SDK。运行间变动较大且过程未随机化，不能宣称预热已修复问题或确定网络根因。", "",
            "随机identity文件读取的SDK中位数为210.40 reads/s，原重复内容/default-gzip为128.60；两者同时改变内容与编码。[跨客户端完整表/图](../phase1-service/SUPPLEMENTAL-RESULTS.md)保留差异与范围，数值代表逻辑payload处理，不能换称网络带宽。", "",
            "官方20轮脚本和内存暂停/恢复见[应用专报](APPLICATION-RESULTS.md)、[生命周期专报](../phase1-service/LIFECYCLE-RESULTS.md)。这些专报使用合并60观测的描述性分位数；本套件通用CSV使用各run分位数的中位数，不能直接混用。缓冲记录对照见[Phase 0报告](../phase0-environment/REPORT.md)：其SDK r1有1个成功请求在声明窗结束后1.798μs发出，原始计数保留，精确窗口指标排除它；这不是记录缺失。"]
    footer=["", "统计与复现：图中吞吐/时延点为3轮统计的中位数，带或须为观察到的min–max，不是置信区间。首条失败表为合并计数；有限样本不支持稳定SLO承诺。host/VM资源分开，只采用完整落在窗口内的采样区间，详见资源覆盖表。", "",
            "时钟补充：VM UTC与host/client UTC的历史对齐未经校准；窗口资源统计保留未平移选择，不作精确跨层归因。客户端单调计时和host/发生器同机CPU保持原口径，见[时钟对齐限制与主矩阵敏感性](../phase0-environment/CLOCK-ALIGNMENT.md)。主矩阵中的假设平移不直接套用于这些补充实验。", "",
            "[补充输入冻结清单](results/supplemental/INPUTS-FROZEN.json) · [主矩阵对照输入清单](results/supplemental/COMPARISON-INPUTS-FROZEN.json) · [对照CSV](results/supplemental/comparisons.csv) · [逐run CSV](results/supplemental/per_run.csv) · [错误样本](results/supplemental/errors.json) · [资源报告](results/supplemental/resources/RESOURCES.md)。"]
    for phase,text in (('phase1-service',svc),('phase2-sdk',sdk)):
        (ROOT/phase/'SUPPLEMENTAL-RESULTS.md').write_text('\n'.join(text+footer)+'\n')


def provenance(data,comparison):
    global_manifest=ROOT/'analysis/SUPPLEMENTAL-INPUTS-FROZEN.json'
    main_refs={e['run_id']:e for e in comparison['main_baselines']}
    for phase in ('phase1-service','phase2-sdk'):
        main=data[(phase,'main-matrix')]['folder']
        observed_main={r['path']:r['sha256'] for r in read(main/'inputs.json')['files']}
        for entry in comparison['main_baselines']:
            if entry['phase']==phase:assert observed_main[entry['operations']['path']]==entry['operations']['sha256']
    for path,digest in data['main_artifact_hashes_before'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest, 'Main artifact changed: '+path
    for phase in ('phase1-service','phase2-sdk'):
        out=data[(phase,'supplemental')]['folder'];manifest=data[(phase,'supplemental')]['INPUTS-FROZEN']
        observed={r['path']:r['sha256'] for r in read(out/'inputs.json')['files']}
        observed.update({r['path']:r['sha256'] for r in read(out/'resources/resource_inputs.json')['files']})
        for e in manifest['inputs']:
            for kind in ('operations','resources'):assert observed[e[kind]['path']]==e[kind]['sha256']
        code=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((ROOT/'analysis').glob('*.py'))]
        save_json(out/'ANALYSIS-PROVENANCE.json',{'generated_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,
            'cpu_affinity':'16-31','global_supplemental_manifest_sha256':hashlib.sha256(global_manifest.read_bytes()).hexdigest(),
            'input_hashes_match':True,'main_baseline_run_ids':sorted(main_refs),'main_artifacts_rewritten':False,
            'main_artifact_hashes_unchanged':data['main_artifact_hashes_before'],
            'scope':'42 supplemental cells plus explicitly listed main comparison baselines; multigenerator/idle excluded','code':code})


def main():
    data,comparison=load();rows=comparisons(data);index=figures(rows,data)
    for phase in ('phase1-service','phase2-sdk'):
        out=data[(phase,'supplemental')]['folder']
        save_json(out/'comparisons.json',rows);write_csv(out/'comparisons.csv',rows)
        save_json(out/'comparison_plot_index.json',[r for r in index if r['phase']==phase])
    reports(rows,data,index);provenance(data,comparison)
    print(json.dumps({'comparison_groups':len(rows),'figures':len(index),'main_rewritten':False}))


if __name__=='__main__':
    main()
