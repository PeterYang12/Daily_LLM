"""Counter semantics that can otherwise invalidate resource attribution."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sample_resources", Path(__file__).with_name("sample_resources.py"))
sampler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampler)


def fixture():
    return {"utc": "2026-09-20T00:00:00Z", "monotonic": 10., "cpu": {"cpu": [0] * 8, "cpu0": [0] * 8},
            "memory_bytes": {}, "pressure": {}, "load_average": [0, 0, 0], "fd_allocated": 0,
            "fd_unused": 0, "fd_limit": 100, "collection_ms": 1, "disks": {}, "network": {}, "selected_processes": {}}


class CounterTests(unittest.TestCase):
    def test_wait_and_steal_are_not_busy_cpu(self):
        v = sampler.cpu_metrics([20, 0, 10, 50, 10, 0, 0, 10], [0] * 8)
        self.assertEqual(v, {"busy_pct": 30., "iowait_pct": 10., "steal_pct": 10.})

    def test_pid_reuse_does_not_create_spurious_cpu(self):
        a = fixture(); b = copy.deepcopy(a); b["monotonic"] = 12
        a["selected_processes"]["42"] = {"start_ticks": 1, "ticks": 10}
        b["selected_processes"]["42"] = {"start_ticks": 2, "ticks": 100000}
        self.assertEqual(sampler.sample(a, b)["selected_processes"]["42"], {"continuous_identity": False})

    def test_short_lived_cgroups_have_explicit_coverage_gap(self):
        a = fixture(); b = copy.deepcopy(a); b["monotonic"] = 12
        cg = {"usage_usec": 0, "throttled_usec": 0, "nr_throttled": 0,
              "memory_bytes": 10, "populated": 1, "quota_cores": 2}
        for s in (a, b):
            s.update(cgroups={}, arp=None, nbd_devices=1024, nbd_attached=0, processes={})
        a["cgroups"] = {"retained": dict(cg), "exited": dict(cg)}
        b["cgroups"] = {"retained": {**cg, "usage_usec": 3000000}, "new": {**cg, "usage_usec": 1000000}}
        result = sampler.sample(a, b)["cgroup_summary"]
        self.assertEqual(result["cpu_cores_continuous_only"], 1.5)
        self.assertEqual((result["new_cgroups"], result["exited_cgroups"]), (1, 1))
        self.assertEqual(result["memory_bytes"], 20)

    def test_disk_await_uses_completed_io_count(self):
        a = fixture(); b = copy.deepcopy(a); b["monotonic"] = 12
        a["disks"]["vda"] = [0] * 11
        b["disks"]["vda"] = [2, 0, 4, 8, 2, 0, 8, 12, 0, 100, 200]
        d = sampler.sample(a, b)["disks"]["vda"]
        self.assertEqual((d["await_ms"], d["iops"], d["busy_pct"]), (5., 2., 5.))

    def test_arp_entries_is_not_summed_across_cpus(self):
        body = "entries table_fulls forced_gc_runs\n00000064 00000002 00000003\n00000064 00000005 00000007\n"
        def read(path):
            return body if str(path).endswith("arp_cache") else "16384"
        with patch.object(Path, "read_text", read):
            self.assertEqual(sampler.arp(), {"entries": 100, "table_fulls": 7, "forced_gc_runs": 10, "gc_thresh3": 16384})

    def test_lag_file_whitelist_cannot_export_arbitrary_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory, "lag.json")
            p.write_text('{"api_key":"secret", "event_loop_lag_ms":{"p95":1.2,"token":"secret","n":2}}')
            d = sampler.lag_summary(p)
            self.assertEqual(d["event_loop_lag_ms"], {"p95": 1.2, "n": 2})
            self.assertNotIn("secret", str(d))


if __name__ == "__main__":
    unittest.main()
