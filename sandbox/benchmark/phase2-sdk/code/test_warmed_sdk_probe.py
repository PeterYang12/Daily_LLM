"""Offline diagnostic contracts, with fake credentials and no network clients."""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from connectrpc.code import Code
from connectrpc.errors import ConnectError

spec = importlib.util.spec_from_file_location("warmed_sdk_probe", Path(__file__).with_name("warmed_sdk_probe.py"))
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)


def arguments(**changes):
    return SimpleNamespace(**({"adapter": "sdk", "scenario": "exec_true", "mode": "closed_loop",
                             "concurrency": 64, "residents": 64, "duration": 30., "repeat": 1} | changes))


def offline_bench():
    b = probe.WarmedSDKBench.__new__(probe.WarmedSDKBench)
    b.args = arguments(); b.stop = asyncio.Event(); b.run_id = "offline"; b.rows = []
    b.base = {"phase": "phase2", "scenario": "exec_true", "instrumentation": "minimal", "comparison_key": "exec_true-v1"}
    b.file = io.StringIO(); b.cfg = {"api_key": "FAKE/api+secret"}
    b.handles = {str(i): SimpleNamespace(_envd_access_token=f"fake-envd-{i}-token", traffic_access_token=f"fake-traffic-{i}-token",
                 _envd_api=SimpleNamespace(headers={"X-Access-Token": f"fake-header-{i}-token"})) for i in range(64)}
    b.offset = lambda: 1.25
    return b


class OfflineContracts(unittest.IsolatedAsyncioTestCase):
    def test_arguments_reject_other_scenarios_and_capacities_before_setup(self):
        probe.validate_arguments(arguments())
        for change in ({"adapter": "service"}, {"scenario": "file_write_1m"}, {"concurrency": 32},
                       {"residents": 32}, {"duration": 0}, {"mode": "open_loop"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                probe.validate_arguments(arguments(**change))

    def test_redacts_all_handles_tokens_and_encoded_credentials(self):
        b = offline_bench(); values = probe.known_credentials(b)
        self.assertIn("fake-envd-63-token", values); self.assertIn("fake-traffic-63-token", values)
        self.assertIn("fake-header-63-token", values)
        encoded = quote(b.cfg["api_key"], safe="")
        encoded64 = base64.b64encode(b.cfg["api_key"].encode()).decode()
        error = ConnectError(Code.UNAVAILABLE, "connection ended: " + " ".join(values) + " " + encoded + " " + encoded64)
        detail = probe.error_detail(b, error)
        self.assertEqual(detail["rpc_code"], "UNAVAILABLE")
        self.assertNotIn(encoded, detail["redacted_message"])
        self.assertNotIn(encoded64, detail["redacted_message"])
        for value in values:
            self.assertNotIn(value, detail["redacted_message"])

    async def test_warms_every_slot_concurrently_with_unique_negative_ids(self):
        b = offline_bench(); seen = []; pending = 0; peak = 0
        async def fake_measured(index, window, slot):
            nonlocal pending, peak
            seen.append((index, window, slot)); pending += 1; peak = max(peak, pending)
            await asyncio.sleep(0); pending -= 1
            return {"status": "success", "state_check": True}
        b.measured = fake_measured; b.guard = AsyncMock()
        with patch.object(probe.runner.Bench, "prepare", new=AsyncMock()) as parent:
            await b.prepare()
        parent.assert_awaited_once(); b.guard.assert_awaited_once()
        self.assertEqual({x[2] for x in seen}, set(range(64)))
        self.assertEqual(len({x[0] for x in seen}), 64)
        self.assertTrue(all(x[0] < 0 and x[1] == "warmup" for x in seen))
        self.assertEqual(peak, 8); self.assertEqual(b.rows, [])
        self.assertTrue(json.loads(b.file.getvalue())["validated"])

    async def test_warmup_failure_refuses_measurement(self):
        b = offline_bench()
        async def measured(index, window, slot):
            return {"status": "error" if slot == 3 else "success", "state_check": slot != 3}
        b.measured = measured; b.guard = AsyncMock()
        with patch.object(probe.runner.Bench, "prepare", new=AsyncMock()):
            with self.assertRaises(probe.runner.InvalidResult): await b.prepare()
        b.guard.assert_not_awaited()
        self.assertFalse(json.loads(b.file.getvalue())["validated"])

    async def test_action_logs_safe_details_and_reraises_identical_exception(self):
        b = offline_bench(); error = ConnectError(Code.UNAVAILABLE, "transport closed fake-envd-63-token FAKE/api+secret")
        with patch.object(probe.runner.Bench, "action", new=AsyncMock(side_effect=error)):
            with self.assertRaises(ConnectError) as caught: await b.action(123, 4)
        self.assertIs(caught.exception, error)
        record = json.loads(b.file.getvalue())
        self.assertEqual(record["rpc_code"], "UNAVAILABLE")
        self.assertEqual(record["operation_id"], "offline:123")
        self.assertEqual(record["instrumentation"], "diagnostic-warmed")
        self.assertEqual(record["comparison_key"], "exec_true_all_connections_warm-batch8-v2")
        self.assertNotIn("fake-envd-63-token", b.file.getvalue())
        self.assertNotIn("FAKE/api+secret", b.file.getvalue())

    async def test_successful_action_does_not_emit_error_detail(self):
        b = offline_bench(); result = {"state_check": True}
        with patch.object(probe.runner.Bench, "action", new=AsyncMock(return_value=result)):
            self.assertIs(await b.action(1, 1), result)
        self.assertEqual(b.file.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
