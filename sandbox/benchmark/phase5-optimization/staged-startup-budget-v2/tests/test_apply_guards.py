"""Staging guard tests against temporary files only."""
import fcntl
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

STAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('staged_apply', STAGE / 'apply_after_stop.py')
apply_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(apply_module)


class ApplyGuardsTests(unittest.TestCase):
    def test_active_lock_prevents_any_apply_window(self):
        with tempfile.TemporaryDirectory(prefix='variant-lock-test-') as directory:
            root = Path(directory)
            for name in ('results/supervision/supervisor.lock', 'results/trainer.lock', 'results/launch.lock'):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            with (root / 'results/supervision/supervisor.lock').open('r+') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, 'Runtime is active'):
                    with apply_module.held_quiescence_locks(root): self.fail('Acquired an active run')

    def test_owned_process_probe_is_read_only_valid_python(self):
        def run(command, **kwargs):
            code = kwargs['input']; compile(code, '<owned-process-check>', 'exec')
            self.assertNotIn('\x00', code)
            self.assertNotIn('os.kill', code)
            return SimpleNamespace(stdout='{"matching_processes": []}')
        with patch.object(apply_module.subprocess, 'run', side_effect=run):
            self.assertEqual(apply_module.no_owned_processes('fixture', 'old-owner')['matching_processes'], [])

    def test_owned_process_presence_rejects_capture(self):
        with patch.object(apply_module.subprocess, 'run', return_value=SimpleNamespace(stdout='{"matching_processes": [{"pid": 1}]}')):
            with self.assertRaisesRegex(RuntimeError, 'still owns live'):
                apply_module.no_owned_processes('fixture', 'old-owner')


if __name__ == '__main__': unittest.main()
