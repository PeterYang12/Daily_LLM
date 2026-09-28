#!/usr/bin/env python3
"""Aggregate measured benchmark JSONL. No requests, workload execution, or inference."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from collections import Counter, defaultdict


CONFIG = (
    "phase", "scenario", "operation", "template", "memory_mib", "resident_count",
    "concurrency", "mode", "instrumentation", "measurement_kind", "comparison_key",
    "arrival_rate_ops_s",
    "payload_bytes", "page_mode", "throughput_scope",
)
REQUIRED = ("phase", "scenario", "template", "memory_mib", "concurrency", "mode", "instrumentation", "repeat")
METRICS = (
    "successful_throughput_ops_s", "completed_throughput_ops_s", "dispatch_rate_ops_s",
    "offered_rate_ops_s",
    "failure_rate_pct", "p50_ms", "p95_ms", "p99_ms", "planned_p50_ms",
    "planned_p95_ms", "planned_p99_ms", "dispatch_lag_p95_ms", "first_output_p95_ms",
    "logical_payload_mib_s",
    "undispatched_rate_pct",
)
SUCCESS = {"success", "ok", "passed"}
TERMINAL = SUCCESS | {"error", "failure", "failed", "timeout", "rejected", "cancelled", "canceled", "invalid"}


class ContractError(ValueError):
    pass


def finite(value, name, location, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{location}: {name} must be a finite number")
    if nonnegative and value < 0:
        raise ContractError(f"{location}: {name} must be nonnegative")
    return float(value)


def quantile(values, probability):
    """Linear interpolation, identical to the common R-7/NumPy default definition."""
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    low = math.floor(index)
    high = math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def failure_axis_upper(rows):
    maximum = max((max(row.get("failure_rate_pct") or 0, row.get("failure_rate_pct_max") or 0,
                       row.get("undispatched_rate_pct") or 0, row.get("undispatched_rate_pct_max") or 0)
                   for row in rows), default=0)
    return max(0.01, 1.3 * maximum) if maximum > 0 else 1


def measurement_eligibility(record, observed_measurement_operations=None):
    """Do not interpret a failed setup's configured-duration placeholder as data."""
    if record.get("measurement_started") is False or record.get("measurement_valid") is False:
        return False, "producer_explicitly_marked_measurement_unstarted_or_invalid"
    count = observed_measurement_operations
    if count is None:
        count = record.get("dispatched_count")
    reason = record.get("stop_reason")
    completed = {None, "", "completed", "complete", "success", "finished"}
    if count == 0 and reason not in completed:
        return False, "failed_before_any_measurement_operation"
    return True, None


def key_for(record, fields=CONFIG):
    return tuple(record.get(field) for field in fields)


def issue(issues, code, message, *, run_id=None, severity="warning"):
    issues.append({"severity": severity, "code": code, "run_id": run_id, "message": message})


def normalize_resident_population(row):
    if row.get("scenario") == "create_delete_churn":
        row["raw_resident_count"] = row.get("resident_count")
        row["resident_count"] = None
        row["resident_count_applicability"] = "not_applicable"
        row["resident_count_provenance"] = (
            "create_delete_churn has no pre-created resident pool; the raw resident_count "
            "is an unused CLI default, preserved separately. Concurrency counts cycle workers."
        )
    return row


def discover(paths):
    files = set()
    for value in paths:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            files.update(path.rglob("*.jsonl"))
        elif path.is_file():
            files.add(path)
        else:
            raise ContractError(f"Input does not exist: {path}")
    if not files:
        raise ContractError("No JSONL inputs found; no measurements or plots were generated")
    return sorted(files)


