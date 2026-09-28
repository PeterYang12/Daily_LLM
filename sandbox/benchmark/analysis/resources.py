#!/usr/bin/env python3
"""Read-only resource reporting with separate host/VM time domains and boundaries."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

from summarize import ContractError, discover, finite, measurement_eligibility, save_json, slug, write_csv


def utc_seconds(value):
    if not isinstance(value, str):
        raise ContractError("UTC timestamp must be an ISO string with a timezone")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"Invalid UTC timestamp: {value}") from exc
    if timestamp.tzinfo is None:
        raise ContractError("Naive timestamps cannot align resource and measurement windows")
    return timestamp.timestamp()


def iso_utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def load_windows(files):
    windows = {}
    issues = []
    for path in files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"{path}:{line_number}: malformed JSON") from exc
            if record.get("record_type") != "run":
                continue
            run_id = record.get("run_id")
            if not run_id:
                raise ContractError(f"{path}:{line_number}: run record lacks run_id")
            if run_id in windows:
                raise ContractError(f"Duplicate resource window for run {run_id}")
            eligible, reason = measurement_eligibility(record)
            if not eligible:
                issues.append({"code": "run_window_excluded", "run_id": run_id, "reason": reason,
                               "stop_reason": record.get("stop_reason"),
                               "message": "Failed/unstarted measurement excluded from per-run resource aggregates; capture remains available as setup diagnostic evidence"})
                continue
            if record.get("measurement_start_utc") and record.get("measurement_end_utc"):
                start = utc_seconds(record["measurement_start_utc"])
                end = utc_seconds(record["measurement_end_utc"])
            elif record.get("run_started_utc") and record.get("measurement_start_offset_s") is not None and record.get("measurement_end_offset_s") is not None:
                origin = utc_seconds(record["run_started_utc"])
                start = origin + finite(record["measurement_start_offset_s"], "measurement_start_offset_s", str(path))
                end = origin + finite(record["measurement_end_offset_s"], "measurement_end_offset_s", str(path))
            else:
                issues.append({"code": "run_window_unavailable", "run_id": run_id, "message": "No UTC measurement window; resources cannot be attributed to this run"})
                continue
            if end <= start:
                raise ContractError(f"Nonpositive resource measurement window for {run_id}")
            windows[run_id] = {"scope_id": f"run:{run_id}", "run_id": run_id, "phase": record.get("phase"), "start": start, "end": end}
    return list(windows.values()), issues


def read_samples(files):
    samples, issues, inputs = [], [], []
    for path in files:
        content = path.read_bytes()
        capture_id = hashlib.sha256(content).hexdigest()[:16]
        inputs.append({"path": str(path), "capture_id": capture_id, "sha256": hashlib.sha256(content).hexdigest()})
        last = {}
        saw_metadata = saw_completion = False
        for number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"{path}:{number}: malformed resource JSON") from exc
            if not isinstance(record, dict):
                raise ContractError(f"{path}:{number}: resource record must be an object")
            kind = record.get("record_type")
            saw_metadata |= kind == "metadata"
            saw_completion |= kind == "completion"
            if kind != "sample":
                continue
            for layer in ("guest", "host"):
                data = record.get(layer)
                if not isinstance(data, dict):
                    issues.append({"code": "missing_layer_sample", "capture_id": capture_id, "layer": layer, "line": number})
                    continue
                location = f"{path}:{number}:{layer}"
                interval = finite(data.get("interval_seconds"), "interval_seconds", location)
                end_mono = finite(data.get("monotonic_seconds"), "monotonic_seconds", location)
                if interval <= 0:
                    raise ContractError(f"{location}: interval_seconds must be positive")
                end = utc_seconds(data.get("utc"))
                start = end - interval
                if layer in last:
                    previous = last[layer]
                    mono_gap = end_mono - interval - previous["mono"]
                    if mono_gap < -1e-5:
                        raise ContractError(f"{location}: overlapping or reversed intervals in one layer's monotonic domain")
                    if mono_gap > 0.05:
                        issues.append({"code": "sampling_gap", "capture_id": capture_id, "layer": layer, "gap_seconds": mono_gap, "line": number})
                    wall_delta = end - previous["utc"]
                    mono_delta = end_mono - previous["mono"]
                    if abs(wall_delta - mono_delta) > 0.25:
                        issues.append({"code": "utc_clock_discontinuity", "capture_id": capture_id, "layer": layer, "difference_seconds": wall_delta - mono_delta, "line": number})
                last[layer] = {"mono": end_mono, "utc": end}
                samples.append({"capture_id": capture_id, "source_file": str(path), "layer": layer,
                                "sample_index": number, "start": start, "end": end,
                                "interval_seconds": interval, "monotonic_end_s": end_mono,
                                "data": data})
        if not saw_metadata or not saw_completion:
            issues.append({"code": "incomplete_capture_markers", "capture_id": capture_id,
                           "has_metadata": saw_metadata, "has_completion": saw_completion})
    if not samples:
        raise ContractError("No host/guest resource samples found; no resource observations inferred")
    if len({i["capture_id"] for i in inputs}) != len(inputs):
        raise ContractError("Identical resource capture supplied more than once; would double-count intervals")
    return samples, issues, inputs


def metric_rows(sample, scope_id):
    """kind: rate (time-weighted), gauge (sample stats), delta (sum), io_mean."""
    data = sample["data"]
    base = {k: sample[k] for k in ("capture_id", "source_file", "layer", "sample_index", "interval_seconds", "monotonic_end_s")}
    base.update(scope_id=scope_id, sample_utc=iso_utc(sample["end"]), interval_start_utc=iso_utc(sample["start"]),
                interval_end_utc=iso_utc(sample["end"]), end_epoch_s=sample["end"])

    def emit(entity, metric, value, unit, kind="gauge", weight=None):
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ContractError(f"Nonfinite resource value: {sample['source_file']} {sample['layer']} {entity}.{metric}")
        yield dict(base, entity=entity, metric=metric, value=value, unit=unit, kind=kind,
                   weight=sample["interval_seconds"] if weight is None else weight)

    for name, unit in (("busy_pct", "%"), ("busy_core_equivalents", "cores"), ("max_core_busy_pct", "%"), ("iowait_pct", "%"), ("steal_pct", "%")):
        yield from emit("cpu", name, data.get("cpu", {}).get(name), unit, "rate")
    yield from emit("cpu", "logical_cpus", data.get("cpu", {}).get("logical_cpus"), "count")
    memory = data.get("memory_bytes", {})
    for name, value in memory.items():
        yield from emit("memory", name, value, "bytes")
    if memory.get("MemTotal") is not None and memory.get("MemAvailable") is not None:
        yield from emit("memory", "unavailable_bytes", memory["MemTotal"] - memory["MemAvailable"], "bytes")
    for resource, states in (data.get("pressure") or {}).items():
        for state, values in (states or {}).items():
            yield from emit(f"psi:{resource}:{state}", "avg10", values.get("avg10"), "%")
    for device, values in data.get("disks", {}).items():
        for name, unit in (("read_bytes_s", "bytes/s"), ("write_bytes_s", "bytes/s"), ("iops", "operations/s"), ("busy_pct", "%"), ("avg_queue", "count")):
            yield from emit(f"disk:{device}", name, values.get(name), unit, "rate")
        yield from emit(f"disk:{device}", "await_ms", values.get("await_ms"), "ms", "io_mean",
                        weight=values.get("iops", 0) * sample["interval_seconds"])
        yield from emit(f"disk:{device}", "in_flight", values.get("in_flight"), "count")
    for interface, values in data.get("network", {}).items():
        for name in ("rx_bytes_s", "tx_bytes_s"):
            yield from emit(f"network:{interface}", name, values.get(name), "bytes/s", "rate")
        for name in ("rx_errors", "rx_drops", "tx_errors", "tx_drops"):
            yield from emit(f"network:{interface}", name, values.get(name), "count", "delta")
    for name in ("fd_allocated", "fd_unused", "fd_limit", "nbd_devices", "nbd_attached", "collection_ms", "sampler_schedule_lag_ms"):
        yield from emit("system", name, data.get(name), "ms" if name.endswith("ms") else "count")
    cgroups = data.get("cgroup_summary", {})
    for name in ("count", "populated", "memory_bytes", "new_cgroups", "exited_cgroups", "cpu_cores_continuous_only", "throttled_seconds_continuous_only"):
        kind = "rate" if name == "cpu_cores_continuous_only" else "delta" if name in {"new_cgroups", "exited_cgroups", "throttled_seconds_continuous_only"} else "gauge"
        unit = "bytes" if name == "memory_bytes" else "cores" if name.startswith("cpu_cores") else "seconds" if name.startswith("throttled_seconds") else "count"
        yield from emit("cgroup_summary", name, cgroups.get(name), unit, kind)
    for name, count in data.get("processes", {}).get("live_process_counts", {}).items():
        yield from emit("process_count", name, count, "count")
    for name, counts in data.get("processes", {}).get("service_fd_counts", {}).items():
        if isinstance(counts, list) and counts:
            yield from emit("service_fd", name, sum(counts), "count")
    arp = data.get("arp") or {}
    for name in ("entries", "gc_thresh3", "table_fulls_delta"):
        yield from emit("arp", name, arp.get(name), "count", "delta" if name.endswith("delta") else "gauge")
    for pid, values in data.get("selected_processes", {}).items():
        if not values or not values.get("continuous_identity"):
            continue
        for name, unit, kind in (("cpu_cores", "cores", "rate"), ("rss_bytes", "bytes", "gauge"), ("read_bytes_s", "bytes/s", "rate"), ("write_bytes_s", "bytes/s", "rate")):
            yield from emit(f"process:{pid}", name, values.get(name), unit, kind)


def select_scopes(samples, windows):
    grouped = defaultdict(list)
    for sample in samples:
        grouped[(sample["capture_id"], sample["layer"])].append(sample)
    selected, coverage = [], []
    for (capture_id, layer), capture_samples in sorted(grouped.items()):
        start = min(s["start"] for s in capture_samples)
        end = max(s["end"] for s in capture_samples)
        scopes = [{"scope_id": f"capture:{capture_id}", "start": start, "end": end, "run_id": None, "phase": None}] + windows
        for scope in scopes:
            overlapping = [s for s in capture_samples if s["end"] > scope["start"] and s["start"] < scope["end"]]
            if not overlapping:
                continue
            # Do not split a measured average across a boundary: doing so assumes
            # uniform activity inside an interval that was not actually observed.
            contained = [s for s in overlapping if s["start"] >= scope["start"] - 1e-6 and s["end"] <= scope["end"] + 1e-6]
            seconds = sum(s["interval_seconds"] for s in contained)
            duration = scope["end"] - scope["start"]
            coverage.append({"scope_id": scope["scope_id"], "run_id": scope.get("run_id"), "phase": scope.get("phase"),
                             "capture_id": capture_id, "layer": layer, "scope_start_utc": iso_utc(scope["start"]),
                             "scope_end_utc": iso_utc(scope["end"]), "scope_duration_s": duration,
                             "covered_interval_s": seconds, "coverage_pct": 100 * seconds / duration,
                             "contained_samples": len(contained), "excluded_boundary_samples": len(overlapping) - len(contained)})
            for sample in contained:
                selected.extend(metric_rows(sample, scope["scope_id"]))
    return selected, coverage


def aggregate_metrics(rows):
    groups = defaultdict(list)
    fields = ("scope_id", "capture_id", "layer", "entity", "metric", "unit", "kind")
    for row in rows:
        groups[tuple(row[k] for k in fields)].append(row)
    result = []
    for key, values in sorted(groups.items()):
        record = dict(zip(fields, key))
        observations = [r["value"] for r in values]
        interval_seconds = sum(r["interval_seconds"] for r in values)
        record.update(sample_count=len(values), observed_interval_s=interval_seconds,
                      observed_min=min(observations), observed_max=max(observations), sample_median=statistics.median(observations),
                      time_weighted_mean=None, operation_weighted_mean=None, interval_total=None, delta_rate_per_second=None)
        if record["kind"] == "rate":
            record["time_weighted_mean"] = sum(r["value"] * r["interval_seconds"] for r in values) / interval_seconds
        elif record["kind"] == "io_mean":
            weight = sum(r["weight"] for r in values)
            record["io_operation_count"] = weight
            record["operation_weighted_mean"] = sum(r["value"] * r["weight"] for r in values) / weight if weight else None
        elif record["kind"] == "delta":
            record["interval_total"] = sum(observations)
            record["delta_rate_per_second"] = sum(observations) / interval_seconds
        result.append(record)
    return result


def resource_plots(rows, output, coverage=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError as exc:
        raise ContractError("Resource figures require matplotlib in the benchmark environment; otherwise use --no-plots") from exc
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none", "savefig.dpi": 200,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.2, "legend.frameon": False})
    plot_dir = output / "plots"
    plot_dir.mkdir(exist_ok=True)
    captures = defaultdict(list)
    for row in rows:
        if row["scope_id"].startswith("capture:"):
            captures[(row["capture_id"], row["layer"])].append(row)
    figures = []
    for (capture_id, layer), values in sorted(captures.items()):
        selected_processes = sorted({r["entity"] for r in values if r["entity"].startswith("process:") and r["metric"] == "cpu_cores"})
        add_process = bool(selected_processes)
        fig, all_axes = plt.subplots(6 if add_process else 5, 1, figsize=(11, 13 if add_process else 11.5), sharex=True)
        axes = [all_axes[0], *all_axes[2:]] if add_process else all_axes

        def draw(axis, entity, metric, label, scale=1):
            points = sorted((r for r in values if r["entity"] == entity and r["metric"] == metric), key=lambda r: r["end_epoch_s"])
            if not points:
                return
            # Segments reflect each observed interval; gaps remain visible.
            kind = points[0]["kind"]
            if kind in {"rate", "io_mean", "delta"}:
                color = axis._get_lines.get_next_color()
                for index, point in enumerate(points):
                    xs = [datetime.fromtimestamp(point["end_epoch_s"] - point["interval_seconds"], timezone.utc), datetime.fromtimestamp(point["end_epoch_s"], timezone.utc)]
                    axis.plot(xs, [point["value"] / scale] * 2, linewidth=1.6, color=color, label=label if index == 0 else None)
            else:
                axis.plot([datetime.fromtimestamp(r["end_epoch_s"], timezone.utc) for r in points], [r["value"] / scale for r in points], marker=".", markersize=3, linestyle="none", label=label)

        draw(axes[0], "cpu", "busy_pct", "Whole-layer busy %")
        draw(axes[0], "cpu", "max_core_busy_pct", "Busiest logical core %")
        draw(axes[0], "cpu", "steal_pct", "Steal %")
        axes[0].set_ylabel("CPU (%)")
        if add_process:
            for entity in selected_processes:
                draw(all_axes[1], entity, "cpu_cores", f"Selected PID {entity[8:]}")
            all_axes[1].set_ylabel("Process CPU (cores)")
        draw(axes[1], "memory", "unavailable_bytes", "MemTotal − MemAvailable", 2 ** 30)
        if layer == "guest":
            draw(axes[1], "cgroup_summary", "memory_bytes", "Sandbox cgroup memory", 2 ** 30)
        axes[1].set_ylabel("Memory (GiB)")
        for entity in sorted({r["entity"] for r in values if r["entity"].startswith("disk:") and not r["entity"].startswith("disk:nbd")}):
            draw(axes[2], entity, "read_bytes_s", f"{entity[5:]} read", 2 ** 20)
            draw(axes[2], entity, "write_bytes_s", f"{entity[5:]} write", 2 ** 20)
        axes[2].set_ylabel("Disk (MiB/s)")
        for entity in sorted({r["entity"] for r in values if r["entity"].startswith("network:")}):
            draw(axes[3], entity, "rx_bytes_s", f"{entity[8:]} receive", 2 ** 20)
            draw(axes[3], entity, "tx_bytes_s", f"{entity[8:]} send", 2 ** 20)
        axes[3].set_ylabel("Network (MiB/s)")
        if layer == "guest":
            draw(axes[4], "process_count", "firecracker", "Live Firecracker processes")
            draw(axes[4], "system", "nbd_attached", "Attached NBD devices")
            draw(axes[4], "arp", "entries", "ARP entries")
        else:
            draw(axes[4], "system", "fd_allocated", "Allocated file handles")
        axes[4].set_ylabel("Count")
        axes[4].set_xlabel("Sampled UTC time (cross-machine alignment requires synchronized clocks)")
        axes[4].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=timezone.utc))
        for axis in all_axes:
            maximum = max((float(y) for line in axis.lines for y in line.get_ydata()), default=1)
            axis.set_ylim(0, max(1, maximum * 1.12))
            if axis.lines:
                axis.legend(loc="upper left", fontsize=8, ncol=3)
            for boundary in coverage or []:
                if boundary["capture_id"] == capture_id and boundary["layer"] == layer and boundary["scope_id"].startswith("run:"):
                    left = datetime.fromisoformat(boundary["scope_start_utc"])
                    right = datetime.fromisoformat(boundary["scope_end_utc"])
                    axis.axvspan(left, right, color="#555555", alpha=0.055, linewidth=0)
                    axis.axvline(left, color="#555555", alpha=0.4, linestyle="--", linewidth=0.7)
                    axis.axvline(right, color="#555555", alpha=0.4, linestyle="--", linewidth=0.7)
        label = "E2B outer VM" if layer == "guest" else "Physical host (includes other host activity)"
        fig.suptitle(f"{label} | capture {capture_id}", fontsize=13)
        fig.text(0.01, 0.012, "Rates are measured interval segments; gauges are sampled points. Shading marks nominal measurement windows when available.\nNo gaps are filled and no host/VM resources are added. Devices remain separate; detailed metrics and boundary coverage are in the accompanying tables.", fontsize=8)
        fig.tight_layout(rect=(0, 0.055, 1, 0.965))
        stem = f"resources-{slug(layer)}-{capture_id}"
        for extension in ("png", "svg"):
            fig.savefig(plot_dir / f"{stem}.{extension}", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        figures.append({"capture_id": capture_id, "layer": layer, "png": f"plots/{stem}.png", "svg": f"plots/{stem}.svg"})
    return figures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Finalized sampler JSONL files")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--windows-jsonl", action="append", default=[], help="Optional operation/run JSONL with UTC measurement windows; repeat this option")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--plot-capture-path", action="append", default=[], help="Restrict figures to these exact raw sampler paths; all captures still enter summaries")
    parser.add_argument("--no-samples-csv", action="store_true", help="Keep summaries/coverage/figures without duplicating the full sampled metric table")
    args = parser.parse_args(argv)
    try:
        files = discover(args.inputs)
        samples, issues, inputs = read_samples(files)
        windows, window_issues = load_windows(discover(args.windows_jsonl)) if args.windows_jsonl else ([], [])
        issues.extend(window_issues)
        if not windows:
            issues.append({"code": "whole_capture_only", "message": "No attributable UTC run windows; metrics describe whole captures only"})
        rows, coverage = select_scopes(samples, windows)
        summary = aggregate_metrics(rows)
        args.output.mkdir(parents=True, exist_ok=True)
        for name, records in (("resource_summary", summary), ("resource_coverage", coverage)):
            write_csv(args.output / f"{name}.csv", records)
            save_json(args.output / f"{name}.json", records)
        if not args.no_samples_csv:
            write_csv(args.output / "resource_samples.csv", rows)
        save_json(args.output / "resource_quality.json", issues)
        save_json(args.output / "resource_inputs.json", {"files": inputs, "window_files": args.windows_jsonl})
        figure_rows = rows
        if args.plot_capture_path:
            selected_paths = {str(Path(path).resolve()) for path in args.plot_capture_path}
            known_paths = {row["source_file"] for row in rows}
            if not selected_paths <= known_paths:
                raise ContractError("Requested representative plot path is not among the parsed captures")
            figure_rows = [row for row in rows if row["source_file"] in selected_paths]
        figures = [] if args.no_plots else resource_plots(figure_rows, args.output, coverage)
        save_json(args.output / "resource_plot_index.json", figures)
        lines = ["# Resource observations", "",
                 "Host and VM measurements are separate. Host totals include other activity; they are not costs attributable solely to E2B.", "",
                 "Rate means are weighted by each layer's actual monotonic sample interval. Gauges report sampled min/max/median. Disk await is weighted by completed I/O count, not time. Delta counters are summed only across observed intervals. Missing observations are not zero-filled.", "",
                 "Run attribution uses UTC windows and requires synchronized clocks. Only sample intervals entirely inside a run window are retained; boundary intervals are excluded, with coverage reported. No rate is split into an unobserved subinterval. Capture summaries may include warmup, setup and cleanup.", "",
                 "For the 2026-09-20 campaign, post-experiment checks found approximately one-second uncertainty in guest/host UTC alignment. Existing VM window summaries remain unshifted and support approximate attribution only. Client monotonic metrics and host/generator CPU use their own common clock domain. See [clock alignment and hypothetical sensitivity](/home/yuhanya/Daily_LLM/sandbox/benchmark/phase0-environment/CLOCK-ALIGNMENT.md); no historical clock correction is asserted.", "",
                 "See `resource_summary.csv/json`, `resource_coverage.csv/json`, and `resource_quality.json`. "
                 + ("The full sampled metric table is in `resource_samples.csv`. " if not args.no_samples_csv else "Raw sampler files remain external; the full sampled metric table was not duplicated here. ")
                 + "Cgroup CPU/throttle measurements cover continuously observed cgroups; omitted new/exited intervals do not establish zero use. NBD per-device coverage may include only active observations.", ""]
        for figure in figures:
            lines.append(f"- {figure['layer']} / {figure['capture_id']}: [PNG]({figure['png']}) · [SVG]({figure['svg']})")
        (args.output / "RESOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output.resolve()), "resource_samples": len(samples), "metric_groups": len(summary), "figures": len(figures), "quality_findings": len(issues)}))
        return 0
    except (ContractError, OSError) as exc:
        print(f"resource analysis error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
