"""Offline timing-window semantics for the descriptive report."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('analyze_controls',Path(__file__).with_name('analyze_controls.py'))
analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)


def rows():
    return [{'record_type':'metadata','instrumentation':'minimal','scenario':'exec_true','repeat':1,'concurrency':32,'resident_count':32},
        {'record_type':'operation','window':'warmup','status':'success','state_check':True,'start_offset_s':8,'end_offset_s':9,'duration_ms':1000},
        {'record_type':'operation','window':'measurement','status':'success','state_check':True,'start_offset_s':11,'end_offset_s':12,'duration_ms':1000},
        {'record_type':'operation','window':'measurement','status':'invalid','state_check':False,'start_offset_s':14,'end_offset_s':15,'duration_ms':1000},
        {'record_type':'operation','window':'measurement','status':'success','state_check':True,'start_offset_s':19,'end_offset_s':21,'duration_ms':2000},
        {'record_type':'run','measurement_start_offset_s':10,'measurement_end_offset_s':20,'cleanup_ok':True,'stop_reason':'completed'}]


class WindowTests(unittest.TestCase):
    def test_drain_and_failure_do_not_count_as_good_completions(self):
        result=analysis.summarize_run(rows())
        self.assertEqual(result['completion_window_goodput_ops_s'],.1)
        self.assertEqual(result['dispatched_operations'],3)
        self.assertEqual(result['completed_in_window'],2)
        self.assertEqual(result['drain_completions'],1)
        self.assertEqual(result['failure_counts'],{'invalid':1})
        self.assertEqual(result['successful_completion_latency_median_ms'],1000)

    def test_incomplete_and_unclean_controls_are_refused(self):
        with self.assertRaises(ValueError):analysis.summarize_run(rows()[:-1])
        bad=rows();bad[-1]['cleanup_ok']=False
        with self.assertRaises(ValueError):analysis.summarize_run(bad)

    def test_zero_measurement_setup_failure_is_not_zero_throughput(self):
        bad=[rows()[0],rows()[-1]]
        with self.assertRaises(ValueError):analysis.summarize_run(bad)

    def test_late_boundary_dispatch_is_labelled_but_not_strict_cohort_or_drain(self):
        data=rows()
        data.insert(-1,{'record_type':'operation','operation_id':'boundary-success',
            'window':'measurement','status':'success','state_check':True,
            'start_offset_s':20.000001798,'end_offset_s':20.1,'duration_ms':99.998202})
        data[-1].update(dispatched_count=4,completed_count=4)
        result=analysis.summarize_run(data)
        self.assertEqual(result['measurement_labeled_operation_count'],4)
        self.assertEqual(result['strict_window_dispatch_count'],3)
        self.assertEqual(result['outside_window_dispatch_count'],1)
        self.assertEqual(result['outside_window_failure_count'],0)
        self.assertEqual(result['drain_completions'],1)
        self.assertEqual(result['measurement_labeled_completions_after_end_count'],2)
        self.assertAlmostEqual(result['outside_window_dispatches'][0]['start_after_window_end_us'],1.798,places=6)
        self.assertEqual(result['completion_window_goodput_ops_s'],.1)
        # Final counters must account for the retained label cohort, not just
        # the strict performance cohort.
        data[-1]['completed_count']=3
        with self.assertRaises(ValueError):analysis.summarize_run(data)

    def test_boundary_failure_remains_in_complete_error_audit(self):
        data=rows()
        data.insert(-1,{'record_type':'operation','operation_id':'boundary-error',
            'window':'measurement','status':'error','state_check':False,
            'start_offset_s':20.000001798,'end_offset_s':20.1,'duration_ms':99.998202})
        result=analysis.summarize_run(data)
        self.assertEqual(result['outside_window_failure_count'],1)
        self.assertEqual(result['failure_counts'],{'invalid':1,'error':1})
        self.assertEqual(result['completion_window_goodput_ops_s'],.1)


if __name__=='__main__':
    unittest.main()
