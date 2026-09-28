#!/usr/bin/env python3
"""Reproduce the idle-pool diagnostic report from frozen files, without RPCs."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics

PHASE = Path(__file__).resolve().parents[1]
RAW = Path('/data2/yuhanya/e2b-benchmark/session-20260920/phase1-service')


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path, lines=False):
    data=Path(path).read_bytes()
    parsed=[json.loads(x) for x in data.splitlines() if x.strip()] if lines else json.loads(data)
    return parsed,{'path':str(path),'bytes':len(data),'sha256':digest(data)}


def cpu_window(rows, start, end):
    """Only nominal intervals fully inside the window; no edge interpolation."""
    inside=[r for r in rows if timestamp(r['guest_utc'])-timedelta(seconds=r['interval_seconds'])>=start
            and timestamp(r['guest_utc'])<=end]
    seconds=sum(r['interval_seconds'] for r in inside)
    return {'requested_start_vm_utc':start.isoformat(),'requested_end_vm_utc':end.isoformat(),
        'fully_contained_intervals':len(inside),'covered_nominal_seconds':seconds,
        'vm_interval_weighted_cores':sum(r['vm_cpu_cores']*r['interval_seconds'] for r in inside)/seconds if seconds else None,
        'cgroup_interval_weighted_cores':sum(r['cgroup_cpu_cores']*r['interval_seconds'] for r in inside)/seconds if seconds else None,
        'interpretation':'Descriptive nominal intervals only; long sequential collector delay prevents precise synchronized attribution.'}


def write_csv(path, rows):
    with Path(path).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def figure(summary, creates, resource, guests, log, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none'})
    directory.mkdir(parents=True,exist_ok=True)
    fail=timestamp(summary['creation_failure']['utc']);began=fail-timedelta(seconds=summary['creation_failure']['duration_seconds'])
    init_start=timestamp(next(x['utc'] for x in log['server_vm_timeline'] if x['event']=='load_snapshot_api_completed'))
    init_end=timestamp(next(x['utc'] for x in log['server_vm_timeline'] if x['event']=='envd_init_failed_after_retries'))
    response=timestamp(next(x['utc'] for x in log['server_vm_timeline'] if x['event']=='POST /sandboxes completed'))
    vm_times=[timestamp(x['guest_utc']) for x in resource]
    fig,axes=plt.subplots(3,1,figsize=(11.5,9.6),layout='constrained',gridspec_kw={'height_ratios':[1,1.25,1]})
    axes[0].step([timestamp(x['utc']) for x in creates],[x['resident_count'] for x in creates],where='post',color='#315f85',label='Successful create responses (cumulative)')
    axes[0].axvspan(began,fail,color='#c66840',alpha=.2,label='Failed create request (host clock)')
    for i,g in enumerate(guests):
        axes[0].axvspan(timestamp(g['sdk_invocation_host_start_utc']),timestamp(g['sdk_invocation_host_end_utc']),
                        color='#6c9872',alpha=.12,label='Three-guest inspection calls' if i==0 else None)
    axes[0].set_title('Host UTC: 116 successful creates, then attempt 117 failed',fontweight='bold')
    axes[0].set_ylabel('Successful creates');axes[0].set_ylim(0,130)
    axes[0].set_xlim(timestamp(summary['started_utc'])-timedelta(seconds=2),fail+timedelta(seconds=7))
    axes[0].legend(loc='upper left',fontsize=8)
    axes[1].step(vm_times,[x['vm_cpu_cores'] for x in resource],where='pre',color='#b2573d',label='Outer VM CPU')
    axes[1].step(vm_times,[x['cgroup_cpu_cores'] for x in resource],where='pre',color='#497d74',label='Continuous sandbox cgroups')
    axes[1].axvspan(init_start,init_end,color='#c66840',alpha=.13,label='envd wait window (VM log)')
    axes[1].set_title('VM UTC: CPU burst; collector delay prevents pointwise attribution',fontweight='bold')
    axes[1].set_ylabel('Sampled CPU cores');axes[1].set_ylim(0,155)
    axes[1].set_xlim(vm_times[0]-timedelta(seconds=2),response+timedelta(seconds=8))
    axes[1].legend(loc='upper left',fontsize=8)
    groups=[('milestone',32),('milestone',64),('milestone',96),('first_creation_failure',116)]
    colors={'oldest':'#315f85','middle':'#7b6395','newest':'#6c9872'}
    for role,offset in [('oldest',-.17),('middle',0),('newest',.17)]:
        ys=[next(g['guest_cpu_cores'] for g in guests if (g['reason'],g['resident_count'])==key and g['selection']==role) for key in groups]
        axes[2].scatter([i+offset for i in range(4)],ys,label=role,color=colors[role],s=48)
    axes[2].set_xticks(range(4),['N=32','N=64','N=96','After failure\nN=116 survivors'])
    axes[2].set_ylabel('Guest CPU cores');axes[2].set_ylim(0,.105)
    axes[2].set_title('Three selected surviving guests: one-second observations, not the failed guest',fontweight='bold')
    axes[2].legend(loc='upper right',fontsize=9)
    for ax in axes[:2]:ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S',tz=timezone.utc))
    for ax in axes:
        ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Slow-ramp idle pool: one creation per second',fontweight='bold',fontsize=14)
    fig.supxlabel('Host and VM clocks are uncorrected (observed gap about 1.1 s); vertical time alignment is approximate.\nPost-failure observations do not cover the preceding envd timeout or the failed new guest.',fontsize=9)
    for ext in ('png','svg'):fig.savefig(directory/f'idle-pool-ramp-cpu.{ext}',dpi=180)
    plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(10.5,6.1),sharex=True,layout='constrained')
    axes[0].step(vm_times,[x['vm_cpu_cores'] for x in resource],where='pre',label='Outer VM',color='#b2573d')
    axes[0].step(vm_times,[x['cgroup_cpu_cores'] for x in resource],where='pre',label='Continuous cgroups',color='#497d74')
    axes[0].set_ylabel('CPU cores');axes[0].legend(loc='upper left')
    axes[1].plot(vm_times,[x['interval_seconds'] for x in resource],marker='.',label='Effective sample interval',color='#315f85')
    axes[1].plot(vm_times,[x['collection_ms']/1000 for x in resource],marker='.',label='Collection wall time',color='#7b6395')
    axes[1].axhline(2,color='#999',linestyle='--',label='Requested interval: 2 s')
    axes[1].set_ylabel('Seconds');axes[1].legend(loc='upper left',fontsize=9)
    for ax in axes:
        ax.axvspan(init_start,init_end,color='#c66840',alpha=.12)
        ax.axvline(response,color='#333',linestyle=':',label='API 500' if ax is axes[0] else None)
        ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
    axes[0].legend(loc='upper left')
    axes[1].set_xlim(init_start-timedelta(seconds=5),response+timedelta(seconds=6))
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S',tz=timezone.utc))
    axes[1].set_xlabel('VM UTC; shaded interval = snapshot loaded to envd initialization failure')
    fig.suptitle('Incident window: irregular collection limits attribution',fontweight='bold')
    for ext in ('png','svg'):fig.savefig(directory/f'idle-pool-incident.{ext}',dpi=180)
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--raw-root',type=Path,default=RAW)
    args=parser.parse_args();sources=[]
    diagnostic,source=load(args.raw_root/'idle-pool-slow-ramp.json');sources.append(source)
    events,source=load(args.raw_root/'idle-pool-slow-ramp.observations.jsonl',True);sources.append(source)
    resources,source=load(args.raw_root/'idle-pool-slow-ramp.resources.jsonl',True);sources.append(source)
    manifest,source=load(args.raw_root/'idle-pool-slow-ramp.manifest.json');sources.append(source)
    log,source=load(PHASE/'results/idle-pool-case4-log-20260920.json');sources.append(source)
    if not diagnostic['cleanup']['verified_zero'] or resources[-1].get('record_type')!='completion':raise ValueError('diagnostic inputs are not finalized')
    if log['run_id']!=diagnostic['run_id']:raise ValueError('log belongs to another run')
    if log['source_diagnostic']['sha256']!=sources[0]['sha256']:raise ValueError('log references a different diagnostic payload')
    created=[e for e in events if e['record_type']=='created'];observations=[e for e in events if e['record_type']=='guest_observation']
    if len(created)!=diagnostic['created_instances']:raise ValueError('create count mismatch')
    fail=timestamp(diagnostic['creation_failure']['utc'])
    create_rows=[{'utc':e['utc'],'offset_seconds':e['offset_seconds'],'resident_count':e['resident_count'],
        'duration_seconds':e['duration_seconds'],'request_start_offset_seconds':e['offset_seconds']-e['duration_seconds']} for e in created]
    guest_rows=[]
    for e in observations:
        g=e['guest'];end=timestamp(e['utc']);begin=end-timedelta(seconds=e['duration_seconds'])
        if e['status']!='success':raise ValueError('guest probe did not complete')
        if any(x['pid']==g['collector_pid_excluded'] for x in g['processes']):raise ValueError('collector PID leaked into process accounting')
        guest_rows.append({'reason':e['reason'],'resident_count':e['resident_count'],'selection':e['selection'],
            'creation_index':e['creation_index'],'sandbox_id':e['sandbox_id'],
            'sdk_invocation_host_start_utc':begin.isoformat(),'sdk_invocation_host_end_utc':end.isoformat(),
            'sdk_duration_seconds':e['duration_seconds'],'guest_counter_interval_seconds':g['interval_seconds'],
            'guest_cpu_cores':g['guest_busy_cpu_cores'],'continuous_process_cpu_cores':sum(x['cpu_cores'] for x in g['processes']),
            'continuous_processes':g['continuous_processes'],'new_processes':len(g['new_pids']),'exited_processes':len(g['exited_pids']),
            'nonzero_process_names':';'.join(x['comm'] for x in g['processes'] if x['cpu_cores']>0),
            'wholly_after_client_failure':begin>=fail})
    resource_rows=[]
    for e in resources:
        if e.get('record_type')!='sample':continue
        g=e['guest'];cg=g['cgroup_summary']
        resource_rows.append({'guest_utc':g['utc'],'host_received_utc':e['received_utc'],'interval_seconds':g['interval_seconds'],
            'vm_cpu_cores':g['cpu']['busy_core_equivalents'],'cgroup_cpu_cores':cg['cpu_cores_continuous_only'],
            'firecracker':g['processes']['live_process_counts']['firecracker'],'cgroups':cg['count'],'nbd_attached':g['nbd_attached'],
            'new_cgroups':cg['new_cgroups'],'exited_cgroups':cg['exited_cgroups'],
            'cgroup_throttled_seconds':cg['throttled_seconds_continuous_only'],
            'available_gib':g['memory_bytes']['MemAvailable']/2**30,'cpu_psi_some_avg10':g['pressure']['cpu']['some']['avg10'],
            'arp_entries':g['arp']['entries'],'arp_table_fulls_delta':g['arp']['table_fulls_delta'],
            'collection_ms':g['collection_ms'],'schedule_lag_ms':g.get('sampler_schedule_lag_ms'),
            'load_generator_cpu_cores':sum(x.get('cpu_cores',0) for x in e['host']['selected_processes'].values()),
            'received_minus_guest_utc_ms':(timestamp(e['received_utc'])-timestamp(g['utc'])).total_seconds()*1000})
    init_start=timestamp(next(x['utc'] for x in log['server_vm_timeline'] if x['event']=='load_snapshot_api_completed'))
    init_end=timestamp(next(x['utc'] for x in log['server_vm_timeline'] if x['event']=='envd_init_failed_after_retries'))
    low_delay=[x['received_minus_guest_utc_ms'] for x in resource_rows if x['collection_ms']<200]
    starts=[x['request_start_offset_seconds'] for x in create_rows]
    last_zero=next(x for x in resource_rows if timestamp(x['guest_utc'])>=init_end and x['firecracker']==x['cgroups']==x['nbd_attached']==0)
    peaks={key:max(x[key] for x in resource_rows) for key in ['vm_cpu_cores','cgroup_cpu_cores','firecracker','cgroups','nbd_attached','cpu_psi_some_avg10','collection_ms','schedule_lag_ms','arp_entries','load_generator_cpu_cores']}
    failure_guests=[x for x in guest_rows if x['reason']=='first_creation_failure']
    report={'schema_version':1,'generated_utc':datetime.now(timezone.utc).isoformat(),'analysis_cpu_affinity':sorted(os.sched_getaffinity(0)),
        'run_id':diagnostic['run_id'],'scope':'Separate slow-ramp diagnostic case4; not a fourth formal-matrix setup failure.',
        'outcome':{'status':diagnostic['status'],'successful_creates':len(created),'create_attempts':diagnostic['create_attempts'],
            'requested_maximum':diagnostic['maximum_requested_instances'],'elapsed_seconds':diagnostic['elapsed_seconds'],
            'minimum_observed_creation_start_spacing_seconds':min(b-a for a,b in zip(starts,starts[1:])),
            'median_successful_create_seconds':statistics.median(x['duration_seconds'] for x in create_rows),
            'creation_failure':diagnostic['creation_failure'],'cleanup':diagnostic['cleanup']},
        'resource_summary':{'samples':len(resource_rows),'sampled_peaks':peaks,'minimum_available_gib':min(x['available_gib'] for x in resource_rows),
            'arp_overflow_delta':sum(x['arp_table_fulls_delta'] for x in resource_rows),
            'cgroup_throttled_seconds':sum(x['cgroup_throttled_seconds'] for x in resource_rows),
            'actual_interval_seconds':{'min':min(x['interval_seconds'] for x in resource_rows),'max':max(x['interval_seconds'] for x in resource_rows)},
            'first_reported_all_zero_after_failure_vm_utc':last_zero['guest_utc']},
        'envd_window_nominal_cpu':cpu_window(resource_rows,init_start,init_end),
        'guest_observations':guest_rows,'after_failure_survivors':failure_guests,
        'clock_domains':{'low_collection_threshold_ms':200,'samples':len(low_delay),
            'received_host_minus_guest_utc_ms':{'min':min(low_delay),'median':statistics.median(low_delay),'max':max(low_delay)},
            'server_client_log_gap_seconds':log['clock_domain_caution']['apparent_timestamp_gap_seconds'],
            'duration_difference_seconds':log['clock_domain_caution']['duration_difference_seconds'],
            'interpretation':'Includes remaining collection/transport delay; not a precise clock-offset or network-latency estimate.'},
        'direct_failure':log['direct_failure'],'failed_sandbox_id':log['failed_sandbox_id'],
        'limits':['Surviving guest probes started after client failure and did not observe the failed new guest or preceding envd wait.',
                  'Cgroup, VM CPU and process counts are sequential reads; 6-second collector delay prevents pointwise subtraction or a reliable peak attribution fraction.',
                  'Only three selected guests are sampled at each checkpoint; no inference of no guest/kernel/KVM fault.',
                  'One slower run with milestone pauses is not a causal rate experiment or a verified mitigation.'],
        'execution_manifest':manifest,'sources':sources}
    output=PHASE/'results';output.mkdir(exist_ok=True)
    (output/'idle-pool-summary.json').write_text(json.dumps(report,indent=2)+'\n')
    write_csv(output/'idle-pool-creation.csv',create_rows);write_csv(output/'idle-pool-timeline.csv',resource_rows);write_csv(output/'idle-pool-guest-observations.csv',guest_rows)
    figure(diagnostic,create_rows,resource_rows,guest_rows,log,PHASE/'figures')
    lines=['# 慢速建立空闲实例池：诊断结果','',
      '**降低到最多每秒一次创建，本次仍在第 117 次请求失败；此前 116 个实例成功建立，最终全部清理。** 这是独立的 slow-ramp 诊断 case4，不替换或增加正式矩阵中的三次 setup 失败。总耗时 288.87 秒，未达到 256 实例目标。','',
      '本次没有模型、测试套件或负载任务；每 32 个实例只在 oldest/middle/newest 三个 owned guest 内执行短 CPU 计数器检查。这些检查本身有开销，也会暂停后续创建。报告只分析已冻结输入；完整结果与来源哈希见 [idle-pool-summary.json](results/idle-pool-summary.json)。','',
      '## 创建、失败与清理','',
      '| 事件 | 时间 / 结果 |','|---|---|',
      f"| 开始（宿主 UTC） | {diagnostic['started_utc']} |",
      '| 32 / 64 / 96 实例 milestone | 开始后 31.98 / 69.32 / 103.20 秒 |',
      f"| 成功请求之间最小开始间隔 | {report['outcome']['minimum_observed_creation_start_spacing_seconds']:.6f} 秒；没有追赶式突发 |",
      f"| 成功创建延迟中位数 | {report['outcome']['median_successful_create_seconds']:.3f} 秒；只是该诊断的描述值 |",
      '| 第 117 次请求 | 宿主 12:50:40.691 返回 SandboxException / HTTP 500，耗时 12.570 秒 |',
      '| 失败后操作 | 不重试创建；先检查三个已有实例，再清理 |',
      '| 最终清理 | verified_zero，146 次空列表观察，0 次清理读/删错误；宿主 12:53:11.141 结束 |','',
      '这 146 次检查主要用于保留未知创建结果的 150 秒观察窗，不表示删除本身耗时 150 秒。外层资源记录在 VM 时钟 12:50:43.821 的采样已看到 FC/cgroup/NBD 都为零；这也是顺序采集的时点记录，不是精确的最后进程退出时间。','',
      '![建立过程、外层 CPU 与抽样 guest](figures/idle-pool-ramp-cpu.png)','',
      '## 直接失败阶段已确认，CPU 原因仍未确定','',
      '关联日志识别出失败的新实例 `'+log['failed_sandbox_id']+'`。VM 日志中，Firecracker API 在 12:50:27.121 启动，snapshot load 在 27.163 完成（约 31.5 ms）；随后 envd 初始化等待在 37.165 失败，累计 58 次、单次 timeout 50 ms。API 在 39.578 返回 500，handler 耗时 12.5599 秒。直接失败阶段与正式 case2/3 的 envd 初始化超时相同，区别于 case1 的首次 metrics socket 连接拒绝。详见 [独立 case4 日志证据](results/idle-pool-case4-log-20260920.md)。','',
      '| 外层采样指标 | 本次结果 |','|---|---:|',
      f"| FC / cgroup / attached NBD 采样峰值 | {peaks['firecracker']} / {peaks['cgroups']} / {peaks['nbd_attached']} |",
      f"| VM CPU 采样峰值 | {peaks['vm_cpu_cores']:.2f} 核（192 核中的约 {peaks['vm_cpu_cores']/192*100:.2f}%） |",
      f"| 连续 cgroup CPU 的独立采样峰值 | {peaks['cgroup_cpu_cores']:.2f} 核；与 VM 峰值不是同一时点 |",
      f"| CPU PSI some avg10 峰值 | {peaks['cpu_psi_some_avg10']:.2f}% |",
      f"| 宿主负载生成进程 CPU 采样峰值 | {peaks['load_generator_cpu_cores']:.3f} 核 |",
      f"| 最低 MemAvailable | {report['resource_summary']['minimum_available_gib']:.2f} GiB |",
      f"| ARP entries 峰值 / 溢出新增 | {peaks['arp_entries']} / {report['resource_summary']['arp_overflow_delta']} |",
      '| 记录到的 cgroup throttle 增量 | 0 |','',
      '这里 117 个运行对象与 116 个成功 SDK handle 不矛盾：失败的新 guest 已部分启动，但 API 没有向客户端交付成功 handle。它不在后续三个 surviving guest 的检查样本中。观察到明显外层 CPU 和 cgroup 活动，宿主 driver 自身没有 CPU 饱和迹象；这不排除请求间接触发 guest/runtime 活动。本数据不足以给出精确的峰值 CPU 归属比例。','',
      '![事发窗口与采集延迟](figures/idle-pool-incident.png)','',
      f"事发时单次采集 wall time 达 **{peaks['collection_ms']/1000:.3f} 秒**，调度迟到最大 **{peaks['schedule_lag_ms']/1000:.3f} 秒**；实际采样间隔从 **{report['resource_summary']['actual_interval_seconds']['min']:.3f} 到 {report['resource_summary']['actual_interval_seconds']['max']:.3f} 秒**，并非整齐的两秒。CPU、cgroup 和进程信息依次读取，因此不能在某一行直接用 VM CPU 减 cgroup CPU 来认定“剩余 CPU 属于管理层”，也不能把各自峰值相除。JSON 仅另保留完整落在 envd 等待窗内的名义区间加权描述，不补造边界样本。",'',
      '## 三个 guest 的低 CPU 仅说明检查时的状态','',
      '| 检查时池大小 | oldest guest 核数 | middle guest 核数 | newest guest 核数 |','|---|---:|---:|---:|']
    for reason,n in [('milestone',32),('milestone',64),('milestone',96),('first_creation_failure',116)]:
        values=[next(x['guest_cpu_cores'] for x in guest_rows if x['reason']==reason and x['resident_count']==n and x['selection']==role) for role in ('oldest','middle','newest')]
        lines.append(f"| {'失败后 ' if reason!='milestone' else ''}{n} | {values[0]:.3f} | {values[1]:.3f} | {values[2]:.3f} |")
    lines+=['',
      '**失败后的三个检查均在客户端错误之后发起（约宿主 12:50:40.695）**，分别在约 42.177、43.648、43.809 返回；各 guest 内计数间隔约一秒。它们只能证明这三个已存活 guest 在各自检查时 CPU 较低。它们没有覆盖此前的十秒 envd 等待，也没有检查失败的新 guest，不能由此排除 guest kernel、envd、其他 guest 或 KVM/VMM 的问题。','',
      '全部 12 个检查都返回约 80 个连续进程，采集器自身 PID 已排除。失败后的非零进程计数只有所选 guest 的 envd，各约 0.01 核；aggregate 仍包含采集器与内核记账等开销，不能将 aggregate 与进程和的差值再作精细归因。未采集进程 args/env，原始计数保存在私有 observations 文件中。','',
      '## 时钟域与结论边界','',
      f"宿主客户端错误时间比 VM 的 API 结束日志晚 **{log['clock_domain_caution']['apparent_timestamp_gap_seconds']:.3f} 秒**，但两端各自测量的请求时长仅差约 **{log['clock_domain_caution']['duration_difference_seconds']*1000:.1f} ms**。低采集耗时的 {len(low_delay)} 个资源样本中，宿主接收 UTC 减 VM 采样 UTC 的中位数约 **{statistics.median(low_delay)/1000:.3f} 秒**，范围 {min(low_delay)/1000:.3f}–{max(low_delay)/1000:.3f} 秒；该差还包括残余采集和输送时间。不能把它当成网络耗时，也没有足够证据给所有事件做精确时钟校正。图分别使用宿主与 VM 自己的时间轴，跨层判断优先使用已知调用顺序和各端单调时长。",'',
      '外层 VM 内部计数峰值和宿主本地 driver CPU 不依赖跨机器 UTC 配准，但精确的跨层同时归属不成立。更完整的跨轮时钟与敏感性边界见 [CLOCK-ALIGNMENT.md](../phase0-environment/CLOCK-ALIGNMENT.md)；本诊断保留未平移的原始 VM 时间序列，事后检查不能证明历史偏移始终恒定。','',
      '本次证明“每秒一次创建”在当前部署与历史状态下仍可能触发 envd 初始化失败，不能把降速称为已验证修复；它没有测出一个固定的实例容量上限。检查只有一次慢速建立、三个位置抽样和事后窗口，CPU 活动来源与 envd 无法及时就绪之间的因果链仍未闭合。正式失败 repeat、这次独立诊断及其全部清理结果分别保留。','',
      '## 复现分析','',
      '```bash','taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B \\',
      '  /home/yuhanya/Daily_LLM/sandbox/benchmark/phase1-service/code/analyze_idle_pool.py','```','',
      '生成器只读取四个冻结的诊断文件和独立 case4 日志 JSON，不调用服务。输出包括 [summary JSON](results/idle-pool-summary.json)、[创建 CSV](results/idle-pool-creation.csv)、[资源时间线 CSV](results/idle-pool-timeline.csv)、[guest 抽样 CSV](results/idle-pool-guest-observations.csv) 及两张 PNG/SVG。来源与生成代码哈希见 [分析 manifest](results/idle-pool-analysis-manifest.json)。']
    (PHASE/'IDLE-POOL-RESULTS.md').write_text('\n'.join(lines)+'\n')
    products=[PHASE/'IDLE-POOL-RESULTS.md',output/'idle-pool-summary.json',output/'idle-pool-creation.csv',output/'idle-pool-timeline.csv',output/'idle-pool-guest-observations.csv',
              *sorted((PHASE/'figures').glob('idle-pool-*'))]
    evidence={'generated_utc':report['generated_utc'],'analysis_cpu_affinity':sorted(os.sched_getaffinity(0)),
        'sources':sources,'generator':{'path':str(Path(__file__)),'sha256':digest(Path(__file__).read_bytes())},
        'outputs':[{'path':str(x),'bytes':x.stat().st_size,'sha256':digest(x.read_bytes())} for x in products]}
    (output/'idle-pool-analysis-manifest.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'status':'written','report':str(PHASE/'IDLE-POOL-RESULTS.md'),'sampled_peaks':peaks,
                      'nominal_envd_window':report['envd_window_nominal_cpu'],'after_failure_guest_cores':[x['guest_cpu_cores'] for x in failure_guests]},indent=2))


if __name__=='__main__':main()