def read_records(files, phases=None):
    runs, operations, issues = {}, [], []
    run_metadata = {}
    operation_ids, unknown = set(), Counter()
    operation_windows = defaultdict(set)
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                location = f"{path}:{line_number}"
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ContractError(f"{location}: malformed JSON: {exc.msg}") from exc
                if not isinstance(record, dict):
                    raise ContractError(f"{location}: each JSONL record must be an object")
                kind = record.get("record_type")
                if kind == "metadata" and record.get("run_id"):
                    run_metadata[record["run_id"]] = {k: record[k] for k in ("client_transport", "attempt_scope", "resource_sampler", "pid") if k in record}
                if kind not in {"operation", "run"}:
                    unknown[str(kind)] += 1
                    continue
                if phases and record.get("phase") and record["phase"] not in phases:
                    continue
                run_id = record.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    raise ContractError(f"{location}: missing string run_id")
                record = dict(record, _source=location)
                if kind == "run":
                    if run_id in runs:
                        raise ContractError(f"{location}: duplicate final run record {run_id}")
                    runs[run_id] = record
                else:
                    operation_id = record.get("operation_id")
                    if not isinstance(operation_id, str) or not operation_id:
                        raise ContractError(f"{location}: missing string operation_id")
                    window = record.get("window", "measurement")
                    identity = (run_id, window, operation_id)
                    if identity in operation_ids:
                        raise ContractError(f"{location}: duplicate logical operation {identity}")
                    operation_ids.add(identity)
                    operation_windows[(run_id, operation_id)].add(window)
                    operations.append(record)
    if unknown:
        issue(issues, "non_operation_records_skipped", json.dumps(dict(unknown), sort_keys=True))
    reused_by_run = Counter(run_id for (run_id, _), windows in operation_windows.items() if len(windows) > 1)
    for run_id, count in reused_by_run.items():
        issue(issues, "operation_id_reused_across_windows", f"{count} operation IDs reused across explicit windows; parser identity is (run_id, window, operation_id), raw IDs preserved", run_id=run_id)
    for run_id, final in list(runs.items()):
        runs[run_id] = dict(run_metadata.get(run_id, {}), **final)
    if not operations and not runs:
        raise ContractError("No operation records matched; no measurements or plots were generated")
    joined = []
    for operation in operations:
        run_id = operation["run_id"]
        run = runs.get(run_id, {})
        row = dict(run, **operation)
        if phases and row.get("phase") not in phases:
            continue
        for field in REQUIRED:
            if row.get(field) is None:
                raise ContractError(f"{operation['_source']}: missing {field}")
            if field in operation and field in run and operation[field] != run[field]:
                raise ContractError(f"{operation['_source']}: {field} disagrees with final run record")
        for field in ("resident_count", "measurement_kind", "comparison_key", "arrival_rate_ops_s", "payload_bytes", "page_mode", "throughput_scope"):
            if field in operation and field in run and operation[field] != run[field]:
                raise ContractError(f"{operation['_source']}: {field} disagrees with final run record")
        if not row.get("operation"):
            raise ContractError(f"{operation['_source']}: missing operation")
        for field in ("memory_mib", "concurrency"):
            finite(row[field], field, operation["_source"])
        if row.get("resident_count") is not None:
            finite(row["resident_count"], "resident_count", operation["_source"])
        if row.get("arrival_rate_ops_s") is not None:
            finite(row["arrival_rate_ops_s"], "arrival_rate_ops_s", operation["_source"])
        for field in ("start_offset_s", "end_offset_s", "duration_ms"):
            row[field] = finite(row.get(field), field, operation["_source"])
        if row["end_offset_s"] < row["start_offset_s"]:
            raise ContractError(f"{operation['_source']}: completion precedes dispatch")
        elapsed_ms = 1000 * (row["end_offset_s"] - row["start_offset_s"])
        if abs(row["duration_ms"] - elapsed_ms) > max(1.0, elapsed_ms * 0.01):
            raise ContractError(f"{operation['_source']}: duration_ms and monotonic offsets disagree")
        if row.get("status") not in TERMINAL:
            raise ContractError(f"{operation['_source']}: unknown or missing terminal status")
        row["_successful"] = row["status"] in SUCCESS and row.get("state_check") is not False
        if row.get("state_check") is not None and not isinstance(row["state_check"], bool):
            raise ContractError(f"{operation['_source']}: state_check must be boolean when present")
        if row.get("planned_offset_s") is not None:
            row["planned_offset_s"] = finite(row["planned_offset_s"], "planned_offset_s", operation["_source"])
            if row["planned_offset_s"] > row["start_offset_s"] + 1e-6:
                raise ContractError(f"{operation['_source']}: planned arrival is after dispatch")
        if row.get("first_output_ms") is not None:
            row["first_output_ms"] = finite(row["first_output_ms"], "first_output_ms", operation["_source"])
            if row["first_output_ms"] > row["duration_ms"] + 1:
                raise ContractError(f"{operation['_source']}: first output follows completion")
        row.setdefault("window", "measurement")
        row.setdefault("measurement_kind", "unspecified")
        row.setdefault("attempt_count", 1)
        attempts = finite(row["attempt_count"], "attempt_count", operation["_source"])
        if attempts < 1 or attempts != int(attempts):
            raise ContractError(f"{operation['_source']}: attempt_count must be a positive integer")
        joined.append(row)
    if not joined and not runs:
        raise ContractError("No operation records matched the phase filter")
    return runs, joined, issues


