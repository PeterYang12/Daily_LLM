#!/usr/bin/env python3
"""Describe earliest-measured-per-worker versus later calls from actual JSONL."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

from summarize import ContractError, discover, read_records, save_json, write_csv


def cohorts(operations):
    by_run = defaultdict(list)
    for row in operations:
        if row.get("window") == "measurement" and row.get("worker_id") is not None:
            by_run[(row["run_id"], row["operation"])].append(row)
    output = []
    for (run_id, operation), rows in sorted(by_run.items()):
        first = {}
        for row in rows:
            worker = row["worker_id"]
            identity = (row["start_offset_s"], row["operation_id"])
            if worker not in first or identity < (first[worker]["start_offset_s"], first[worker]["operation_id"]):
                first[worker] = row
        first_ids = {row["operation_id"] for row in first.values()}
        for label, selected in (("first_measured_per_worker", list(first.values())),
                                ("later_measured_calls", [r for r in rows if r["operation_id"] not in first_ids])):
            failed = [r for r in selected if not r["_successful"]]
            out = {k: rows[0].get(k) for k in ("phase", "scenario", "template", "concurrency", "resident_count", "repeat", "instrumentation", "comparison_key")}
            out.update(run_id=run_id, operation=operation, cohort=label, calls=len(selected),
                       failures=len(failed), failure_rate_pct=100 * len(failed) / len(selected) if selected else None,
                       error_classes=dict(Counter(r.get("error_class", r["status"]) for r in failed)),
                       rpc_codes=dict(Counter(r.get("rpc_code") or "unspecified" for r in failed)),
                       first_definition="earliest measurement start_offset_s per worker; setup operations can precede it")
            output.append(out)
    return output


def aggregate(rows):
    grouped = defaultdict(list)
    fields = ("phase", "scenario", "operation", "template", "concurrency", "resident_count", "instrumentation", "comparison_key", "cohort")
    for row in rows:
        grouped[tuple(row.get(k) for k in fields)].append(row)
    result = []
    for key, group in sorted(grouped.items(), key=lambda item: repr(item[0])):
        calls = sum(r["calls"] for r in group)
        failures = sum(r["failures"] for r in group)
        result.append(dict(zip(fields, key), calls=calls, failures=failures,
                           failure_rate_pct=100 * failures / calls if calls else None,
                           run_count=len(group), run_ids=[r["run_id"] for r in group],
                           statistic="pooled failure counts; per-run results provided separately"))
    return result


def plots(rows, summary, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none", "savefig.dpi": 200,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.2})
    groups = defaultdict(list)
    for row in rows:
        key = tuple(row.get(k) for k in ("phase", "scenario", "operation", "concurrency", "resident_count", "instrumentation"))
        groups[key].append(row)
    index = []
    for key, group in groups.items():
        fig, axes = plt.subplots(1, 2, figsize=(10, 4.6))
        for axis, label, title, color in ((axes[0], "first_measured_per_worker", "First measured call per worker", "#D55E00"),
                                         (axes[1], "later_measured_calls", "Later measured calls", "#0072B2")):
            selected = sorted((r for r in group if r["cohort"] == label), key=lambda r: str(r["repeat"]))
            bars = axis.bar([f"Repeat {r['repeat']}" for r in selected], [r["failure_rate_pct"] or 0 for r in selected], color=color)
            maximum = max((r["failure_rate_pct"] or 0 for r in selected), default=0)
            axis.set_ylim(0, max(0.002, maximum * 1.35) if maximum else 1)
            axis.set_title(title)
            axis.set_ylabel("Failure rate (%)")
            for bar, row in zip(bars, selected):
                axis.annotate(f"{row['failures']}/{row['calls']:,}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                              xytext=(0, 5), textcoords="offset points", ha="center", fontsize=9)
        phase, scenario, operation, concurrency, residents, instrumentation = key
        scenario_label = "64 slots exercised at warmup C=8" if scenario == "exec_true_all_connections_warm" else scenario
        fig.suptitle(f"{phase} | {scenario_label} | measured C={concurrency}, N={residents}", fontsize=12)
        fig.text(0.01, 0.015, "Panels use different vertical scales; labels show failure/call counts. Each worker contributes one first-measured call per run.\nThe first-measured definition follows timestamps; setup calls can precede it. This descriptive association does not identify a root cause.", fontsize=8)
        fig.tight_layout(rect=(0, 0.11, 1, 0.91))
        name = "first-call-cohorts-" + hashlib.sha256(repr(key).encode()).hexdigest()[:12]
        folder = output / "plots"
        folder.mkdir(exist_ok=True)
        for extension in ("png", "svg"):
            fig.savefig(folder / f"{name}.{extension}", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        index.append({"phase": phase, "scenario": scenario, "concurrency": concurrency,
                      "png": f"plots/{name}.png", "svg": f"plots/{name}.svg"})
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        files = discover(args.inputs)
        _, operations, issues = read_records(files)
        rows = cohorts(operations)
        if not rows:
            raise ContractError("No measurement records with worker IDs")
        summary = aggregate(rows)
        args.output.mkdir(parents=True, exist_ok=True)
        for name, records in (("first_call_per_run", rows), ("first_call_summary", summary)):
            write_csv(args.output / f"{name}.csv", records)
            save_json(args.output / f"{name}.json", records)
        save_json(args.output / "first_call_quality.json", issues)
        save_json(args.output / "first_call_plot_index.json", plots(rows, summary, args.output))
        save_json(args.output / "first_call_inputs.json", [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files])
        print(json.dumps({"output": str(args.output), "rows": len(rows), "summary": summary}))
        return 0
    except (ContractError, OSError) as exc:
        print(f"first-call analysis error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
