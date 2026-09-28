"""Artificial in-memory fixtures verify accounting; never benchmark observations."""

import json
from pathlib import Path
import tempfile
import unittest

import summarize


def metadata(run_id="run-a"):
    return {
        "record_type": "run", "run_id": run_id, "phase": "phase1-service",
        "scenario": "unit-fixture", "template": "test-only", "memory_mib": 512,
        "resident_count": 1, "concurrency": 2, "mode": "closed_loop",
        "instrumentation": "minimal", "repeat": 1, "measurement_kind": "steady_state",
        "measurement_start_offset_s": 0, "measurement_end_offset_s": 2,
        "scheduled_count": 2, "dispatched_count": 2, "completed_count": 2,
        "cleanup_ok": True, "stop_reason": "complete", "_source": "in-memory fixture",
    }


def operation(index, start, end, *, status="success", run_id="run-a", **extra):
    value = dict(metadata(run_id), record_type="operation", operation_id=str(index),
                 operation="exec_true", start_offset_s=start, end_offset_s=end,
                 duration_ms=(end - start) * 1000, status=status, state_check=True,
                 attempt_count=1, window="measurement", _successful=status == "success")
    value.update(extra)
    return value


class StatisticsTests(unittest.TestCase):
    def test_quantile_definition_and_empty_input(self):
        self.assertIsNone(summarize.quantile([], 0.99))
        self.assertEqual(summarize.quantile([2], 0.99), 2)
        self.assertAlmostEqual(summarize.quantile([1, 2, 3, 4], 0.95), 3.85)

    def test_drain_is_in_latency_but_not_steady_state_throughput(self):
        records = [operation(1, 0, 0.5), operation(2, 1, 3)]
        runs, rows = summarize.summarize_runs({"run-a": metadata()}, records, [])
        self.assertEqual(rows[0]["successful_sample_count"], 2)
        self.assertEqual(rows[0]["successful_throughput_ops_s"], 0.5)
        self.assertEqual(rows[0]["p50_ms"], 1250)
        self.assertTrue(runs[0]["data_complete"])

    def test_failed_operations_remain_in_denominator(self):
        records = [operation(1, 0, 0.5), operation(2, 1, 1.5, status="timeout")]
        _, rows = summarize.summarize_runs({"run-a": metadata()}, records, [])
        self.assertEqual(rows[0]["failure_rate_pct"], 50)
        self.assertEqual(rows[0]["p99_ms"], 500)
        self.assertEqual(rows[0]["completed_throughput_ops_s"], 1)

    def test_missing_operation_is_not_silently_successful(self):
        issues = []
        _, rows = summarize.summarize_runs({"run-a": metadata()}, [operation(1, 0, 0.5)], issues)
        self.assertIsNone(rows[0]["failure_rate_pct"])
        self.assertIsNone(rows[0]["successful_throughput_ops_s"])
        self.assertFalse(rows[0]["data_complete"])
        self.assertIn("dispatch_count_mismatch", {i["code"] for i in issues})

    def test_labelled_late_dispatch_is_boundary_evidence_not_missing_record(self):
        final = dict(metadata(), scheduled_count=3, dispatched_count=3, completed_count=3)
        records = [operation(1, 0, 0.5), operation(2, 1, 1.5),
                   operation(3, 2.000002, 2.1, status="error")]
        issues=[]
        runs, rows = summarize.summarize_runs({"run-a":final}, records, issues)
        self.assertTrue(runs[0]["data_complete"])
        self.assertEqual(runs[0]["measurement_labeled_operation_count"],3)
        self.assertEqual(runs[0]["observed_dispatch_count"],2)
        self.assertEqual(runs[0]["outside_window_dispatch_count"],1)
        self.assertEqual(runs[0]["outside_window_failure_count"],1)
        self.assertEqual(runs[0]["unaccounted_dispatches"],0)
        self.assertNotIn("dispatch_count_mismatch", {i["code"] for i in issues})

    def test_missing_final_record_never_infers_throughput(self):
        _, rows = summarize.summarize_runs({}, [operation(1, 0, 0.5)], [])
        self.assertEqual(rows[0]["p50_ms"], 500)
        self.assertIsNone(rows[0]["successful_throughput_ops_s"])

    def test_warmup_completion_counts_but_warmup_latency_does_not(self):
        final = dict(metadata(), measurement_start_offset_s=1, measurement_end_offset_s=3,
                     dispatched_count=1, scheduled_count=1, completed_count=1)
        records = [operation(1, 0.5, 1.5, window="warmup"), operation(2, 2, 2.5)]
        _, rows = summarize.summarize_runs({"run-a": final}, records, [])
        self.assertEqual(rows[0]["successful_throughput_ops_s"], 1)
        self.assertEqual(rows[0]["successful_sample_count"], 1)
        self.assertEqual(rows[0]["p50_ms"], 500)

    def test_zero_dispatch_run_is_retained(self):
        final = dict(metadata(), mode="open_loop", scheduled_count=3, dispatched_count=0, completed_count=0)
        runs, rows = summarize.summarize_runs({"run-a": final}, [], [])
        self.assertEqual(len(runs), 1)
        self.assertEqual(rows, [])
        self.assertEqual(runs[0]["observed_dispatch_count"], 0)
        self.assertEqual(runs[0]["offered_rate_ops_s"], 1.5)

    def test_failed_setup_window_is_provenance_not_a_capacity_point(self):
        final = dict(metadata(), measurement_start_offset_s=0.027,
                     measurement_end_offset_s=30.027, dispatched_count=0,
                     completed_count=0, scheduled_count=0, stop_reason="SandboxException")
        issues = []
        runs, rows = summarize.summarize_runs({"run-a": final}, [], issues)
        self.assertEqual(rows, [])
        self.assertFalse(runs[0]["performance_eligible"])
        self.assertIsNone(runs[0]["measurement_duration_s"])
        self.assertIsNone(runs[0]["dispatch_rate_ops_s"])
        self.assertEqual(runs[0]["reported_measurement_end_offset_s"], 30.027)
        self.assertTrue(runs[0]["cleanup_ok"])
        self.assertIn("failed_setup_window_excluded", {row["code"] for row in issues})

    def test_completed_retry_is_separate_from_failed_setup(self):
        failed = dict(metadata("failed"), dispatched_count=0, scheduled_count=0,
                      completed_count=0, stop_reason="SandboxException")
        retry = metadata("retry")
        records = [operation(1, 0, 0.5, run_id="retry"), operation(2, 1, 1.5, run_id="retry")]
        runs, rows = summarize.summarize_runs({"failed": failed, "retry": retry}, records, [])
        self.assertEqual(len(runs), 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "retry")
        self.assertEqual(rows[0]["successful_throughput_ops_s"], 1)
        self.assertEqual(summarize.aggregate(rows)[0]["run_count"], 1)

    def test_mixed_script_uses_whole_window(self):
        records = [operation(1, 0, 0.5), operation(2, 1, 1.5, operation="create")]
        _, rows = summarize.summarize_runs({"run-a": metadata()}, records, [])
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["contextual_operation_rate"])
            self.assertEqual(row["successful_throughput_ops_s"], 0.5)
            self.assertIsNone(row["offered_rate_ops_s"])

    def test_summary_does_not_omit_missing_metric_from_one_repeat(self):
        records = [operation(1, 0, 0.5), operation(2, 1, 1.5)]
        _, rows = summarize.summarize_runs({"run-a": metadata()}, records, [])
        other = dict(rows[0], run_id="run-b", repeat=2, p99_ms=None)
        result = summarize.aggregate([rows[0], other])[0]
        self.assertEqual(result["run_count"], 2)
        self.assertIsNone(result["p99_ms"])

    def test_different_arrival_rates_do_not_collapse(self):
        records = [operation(1, 0, 0.5), operation(2, 1, 1.5)]
        _, rows = summarize.summarize_runs({"run-a": metadata()}, records, [])
        one = dict(rows[0], arrival_rate_ops_s=10)
        two = dict(rows[0], run_id="run-b", arrival_rate_ops_s=20)
        self.assertEqual(len(summarize.aggregate([one, two])), 2)

    def test_comparison_rejects_contextual_and_unmatched_resident(self):
        records = [operation(1, 0, 0.5), operation(2, 1, 1.5)]
        _, rows = summarize.summarize_runs({"run-a": metadata()}, records, [])
        one = summarize.aggregate(rows)[0]
        two = dict(one, phase="phase2-sdk", contextual_operation_rate=True)
        self.assertEqual(summarize.matched_phase_groups([one, two]), [])
        two = dict(one, phase="phase2-sdk", resident_count=2)
        self.assertEqual(summarize.matched_phase_groups([one, two]), [])
        two = dict(one, phase="phase2-sdk")
        self.assertEqual(len(summarize.matched_phase_groups([one, two])), 1)


