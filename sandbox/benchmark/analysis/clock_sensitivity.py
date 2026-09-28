#!/usr/bin/env python3
"""Post hoc guest-UTC alignment sensitivity; counter rates and raw files stay unchanged."""

from datetime import datetime, timezone
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

import resources
from summarize import save_json, write_csv

ROOT=Path('/home/yuhanya/Daily_LLM/sandbox/benchmark')
OUT=ROOT/'phase0-environment/results/clock-sensitivity'
PROBE=ROOT/'phase0-environment/results/clock-check-final.json'
SHIFTS=(0.0,0.8,1.0,1.2)


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(4*1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def tail_final(path):
    path=Path(path)
    with path.open('rb') as handle:
        handle.seek(max(0,path.stat().st_size-65536))
        final=json.loads(handle.read().splitlines()[-1])
    assert final.get('record_type')=='run',path
    return final


def select(samples,start,end,shift):
    # Only selection labels move. The stored data, measured rate and interval
    # duration are not changed, prorated, interpolated, or recomputed.
    overlapping=[s for s in samples if s['end']+shift>start and s['start']+shift<end]
    contained=[s for s in overlapping if s['start']+shift>=start-1e-6 and s['end']+shift<=end+1e-6]
    covered=sum(s['interval_seconds'] for s in contained)
    cpu=sum(s['data']['cpu']['busy_core_equivalents']*s['interval_seconds'] for s in contained)/covered if covered else None
    return {'vm_cpu_mean_cores':cpu,'covered_seconds':covered,'coverage_pct':100*covered/(end-start),
            'contained_samples':len(contained),'excluded_boundary_samples':len(overlapping)-len(contained),
            'selected_source_line_indices':[s['sample_index'] for s in contained]}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    inputs=[];rows=[];published_difference=[]
    for phase in ('phase1-service','phase2-sdk'):
        folder=ROOT/phase/'results/main-matrix'
        manifest=read(folder/'INPUTS-FROZEN.json')
        eligible={r['run_id']:r for r in read(folder/'runs.json') if r.get('performance_eligible')}
        published={(r['scope_id'],r['capture_id']):r['time_weighted_mean']
                   for r in read(folder/'resources/resource_summary.json')
                   if r['layer']=='guest' and r['entity']=='cpu' and r['metric']=='busy_core_equivalents'}
        for entry in manifest['inputs']:
            if entry['run_id'] not in eligible:continue
            run=eligible[entry['run_id']]
            for field in ('operations','resources'):
                assert sha(entry[field]['path'])==entry[field]['sha256'],entry[field]['path']
            final=tail_final(entry['operations']['path'])
            assert final['run_id']==entry['run_id']
            origin=resources.utc_seconds(final['run_started_utc'])
            start=origin+final['measurement_start_offset_s'];end=origin+final['measurement_end_offset_s']
            samples,issues,_=resources.read_samples([Path(entry['resources']['path'])])
            guest=[s for s in samples if s['layer']=='guest']
            capture=entry['resources']['sha256'][:16]
            baseline=select(guest,start,end,0.0)
            previous=published[('run:'+entry['run_id'],capture)]
            difference=abs(previous-baseline['vm_cpu_mean_cores'])
            published_difference.append(difference)
            assert difference<1e-8,(entry['run_id'],previous,baseline)
            input_row={'run_id':entry['run_id'],'phase':phase,'operations':entry['operations'],'resources':entry['resources'],
                       'main_manifest_path':str(folder/'INPUTS-FROZEN.json'),'main_manifest_sha256':sha(folder/'INPUTS-FROZEN.json')}
            inputs.append(input_row)
            cpus=guest[0]['data']['cpu']['logical_cpus']
            for shift in SHIFTS:
                selected=select(guest,start,end,shift)
                value=selected['vm_cpu_mean_cores'];base=baseline['vm_cpu_mean_cores']
                row={k:run.get(k) for k in ('run_id','phase','scenario','repeat','concurrency','resident_count','mode','arrival_rate_ops_s')}
                row.update(guest_utc_assumed_shift_s=shift,alignment_status='hypothetical sensitivity; not corrected historical truth',
                           client_window_start_utc=resources.iso_utc(start),client_window_end_utc=resources.iso_utc(end),
                           window_seconds=end-start,logical_vm_cpus=cpus,**selected,
                           published_unshifted_cpu_mean_cores=previous,
                           delta_cpu_cores_from_unshifted=value-base if value is not None else None,
                           delta_pct_of_unshifted=100*(value-base)/base if value is not None and base else None,
                           vm_mean_pct_of_logical_capacity=100*value/cpus if value is not None else None,
                           resource_quality_findings=issues,source_capture=entry['resources']['path'])
                rows.append(row)
    assert len(inputs)==104 and len(rows)==416,(len(inputs),len(rows))
    groups=defaultdict(list)
    keys=('phase','scenario','concurrency','resident_count','mode','arrival_rate_ops_s','guest_utc_assumed_shift_s')
    for row in rows:groups[tuple(row[k] for k in keys)].append(row)
    summaries=[]
    for key,group in sorted(groups.items(),key=lambda item:repr(item[0])):
        values=[r['vm_cpu_mean_cores'] for r in group if r['vm_cpu_mean_cores'] is not None]
        complete=len(values)==len(group)
        summaries.append(dict(zip(keys,key),run_count=len(group),runs_with_samples=len(values),
            vm_cpu_mean_cores_median=statistics.median(values) if complete else None,
            vm_cpu_mean_cores_min=min(values) if values else None,vm_cpu_mean_cores_max=max(values) if values else None,
            minimum_coverage_pct=min(r['coverage_pct'] for r in group)))
    shifted=[r for r in rows if r['guest_utc_assumed_shift_s']]
    maximum_change=max(shifted,key=lambda r:abs(r['delta_cpu_cores_from_unshifted']) if r['delta_cpu_cores_from_unshifted'] is not None else -1)
    maximum_cpu=max(rows,key=lambda r:r['vm_cpu_mean_cores'] if r['vm_cpu_mean_cores'] is not None else -1)
    probe=read(PROBE)
    lower=max(r['offset_lower_ms'] for r in probe['samples']);upper=min(r['offset_upper_ms'] for r in probe['samples'])
    overview={'eligible_main_runs':len(inputs),'sensitivity_rows':len(rows),'assumed_shifts_s':list(SHIFTS),
              'historical_offset_correction_claimed':False,'counter_rates_modified':False,'workloads_rerun':False,
              'unshifted_max_difference_from_published_cores':max(published_difference),
              'rows_without_contained_intervals':sum(r['vm_cpu_mean_cores'] is None for r in rows),
              'largest_absolute_change':maximum_change,'largest_hypothetical_vm_mean':maximum_cpu,
              'post_probe_constant_offset_intersection_ms_if_assumed':[lower,upper],
              'main_primary_client_metrics_recomputed':False}
    for name,data in (('per_run',rows),('by_configuration',summaries)):
        save_json(OUT/(name+'.json'),data);write_csv(OUT/(name+'.csv'),data)
    save_json(OUT/'summary.json',overview)
    save_json(OUT/'INPUTS.json',{'probe':{'path':str(PROBE),'sha256':sha(PROBE)},'runs':inputs})
    lines=['# VM / host 时钟对齐：事后敏感性检查','',
        '事后检查发现 VM UTC 与 host/client UTC 不能假定完全同步。客户端的延迟与吞吐使用本进程单调时钟，未受此次跨机对齐问题影响；host/发生器 CPU 也使用同机时间域。原报告的 VM 平均 CPU 按未平移 UTC 选择采样窗口，应作为近似归属，不能据此做精确的逐请求跨层归因。','',
        f"[13:03 UTC 的三次 SSH 括界检查](results/clock-check-final.json)中，guest−host 中点估计分别约 −835.5、−962.8、−964.2 ms，RTT 为300–557 ms。若额外假定这三次之间偏移恒定，区间交集约为 [{lower:.2f}, {upper:.2f}] ms；中点不是已知真实偏移。这次事后探测不能证明此前数小时一直具有相同偏移。sampler的received_utc−guest.utc还包含采集/传递时间，不能直接拿来校准。",'',
        '本检查保留所有原始UTC、单调时钟、计数器速率和原报告数值，只在窗口选择时假设guest UTC标签加0、0.8、1.0或1.2秒。每种假设仍只选完整落在client测量窗内的guest采样区间，按原interval_seconds加权；不切分边界，不插值，不把缺失值置零。正向平移用于检验guest时间落后host时可能改变的样本归属。','',
        f"覆盖主矩阵全部 **104个有效run × 4种对齐假设 = 416条结果**。零平移对原报告CPU均值的最大复现差为 {max(published_difference):.3g} 核；没有重算客户端延迟/吞吐，也没有重跑工作负载或调整系统时钟。三个失败setup不进入测量窗敏感性；同一VM内部的计数器峰值和整段capture观察不依赖跨机平移。",'',
        '| exec_true 路径 | C=N | 有效轮数 | 未平移 VM平均核中位数 | +0.8s | +1.0s | +1.2s |','|---|---:|---:|---:|---:|---:|---:|']
    for phase in ('phase1-service','phase2-sdk'):
        for c in (8,32,64):
            selected=[next(r for r in summaries if r['phase']==phase and r['scenario']=='exec_true' and r['concurrency']==c and r['resident_count']==c and r['guest_utc_assumed_shift_s']==s) for s in SHIFTS]
            values=[r['vm_cpu_mean_cores_median'] for r in selected]
            label='HTTPX' if phase=='phase1-service' else 'SDK'
            lines.append(f"| {label} | {c} | {selected[0]['run_count']} | "+' | '.join(f'{v:.2f}' if v is not None else '无完整区间' for v in values)+' |')
    lines += ['',f"全部场景中，最大的窗口均值改变量为 {maximum_change['delta_cpu_cores_from_unshifted']:+.2f} 核：{maximum_change['phase']} / {maximum_change['scenario']} / C={maximum_change['concurrency']} / repeat={maximum_change['repeat']} / shift=+{maximum_change['guest_utc_assumed_shift_s']:.1f}s。各假设下最大的VM平均CPU为 {maximum_cpu['vm_cpu_mean_cores']:.2f}/192 核（{maximum_cpu['vm_mean_pct_of_logical_capacity']:.1f}%）。无完整区间的结果共有 {overview['rows_without_contained_intervals']} 条。较短批次和时间变化较快的负载，其窗口均值更敏感；这些差异不用于宣称精确历史CPU值。",'',
        '在这组假设范围内，主矩阵不存在“整台192-vCPU VM的平均CPU已耗尽”的证据，这一总量判断保持成立。局部热核、串行路径或瞬时尖峰仍可能限制性能。客户端/发生器方向的证据主要来自同机发生器CPU以及保持global C/N后的多进程对照，并不依赖把某个VM采样点精确对齐到某条RPC；其量化因果解释仍有实验设计限制。', '',
        '本敏感性不等于历史校准，不把同样偏移强行套到多发生器或idle实验。相关报告保留未校准UTC选择的VM数值并明确限制；精确跨层时序需要在实验期间记录双向时钟界限或可靠的同步状态。','',
        '[逐run CSV](results/clock-sensitivity/per_run.csv) · [逐run JSON](results/clock-sensitivity/per_run.json) · [按配置汇总](results/clock-sensitivity/by_configuration.csv) · [总览](results/clock-sensitivity/summary.json) · [输入SHA](results/clock-sensitivity/INPUTS.json) · [分析源码](../analysis/clock_sensitivity.py)。','',
        '复现仅做本地读取：','',
        '```bash','taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \\',
        '  /home/yuhanya/Daily_LLM/sandbox/benchmark/analysis/clock_sensitivity.py','```','']
    (ROOT/'phase0-environment/CLOCK-ALIGNMENT.md').write_text('\n'.join(lines))
    save_json(OUT/'ANALYSIS-PROVENANCE.json',{'generated_utc':datetime.now(timezone.utc).isoformat(),
        'analysis_script':str(Path(__file__)),'analysis_sha256':sha(__file__),
        'resource_reader_sha256':sha(ROOT/'analysis/resources.py'),'python':sys.version,'cpu_affinity':'16-31',
        'raw_files_modified':False,'counter_rates_modified':False,'clocks_adjusted':False,'service_requests':0})
    print(json.dumps({'eligible_runs':len(inputs),'rows':len(rows),'zero_shift_max_difference_cores':max(published_difference),
                      'largest_abs_delta_cores':abs(maximum_change['delta_cpu_cores_from_unshifted']),
                      'largest_vm_mean_cores':maximum_cpu['vm_cpu_mean_cores'],'missing_rows':overview['rows_without_contained_intervals'],
                      'report':str(ROOT/'phase0-environment/CLOCK-ALIGNMENT.md')}))


if __name__=='__main__':
    main()
