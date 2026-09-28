"""Artificial interval fixtures verify alignment math without producing results."""

import copy
import unittest
from clock_sensitivity import select


class ClockSensitivityTests(unittest.TestCase):
    def test_positive_shift_reselects_whole_intervals_without_changing_rates(self):
        samples=[{'start':12,'end':14,'interval_seconds':2,'sample_index':1,'data':{'cpu':{'busy_core_equivalents':10}}},
                 {'start':14,'end':16,'interval_seconds':2,'sample_index':2,'data':{'cpu':{'busy_core_equivalents':20}}}]
        before=copy.deepcopy(samples)
        self.assertEqual(select(samples,13,17,0)['vm_cpu_mean_cores'],20)
        shifted=select(samples,13,17,1)
        self.assertEqual(shifted['vm_cpu_mean_cores'],15)
        self.assertEqual(shifted['coverage_pct'],100)
        self.assertEqual(samples,before)

    def test_boundary_only_intervals_remain_unknown(self):
        samples=[{'start':0,'end':2,'interval_seconds':2,'sample_index':1,'data':{'cpu':{'busy_core_equivalents':10}}}]
        result=select(samples,.5,1.5,0)
        self.assertIsNone(result['vm_cpu_mean_cores'])
        self.assertEqual(result['coverage_pct'],0)
        self.assertEqual(result['excluded_boundary_samples'],1)


if __name__=='__main__':unittest.main()