def summarize_runs(runs, operations, issues):
    by_run = defaultdict(list)
    for run_id in runs:
        by_run[run_id] = []
    for record in operations:
        by_run[record["run_id"]].append(record)
    run_rows, operation_rows = [], []
    for run_id, records in sorted(by_run.items()):
        final = runs.get(run_id)
        metadata = final or records[0]
        observed_measurement_count = sum(r["window"] == "measurement" for r in records)
        performance_eligible, exclusion_reason = measurement_eligibility(metadata, observed_measurement_count)
        start = end = seconds = None
        complete = final is not None
        if final:
            start = final.get("measurement_start_offset_s")
            end = final.get("measurement_end_offset_s")
            if not performance_eligible:
                start = end = None
                issue(issues, "failed_setup_window_excluded", f"{exclusion_reason}; reported run window retained as provenance, not a performance denominator", run_id=run_id)
            elif start is not None and end is not None:
                start = finite(start, "measurement_start_offset_s", final["_source"])
                end = finite(end, "measurement_end_offset_s", final["_source"])
                if end <= start:
                    raise ContractError(f"{final['_source']}: measurement window must be positive")
                seconds = end - start
            else:
                complete = False
                issue(issues, "missing_measurement_window", "No throughput denominator; throughput remains null", run_id=run_id)
        else:
            issue(issues, "missing_final_run", "Interrupted/unfinalized run retained; throughput remains null", run_id=run_id)
        measured = [r for r in records if r["window"] == "measurement"]
        if start is not None and end is not None:
            dispatched = [r for r in measured if start <= r["start_offset_s"] < end]
            # In steady state, a warmup-dispatched operation can complete in the
            # measurement window. Count its completion without adding its latency
            # to the measurement dispatch cohort.
            completions = [r for r in records if start <= r["end_offset_s"] <= end]
            outside = len(measured) - len(dispatched)
            if outside:
                issue(issues, "dispatch_outside_measurement", f"{outside} measurement-labelled operations dispatched outside the window", run_id=run_id)
        else:
            dispatched = measured
            completions = []
        recorded_dispatches = len(dispatched)
        labeled_dispatches = len(measured)
        dispatched_identities = {id(r) for r in dispatched}
        outside_dispatches = [r for r in measured if id(r) not in dispatched_identities]
        reported_dispatches = metadata.get("dispatched_count")
        unaccounted = None
        if reported_dispatches is not None:
            reported_dispatches = finite(reported_dispatches, "dispatched_count", metadata["_source"])
            if reported_dispatches != int(reported_dispatches):
                raise ContractError(f"{metadata['_source']}: dispatched_count must be an integer")
            unaccounted = int(reported_dispatches) - labeled_dispatches
            if unaccounted != 0:
                complete = False
                issue(issues, "dispatch_count_mismatch", f"Final dispatched_count={int(reported_dispatches)}, measurement-labelled terminal records={labeled_dispatches}; failure rates incomplete", run_id=run_id, severity="error")
        else:
            complete = False
            issue(issues, "missing_dispatch_count", "Cannot verify whether every dispatched operation has a terminal record", run_id=run_id)
        for field in ("scheduled_count", "completed_count"):
            if metadata.get(field) is not None:
                value = finite(metadata[field], field, metadata["_source"])
                if value != int(value):
                    raise ContractError(f"{metadata['_source']}: {field} must be an integer")
        if metadata.get("scheduled_count") is not None and reported_dispatches is not None and metadata["scheduled_count"] < reported_dispatches:
            complete = False
            issue(issues, "scheduled_count_mismatch", "More operations dispatched than scheduled", run_id=run_id, severity="error")
        if metadata.get("completed_count") is not None and metadata["completed_count"] != labeled_dispatches:
            complete = False
            issue(issues, "completion_count_mismatch", "Final completed_count differs from terminal measurement-cohort operation records (including drain)", run_id=run_id, severity="error")
        if metadata.get("cleanup_ok") is False:
            issue(issues, "cleanup_failed", "Run retained; cleanup failed and requires investigation", run_id=run_id, severity="error")
        if metadata.get("cleanup_ok") is None:
            issue(issues, "cleanup_unreported", "Cleanup outcome not present", run_id=run_id)
        if metadata.get("post_validation_ok") is False:
            issue(issues, "post_validation_failed", "Final state validation failed; successful response counts do not establish correct workload results", run_id=run_id, severity="error")
        if metadata.get("measurement_kind", "unspecified") == "unspecified":
            issue(issues, "measurement_kind_unspecified", "Do not interpret whole-run rates as sustainable peak capacity", run_id=run_id)
        kinds = sorted({r["operation"] for r in dispatched + completions})
        open_loop = metadata.get("mode") in {"open_loop", "open-loop"}
        run_row = {field: metadata.get(field) for field in (*REQUIRED, "resident_count", "measurement_kind", "comparison_key", "arrival_rate_ops_s")}
        run_row.update(
            run_id=run_id, operation_types=kinds, measurement_duration_s=seconds,
            measurement_start_offset_s=start, measurement_end_offset_s=end,
            reported_measurement_start_offset_s=metadata.get("measurement_start_offset_s"),
            reported_measurement_end_offset_s=metadata.get("measurement_end_offset_s"),
            performance_eligible=performance_eligible, measurement_exclusion_reason=exclusion_reason,
            observed_dispatch_count=recorded_dispatches, reported_dispatch_count=reported_dispatches,
            measurement_labeled_operation_count=labeled_dispatches,
            outside_window_dispatch_count=len(outside_dispatches),
            outside_window_failure_count=sum(not r["_successful"] for r in outside_dispatches),
            unaccounted_dispatches=unaccounted, observed_success_count=sum(r["_successful"] for r in dispatched),
            observed_failure_count=sum(not r["_successful"] for r in dispatched),
            scheduled_count=metadata.get("scheduled_count"), reported_completed_count=metadata.get("completed_count"),
            undispatched_scheduled_count=(metadata["scheduled_count"] - reported_dispatches)
                if metadata.get("scheduled_count") is not None and reported_dispatches is not None else None,
            offered_rate_ops_s=metadata["scheduled_count"] / seconds if open_loop and seconds and metadata.get("scheduled_count") is not None else None,
            dispatch_rate_ops_s=recorded_dispatches / seconds if seconds else None,
            cleanup_ok=metadata.get("cleanup_ok"), stop_reason=metadata.get("stop_reason"), data_complete=complete,
            post_validation_ok=metadata.get("post_validation_ok"), client_transport=metadata.get("client_transport"),
            attempt_scope=metadata.get("attempt_scope"), event_loop_lag_ms=metadata.get("event_loop_lag_ms"),
            error_class=metadata.get("error_class"), error_message=metadata.get("error_message"),
        )
        normalize_resident_population(run_row)
        if run_row.get("resident_count_applicability") == "not_applicable":
            issue(issues, "resident_count_not_applicable", run_row["resident_count_provenance"], run_id=run_id)
        run_rows.append(run_row)
        if not performance_eligible:
            continue
        for operation in kinds:
            cohort = [r for r in dispatched if r["operation"] == operation]
            completed = [r for r in completions if r["operation"] == operation]
            successful = [r for r in cohort if r["_successful"]]
            durations = [r["duration_ms"] for r in successful]
            planned = [1000 * (r["end_offset_s"] - r["planned_offset_s"]) for r in successful if r.get("planned_offset_s") is not None]
            lag = [1000 * (r["start_offset_s"] - r["planned_offset_s"]) for r in cohort if r.get("planned_offset_s") is not None]
            output = [r["first_output_ms"] for r in successful if r.get("first_output_ms") is not None]
            sample_metadata = (cohort or completed)[0]
            row = {field: sample_metadata.get(field) for field in CONFIG}
            row.update(
                run_id=run_id, repeat=sample_metadata["repeat"], measurement_duration_s=seconds,
                data_complete=complete, contextual_operation_rate=len(kinds) > 1,
                observed_dispatch_count=len(cohort), successful_sample_count=len(successful),
                observed_failure_count=len(cohort) - len(successful),
                completion_window_count=len(completed),
                completion_window_success_count=sum(r["_successful"] for r in completed),
                successful_throughput_ops_s=(sum(r["_successful"] for r in completed) / seconds) if seconds and complete else None,
                completed_throughput_ops_s=(len(completed) / seconds) if seconds and complete else None,
                dispatch_rate_ops_s=len(cohort) / seconds if seconds and complete else None,
                offered_rate_ops_s=(metadata["scheduled_count"] / seconds) if open_loop and seconds and metadata.get("scheduled_count") is not None and len(kinds) == 1 else None,
                failure_rate_pct=(100 * (len(cohort) - len(successful)) / len(cohort)) if complete and cohort else None,
                observed_failure_rate_pct=100 * (len(cohort) - len(successful)) / len(cohort) if cohort else None,
                undispatched_rate_pct=(100 * (metadata["scheduled_count"] - reported_dispatches) / metadata["scheduled_count"])
                    if open_loop and metadata.get("scheduled_count") and reported_dispatches is not None and len(kinds) == 1 else None,
                p50_ms=quantile(durations, 0.50), p95_ms=quantile(durations, 0.95), p99_ms=quantile(durations, 0.99),
                planned_p50_ms=quantile(planned, 0.50), planned_p95_ms=quantile(planned, 0.95), planned_p99_ms=quantile(planned, 0.99),
                planned_latency_sample_count=len(planned), dispatch_lag_p95_ms=quantile(lag, 0.95),
                first_output_p95_ms=quantile(output, 0.95), first_output_sample_count=len(output),
                attempt_count=sum(r["attempt_count"] for r in cohort),
                validation_sample_count=sum(r.get("state_check") is not None for r in cohort),
                cleanup_ok=metadata.get("cleanup_ok"),
                post_validation_ok=metadata.get("post_validation_ok"), client_transport=metadata.get("client_transport"),
                error_classes=dict(Counter((r.get("error_class") or ("state_validation" if r.get("state_check") is False else r["status"])) for r in cohort if not r["_successful"])),
                rpc_codes=dict(Counter((r.get("rpc_code") or "unspecified") for r in cohort if not r["_successful"])),
                p99_evidence="limited: fewer than 1000 successes" if len(successful) < 1000 else "at least 1000 successes; inspect repeat variation",
            )
            row["logical_payload_mib_s"] = (row["successful_throughput_ops_s"] * row["payload_bytes"] / 2 ** 20
                                              if str(operation).startswith("file_") and row.get("payload_bytes") is not None
                                              and row["successful_throughput_ops_s"] is not None else None)
            if successful and len(successful) < 1000:
                issue(issues, "limited_p99_samples", f"{operation}: {len(successful)} success samples; p99 is descriptive and weakly supported", run_id=run_id)
            if row["validation_sample_count"] < len(cohort):
                issue(issues, "incomplete_state_validation", f"{operation}: correctness check reported for {row['validation_sample_count']}/{len(cohort)} operations", run_id=run_id)
            if row.get("mode") in {"open_loop", "open-loop"} and len(lag) != len(cohort):
                issue(issues, "open_loop_missing_schedule", f"{operation}: planned offsets missing; arrival-to-completion latency cannot be fully assessed", run_id=run_id, severity="error")
            normalize_resident_population(row)
            operation_rows.append(row)
    return run_rows, operation_rows


