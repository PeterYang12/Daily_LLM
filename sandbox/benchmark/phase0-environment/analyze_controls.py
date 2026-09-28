#!/usr/bin/env python3
"""Reproduce phase0 descriptive controls report from completed local artifacts.

No service calls. Use taskset -c 16-31 with the shared Python environment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
RAW = Path('/data2/yuhanya/e2b-benchmark/session-20260920')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).open() if line.strip()]


def summarize_run(rows):
    runs = [x for x in rows if x.get('record_type') == 'run']
    if not runs or rows[-1].get('record_type') != 'run':
        raise ValueError('refusing incomplete run artifact')
    final = runs[-1]; meta = next(x for x in rows if x.get('record_type') == 'metadata')
    start, end = final['measurement_start_offset_s'], final['measurement_end_offset_s']
    if end <= start or not final.get('cleanup_ok') or final.get('stop_reason') != 'completed':
        raise ValueError('control did not complete its measurement and cleanup')
    ops = [x for x in rows if x.get('record_type') == 'operation' and x.get('window') == 'measurement']
    if not ops:
        raise ValueError('no measured operations')
    eligible = [x for x in ops if start <= x['start_offset_s'] < end]
    outside = [x for x in ops if not start <= x['start_offset_s'] < end]
    # Terminal counters describe all measurement-labelled records, including a
    # worker that passed its deadline check immediately before the boundary.
    # Performance still uses the strict [start,end) dispatch cohort below.
    for field in ('dispatched_count', 'completed_count'):
        if field in final and final[field] != len(ops):
            raise ValueError('terminal counter does not match measurement-labelled records: '+field)
    completed = [x for x in eligible if x['end_offset_s'] <= end]
    good = [x for x in completed if x['status'] == 'success' and x.get('state_check') is True]
    if not good:
        raise ValueError('no valid successful completions in window')
    errors = []
    for op in ops:
        if op['status'] != 'success':
            message = op.get('error_message')
            # Export only the observed protocol diagnostic, never arbitrary text.
            errors.append({'status':op['status'],'error_class':op.get('error_class'),
                'rpc_code':op.get('rpc_code'),'message':message if message == 'Connect error: unavailable' else None})
    return {'run_id':meta.get('run_id'),'started_utc':meta.get('run_started_utc'),
        'instrumentation':meta['instrumentation'],'runner_revision':meta.get('runner_revision','v1 historical main'),
        'scenario':meta['scenario'],'repeat':meta['repeat'],'concurrency':meta['concurrency'],
        'residents':meta['resident_count'],'parameters':meta.get('parameters',{}),
        'window_seconds':end-start,'successful_window_completions':len(good),
        'completion_window_goodput_ops_s':len(good)/(end-start),
        'successful_completion_latency_median_ms':statistics.median(x['duration_ms'] for x in good),
        'dispatched_operations':len(ops),'completed_in_window':len(completed),
        'measurement_labeled_operation_count':len(ops),
        'strict_window_dispatch_count':len(eligible),
        'outside_window_dispatch_count':len(outside),
        'outside_window_failure_count':sum(x['status']!='success' for x in outside),
        'outside_window_dispatches':[{
            'operation_id':x.get('operation_id'),'status':x['status'],
            'start_offset_s':x['start_offset_s'],'end_offset_s':x['end_offset_s'],
            'window_end_offset_s':end,
            'start_after_window_end_us':(x['start_offset_s']-end)*1e6 if x['start_offset_s']>=end else None,
        } for x in outside],
        'drain_completions':sum(x['end_offset_s']>end for x in eligible),
        'measurement_labeled_completions_after_end_count':sum(x['end_offset_s']>end for x in ops),
        'failure_counts':dict(Counter(x['status'] for x in ops if x['status']!='success')),
        'failures':errors,'cleanup_ok':True,'stop_reason':final['stop_reason'],
        'driver_lag_scope':'whole driver background loop; includes setup, not a measurement-only lag series',
        'driver_event_loop_lag_ms':final.get('event_loop_lag_ms')}


def distribution(values):
    return {'median':statistics.median(values),'min':min(values),'max':max(values),'n':len(values)}


def aggregate(points):
    return {'repeats':len(points),
        'completion_window_goodput_ops_s':distribution([x['completion_window_goodput_ops_s'] for x in points]),
        'per_run_latency_median_ms':distribution([x['successful_completion_latency_median_ms'] for x in points]),
        'dispatched_operations':sum(x['dispatched_operations'] for x in points),
        'measurement_labeled_operation_count':sum(x['measurement_labeled_operation_count'] for x in points),
        'strict_window_dispatch_count':sum(x['strict_window_dispatch_count'] for x in points),
        'outside_window_dispatch_count':sum(x['outside_window_dispatch_count'] for x in points),
        'outside_window_failure_count':sum(x['outside_window_failure_count'] for x in points),
        'unsuccessful_operations':sum(sum(x['failure_counts'].values()) for x in points)}


def plots(comparison, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none'})
    directory.mkdir(parents=True,exist_ok=True)
    fig,axes=plt.subplots(1,2,figsize=(10,4.7),layout='constrained')
    colors={'minimal':'#3c638c','buffered-events':'#7972a7'}
    for ax,adapter,title in zip(axes,['service','sdk'],['Service client: JSON Connect','Public E2B SDK']):
        for xpos,mode in enumerate(['minimal','buffered-events']):
            points=[x for x in comparison['runs'] if x['adapter']==adapter and x['instrumentation']==mode]
            values=[x['completion_window_goodput_ops_s'] for x in points]
            mid=statistics.median(values)
            ax.errorbar([xpos],[mid],yerr=[[mid-min(values)],[max(values)-mid]],fmt='D',color=colors[mode],
                        markersize=7,capsize=7,elinewidth=2,zorder=2)
            for i,(row,value) in enumerate(zip(points,values)):
                x=xpos+[-.07,0,.07][i]
                marker='*' if row['failures'] else 'o'
                ax.scatter(x,value,s=90 if marker=='*' else 30,marker=marker,color=colors[mode],zorder=3)
                ax.annotate('r'+str(row['repeat']),(x,value),xytext=(7,3),textcoords='offset points',fontsize=8)
        ax.set_title(title,fontweight='bold');ax.set_xticks([0,1],['Immediate JSON\n(earlier v1)','Buffered JSON\n(later v2)'])
        ax.set_xlim(-.4,1.5);ax.set_ylabel('Successful completions in 20 s / 20 (ops/s)')
        ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Recording control: C32 / N32, three runs per mode',fontsize=13,fontweight='bold')
    fig.supxlabel('Diamond = median; whiskers = range; dots = runs.  * One protocol error retained.\nDescriptive comparison; timing, memory retention and source revision are not fully controlled.',fontsize=9)
    for suffix in ('png','svg'):
        fig.savefig(directory/f'recording-comparison.{suffix}',dpi=180)
    plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw-root',type=Path,default=RAW)
    p.add_argument('--no-plot',action='store_true')
    args=p.parse_args();out=HERE/'results';out.mkdir(exist_ok=True)
    inputs=[];points=[]
    checked_parameters=['adapter','scenario','template','concurrency','residents','duration','mode','bytes']
    for adapter,phase in [('service','phase1-service'),('sdk','phase2-sdk')]:
        for repeat in (1,2,3):
            pair=[]
            for mode,path in [('minimal',args.raw_root/phase/f'exec_true-c32-r{repeat}-{adapter}.jsonl'),
                              ('buffered-events',args.raw_root/'phase0'/f'overhead-buffered-{adapter}-c32-r{repeat}.jsonl')]:
                item=summarize_run(read_rows(path));item.update(adapter=adapter,source_path=str(path),source_sha256=sha(path))
                if item['instrumentation']!=mode or item['concurrency']!=32 or item['residents']!=32 or abs(item['window_seconds']-20)>1e-8:
                    raise ValueError('unexpected control cell')
                inputs.append(path);points.append(item);pair.append(item)
            if any(pair[0]['parameters'].get(k)!=pair[1]['parameters'].get(k) for k in checked_parameters):
                raise ValueError('unmatched recording-control parameters')
    groups={}
    for adapter in ('service','sdk'):
        groups[adapter]={mode:aggregate([x for x in points if x['adapter']==adapter and x['instrumentation']==mode])
                         for mode in ('minimal','buffered-events')}
        a=groups[adapter]['minimal']['completion_window_goodput_ops_s']['median']
        b=groups[adapter]['buffered-events']['completion_window_goodput_ops_s']['median']
        groups[adapter]['observed_median_goodput_change_percent']=(b/a-1)*100
    now=datetime.now(timezone.utc).isoformat()
    comparison={'schema_version':2,'generated_utc':now,'analysis_cpu_affinity':sorted(os.sched_getaffinity(0)),
        'definition':'Successful operations submitted within [start,end), completed by end, divided by end-start; drain excluded.',
        'counter_definition':'dispatched_operations is a backward-compatible alias for all measurement-labelled records. Strict-window dispatches, outside-window dispatches/failures, and in-window-start drain completions are separate fields.',
        'latency_definition':'Per-run median of successful completion-window operations; group shows median and min/max of three run medians, not pooled requests.',
        'runs':points,'groups':groups,'all_cleanup_confirmed':all(x['cleanup_ok'] for x in points),
        'scope':'recording control, not zero-instrumentation or whole-sampler A/B',
        'confounders':['Earlier v1 baseline versus later v2 error-diagnostics runner; non-interleaved.',
                       'Buffering retains additional row dictionaries and can change memory/GC behavior.',
                       'Timers, result bookkeeping, guards and external sampler remain active.']}
    (out/'recording-comparison.json').write_text(json.dumps(comparison,indent=2)+'\n')
    with (out/'recording-comparison.csv').open('w',newline='') as f:
        columns=['adapter','instrumentation','repeat','started_utc','successful_window_completions',
                 'completion_window_goodput_ops_s','successful_completion_latency_median_ms','dispatched_operations',
                 'measurement_labeled_operation_count','strict_window_dispatch_count',
                 'outside_window_dispatch_count','outside_window_failure_count','drain_completions',
                 'measurement_labeled_completions_after_end_count']
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(points)
    if not args.no_plot:plots(comparison,HERE/'figures')
    transport_path=out/'file-transport-probe.json';transport=json.loads(transport_path.read_text())
    if transport['status']!='passed' or not transport['cleanup_ok'] or len(transport['observations'])!=14 or not all(x['content_matches'] for x in transport['observations']):
        raise ValueError('transport correctness control incomplete')
    life_paths=[args.raw_root/'phase0'/x for x in ['lifecycle-smoke-service.jsonl','lifecycle-smoke-service-fixed.jsonl']]
    lifecycle=[]
    for path in life_paths:
        rows=read_rows(path);final=next(x for x in reversed(rows) if x.get('record_type')=='run')
        lifecycle.append({'source_path':str(path),'source_sha256':sha(path),'cleanup_ok':final['cleanup_ok'],
            'stop_reason':final['stop_reason'],'completed_count':final['completed_count'],
            'roundtrips_completed':final['roundtrips_completed'],
            'operations':[{k:x.get(k) for k in ('operation','status','state_check')} for x in rows if x.get('record_type')=='operation']})
        inputs.append(path)
    if lifecycle[-1]['completed_count']!=3 or not lifecycle[-1]['cleanup_ok'] or any(x['state_check'] is not True for x in lifecycle[-1]['operations']):
        raise ValueError('fixed lifecycle state control incomplete')
    (out/'lifecycle-validation.json').write_text(json.dumps({'generated_utc':now,'controls':lifecycle,'scope':'one state roundtrip; functional validation only'},indent=2)+'\n')
    fmt=lambda d: f"{d['median']:.2f} [{d['min']:.2f}, {d['max']:.2f}]"
    lines=['# 阶段 0：测量环境与口径验收','',
      '**阶段 0 已具备可复用的环境、协议、状态及计数口径检查。** 记录处理对照没有显示一致方向的吞吐收益，不能据此给出纯 JSON 记录开销百分比；完整关闭插桩/资源采样的 A/B 尚未做。本文只报告这些测量控制，不表示所有服务、SDK 或 RL 实验已经完成。','',
      '## 已建立的控制','',
      '| 控制 | 实际结果 | 解释边界 |','|---|---|---|',
      '| 环境 / 资源采样 | VM 192 vCPU、约 1486.84 GiB；普通页；NBD 1024；有限 sampler 链路通过 | inventory 是 10:29 UTC 时点状态，测试前仍需重新检查外来负载 |',
      '| 协议与计时格式 | command 3/3、file write 8/8、create/delete 3/3；全部清理 | 功能烟测，不据少量请求估计稳定尾延迟 |',
      '| 生命周期状态 | 修正 root 用户与唯一路径后，pause / resume-connect / ready-probe 3 项通过 | 一次 roundtrip；保留最初权限失败记录 |',
      '| 文件传输口径 | 14 个读取观察均内容 / SHA256 一致，清理成功 | 明确逻辑字节、压缩 HTTP entity 与 SDK counter 的区别 |',
      '| 记录处理 | Service / SDK，各 3 次 minimal 和 3 次 buffered，C32/N32、20 秒 | 时间顺序、版本和内存保留未完全控制 |','',
      'CPU 核对记录将负载生成器限制到 0–15，分析限制到 16–31，QEMU 使用 192–383；宿主为两个 192 核 socket、每核一个线程。本报告生成时实际分析 affinity 已记录在 JSON。CPU 分开不等于消除共享内存、缓存或其他用户负载的影响。','',
      '## 记录处理对照','',
      '`minimal` 在每次操作返回后立即做 JSON 序列化和行写入；`buffered-events` 保留行对象，在测量、后验和清理后、最终 run 记录前写出。两者仍有单调时钟、行对象、内存结果数组、guards 和外部 sampler。单次 latency 的计时在记录前结束，因此比较主要反映事件循环与后续请求受到的间接影响。','',
      '**成功完成窗吞吐** = 严格在 `[start,end)` 内发起、且在 20 秒窗结束前成功完成并通过状态检查的请求数 / 20。窗内发起但窗后完成的排空请求不计入该吞吐；measurement标记但实际在窗外发起的记录也单独排除，不能混称为排空。全部measurement标记的记录仍与最终计数对账，所有失败均保留在对应分母和错误表中。表中为三次 run 的统计量中位数及最小–最大范围，没有把所有请求混池计算分位数。','',
      '| 调用路径 | 记录方式 | 成功完成窗 ops/s，中位 [范围] | 每 run 请求 P50 ms，中位 [范围] | 全部measurement标记失败 / 记录数 |',
      '|---|---|---:|---:|---:|']
    for adapter,label in [('service','Service JSON Connect'),('sdk','公开 E2B SDK')]:
        for mode in ('minimal','buffered-events'):
            g=groups[adapter][mode];lines.append(f"| {label} | {mode} | {fmt(g['completion_window_goodput_ops_s'])} | {fmt(g['per_run_latency_median_ms'])} | {g['unsuccessful_operations']} / {g['dispatched_operations']:,} |")
    boundary = next(x for x in points if x['adapter']=='sdk' and x['instrumentation']=='buffered-events' and x['repeat']==1)
    late = boundary['outside_window_dispatches']
    if len(late)!=1:
        raise ValueError('Expected the frozen buffered SDK r1 single boundary dispatch')
    late=late[0]
    drain_counts=Counter(x['drain_completions'] for x in points)
    boundary_note=(f"边界核对：12份run中，严格窗内发起的排空数分布为 "
        + '、'.join(f'{count}条（{number}份run）' for count,number in sorted(drain_counts.items()))
        + f"。buffered SDK r1共{boundary['measurement_labeled_operation_count']:,}条measurement标记，严格窗内发起{boundary['strict_window_dispatch_count']:,}条，"
        + f"其中{boundary['drain_completions']}条窗后完成；另有1条成功请求`{late['operation_id']}`在截止点后{late['start_after_window_end_us']:.3f}微秒才发起"
        + f"（start={late['start_offset_s']:.14f}，end={late['window_end_offset_s']:.14f}）。该记录保留在标签计数中，但不计入严格发起cohort或goodput，且不是窗内发起的排空请求。"
        + "本次只是补齐边界分类与表述，成功完成窗吞吐数值不变。")
    lines+=['',boundary_note,'',f"Service 的 observed median 吞吐变化为 **{groups['service']['observed_median_goodput_change_percent']:+.1f}%**，SDK 为 **{groups['sdk']['observed_median_goodput_change_percent']:+.1f}%**。这两个数字是观察差异，不是插桩开销估计。baseline 在约 10:43–11:08 UTC 使用保存的 v1；buffered 在约 12:12–12:21 UTC 使用 v2 error diagnostics，未交错随机执行。缓冲还保留额外字典，可能改变 GC 和内存行为。不能将吞吐下降解释成“记录具有负开销”，也不能把某次上升直接归因于取消 JSON 写入。",'',
      'Service buffered 第 3 次保留 1 条 `InvalidResult: Connect error: unavailable`，该 run 仍正常完成清理。请求总数为 8,220；本分析没有删除该 run 或把失败补成成功。其余 11 个对照 run 没有测量请求失败。','',
      '![记录处理对照](figures/recording-comparison.png)','',
      '图中点表示独立 run、菱形表示中位数、线表示范围。两个调用路径使用独立坐标轴；此图比较各自记录方式，不把 JSON Connect 与 SDK 内部 transport 的差异称为纯 SDK 开销。原始逐 run 数字见 [JSON](results/recording-comparison.json) / [CSV](results/recording-comparison.csv)，[SVG](figures/recording-comparison.svg) 可用于汇报。','',
      '## 文件字节数不能混用','',
      '逻辑文件均为 1 MiB，native HTTPX 原始 entity 的实测值如下。原始 entity 不含 HTTP headers、传输分帧或 TCP/TLS 开销，不能当作网卡线速字节。','',
      '| 文件内容 / 编码 | 验证后的逻辑字节 | 原始 HTTP entity 字节 |','|---|---:|---:|',
      '| 重复 x / gzip | 1,048,576 | 1,055 |','| 重复 x / identity | 1,048,576 | 1,048,576 |',
      '| 确定性 SHA256 counter / gzip | 1,048,576 | 1,048,919 |','',
      'SDK 使用 pyqwest transport，送到 HTTPX 时可能已经解压并去掉 Content-Encoding；其 `num_bytes_downloaded=1,048,576` 和 `files.read` 返回值是这条路径上的解码后内容口径，不能证明线上传输了 1 MiB。`gzip=False` 本身也不能证明请求最终使用了 Accept-Encoding: identity。读取重复 x 的逻辑 MiB/s 因而不能直接报告成网络 MiB/s。完整 14 项观察、header 与 checksum 见 [file-transport-probe.json](results/file-transport-probe.json)。','',
      '## 生命周期与观测限制','',
      '首次 lifecycle preflight 在测量前发生 SandboxException，未完成 roundtrip，但清理成功；它对应默认 SDK 用户与现有 /tmp 文件权限的准备问题。改为 root 并为每轮使用唯一路径后，pause、resume-connect、ready-state-probe 全部成功，进程身份/内存计数与文件标记的检查通过。失败和修正后的源文件均保留在 [lifecycle-validation.json](results/lifecycle-validation.json)，这些结果不构成生命周期性能或可靠性 SLA。','',
      '有限 sampler 只读 `/proc`、sysfs 与计数器。初始两个 1 秒样本的 guest 采集 wall time 约 60 ms、host 约 2 ms；压力下采集耗时会变化，单次 wall time 不等于观测器 CPU 开销。短生命周期会遗漏在 cgroup 的两点增量之外，guest/host monotonic 时钟有各自原点，需用 UTC 对齐。全量采样有/无 A/B 尚未执行。','',
      '时钟补充：事后SSH括界发现VM UTC与host/client UTC约1秒级对齐不确定性；sampler传递时差也包含采集和传输。未平移的VM窗口均值只能近似归属，不能精确跨层关联；客户端单调计时、host/发生器同机CPU和VM内计数器峰值保持原口径。见[时钟对齐与假设性敏感性](CLOCK-ALIGNMENT.md)，该检查不证明历史偏移恒定，也没有调整系统时钟。','',
      'driver 的 event-loop lag 汇总来自整个后台循环，包括准备期，不能当成严格的 measurement-only lag 或直接等同 RPC queue time。对照也未保证每个 resident 的所有 RPC 连接都已预热。','',
      '当前 OTel 不能表述成“所有指标都已 Noop”。主 OTel 客户端在 endpoint 缺失时关闭，但独立的 API team 与 orchestrator sandbox observer 仍可能启动 exporter，产生空 endpoint 导出错误。这些遥测警告与 Firecracker 本地 /metrics socket 配置失败不是同一路径，也不等于业务请求失败；参见 [日志核对](../phase1-service/results/setup-failure-log-20260920.md)。本阶段未修改该服务配置。','',
      '## 复现与证据','',
      '```bash','taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B \\',
      '  /home/yuhanya/Daily_LLM/sandbox/benchmark/phase0-environment/analyze_controls.py','```','',
      '生成器只读已结束的本地 run，拒绝缺少最终 run 记录、未清理或零 measurement 的控制样本。源 JSONL、历史 v1、当前 v2、wrapper、transport/CPU 核对与输出文件的 SHA256 见 [manifest.json](results/manifest.json)。主实验的完整最终 source manifest 由总报告另行维护。原始私有材料在 `/data2/yuhanya/e2b-benchmark/session-20260920`，不复制密钥或业务日志到仓库。']
    (HERE/'REPORT.md').write_text('\n'.join(lines)+'\n')
    status=json.loads((out/'summary.json').read_text());status.update(updated_utc=now,status='measurement_controls_available')
    status['recording_overhead_probe']={'status':'descriptive_comparison_complete','runs':12,'buffered_runs':6,
        'service_observed_median_change_percent':groups['service']['observed_median_goodput_change_percent'],
        'sdk_observed_median_change_percent':groups['sdk']['observed_median_goodput_change_percent'],
        'unsuccessful_requests_retained':1,'full_no_instrumentation_or_sampling_ab':False}
    status['file_transport_control']={'observations':14,'status':'passed','cleanup_ok':True,'sdk_byte_counter_is_not_wire_proof':True}
    status['lifecycle_control']={'initial_failed_preflight_retained':True,'fixed_roundtrips':1,'fixed_operations':3,'cleanup_ok':True}
    (out/'summary.json').write_text(json.dumps(status,indent=2)+'\n')
    overhead=json.loads((out/'overhead-probe.json').read_text());overhead.update(status='descriptive_comparison_complete',updated_utc=now,
        live_comparison_available=True,results='recording-comparison.json',full_sampling_ab=False)
    overhead.pop('live_comparison_executed_by_this_agent',None)
    (out/'overhead-probe.json').write_text(json.dumps(overhead,indent=2)+'\n')
    v1=HERE.parent/'phase1-service/code/executed/runner-20260920-v1.py';v2=HERE.parent/'phase1-service/code/runner.py'
    inputs += [args.raw_root/'phase0'/n for n in ['inventory.json','inventory-final.json','sampler-smoke-2x1s.jsonl',
        'protocol-command-smoke.jsonl','protocol-files-smoke.jsonl','protocol-create-smoke.jsonl']]
    sources=[*sorted(HERE.glob('*.py')),HERE/'README.md',HERE/'REPORT.md',v1,v2,
             *sorted(p for p in out.glob('*') if p.name!='manifest.json' and p.is_file()),
             *sorted((HERE/'figures').glob('recording-comparison.*')),*inputs]
    unique=list(dict.fromkeys(sources))
    manifest={'schema_version':2,'updated_utc':now,'analysis_cpu_affinity':sorted(os.sched_getaffinity(0)),
        'command':'taskset -c 16-31 env/bin/python -B phase0-environment/analyze_controls.py',
        'scope':'phase0 controls and their completed source artifacts; excludes unfinished live files and independently maintained log reports',
        'source_versions':{'historical_main_v1':{'path':str(v1),'sha256':sha(v1)},'buffered_dependency_v2_at_analysis':{'path':str(v2),'sha256':sha(v2)}},
        'artifacts':[{'path':str(x),'bytes':x.stat().st_size,'sha256':sha(x)} for x in unique]}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'status':'written','report':str(HERE/'REPORT.md'),'runs':len(points),'groups':groups,
                      'analysis_cpu_affinity':sorted(os.sched_getaffinity(0))},indent=2))


if __name__=='__main__':
    main()
