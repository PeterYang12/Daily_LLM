"""Offline interval-boundary checks; no synthetic live guests."""
from datetime import datetime,timezone
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('analyze_idle_pool',Path(__file__).with_name('analyze_idle_pool.py'))
analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)


class WindowTests(unittest.TestCase):
    def test_only_fully_contained_intervals_and_duration_weights(self):
        rows=[{'guest_utc':'2026-09-20T00:00:03Z','interval_seconds':3,'vm_cpu_cores':90,'cgroup_cpu_cores':80},
              {'guest_utc':'2026-09-20T00:00:05Z','interval_seconds':2,'vm_cpu_cores':10,'cgroup_cpu_cores':8},
              {'guest_utc':'2026-09-20T00:00:06Z','interval_seconds':1,'vm_cpu_cores':40,'cgroup_cpu_cores':20},
              {'guest_utc':'2026-09-20T00:00:09Z','interval_seconds':3,'vm_cpu_cores':100,'cgroup_cpu_cores':90}]
        result=analysis.cpu_window(rows,analysis.timestamp('2026-09-20T00:00:02Z'),analysis.timestamp('2026-09-20T00:00:07Z'))
        self.assertEqual(result['fully_contained_intervals'],2)
        self.assertEqual(result['covered_nominal_seconds'],3)
        self.assertEqual(result['vm_interval_weighted_cores'],20)
        self.assertEqual(result['cgroup_interval_weighted_cores'],12)

    def test_empty_window_does_not_invent_zero_cpu(self):
        result=analysis.cpu_window([],analysis.timestamp('2026-09-20T00:00:00Z'),analysis.timestamp('2026-09-20T00:00:10Z'))
        self.assertIsNone(result['vm_interval_weighted_cores'])
        self.assertEqual(result['covered_nominal_seconds'],0)


if __name__=='__main__':unittest.main()