class ContractTests(unittest.TestCase):
    def read(self, records):
        with tempfile.TemporaryDirectory(prefix="benchmark-analysis-unit-") as directory:
            path = Path(directory) / "test-only.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            return summarize.read_records([path])

    def test_duplicate_logical_operation_rejected(self):
        record = operation(1, 0, 0.5)
        with self.assertRaisesRegex(summarize.ContractError, "duplicate logical"):
            self.read([metadata(), record, record])

    def test_cross_window_reuse_is_explicit_and_keeps_raw_ids(self):
        record = operation(1, 0, 0.5)
        _, operations, issues = self.read([metadata(), record, dict(record, window="warmup")])
        self.assertEqual([r["operation_id"] for r in operations], ["1", "1"])
        self.assertIn("operation_id_reused_across_windows", {i["code"] for i in issues})

    def test_units_mismatch_rejected(self):
        with self.assertRaisesRegex(summarize.ContractError, "disagree"):
            self.read([metadata(), operation(1, 0, 0.5, duration_ms=0.5)])

    def test_false_validation_overrides_success_status(self):
        _, operations, _ = self.read([metadata(), operation(1, 0, 0.5, state_check=False)])
        self.assertFalse(operations[0]["_successful"])

    def test_final_without_operations_is_retained(self):
        runs, operations, _ = self.read([dict(metadata(), dispatched_count=0, completed_count=0)])
        self.assertIn("run-a", runs)
        self.assertEqual(operations, [])

    def test_same_id_cannot_change_configuration(self):
        with self.assertRaisesRegex(summarize.ContractError, "concurrency disagrees"):
            self.read([metadata(), operation(1, 0, 0.5, concurrency=9)])


if __name__ == "__main__":
    unittest.main()
