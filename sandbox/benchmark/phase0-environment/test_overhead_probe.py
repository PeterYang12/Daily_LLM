"""Offline contract tests; never instantiate a network client or read a key."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("overhead_probe", Path(__file__).with_name("overhead_probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def offline_bench():
    bench = probe.BufferedBench.__new__(probe.BufferedBench)
    bench._operation_events = []
    bench.file = io.StringIO()
    bench.base = {"run_id": "offline", "phase": "phase1", "scenario": "exec_true", "instrumentation": "minimal"}
    return bench


class RecordingContract(unittest.TestCase):
    def test_operation_does_not_serialize_or_write_during_measurement(self):
        b = offline_bench()
        with patch.object(probe.json, "dumps", side_effect=AssertionError("serialized inside operation")):
            b.write("operation", duration_ms=2., window="measurement", operation_id="offline:0", status="success")
        self.assertEqual(b.file.getvalue(), "")
        self.assertEqual(len(b._operation_events), 1)

    def test_final_run_flushes_exact_schema_first_and_only_once(self):
        b = offline_bench()
        b.write("metadata", pid=123)
        fields = {"operation_id": "offline:0", "duration_ms": 2.5,
                  "start_offset_s": 1., "end_offset_s": 1.0025, "status": "success", "window": "measurement"}
        b.write("operation", **fields)
        # Later base changes cannot retroactively alter a buffered event.
        b.base["scenario"] = "later"
        b.write("run", cleanup_ok=True, measurement_start_offset_s=1., measurement_end_offset_s=2.)
        rows = [json.loads(line) for line in b.file.getvalue().splitlines()]
        self.assertEqual([r["record_type"] for r in rows], ["metadata", "operation", "run"])
        self.assertEqual(rows[1], {"record_type": "operation", "run_id": "offline", "phase": "phase1",
                                  "scenario": "exec_true", "instrumentation": "buffered-events", **fields})
        saved = b.file.getvalue(); b.flush_operations()
        self.assertEqual(b.file.getvalue(), saved)
        self.assertEqual(b._operation_events, [])

    def test_only_instrumentation_group_value_changes(self):
        b = offline_bench()
        b.write("operation", status="success", duration_ms=1.25)
        original = probe.runner.Bench.__new__(probe.runner.Bench)
        original.base = {**b.base, "instrumentation": "minimal"}; original.file = io.StringIO()
        original.write("operation", status="success", duration_ms=1.25)
        baseline = json.loads(original.file.getvalue())
        buffered = b._operation_events[0]
        self.assertEqual({k:v for k,v in baseline.items() if k != "instrumentation"},
                         {k:v for k,v in buffered.items() if k != "instrumentation"})

    def test_warmup_also_retained_and_non_operation_events_stay_immediate(self):
        b = offline_bench()
        b.write("operation", operation_id="offline:warmup", window="warmup")
        b.write("template_check", memory_mib=4096)
        self.assertEqual(json.loads(b.file.getvalue())["record_type"], "template_check")
        b.write("run", cleanup_ok=True)
        rows = [json.loads(line) for line in b.file.getvalue().splitlines()]
        self.assertEqual(rows[-2]["window"], "warmup")
        self.assertEqual(rows[-1]["record_type"], "run")

    def test_runner_cli_parser_is_reused(self):
        argv = ["overhead_probe.py", "--adapter", "service", "--phase", "phase1", "--scenario", "exec_true",
                "--concurrency", "32", "--residents", "32", "--count", "100", "--repeat", "3", "--output", "/tmp/not-created.jsonl"]
        with patch.object(sys, "argv", argv):
            args = probe.runner.arguments()
        self.assertEqual((args.concurrency, args.residents, args.repeat, args.scenario), (32, 32, 3, "exec_true"))


if __name__ == "__main__":
    unittest.main()