def aggregate(operation_rows):
    groups = defaultdict(list)
    for row in operation_rows:
        groups[key_for(row)].append(row)
    output = []
    for _, rows in sorted(groups.items(), key=lambda item: repr(item[0])):
        result = {field: rows[0].get(field) for field in CONFIG}
        result.update(
            run_count=len(rows), run_ids=[r["run_id"] for r in rows],
            repeat_ids=[r["repeat"] for r in rows], data_complete=all(r["data_complete"] for r in rows),
            successful_sample_count=sum(r["successful_sample_count"] for r in rows),
            min_successful_samples_per_run=min(r["successful_sample_count"] for r in rows),
            observed_dispatch_count=sum(r["observed_dispatch_count"] for r in rows),
            observed_failure_count=sum(r["observed_failure_count"] for r in rows),
            contextual_operation_rate=any(r["contextual_operation_rate"] for r in rows),
            cleanup_failure_count=sum(r["cleanup_ok"] is False for r in rows),
            post_validation_failure_count=sum(r.get("post_validation_ok") is False for r in rows),
            error_classes=dict(sum((Counter(r["error_classes"]) for r in rows), Counter())),
            rpc_codes=dict(sum((Counter(r.get("rpc_codes", {})) for r in rows), Counter())),
        )
        if rows[0].get("resident_count_applicability") == "not_applicable":
            result.update(resident_count_applicability="not_applicable",
                          resident_count_provenance=rows[0]["resident_count_provenance"],
                          raw_resident_count_values=sorted({r.get("raw_resident_count") for r in rows}, key=repr))
        for metric in METRICS:
            values = [r[metric] for r in rows if r[metric] is not None]
            # An incomplete run must not disappear from a cross-run performance estimate.
            if len(values) != len(rows):
                values = []
            result[metric] = statistics.median(values) if values else None
            result[metric + "_min"] = min(values) if values else None
            result[metric + "_max"] = max(values) if values else None
        output.append(result)
    return output


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def slug(value):
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in str(value)).strip("-")[:64] or "plot"


