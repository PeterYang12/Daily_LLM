#!/usr/bin/env python3
"""Buffer phase1 operation JSON recording outside its measured workload.

This is a recording-overhead control, not a no-instrumentation control. Timers,
in-memory row bookkeeping, guards, load-generator lag sampling, and the external
resource sampler are unchanged. All runner command-line arguments are forwarded.
No code in the frozen phase1 runner is modified.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import signal

RUNNER_PATH = Path(__file__).resolve().parents[1] / "phase1-service" / "code" / "runner.py"
_spec = importlib.util.spec_from_file_location("e2b_phase1_recording_control_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class BufferedBench(runner.Bench):
    def __init__(self, args):
        # The parent calls write(metadata) during construction.
        self._operation_events = []
        super().__init__(args)

    def write(self, kind, **data):
        self.base["instrumentation"] = "buffered-events"
        if kind == "operation":
            # Snapshot the same flat record the parent's write would serialize.
            # Operation values are scalar IDs/counts/times/statuses in the frozen
            # runner; do not serialize or perform disk I/O in this branch.
            self._operation_events.append({"record_type": kind, **self.base, **data})
            return
        if kind == "run":
            # Bench.run invokes this after measurement, post-validation and
            # owned-sandbox cleanup. Its measurement/drain timestamps have
            # already been captured, so flushing cannot enter their intervals.
            self.flush_operations()
        super().write(kind, **data)

    def flush_operations(self):
        for event in self._operation_events:
            self.file.write(json.dumps(event) + "\n")
        self._operation_events.clear()


async def main():
    bench = BufferedBench(runner.arguments())
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, bench.stop.set)
    return await bench.run()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
