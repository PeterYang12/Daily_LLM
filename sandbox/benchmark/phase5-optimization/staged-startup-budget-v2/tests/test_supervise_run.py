"""CPU-only supervisor acceptance and failure-path tests; no Docker or GPU use."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import supervise_run as sr


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ua-supervisor-test-')
        self.root = Path(self.temporary.name)
        self.saved_signals = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def tearDown(self):
        for sig, handler in self.saved_signals.items(): signal.signal(sig, handler)
        self.temporary.cleanup()

    def args(self):
        return SimpleNamespace(root=self.root, report=self.root / 'report/STATUS.md',
            poll_interval=.01, heartbeat_interval=.02, analysis_interval=600,
            analysis_timeout=.05, fatal_stop_grace=.02, resume_owned=False)

    def write_lines(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def validation_fixture(self):
        ids = [f'task-{i}' for i in range(16)]
        sr.atomic(self.root / 'configs/datasets-qualified.json', {'val': {'instance_ids': ids}})
        for step in (0, 25, 50, 75, 100):
            rows = []
            for index, iid in enumerate(ids):
                uid = f'u{step}-{index}'; sid = f'session-{step}-{index}'; reward = index % 2
                rows.append({'uid': uid + '_0_0', 'step': step, 'score': reward})
                sr.atomic(self.root / f'results/sessions/{sid}/result.json', {
                    'phase': 'validation', 'global_step': step, 'instance_id': iid,
                    'status': 'completed', 'sample_uid': uid, 'rollout_index': 0,
                    'reward': reward, 'extra_info': {'eval_completed': True}})
            self.write_lines(self.root / f'results/validation/{step}.jsonl', rows)

    def test_native_metric_step_count_ignores_rollout_labels_and_gaps(self):
        self.write_lines(self.root / 'results/train-metrics.jsonl', [
            {'step': 0, 'data': {'validation': .5}},
            {'step': 1, 'data': {'training/global_step': 1, 'timing_s/step': 10}},
            {'step': 100, 'data': {'rollouts': 25600}},
            {'step': 3, 'data': {'training/global_step': 3, 'timing_s/step': 30}}])
        result = sr.metric_snapshot(self.root)
        self.assertEqual(result['completed_native_global_step'], 1)
        self.assertEqual(result['observed_step_numbers'], [1, 3])

    def test_each_evaluation_round_joins_exact_task_session_and_reward(self):
        self.validation_fixture()
        self.assertEqual(len(sr.validate_evaluations(self.root, final=True)), 5)
        path = self.root / 'results/validation/100.jsonl'
        rows = list(sr.jsonlines(path)); rows[1]['uid'] = rows[0]['uid']
        self.write_lines(path, rows)
        with self.assertRaises(sr.SupervisorError): sr.validate_evaluations(self.root, final=True)

    def test_wrong_task_in_round_is_rejected_even_with_sixteen_rows(self):
        self.validation_fixture()
        path = self.root / 'results/sessions/session-25-1/result.json'
        row = sr.read(path); row['instance_id'] = 'task-0'; sr.atomic(path, row)
        with self.assertRaises(sr.SupervisorError): sr.validate_evaluations(self.root, final=True)

    def test_optimizer_audit_requires_every_native_step_on_every_rank(self):
        for rank in range(8):
            self.write_lines(self.root / f'results/optimizer/rank-{rank}.jsonl', [
                {'global_step': step, 'supervisor_attempt_id': 'owned', 'successful': True,
                 'gradient_finite': True} for step in (1, 2)])
        result = sr.optimizer_audit(self.root, [1, 2], {'owned'})
        self.assertEqual(result['0']['native_optimizer_calls'], 2)
        path = self.root / 'results/optimizer/rank-7.jsonl'
        rows = list(sr.jsonlines(path)); rows[1]['gradient_finite'] = False; self.write_lines(path, rows)
        with self.assertRaises(sr.SupervisorError): sr.optimizer_audit(self.root, [1, 2], {'owned'})

    def test_private_logs_are_bounded_and_report_evicted_prefix(self):
        log = sr.RotatingLog(self.root, limit=16, keep=3)
        log.write(b'x' * 80); log.close()
        self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob('*.log')), 48)
        self.assertEqual(sr.read(self.root / 'log-retention.json')['evicted_prefix_bytes'], 32)

    def test_reload_validation_failure_does_not_orphan_active_child(self):
        supervisor = sr.Supervisor(self.args())
        supervisor.container = {'id': 'fixture-container'}
        supervisor.state['state'] = 'acceptance_validating'
        configs = self.root / 'configs'; configs.mkdir()
        for name in ('train.yaml', 'task_config.yaml', 'training-plan.json', 'source-provenance.json'):
            (configs / name).write_text('{}')
        supervisor.docker_command = lambda env, command: ([sys.executable, '-u', '-c',
            'import time; print("owned fixture child", flush=True); time.sleep(10)']
            if command[0] == 'bash' else ['fixture-prepare'])
        supervisor.analyze_tick = lambda **kwargs: None
        killed = []
        def terminate(attempt, signum):
            killed.append((attempt, signum))
            supervisor.active_attempt[1].terminate()
            return {}
        supervisor.terminate_owned_attempt = terminate
        with patch.object(sr, 'inspect_container', return_value={'id': 'fixture-container'}), \
             patch.object(sr.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(sr, 'reload_proof', side_effect=sr.SupervisorError('fixture reload mismatch')):
            with self.assertRaises(sr.SupervisorError): supervisor.attempt(False)
        self.assertTrue(killed)
        self.assertIsNotNone(supervisor.active_attempt[1].poll())
        self.assertTrue((self.root / 'results/stop-after-checkpoint.request').is_file())

    def test_owned_termination_program_has_no_null_source_or_secret_output(self):
        supervisor = sr.Supervisor(self.args()); supervisor.container = {'id': 'fixture'}
        captured = {}
        def run(command, **kwargs):
            program = kwargs['input']; captured['program'] = program
            compile(program, '<ownership-helper>', 'exec')
            self.assertNotIn('\x00', program)
            return SimpleNamespace(returncode=0, stdout='{"signalled_owned_processes": []}')
        with patch.object(sr.subprocess, 'run', side_effect=run):
            supervisor.terminate_owned_attempt('owned-test-attempt', signal.SIGTERM)
        self.assertIn('pidfd_send_signal', captured['program'])
        self.assertIn('UA_SUPERVISOR_ATTEMPT_ID', captured['program'])

    def test_final_analysis_is_single_and_reaped(self):
        (self.root / 'code').mkdir()
        (self.root / 'code/profile_analyze.py').write_text('import time; time.sleep(5)\n')
        supervisor = sr.Supervisor(self.args())
        supervisor.finish_analysis()
        self.assertIsNone(supervisor.analysis)
        self.assertEqual(len(list((supervisor.directory / 'analysis').glob('run-*'))), 1)


if __name__ == '__main__': unittest.main()
