"""Artificial accounting fixtures, not benchmark measurements."""

import json
from pathlib import Path
import tempfile
import unittest

import resources


def sample(end, interval, value, layer="guest"):
    return {"capture_id": "unit-only", "source_file": "in-memory", "layer": layer,
            "sample_index": end, "start": end - interval, "end": end,
            "interval_seconds": interval, "monotonic_end_s": end,
            "data": {"cpu": {"busy_pct": value}}}


class ResourceTests(unittest.TestCase):
    def test_rates_weight_actual_intervals_and_keep_layers_separate(self):
        samples = [sample(1, 1, 10), sample(4, 3, 30), sample(4, 4, 70, "host")]
        rows, _ = resources.select_scopes(samples, [])
        summary = resources.aggregate_metrics(rows)
        values = {r["layer"]: r["time_weighted_mean"] for r in summary}
        self.assertEqual(values, {"guest": 25, "host": 70})

    def test_partial_boundary_intervals_are_not_prorated(self):
        samples = [sample(1, 1, 10), sample(2, 1, 20), sample(3, 1, 30)]
        window = {"scope_id": "run:test", "start": 0.5, "end": 2.5}
        rows, coverage = resources.select_scopes(samples, [window])
        selected = [r for r in rows if r["scope_id"] == "run:test"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["value"], 20)
        detail = next(r for r in coverage if r["scope_id"] == "run:test")
        self.assertEqual(detail["coverage_pct"], 50)
        self.assertEqual(detail["excluded_boundary_samples"], 2)

    def test_disk_await_is_operation_weighted(self):
        one, two = sample(1, 1, 0), sample(4, 3, 0)
        one["data"] = {"disks": {"vda": {"iops": 100, "await_ms": 1}}}
        two["data"] = {"disks": {"vda": {"iops": 1, "await_ms": 10}}}
        rows, _ = resources.select_scopes([one, two], [])
        stats = next(r for r in resources.aggregate_metrics(rows) if r["metric"] == "await_ms")
        self.assertAlmostEqual(stats["operation_weighted_mean"], 130 / 103)
        self.assertIsNone(stats["time_weighted_mean"])

    def test_empty_io_intervals_do_not_claim_zero_latency(self):
        one = sample(1, 1, 0)
        one["data"] = {"disks": {"vda": {"iops": 0, "await_ms": 0}}}
        rows, _ = resources.select_scopes([one], [])
        stats = next(r for r in resources.aggregate_metrics(rows) if r["metric"] == "await_ms")
        self.assertIsNone(stats["operation_weighted_mean"])

    def test_delta_is_summed_without_duration_multiplication(self):
        one, two = sample(1, 1, 0), sample(4, 3, 0)
        one["data"] = {"network": {"eth0": {"rx_drops": 2}}}
        two["data"] = {"network": {"eth0": {"rx_drops": 3}}}
        rows, _ = resources.select_scopes([one, two], [])
        stats = resources.aggregate_metrics(rows)[0]
        self.assertEqual(stats["interval_total"], 5)
        self.assertEqual(stats["delta_rate_per_second"], 1.25)

    def test_gauges_remain_sample_statistics(self):
        one = sample(1, 1, 0)
        one["data"] = {"memory_bytes": {"MemAvailable": 40, "MemTotal": 100}}
        rows, _ = resources.select_scopes([one], [])
        stats = next(r for r in resources.aggregate_metrics(rows) if r["metric"] == "unavailable_bytes")
        self.assertEqual(stats["sample_median"], 60)
        self.assertIsNone(stats["time_weighted_mean"])

    def test_utc_must_have_explicit_zone(self):
        with self.assertRaises(resources.ContractError):
            resources.utc_seconds("2026-09-20T01:00:00")

    def test_failed_setup_placeholder_window_is_excluded(self):
        failed = {"record_type": "run", "run_id": "failed-setup", "dispatched_count": 0,
                  "stop_reason": "SandboxException", "run_started_utc": "2026-09-20T01:00:00Z",
                  "measurement_start_offset_s": 0.027, "measurement_end_offset_s": 30.027}
        retry = dict(failed, run_id="completed-retry", dispatched_count=5, stop_reason="completed")
        with tempfile.TemporaryDirectory(prefix="benchmark-window-unit-") as directory:
            path = Path(directory) / "artificial-windows.jsonl"
            path.write_text(json.dumps(failed) + "\n" + json.dumps(retry) + "\n")
            windows, issues = resources.load_windows([path])
        self.assertEqual([w["run_id"] for w in windows], ["completed-retry"])
        self.assertEqual(issues[0]["code"], "run_window_excluded")
        self.assertEqual(issues[0]["run_id"], "failed-setup")


if __name__ == "__main__":
    unittest.main()
