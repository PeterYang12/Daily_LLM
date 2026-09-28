#!/usr/bin/env python3
"""Write the frozen 2026-09-20 main-matrix narrative from derived observations."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

from summarize import save_json, write_csv

ROOT = Path("/home/yuhanya/Daily_LLM/sandbox/benchmark")
PROBE = "/data2/yuhanya/e2b-benchmark/session-20260920/phase0/file-transport-probe.json"


def read(path):
    return json.loads(path.read_text())


def load_phase(phase):
    folder = ROOT / phase / "results/main-matrix"
    data = {name: read(folder / f"{name}.json") for name in ("runs", "per_run", "summary", "plot_index", "INPUTS-FROZEN")}
    data["folder"] = folder
    data["coverage"] = read(folder / "resources/resource_coverage.json")
    resource_input = read(folder / "resources/resource_inputs.json")["files"]
    cap_by_path = {r["path"]: r["capture_id"] for r in resource_input}
    metrics = read(folder / "resources/resource_summary.json")
    lookup = {(r["scope_id"], r["capture_id"], r["layer"], r["entity"], r["metric"]): r for r in metrics}
    entries = {r["run_id"]: r for r in data["INPUTS-FROZEN"]["inputs"]}
    resources = []
    for run in data["runs"]:
        entry = entries[run["run_id"]]
        capture = cap_by_path[entry["resources"]["path"]]
        eligible = run["performance_eligible"]
        scope = "run:" + run["run_id"] if eligible else "capture:" + capture
        def metric(layer, entity, name, statistic="time_weighted_mean"):
            record = lookup.get((scope, capture, layer, entity, name), {})
            return record.get(statistic)
        processes = [r for r in metrics if r["scope_id"] == scope and r["capture_id"] == capture and r["layer"] == "host"
                     and r["entity"].startswith("process:") and r["metric"] == "cpu_cores"]
        row = {k: run.get(k) for k in ("run_id", "phase", "scenario", "repeat", "concurrency", "resident_count", "performance_eligible", "stop_reason", "cleanup_ok")}
        row.update(resource_scope="measurement intervals" if eligible else "whole capture; failed setup diagnostic only",
                   raw_resource_path=entry["resources"]["path"], capture_id=capture,
                   generator_cpu_mean_cores=processes[0]["time_weighted_mean"] if len(processes) == 1 else None,
                   vm_cpu_mean_cores=metric("guest", "cpu", "busy_core_equivalents"),
                   host_cpu_mean_cores=metric("host", "cpu", "busy_core_equivalents"),
                   vm_iowait_pct=metric("guest", "cpu", "iowait_pct"),
                   vm_vda_busy_pct=metric("guest", "disk:vda", "busy_pct"),
                   sampled_max_firecracker=metric("guest", "process_count", "firecracker", "observed_max"),
                   vm_memory_unavailable_max_bytes=metric("guest", "memory", "unavailable_bytes", "observed_max"))
        coverage = [r for r in data["coverage"] if r["scope_id"] == scope and r["capture_id"] == capture]
        row["resource_coverage_min_pct"] = min((r["coverage_pct"] for r in coverage), default=None)
        resources.append(row)
    data["resource_by_run"] = resources
    save_json(folder / "resource_by_run.json", resources)
    write_csv(folder / "resource_by_run.csv", resources)
    return data


def get(data, scenario, concurrency, residents=None, mode="closed_loop", rate=None):
    found = [r for r in data["summary"] if r["scenario"] == scenario and r["concurrency"] == concurrency
             and r["resident_count"] == residents and r["mode"] == mode and r.get("arrival_rate_ops_s") == rate]
    assert len(found) == 1, (scenario, concurrency, residents, mode, rate)
    return found[0]


def throughput(row):
    return f"{row['successful_throughput_ops_s']:.2f} [{row['successful_throughput_ops_s_min']:.2f}, {row['successful_throughput_ops_s_max']:.2f}]"


def graph(data, kind, scenario):
    matches = [r for r in data["plot_index"] if r["kind"] == kind and r["configuration"]["scenario"] == scenario]
    assert matches
    return "results/main-matrix/" + matches[0]["png"]


def median_resource(data, concurrency, field):
    rows = [r for r in data["resource_by_run"] if r["performance_eligible"] and r["scenario"] == "exec_true"
            and r["concurrency"] == concurrency and r["resident_count"] == concurrency]
    return statistics.median(r[field] for r in rows if r[field] is not None)


def baseline_table(data):
    lines = ["| C=N | 有效轮数 | 成功吞吐 ops/s，中位数 [最小, 最大] | P95 / P99 ms | 失败/请求 |",
             "|---:|---:|---:|---:|---:|"]
    for c in (1, 8, 32, 64):
        r = get(data, "exec_true", c, c)
        lines.append(f"| {c} | {r['run_count']} | {throughput(r)} | {r['p95_ms']:.2f} / {r['p99_ms']:.2f} | {r['observed_failure_count']}/{r['observed_dispatch_count']:,} |")
    return lines


def file_table(data):
    lines = ["| 文件场景 | C=N=1 ops/s | C=N=8 ops/s | C=N=32 ops/s |",
             "|---|---:|---:|---:|"]
    for scenario in ("file_read_1k", "file_read_1m", "file_write_1k"):
        rates = [get(data, scenario, c, c)["successful_throughput_ops_s"] for c in (1, 8, 32)]
        lines.append(f"| {scenario} | {rates[0]:.2f} | {rates[1]:.2f} | {rates[2]:.2f} |")
    return lines


def common_notes(data):
    return ["", "口径：吞吐按显式测量窗内成功完成数计算；时延按窗内发出的成功请求计算，保留排空期间完成的样本。图表为逐轮统计的中位数与实际最小—最大范围，不是置信区间；失败计数另列。C=N 主曲线同时改变并发与驻留量。数据面窗口为 20–30 秒，尚未冻结业务 SLO，也未完成长稳验证。", "",
            "时钟补充：事后核验发现VM UTC与host/client UTC存在约1秒级对齐不确定性。本文VM窗口资源均值保留未平移选择，只能作近似归属；客户端单调计时和host/发生器同机CPU不受这一跨机问题影响。[事后敏感性检查](../phase0-environment/CLOCK-ALIGNMENT.md)只是假设性窗口平移，不构成历史时钟校准或精确跨层归因。", "",
            f"文件读取使用重复字节。独立[传输诊断]({PROBE})已确认：1 MiB 逻辑内容对应默认 gzip HTTP 实体仅 1,055 字节；SDK 可自动解压并移除相关响应头。因此大文件结果代表逻辑 payload/解压工作负载，不能作为网络 MiB/s。HTTPX 与 SDK/pyqwest 的客户端传输成本也不同。文件写入仅在窗口结束后核对每个 worker 的最终内容，9 个写入 run 的此项检查均通过；这不等于逐次写入的独立内容校验。", "",
            "资源指标按实际采样间隔加权，host 与 VM 分开；边界采样不按比例切分，缺失值不补零。原始文件保存在外部数据目录，报告只读取 [INPUTS-FROZEN.json](results/main-matrix/INPUTS-FROZEN.json) 列出的主矩阵文件。补充对照独立归档，其结果不进入本页主矩阵统计。", "",
            "完整数据：[逐轮统计](results/main-matrix/per_run.csv)、[聚合统计](results/main-matrix/summary.csv)、[run/失败 setup](results/main-matrix/runs.csv)、[错误样本](results/main-matrix/errors.json)、[资源按 run 对齐](results/main-matrix/resource_by_run.csv)、[资源报告](results/main-matrix/resources/RESOURCES.md)、[质量检查](results/main-matrix/data_quality.json)。"]


def service_report(data):
    count = sum(r["observed_dispatch_count"] for r in data["per_run"])
    lines = ["# Phase 1 主矩阵结果", "",
             f"本页冻结了 **65 个有效性能 run、3 次 setup 失败**。测量阶段 {count:,} 个操作未记录客户端失败，全部 run 的归属清理成功。N=256/C=8 仅有 **2 轮有效测量**：5 次建池尝试中 2 次进入测量、3 次失败，r3 没有性能数据。失败 setup 的错误窗口已排除，未绘成 0 ops/s。", "",
             "`exec_true` 经 HTTPX 的结果如下；每行除特别标注外均为三轮。"] + [""] + baseline_table(data)
    lines += ["", f"[命令曲线]({graph(data, 'coupled-population-concurrency', 'exec_true')})显示 C=N=8 之后吞吐下降、长尾增大。C=N=32 时发生器 CPU 的逐轮均值中位数为 {median_resource(data,32,'generator_cpu_mean_cores'):.2f} 核，VM 为 {median_resource(data,32,'vm_cpu_mean_cores'):.2f}/192 核；C=N=64 时 VM 为 {median_resource(data,64,'vm_cpu_mean_cores'):.2f}/192 核。该证据支持优先检查客户端传输、连接管理和串行路径，具体归因仍需补充控制实验。", "",
              "固定 C=8 的驻留观察：", "", "| N | 有效轮数 | 成功 ops/s，中位数 [最小, 最大] | P95 ms |", "|---:|---:|---:|---:|"]
    for n in (8,64,256):
        r=get(data,"exec_true",8,n)
        lines.append(f"| {n} | {r['run_count']} | {throughput(r)} | {r['p95_ms']:.2f} |")
    lines += ["", f"[固定并发驻留曲线]({graph(data,'residents','exec_true')})明确标注每点轮数。三次失败建池的采样峰值分别为 163、162、217 个 Firecracker 进程，VM 不可用内存峰值约 23.7、24.4、27.2 GiB；这些是采样观察，不能当作精确失败瞬间或满内存容量。", "",
              "生命周期采用 create→delete 循环，当前服务启动准入仍为 3。C 是循环 worker 数，不是驻留实例数；吞吐包括删除节奏，延迟仅计 create 调用。", "",
              "| 循环 C | 有效轮数 | 成功循环吞吐 ops/s | create P95 ms |", "|---:|---:|---:|---:|"]
    for c in (1,2,3,8):
        r=get(data,"create_delete_churn",c)
        lines.append(f"| {c} | {r['run_count']} | {r['successful_throughput_ops_s']:.2f} | {r['p95_ms']:.2f} |")
    lines += ["", "到达率实验是有限批次，计时包含排空；0 个服务错误不能掩盖发生器丢弃：", "",
              "| 配置 λ /s | 计划 / 实发（三轮合计） | 未发出 | 成功 ops/s 中位数 | create P95 ms |", "|---:|---:|---:|---:|---:|"]
    for rate in (2,8,20):
        r=get(data,"create_delete_churn",16,None,"open_loop",float(rate))
        selected=[run for run in data['runs'] if run['scenario']=='create_delete_churn' and run['mode']=='open_loop' and run['arrival_rate_ops_s']==rate]
        scheduled=sum(run['scheduled_count'] for run in selected);dispatched=sum(run['observed_dispatch_count'] for run in selected)
        lines.append(f"| {rate} | {scheduled}/{dispatched} | {scheduled-dispatched} ({100*(scheduled-dispatched)/scheduled:.1f}%) | {r['successful_throughput_ops_s']:.2f} | {r['p95_ms']:.2f} |")
    lines += ["", f"[到达率与未发出比例]({graph(data,'arrival-rate','create_delete_churn')})分别使用计划数和实发数作分母。每轮生命周期样本较少，P99 仅供描述，不能证明稳定尾延迟。", ""] + file_table(data)
    cov=[r['coverage_pct'] for r in data['coverage'] if r['run_id']]
    lines += ["",f"服务资源测量覆盖率中位数为 {statistics.median(cov):.1f}%，范围 {min(cov):.1f}–{max(cov):.1f}%；短批次排除边界后覆盖较低。三个 setup 失败只保留整段资源作为诊断证据，不进入测量窗资源聚合。"]
    return "\n".join(lines+common_notes(data))+"\n"


def sdk_report(data):
    count=sum(r['observed_dispatch_count'] for r in data['per_run']);failed=sum(r['observed_failure_count'] for r in data['per_run'])
    c64=get(data,'exec_true',64,64)
    lines=["# Phase 2 主矩阵结果","",f"主矩阵包含 **39 个有效 run、13 个配置，每配置 3 轮**；共 {count:,} 个测量请求、{failed} 个失败，全部归属清理成功。19 个失败均发生在 `exec_true` C=N=64。本页保留这些失败，不把高吞吐等同于满足可靠性或业务 SLO。",""]+baseline_table(data)
    lines += ["",f"[命令吞吐、长尾及失效率]({graph(data,'coupled-population-concurrency','exec_true')})显示：C=N=32 到 64 时，吞吐中位数从 2,025.25 变为 1,956.60 ops/s，P95 从 27.97 增至 70.03 ms。C64 合并失效率为 {c64['observed_failure_count']}/{c64['observed_dispatch_count']:,} = {100*c64['observed_failure_count']/c64['observed_dispatch_count']:.5f}%；图中则使用逐轮失效率的中位数与最小—最大范围。", "",
              "**错误集中于每个 worker 的第一条测量调用。** 按 start_offset_s 排序识别首条，不按文件行顺序；setup 调用可以更早发生，不能把此分类叫作第一次 SDK 操作。原始 warmup 只覆盖 slot 0。", "",
              "| 重复 | 每 worker 首条测量调用：失败/请求 | 后续测量调用：失败/请求 |", "|---:|---:|---:|"]
    cohorts=read(data['folder']/'first_call_per_run.json')
    for repeat in (1,2,3):
        first=next(r for r in cohorts if r['repeat']==repeat and r['cohort']=='first_measured_per_worker')
        later=next(r for r in cohorts if r['repeat']==repeat and r['cohort']=='later_measured_calls')
        lines.append(f"| {repeat} | {first['failures']}/{first['calls']} ({first['failure_rate_pct']:.3f}%) | {later['failures']}/{later['calls']:,} ({later['failure_rate_pct']:.6f}%) |")
    first_plot=read(data['folder']/'first_call_plot_index.json')[0]['png']
    errors=read(data['folder']/'errors.json');timeout=next(r for r in errors if r['error_class']=='TimeoutException')
    lines += ["",f"合计首条为 **18/192 = 9.375%**，后续为 **1/171,318 = 0.000584%**。[分组图](results/main-matrix/{first_plot})与[分组原表](results/main-matrix/first_call_per_run.csv)保留该差异；这描述了位置关联，尚不能判定网络或服务根因。", "",
              f"错误分类为 18 个 ConnectError、1 个 TimeoutException；RPC code 为 16 个 unavailable、3 个未记录。TimeoutException 在原始记录中属于 error，耗时约 {timeout['duration_ms']:.1f} ms；其名称不能据此解释为 1,800 秒 sandbox TTL 到期。完整错误样本及所有分母均保留。", "",
              f"C=N=32 时发生器 CPU 逐轮均值的中位数为 {median_resource(data,32,'generator_cpu_mean_cores'):.2f} 核，VM 为 {median_resource(data,32,'vm_cpu_mean_cores'):.2f}/192 核；C=N=64 时分别为 {median_resource(data,64,'generator_cpu_mean_cores'):.2f} 和 {median_resource(data,64,'vm_cpu_mean_cores'):.2f}/192 核。资源总量有余量不排除局部串行瓶颈；完整预热、多个发生器和记录开销对照独立归档。", ""]+file_table(data)
    cov=[r['coverage_pct'] for r in data['coverage'] if r['run_id']]
    lines += ["",f"[1 MiB 文件曲线]({graph(data,'coupled-population-concurrency','file_read_1m')})需按下述压缩口径解读。SDK 资源测量覆盖率中位数为 {statistics.median(cov):.1f}%，范围 {min(cov):.1f}–{max(cov):.1f}%；未发现采样缺口或时钟跳变。"]
    return "\n".join(lines+common_notes(data))+"\n"


def provenance(data):
    folder=data['folder'];manifest=data['INPUTS-FROZEN']
    observed={r['path']:r['sha256'] for r in read(folder/'inputs.json')['files']}
    observed.update({r['path']:r['sha256'] for r in read(folder/'resources/resource_inputs.json')['files']})
    for entry in manifest['inputs']:
        for kind in ('operations','resources'):
            assert observed.get(entry[kind]['path'])==entry[kind]['sha256'], entry[kind]['path']
    code=list((ROOT/'analysis').glob('*.py'))+[ROOT/'phase1-service/code/executed/runner-20260920-v1.py',ROOT/'phase1-service/code/runner.py']
    code_hashes=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(code) if p.is_file()]
    save_json(folder/'ANALYSIS-PROVENANCE.json',{
        'generated_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,
        'input_manifest_sha256':hashlib.sha256((folder/'INPUTS-FROZEN.json').read_bytes()).hexdigest(),
        'all_input_hashes_match_frozen_manifest':True,'analysis_cpu_affinity':'16-31',
        'scope':'main matrix only; supplementary experiments excluded',
        'code':code_hashes,'runner_note':'v1 preserved; continuation runner adds setup reconciliation and error detail; source identities retained',
        'aggregate_definition':'median of per-run statistics; run min/max; failure counts retained',
        'resource_definition':'own-layer interval weighting; whole intervals only; failed setup measurement windows excluded'})


def main():
    service=load_phase('phase1-service');sdk=load_phase('phase2-sdk')
    for data,report in ((service,service_report(service)),(sdk,sdk_report(sdk))):
        provenance(data)
        path=ROOT/data['INPUTS-FROZEN']['phase']/'MAIN-RESULTS.md'
        path.write_text(report)
        print(path)


if __name__=='__main__':
    main()
