"""Classification fixtures are artificial and are never benchmark results."""

import unittest
from first_call_cohorts import cohorts, aggregate


class FirstCallTests(unittest.TestCase):
    def test_first_uses_timestamps_not_file_order_and_ignores_warmup(self):
        base = {"run_id": "test", "operation": "exec_true", "worker_id": 0,
                "window": "measurement", "status": "success", "_successful": True}
        rows = [dict(base, operation_id="later", start_offset_s=3),
                dict(base, operation_id="warmup", window="warmup", start_offset_s=0),
                dict(base, operation_id="first", start_offset_s=1, status="error", _successful=False, error_class="ConnectError"),
                dict(base, operation_id="worker1", start_offset_s=2, worker_id=1)]
        result = {r["cohort"]: r for r in cohorts(rows)}
        self.assertEqual((result["first_measured_per_worker"]["calls"], result["first_measured_per_worker"]["failures"]), (2, 1))
        self.assertEqual((result["later_measured_calls"]["calls"], result["later_measured_calls"]["failures"]), (1, 0))
        self.assertEqual(sum(r["calls"] for r in aggregate(list(result.values()))), 3)


if __name__ == "__main__":
    unittest.main()
