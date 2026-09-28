"""CPU-only staged continuation tests; no active runtime mutation or service calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import fcntl
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import baseline_prefix as bp
import supervise_run as sr


class StartupVariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='startup-variant-test-')
        self.root = Path(self.temp.name)
        self.old = 'old-session-attempt-01'; self.new = 'new-session-attempt-01'
        self.recipe = {'defaults': ['ppo_megatron_trainer', '_self_'],
            'data': {'train_files': '/experiment/data/qualified/train.parquet', 'val_files': '/experiment/data/qualified/val.parquet', 'seed': 20260920, 'train_batch_size': 32},
            'actor_rollout_ref': {'model': {'path': '/models/Qwen3-Coder-30B-A3B-Instruct'},
                'actor': {'policy_loss': {'loss_mode': 'gspo'}}, 'rollout': {'n': 8}},
            'trainer': {'total_training_steps': 100, 'resume_mode': 'disable', 'val_before_train': True, 'test_freq': 25},
            'ray_kwargs': {'ray_init': {'runtime_env': {'env_vars': {
                'UA_SUPERVISOR_ATTEMPT_ID': self.old, 'UA_PAUSE_AFTER_STEP': '1',
                'UA_PROFILE_SEPARATE_CREATE_GATE': '0', 'UA_MAX_VALIDATION_EPISODES': '80'}}}}}
        self.directory = self.root / 'results/supervision/old-session/attempt-01'
        self.directory.mkdir(parents=True)
        status = {'session_id': 'old-session', 'state': 'paused', 'exit_code': 0, 'ended_utc': 'finished',
            'supervisor_pid': 99999998, 'supervisor_start_ticks': 'old', 'attempts': [{
                'attempt_id': self.old, 'exit_code': 0, 'started_unix': 1, 'ended_unix': 3,
                'docker_client_pid': 99999999, 'docker_client_start_ticks': 'old'}]}
        sr.atomic(self.directory.parent / 'status.json', status)
        sr.atomic(self.directory / 'trainer-outcome.json', {'supervisor_attempt_id': self.old,
            'status': 'paused_after_checkpoint', 'exit_code': 0})
        sr.atomic(self.directory / 'cleanup.json', {'supervisor_attempt_id': self.old, 'remaining': [], 'errors': []})
        env = {'UA_DATA_VARIANT': 'qualified', 'UA_RESUME_MODE': 'disable', 'UA_PAUSE_AFTER_STEP': '1',
               'UA_EVAL_BEFORE_TRAIN': '1', 'UA_PROFILE_SEPARATE_CREATE_GATE': '0', 'UA_WARMUP_BATCHES': '0',
               'WANDB_RUN_ID': sr.RUN_ID, 'WANDB_RESUME': 'allow'}
        sr.atomic(self.directory / 'source-and-config.json', {'attempt_id': self.old, 'public_launch_settings': env, 'source_sha256': {}})
        for name in ('train.yaml', 'task_config.yaml', 'training-plan.json', 'source-provenance.json'):
            (self.directory / name).write_text('{}')
        for name in ('actor/ckpt_contents.json', 'checkpoint-record.json'):
            sr.atomic(self.root / 'checkpoints/global_step_1' / name, {'global_step': 1})
        (self.root / 'checkpoints/latest_checkpointed_iteration.txt').write_text('1')
        (self.root / 'checkpoints/global_step_1/data.pt').write_bytes(b'fixture-dataloader-state')
        self.cp = {'global_step': 1, 'complete': True, 'native_manifest_sha256': 'native-manifest', 'checkpoint_dir': str(self.root / 'checkpoints/global_step_1')}
        self.cp_patch = patch.object(sr, 'checkpoint', return_value=self.cp)
        self.cp_patch.start()
        self.write_lines(self.root / 'results/train-metrics.jsonl', [{'step': 1, 'data': {'training/global_step': 1, 'timing_s/step': 10}}])
        for rank in range(8):
            sr.atomic(self.root / f'results/reload-proof/save/rank-{rank}.json', {'rank': rank, 'global_step': 1,
                'supervisor_attempt_id': self.old, 'time_unix': 2, 'model_probes': [{'hash': 'fixed'}],
                'scheduler_scalars': {'num_steps': 1}, 'optimizer': {'step_counters': {'step': 1}, 'unavailable': []}})
            self.write_lines(self.root / f'results/optimizer/rank-{rank}.jsonl', [self.optimizer_row(rank, 1, self.old)])
        self.ids = [f'case-{i}' for i in range(16)]
        sr.atomic(self.root / 'configs/datasets-qualified.json', {'val': {'instance_ids': self.ids}})
        self.add_eval(0, self.old)
        self.set_budgets(32, 16)

    def tearDown(self):
        self.cp_patch.stop(); self.temp.cleanup()

    def write_lines(self, path, rows, append=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a' if append else 'w') as stream:
            for row in rows: stream.write(json.dumps(row) + '\n')

    def optimizer_row(self, rank, step, owner):
        return {'rank': rank, 'global_step': step, 'supervisor_attempt_id': owner, 'successful': True, 'gradient_finite': True}

    def add_eval(self, step, owner):
        rows = []
        for i, iid in enumerate(self.ids):
            uid = f'uid-{step}-{i}'; sid = f'sid-{step}-{i}'; reward = i % 2
            rows.append({'uid': uid + '_0_0', 'step': step, 'score': reward})
            sr.atomic(self.root / f'results/sessions/{sid}/result.json', {'phase': 'validation',
                'global_step': step, 'instance_id': iid, 'sample_uid': uid, 'rollout_index': 0, 'session_id': sid,
                'supervisor_attempt_id': owner, 'status': 'completed', 'reward': reward,
                'extra_info': {'eval_completed': True}})
        self.write_lines(self.root / f'results/validation/{step}.jsonl', rows)

    def set_budgets(self, train, val):
        for kind, count, limit, reason in [('train_prompts', train, 3520, 'planned_batch'), ('validation_episodes', val, 80, 'unspecified')]:
            sr.atomic(self.root / f'results/budgets/{kind}.json', {'run_id': sr.RUN_ID, 'submitted': count,
                'limit': limit, 'attempted_by_reason': {reason: count}})

    def capture(self):
        prefix = bp.capture(self.root, sr, self.recipe, check_process=False)
        sr.atomic(self.root / bp.PREFIX_REL, prefix)
        return prefix

    def candidate(self):
        cfg = copy.deepcopy(self.recipe)
        cfg['trainer'].update(resume_mode='auto', val_before_train=False)
        cfg['ray_kwargs']['ray_init']['runtime_env']['env_vars'].update(
            UA_SUPERVISOR_ATTEMPT_ID=self.new, UA_PAUSE_AFTER_STEP='0',
            SANDBOX_STARTUP_TIMEOUT='1800', UA_TIMING_VARIANT='startup-budget-v2-1800')
        return cfg

    def complete_resume(self):
        self.write_lines(self.root / 'results/train-metrics.jsonl', [
            {'step': step, 'data': {'training/global_step': step}} for step in range(2, 101)], append=True)
        for rank in range(8):
            self.write_lines(self.root / f'results/optimizer/rank-{rank}.jsonl',
                [self.optimizer_row(rank, step, self.new) for step in range(2, 101)], append=True)
        for step in (25, 50, 75, 100): self.add_eval(step, self.new)
        self.set_budgets(3200, 80)

    def test_prefix_capture_after_hold_does_not_require_old_acceptance_file(self):
        self.assertFalse((self.directory.parent / 'checkpoint1-acceptance.json').exists())
        prefix = self.capture()
        self.assertEqual(prefix['baseline_attempt_id'], self.old)
        self.assertEqual(prefix['evaluations'][0]['tasks'], 16)
        self.assertEqual(prefix['cumulative_budgets']['validation_episodes']['value']['submitted'], 16)

    def test_mixed_saved_proof_owner_is_rejected(self):
        path = self.root / 'results/reload-proof/save/rank-7.json'
        row = sr.read(path); row['supervisor_attempt_id'] = 'foreign'; sr.atomic(path, row)
        with self.assertRaises(sr.SupervisorError): self.capture()

    def test_live_or_unclean_old_supervisor_is_rejected(self):
        path = self.directory.parent / 'status.json'; row = sr.read(path); row['state'] = 'stop_pending'; sr.atomic(path, row)
        with self.assertRaises(sr.SupervisorError): self.capture()

    def test_mutated_baseline_metrics_or_optimizer_prefix_is_rejected(self):
        self.capture()
        path = self.root / 'results/optimizer/rank-0.jsonl'
        data = path.read_text(); path.write_text(data.replace('true', 'false', 1))
        with self.assertRaises(sr.SupervisorError): bp.verify(self.root, sr)

    def test_baseline_eval_owner_or_payload_cannot_be_replaced(self):
        self.capture(); path = self.root / 'results/sessions/sid-0-0/result.json'
        row = sr.read(path); row['supervisor_attempt_id'] = self.new; sr.atomic(path, row)
        with self.assertRaises(sr.SupervisorError): bp.verify(self.root, sr)

    def test_only_timeout_and_resume_bookkeeping_are_accepted(self):
        result = bp.compare_recipe(self.recipe, self.candidate(), 1800, sr)
        self.assertTrue(result['model_data_objective_unchanged'])
        for key in ('loss', 'data', 'tokio', 'gate'):
            cfg = self.candidate()
            if key == 'loss': cfg['actor_rollout_ref']['actor']['policy_loss']['loss_mode'] = 'ppo'
            elif key == 'data': cfg['data']['seed'] += 1
            elif key == 'tokio': cfg['ray_kwargs']['ray_init']['runtime_env']['env_vars']['TOKIO_WORKER_THREADS'] = '1'
            else: cfg['ray_kwargs']['ray_init']['runtime_env']['env_vars']['UA_PROFILE_SEPARATE_CREATE_GATE'] = '1'
            with self.assertRaises(sr.SupervisorError): bp.compare_recipe(self.recipe, cfg, 1800, sr)

    def test_final_audit_keeps_old_step1_and_new_steps2_through100(self):
        prefix = self.capture(); self.complete_resume()
        bp.verify(self.root, sr)
        result = bp.final_ownership_audit(self.root, sr, prefix, self.new)
        self.assertEqual(result['optimizer']['0']['native_optimizer_calls'], 100)
        self.assertEqual(result['attempt_step_ownership'][self.old], [1])
        self.assertEqual(len(result['evaluations']), 5)

    def test_new_attempt_cannot_reexecute_optimizer_step1(self):
        prefix = self.capture(); self.complete_resume()
        self.write_lines(self.root / 'results/optimizer/rank-0.jsonl', [self.optimizer_row(0, 1, self.new)], append=True)
        with self.assertRaises(sr.SupervisorError): bp.final_ownership_audit(self.root, sr, prefix, self.new)

    def test_old_owner_cannot_supply_post_resume_evaluation(self):
        prefix = self.capture(); self.complete_resume(); path = self.root / 'results/sessions/sid-100-0/result.json'
        row = sr.read(path); row['supervisor_attempt_id'] = self.old; sr.atomic(path, row)
        with self.assertRaises(sr.SupervisorError): bp.final_ownership_audit(self.root, sr, prefix, self.new)

    def test_pruning_checkpoint1_weights_does_not_destroy_archived_proof(self):
        prefix = self.capture(); self.complete_resume()
        shutil.rmtree(self.root / 'checkpoints/global_step_1')
        with patch.object(sr, 'checkpoint', side_effect=AssertionError('Checkpoint1 payload must not be required at final')):
            bp.verify(self.root, sr, require_checkpoint=False)
            bp.final_ownership_audit(self.root, sr, prefix, self.new)
        self.assertTrue(prefix['archived_checkpoint_metadata'])

    def test_unreviewed_source_change_is_rejected(self):
        prefix = self.capture()
        review = {'run_id': sr.RUN_ID, 'review_status': 'accepted', 'baseline_prefix_sha256': sr.sha(self.root / bp.PREFIX_REL),
                  'startup_timeout_seconds': 1800, 'changed_source_files': {}}
        sr.atomic(self.root / bp.REVIEW_REL, review)
        bp.validate_review(self.root, sr, prefix, 1800)
        (self.root / 'code').mkdir(); (self.root / 'code/rogue.py').write_text('print("changed")\n')
        with self.assertRaises(sr.SupervisorError): bp.validate_review(self.root, sr, prefix, 1800)

    def test_budget_reset_is_rejected(self):
        self.capture(); self.set_budgets(0, 0)
        with self.assertRaises(sr.SupervisorError): bp.verify(self.root, sr)

    def test_supervisor_flag_routes_only_startup_budget_change(self):
        instance = object.__new__(sr.Supervisor)
        instance.args = SimpleNamespace(startup_timeout=1800)
        env = instance.environment(False, self.new)
        self.assertEqual(env['SANDBOX_STARTUP_TIMEOUT'], '1800')
        self.assertEqual(env['UA_PROFILE_SEPARATE_CREATE_GATE'], '0')
        self.assertEqual(env['UA_EVAL_BEFORE_TRAIN'], '0')
        self.assertEqual(env['UA_SAVE_FREQ'], '1')
        self.assertNotIn('TOKIO_WORKER_THREADS', env)

    def test_cli_has_explicit_flag_and_rejects_v2_without_resume(self):
        result = subprocess.run([sys.executable, sr.__file__, '--startup-timeout', '1800'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--resume-owned', result.stderr)

    def test_atomic_public_mode_survives_private_umask(self):
        previous = os.umask(0o077)
        try:
            path = self.root / 'STATUS.md'; sr.atomic(path, 'public status\n', mode=0o644)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            private = self.root / 'private.json'; sr.atomic(private, {'value': 1})
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        finally: os.umask(previous)


if __name__ == '__main__': unittest.main()
