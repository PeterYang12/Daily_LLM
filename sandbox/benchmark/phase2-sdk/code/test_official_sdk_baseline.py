"""Offline checks for result accounting and cleanup; no service calls."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


spec = importlib.util.spec_from_file_location('baseline', Path(__file__).with_name('official_sdk_baseline.py'))
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class FakeSandbox:
    instances = []
    fail_code = False

    def __init__(self):
        self.sandbox_id = 'fake-' + str(len(self.instances))
        self.killed = False
        self.commands = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(exit_code=0, stdout='"OK"'))

    @classmethod
    def create(cls, **kwargs):
        value = cls()
        cls.instances.append(value)
        return value

    def run_code(self, *args, **kwargs):
        return SimpleNamespace(error='intentional failure' if self.fail_code else None,
                               logs=SimpleNamespace(stdout=['Hello, world!\n']),
                               execution_count=1)

    def kill(self, **kwargs):
        self.killed = True

    @classmethod
    def list(cls, **kwargs):
        return SimpleNamespace(has_next=False)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        FakeSandbox.instances = []
        FakeSandbox.fail_code = False

    def execute(self, iterations=2):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            result = baseline.run_baseline(
                {'api_url': 'http://localhost', 'sandbox_url': 'http://localhost',
                 'api_key': 'test-secret-do-not-record'},
                'fake-template', path, iterations=iterations,
                sandbox_class=FakeSandbox)
            raw = path.read_text()
            self.assertNotIn('test-secret-do-not-record', raw)
            return result, [json.loads(line) for line in raw.splitlines()]

    def test_four_operations_are_measured_and_cleanup_is_separate(self):
        result, rows = self.execute()
        operations = [row for row in rows if row['record_type'] == 'operation']
        self.assertEqual([row['operation'] for row in operations],
                         ['create', 'health_check', 'run_code_first', 'run_code_second'] * 2)
        self.assertEqual(result['completed_count'], 8)
        self.assertTrue(result['cleanup_ok'])
        self.assertTrue(all(sbx.killed for sbx in FakeSandbox.instances))
        self.assertTrue(all(row['duration_ms'] >= 0 for row in operations))
        self.assertLessEqual(operations[-1]['end_offset_s'],
                             [row for row in rows if row['record_type'] == 'cleanup'][-1]['start_offset_s'])

    def test_invalid_code_stops_future_iterations_and_cleans_up(self):
        FakeSandbox.fail_code = True
        result, rows = self.execute()
        self.assertEqual(result['dispatched_count'], 3)
        self.assertEqual(result['completed_count'], 3)
        self.assertEqual(result['successful_count'], 2)
        self.assertEqual(result['failed_count'], 1)
        self.assertTrue(result['stop_reason'].startswith('invalid'))
        self.assertTrue(result['cleanup_ok'])
        self.assertEqual(len(FakeSandbox.instances), 1)
        self.assertTrue(FakeSandbox.instances[0].killed)


if __name__ == '__main__':
    unittest.main()