def plot_reports(summary, output, include_single_points=False):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ContractError("matplotlib is not installed in this interpreter; use the benchmark venv or --no-plots") from exc
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 11,
        "axes.labelsize": 10, "figure.dpi": 120, "savefig.dpi": 200,
        "svg.fonttype": "none", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.20, "legend.frameon": False,
    })
    plots = []
    plot_dir = output / "plots"
    plot_dir.mkdir(exist_ok=True)

    def finish(fig, kind, config, caption, point_count=None):
        identity = json.dumps(config, sort_keys=True, ensure_ascii=False)
        name = f"{kind}-{slug(config.get('phase', 'matched'))}-{slug(config.get('scenario', config.get('comparison_key', 'workload')))}-{slug(config.get('operation', 'operation'))}-{hashlib.sha256(identity.encode()).hexdigest()[:10]}"
        fig.text(0.01, 0.015, caption, ha="left", va="bottom", fontsize=8, color="#444444", wrap=True)
        fig.tight_layout(rect=(0, 0.085, 1, 0.93))
        for extension in ("png", "svg"):
            fig.savefig(plot_dir / f"{name}.{extension}", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        plots.append({"kind": kind, "configuration": config, "observed_points": point_count,
                      "png": f"plots/{name}.png", "svg": f"plots/{name}.svg", "caption": caption})

    def line(axis, rows, xfield, metric, label, color=None):
        points = sorted((r for r in rows if r.get(xfield) is not None and r.get(metric) is not None), key=lambda r: r[xfield])
        if not points:
            return
        x = [r[xfield] for r in points]
        y = [r[metric] for r in points]
        artist, = axis.plot(x, y, marker="o", markersize=4, linewidth=1.7, label=label, color=color)
        low = [r.get(metric + "_min", r[metric]) for r in points]
        high = [r.get(metric + "_max", r[metric]) for r in points]
        axis.fill_between(x, low, high, color=artist.get_color(), alpha=0.12, linewidth=0)

    groups = defaultdict(list)
    for row in summary:
        xfield = "arrival_rate_ops_s" if row.get("arrival_rate_ops_s") is not None else "concurrency"
        fields = tuple(f for f in CONFIG if f != xfield)
        groups[(xfield, key_for(row, fields))].append(row)
    for (xfield, values), rows in sorted(groups.items(), key=lambda item: repr(item[0])):
        if len(rows) < 2 and not include_single_points:
            continue
        fields = tuple(f for f in CONFIG if f != xfield)
        config = dict(zip(fields, values))
        fig, axes = plt.subplots(3, 1, figsize=(8.8, 8.6), sharex=True)
        line(axes[0], rows, xfield, "successful_throughput_ops_s", "Successful completions", "#0072B2")
        line(axes[0], rows, xfield, "dispatch_rate_ops_s", "Dispatched operations", "#E69F00")
        line(axes[0], rows, xfield, "offered_rate_ops_s", "Scheduled operations", "#009E73")
        axes[0].set_ylabel("Operations / second")
        for metric, label, color in (("p50_ms", "P50", "#009E73"), ("p95_ms", "P95", "#0072B2"), ("p99_ms", "P99 (descriptive)", "#D55E00")):
            line(axes[1], rows, xfield, metric, label, color)
        axes[1].set_ylabel("Successful latency (ms)")
        line(axes[2], rows, xfield, "failure_rate_pct", "Non-successful operations", "#CC79A7")
        line(axes[2], rows, xfield, "undispatched_rate_pct", "Scheduled but not dispatched", "#D55E00")
        axes[2].set_ylabel("Outcome rates (%)" if xfield == "arrival_rate_ops_s" else "Failure rate (%)")
        axes[2].set_ylim(0, failure_axis_upper(rows))
        lifecycle = config.get("scenario") == "create_delete_churn"
        axes[2].set_xlabel("Configured arrival rate (operations/s)" if xfield == "arrival_rate_ops_s" else "Configured create/delete cycle workers" if lifecycle else "Configured client concurrency")
        axes[2].set_xticks(sorted({r[xfield] for r in rows if r.get(xfield) is not None}))
        for axis in axes:
            if axis.lines:
                axis.legend(loc="best", fontsize=9)
        title = f"{config['phase']} | {config['scenario']} | {config['operation']}"
        resident_label = "no pre-created resident pool" if lifecycle else f"resident={config['resident_count']}"
        subtitle = f"template={config['template']}  memory={config['memory_mib']} MiB  {resident_label}  {config['mode']}  {config['instrumentation']}"
        fig.suptitle(title + "\n" + subtitle, fontsize=11)
        minimum = min(r["min_successful_samples_per_run"] for r in rows)
        repeats = f"{min(r['run_count'] for r in rows)}–{max(r['run_count'] for r in rows)}"
        caption = f"Points: median of per-run statistics; shading: run min–max, not a confidence interval. Runs/point: {repeats}; minimum successes/run: {minimum}.\n"
        caption += "P99 needs more evidence below 1,000 successes/run. Failure rate includes recorded errors, timeouts, rejections, cancellations and invalid results."
        if xfield == "arrival_rate_ops_s":
            caption += "\nFailure rate denominator: dispatched operations. Undispatched rate denominator: scheduled arrivals."
        if any(r["contextual_operation_rate"] for r in rows):
            caption += "\nMixed-script operation rates use the whole run window; they are not standalone capacity."
        if config.get("throughput_scope") and config["throughput_scope"] != "standalone operation":
            caption += f"\nThroughput scope: {config['throughput_scope']}; latency is the named operation's duration."
        finish(fig, "arrival-rate" if xfield == "arrival_rate_ops_s" else "concurrency", config, caption, len(rows))

    fields = tuple(f for f in CONFIG if f not in {"resident_count", "concurrency"})
    anchors = defaultdict(list)
    for row in summary:
        if row.get("resident_count") is not None:
            anchors[key_for(row, fields)].append(row)
    for values, rows in sorted(anchors.items(), key=lambda item: repr(item[0])):
        fixed_concurrencies = [c for c in {r["concurrency"] for r in rows}
                               if len({r["resident_count"] for r in rows if r["concurrency"] == c}) >= 2]
        if not fixed_concurrencies:
            continue
        rows = [r for r in rows if r["concurrency"] in fixed_concurrencies]
        config = dict(zip(fields, values))
        fig, axes = plt.subplots(2, 1, figsize=(8.8, 6.5), sharex=True)
        for concurrency in sorted({r["concurrency"] for r in rows}):
            subset = [r for r in rows if r["concurrency"] == concurrency]
            line(axes[0], subset, "resident_count", "successful_throughput_ops_s", f"C={concurrency}")
            line(axes[1], subset, "resident_count", "p95_ms", f"C={concurrency}")
            for row in subset:
                if row.get("successful_throughput_ops_s") is not None:
                    axes[0].annotate(f"n={row['run_count']}", (row["resident_count"], row["successful_throughput_ops_s"]),
                                     xytext=(0, 8), textcoords="offset points", ha="center", fontsize=9)
        axes[0].set_ylabel("Successful operations / s")
        axes[1].set_ylabel("Successful P95 (ms)")
        axes[1].set_xlabel("Resident sandboxes (N)")
        axes[1].set_xticks(sorted({r["resident_count"] for r in rows}))
        for axis in axes:
            if axis.lines:
                axis.legend()
        fig.suptitle(f"Resident population | {config['phase']} | {config['scenario']} | {config['operation']}", fontsize=11)
        finish(fig, "residents", config, "Configured memory does not establish actual resident memory. Points: median across runs; shading: run min–max.", len(rows))

    # This campaign's initial matrix increases resident population with client
    # concurrency. Display the observed diagonal explicitly; do not present it as
    # an experiment that held N constant.
    coupled = defaultdict(list)
    for row in summary:
        if row.get("resident_count") == row.get("concurrency") and row.get("resident_count") is not None:
            coupled[key_for(row, fields)].append(row)
    for values, rows in sorted(coupled.items(), key=lambda item: repr(item[0])):
        if len({r["concurrency"] for r in rows}) < 2:
            continue
        config = dict(zip(fields, values))
        fig, axes = plt.subplots(3, 1, figsize=(8.8, 8.6), sharex=True)
        line(axes[0], rows, "concurrency", "successful_throughput_ops_s", "Successful completions", "#0072B2")
        axes[0].set_ylabel("Successful operations / s")
        for metric, label, color in (("p50_ms", "P50", "#009E73"), ("p95_ms", "P95", "#0072B2"), ("p99_ms", "P99", "#D55E00")):
            line(axes[1], rows, "concurrency", metric, label, color)
        axes[1].set_ylabel("Successful latency (ms)")
        line(axes[2], rows, "concurrency", "failure_rate_pct", "Non-successful operations", "#CC79A7")
        axes[2].set_ylabel("Failure rate (%)")
        axes[2].set_ylim(0, failure_axis_upper(rows))
        axes[2].set_xlabel("Configured concurrency AND resident population (C = N)")
        axes[2].set_xticks(sorted({r["concurrency"] for r in rows}))
        for axis in axes:
            if axis.lines:
                axis.legend(loc="best")
        fig.suptitle(f"Combined concurrency/population sweep | {config['phase']}\n{config['scenario']} | {config['operation']} | {config['template']}", fontsize=11)
        low_repeat = min(r["run_count"] for r in rows)
        high_repeat = max(r["run_count"] for r in rows)
        repeat_range = str(low_repeat) if low_repeat == high_repeat else f"{low_repeat}–{high_repeat}"
        min_samples = min(r["min_successful_samples_per_run"] for r in rows)
        finish(fig, "coupled-population-concurrency", config,
               f"Both C and N change; effects cannot be attributed to C alone. Runs/point: {repeat_range}; minimum successes/run: {min_samples}.\nPoints: median of per-run metrics; shading: observed run min–max, not a confidence interval.\nHTTPX and pyqwest include different client costs. Short characterization runs do not establish sustainable capacity.", len(rows))

    comparisons = matched_phase_groups(summary)
    for config, rows in comparisons:
        fig, axes = plt.subplots(1, 2, figsize=(10, 4.9))
        labels = [r["phase"] for r in rows]
        for axis, metric, ylabel in ((axes[0], "successful_throughput_ops_s", "Successful operations / second"), (axes[1], "p95_ms", "Successful P95 latency (ms)")):
            values = [r.get(metric) for r in rows]
            if any(value is None for value in values):
                axis.text(0.5, 0.5, "Incomplete measurements", ha="center", transform=axis.transAxes)
            else:
                low = [r[metric] - r[metric + "_min"] for r in rows]
                high = [r[metric + "_max"] - r[metric] for r in rows]
                axis.bar(labels, values, color=["#0072B2", "#E69F00", "#009E73", "#CC79A7"][:len(rows)], yerr=[low, high], capsize=4)
            axis.set_ylabel(ylabel)
            axis.tick_params(axis="x", labelrotation=12)
        population = "" if config.get("scenario") == "create_delete_churn" else f" | N={config['resident_count']}"
        fig.suptitle(f"Matched workload | {config.get('comparison_key') or config['scenario']} | {config['operation']} | C={config['concurrency']}{population}", fontsize=11)
        finish(fig, "phase-comparison", config, "Matched recorded configuration; bars show per-run medians and run min–max. Differences are not automatically SDK overhead.\nThis campaign compares HTTPX service calls with the SDK's pyqwest transport; generator and transport costs remain in the result.", len(rows))
    return plots


def matched_phase_groups(summary):
    # Scenario is the default workload contract. An explicit comparison_key allows
    # differently named scenarios only when the producer asserts semantic equality.
    fields = tuple(f for f in CONFIG if f not in {"phase", "scenario", "comparison_key"})
    groups = defaultdict(list)
    for row in summary:
        if row.get("measurement_kind") in {None, "unspecified"} or (row.get("resident_count") is None and row.get("scenario") != "create_delete_churn"):
            continue
        identity = row.get("comparison_key") or row.get("scenario")
        groups[(identity, *key_for(row, fields))].append(row)
    matches = []
    for _, rows in sorted(groups.items(), key=lambda item: repr(item[0])):
        phases = [r["phase"] for r in rows]
        if len(set(phases)) < 2 or len(phases) != len(set(phases)):
            continue
        # Whole-script contextual rates cannot be compared with isolated operation rates.
        if len({r["contextual_operation_rate"] for r in rows}) != 1:
            continue
        if not all(r["data_complete"] for r in rows):
            continue
        matches.append(({k: rows[0].get(k) for k in CONFIG if k != "phase"}, sorted(rows, key=lambda r: r["phase"])))
    return matches


def report_markdown(summary, run_rows, issues, plots):
    errors = sum(i["severity"] == "error" for i in issues)
    excluded = sum(row.get("performance_eligible") is False for row in run_rows)
    lines = [
        "# Measured benchmark report", "",
        f"Recorded runs: **{len(run_rows)}**. Configuration/operation groups: **{len(summary)}**. Data-quality errors: **{errors}**.", "",
        f"Attempts excluded from performance measurement: **{excluded}**. They remain in the run evidence and must be included when assessing setup reliability.", "",
        "This report contains only recorded observations. It does not establish sustainable capacity or an RL speedup.", "",
        "The 2026-09-20 thin service path uses HTTPX and the SDK path uses pyqwest. Cross-phase differences include client transport and generator costs. Diagonal C=N sweeps change concurrency and resident population together; separate fixed-resident anchors are needed to isolate their effects.", "",
        "For create/delete churn, the raw resident_count is an unused CLI default and is preserved as provenance. Derived tables mark resident count not applicable, and lifecycle figures omit N. The configured concurrency counts create/delete cycle workers; throughput includes cycle pacing while create latency covers the create call.", "",
        "Failed setup attempts with no measured operations stay in runs.csv/json with their stop reason and reported window preserved. Their window is not a performance denominator, and they do not produce zero-throughput capacity points. A completed retry contributes its own measured result; the failed attempt remains separate evidence.", "",
        "- `runs.csv/json`: whole-run windows, record completeness, cleanup and stop outcomes.",
        "- `per_run.csv/json`: operation statistics for each independent run and repeat.",
        "- `summary.csv/json`: median of per-run statistics, run min/max and sample counts.",
        "- `data_quality.json`: gaps and errors; failed runs remain in the data.", "",
        "Throughput counts completions inside the explicit measurement window. Latency covers operations dispatched inside that window, including their drain completions. Mixed-script operation rates use the entire script window. Failure rates count all observed terminal non-success outcomes, including invalid results; rates are null when dispatch accounting is incomplete.", "",
        "Final counters are checked against all measurement-labelled terminal records. Exact timestamp windows still govern performance cohorts. Labelled operations starting outside the window are counted separately in runs.csv/json, and any failures remain in errors.csv/json; timestamps are not padded or changed.", "",
        "Successful-operation latency quantiles exclude failed operations and must be read with failure and completeness counts. P99 based on fewer than 1,000 successful operations per run is weak descriptive evidence. Run min/max bands show observed variation, not confidence intervals. Cross-run summaries do not silently omit runs with missing metric values.", "",
        "errors.csv/json retains failed operation timing, error class and RPC code. Exception names and messages describe observed failures; a TimeoutException name alone does not establish an elapsed timeout budget or sandbox TTL expiry. File payload rates describe logical application bytes, not measured network bandwidth; compression and transport behavior require separate diagnosis.", "",
        "Open-loop figures distinguish failures among dispatched operations from scheduled arrivals that were never dispatched. Zero service errors do not mean every scheduled arrival was served. The current lifecycle rate sweeps are finite batches whose elapsed window includes drain, not steady-state capacity certifications.", "",
    ]
    if issues:
        lines += ["## Data quality", "", "| Severity | Finding | Count |", "|---|---|---:|"]
        counts = Counter((i["severity"], i["code"]) for i in issues)
        lines += [f"| {severity} | {code} | {count} |" for (severity, code), count in sorted(counts.items())]
        lines.append("")
    if plots:
        lines += ["## Figures", ""]
        for plot in plots:
            if plot.get("observed_points") is not None and plot["observed_points"] < 2:
                continue
            config = plot["configuration"]
            label = f"{plot['kind']}: {config.get('phase', 'matched phases')} / {config.get('scenario')} / {config.get('operation')}"
            lines += [f"- {label}: [PNG]({plot['png']}) · [SVG]({plot['svg']})"]
        lines.append("")
    if not matched_phase_groups(summary):
        lines += ["No cross-phase figure was produced because this input did not contain an eligible matched pair. No missing comparison results were inferred.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Explicit raw JSONL files or directories; directories searched recursively")
    parser.add_argument("--output", required=True, type=Path, help="Derived-report destination, e.g. a phase folder's report directory")
    parser.add_argument("--phase", action="append", help="Restrict to this phase; repeat to include several phases")
    parser.add_argument("--no-plots", action="store_true", help="Use only the Python standard library")
    parser.add_argument("--include-single-point-plots", action="store_true", help="Also render isolated one-point charts; default focuses on meaningful curves")
    args = parser.parse_args(argv)
    try:
        files = discover(args.inputs)
        runs, operations, issues = read_records(files, set(args.phase) if args.phase else None)
        run_rows, per_run = summarize_runs(runs, operations, issues)
        summary = aggregate(per_run)
        if not summary and not run_rows:
            raise ContractError("No measurement-window operation groups or final run records found")
        args.output.mkdir(parents=True, exist_ok=True)
        for name, rows in (("runs", run_rows), ("per_run", per_run), ("summary", summary)):
            write_csv(args.output / f"{name}.csv", rows)
            save_json(args.output / f"{name}.json", rows)
        error_fields = ("run_id", "phase", "scenario", "operation", "operation_id", "window", "worker_id",
                        "status", "error_class", "rpc_code", "http_status", "duration_ms", "start_offset_s",
                        "end_offset_s", "state_check", "error_message")
        errors = [{field: row.get(field) for field in error_fields} for row in operations if not row["_successful"]]
        write_csv(args.output / "errors.csv", errors)
        save_json(args.output / "errors.json", errors)
        save_json(args.output / "data_quality.json", issues)
        save_json(args.output / "inputs.json", {"schema_version": 1, "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files], "phases": args.phase})
        plots = [] if args.no_plots else plot_reports(summary, args.output, args.include_single_point_plots)
        save_json(args.output / "plot_index.json", plots)
        (args.output / "REPORT.md").write_text(report_markdown(summary, run_rows, issues, plots), encoding="utf-8")
        print(json.dumps({"output": str(args.output.resolve()), "runs": len(run_rows), "operation_groups": len(summary), "figures": len(plots), "quality_warnings": len(issues), "quality_errors": sum(i["severity"] == "error" for i in issues)}, ensure_ascii=False))
        return 0
    except (ContractError, OSError) as exc:
        print(f"analysis error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
