"""CPU fixtures for the checkpoint-1 acceptance boundary; never inspect live outcomes."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'review_checkpoint1_prefix.py'
spec = importlib.util.spec_from_file_location('prefix_review', SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


class PrefixReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='checkpoint-prefix-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'run'
        self.root.mkdir()
        self.attempt = self.root / 'results/supervision' / review.SESSION_ID / 'attempt-01'
        self.attempt.mkdir(parents=True)
        (self.root / 'code').mkdir()
        # Use the actual CPU-only checkpoint/evaluation/optimizer inspection helpers.
        for name in ('supervise_run.py', 'verify_checkpoint.py', 'runtime_common.py'):
            shutil.copyfile(review.DEFAULT_ROOT / 'code' / name, self.root / 'code' / name)
        for relative in ('results/supervision/supervisor.lock', 'results/trainer.lock'):
            (self.root / relative).touch()
        self.json('results/supervision/' + review.SESSION_ID + '/status.json', {
            'run_id': review.RUN_ID, 'session_id': review.SESSION_ID,
            'state': 'paused', 'exit_code': 0, 'ended_utc': '2026-09-20T20:00:00Z',
            'supervisor_pid': 2**30, 'supervisor_start_ticks': '1',
            'attempts': [{'attempt_id': review.ATTEMPT_ID, 'kind': 'checkpoint1_acceptance',
                'started_unix': 100, 'ended_unix': 200, 'exit_code': 0,
                'docker_client_pid': 2**30 + 1, 'docker_client_start_ticks': '2'}]})
        self.json(self.attempt / 'cleanup.json', {
            'remaining': [], 'errors': [], 'supervisor_attempt_id': review.ATTEMPT_ID})
        ids = ['task-' + str(index) for index in range(16)]
        config_names = ('train.yaml', 'task_config.yaml', 'training-plan.json', 'source-provenance.json',
                        'datasets-qualified.json', 'datasets-template-map.json', 'supervisor-prerequisites.json')
        for name in config_names:
            self.json('configs/' + name, {'val': {'instance_ids': ids}} if name == 'datasets-qualified.json' else {})
        for name in config_names[:4]:
            shutil.copyfile(self.root / 'configs' / name, self.attempt / name)
        self.json(self.attempt / 'source-and-config.json', {
            'attempt_id': review.ATTEMPT_ID,
            'source_sha256': {str(p.relative_to(self.root)): review.sha(p) for p in (self.root / 'code').iterdir()},
            'config_sha256': {'configs/' + name: review.sha(self.root / 'configs' / name) for name in config_names}})
        for split in ('train', 'validation'):
            p = self.root / 'data' / (split + '.parquet')
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(('fixture parquet bytes: ' + split).encode())
        self.json('results/resolved-config.json', {'data': {
            'train_files': '/experiment/data/train.parquet', 'val_files': '/experiment/data/validation.parquet'}})
        ckpt = self.root / 'checkpoints/global_step_1'
        actor = ckpt / 'actor'
        contents = {key: {'path': key} for key in ('model', 'optimizer', 'rng_state', 'lr_scheduler')}
        for key in contents:
            p = actor / key / 'payload.bin'
            p.parent.mkdir(parents=True)
            p.write_bytes(b'CPU fixture payload only')
        hf = actor / 'model/huggingface'
        hf.mkdir()
        (hf / 'model.safetensors').write_bytes(b'CPU fixture tensor bytes only')
        write_json(hf / 'config.json', {})
        write_json(actor / 'ckpt_contents.json', {'global_step': 1, 'contents': contents})
        (ckpt / 'data.pt').write_bytes(b'CPU fixture dataloader bytes only')
        (self.root / 'checkpoints/latest_checkpointed_iteration.txt').write_text('1\n')
        checkpoint = {'run_id': review.RUN_ID, 'global_step': 1, 'complete': True,
                      'native_manifest_sha256': review.sha(actor / 'ckpt_contents.json')}
        self.json('results/latest-checkpoint.json', checkpoint)
        write_json(ckpt / 'checkpoint-record.json', checkpoint)
        self.json(self.attempt / 'trainer-outcome.json', {
            'run_id': review.RUN_ID, 'supervisor_attempt_id': review.ATTEMPT_ID,
            'status': 'paused_after_checkpoint', 'exit_code': 0, 'paused_checkpoint': checkpoint,
            'data': {split: {'sha256': review.sha(self.root / 'data' / (split + '.parquet'))}
                     for split in ('train', 'validation')}})
        workers = []
        acknowledgements = []
        for rank in range(8):
            worker = {'rank': rank, 'actor_id': 'actor-' + str(rank), 'pid': 1000 + rank,
                'pid_start_ticks': str(100 + rank), 'world_size': 8, 'job_id': 'job', 'node_id': 'node',
                'run_id': review.RUN_ID, 'namespace': review.RUN_ID, 'attempt_id': review.ATTEMPT_ID,
                'experiment_root': '/experiment', 'is_actor': True, 'role': 'actor_rollout'}
            workers.append(worker)
            acknowledgements.append({**worker, 'applied': True, 'replacement_code_equal_after': True,
                'other_module_bindings_changed': [], 'replacement_source_sha256': review.OBSERVER_SHA,
                'patch_scope': 'runtime_observe._optimizer_checkpoint_scalars binding only',
                'model_optimizer_device_methods_called': False, 'live_source_file_modified': False,
                'frozen_code_equal_before': True,
                # Deliberately unequal: marshal hashes are not code-object equality.
                'old_function_code_sha256': 'a' * 64, 'expected_frozen_function_code_sha256': 'b' * 64})
            self.json(f'results/reload-proof/save/rank-{rank}.json', {
                'phase': 'save', 'global_step': 1, 'rank': rank, 'supervisor_attempt_id': review.ATTEMPT_ID,
                'time_unix': 175, 'model_probes': [{'name': 'weight', 'sample_count': 256,
                                                'sha256_float32': 'c' * 64}],
                'scheduler_scalars': {'num_steps': 256},
                'optimizer': {'step_counters': {'dense': 1, 'unselected_expert': 0}, 'unavailable': []}})
            write_lines(self.root / f'results/optimizer/rank-{rank}.jsonl', [{
                'supervisor_attempt_id': review.ATTEMPT_ID, 'global_step': 1, 'rank': rank,
                'time_unix': 170, 'optimizer_update': 1, 'successful': True, 'gradient_finite': True}])
        self.observer = self.root / 'results/operator/checkpoint-scalar-compat/apply-fixture.json'
        write_json(self.observer, {'applied': True, 'time_unix': 150, 'namespace': review.RUN_ID,
            'attempt_id': review.ATTEMPT_ID, 'replacement_source_sha256': review.OBSERVER_SHA,
            'workers': workers, 'patch_acknowledgements': acknowledgements, 'errors': []})
        write_lines(self.root / 'results/train-metrics.jsonl', [
            {'step': 0, 'data': {'validation': 0.375}}, {'step': 1, 'data': {'training/global_step': 1}}])
        validation = []
        for index, iid in enumerate(ids):
            reward = int(index < 6)
            self.json(f'results/sessions/session-{index}/result.json', {
                'phase': 'validation', 'global_step': 0, 'supervisor_attempt_id': review.ATTEMPT_ID,
                'instance_id': iid, 'status': 'completed', 'extra_info': {'eval_completed': True},
                'sample_uid': 'sample-' + str(index), 'rollout_index': 0, 'reward': reward})
            validation.append({'step': 0, 'uid': f'sample-{index}_0_suffix', 'score': reward})
        write_lines(self.root / 'results/validation/0.jsonl', validation)
        self.json('results/budgets/train_prompts.json', {'run_id': review.RUN_ID, 'submitted': 32,
            'limit': 3520, 'planned_training_prompts': 3200, 'attempted_by_reason': {'planned_batch': 32}})
        self.json('results/budgets/validation_episodes.json', {'run_id': review.RUN_ID, 'submitted': 16,
            'limit': 80, 'attempted_by_reason': {'unspecified': 16}})
        self.json('results/stop-after-checkpoint.request', {'after_step': 1, 'supervisor_session': review.SESSION_ID})

    def json(self, path, value):
        write_json(self.root / path, value)

    def change(self, path, mutate):
        path = self.root / path
        value = json.loads(path.read_text())
        mutate(value)
        write_json(path, value)

    def run_checker(self, *args):
        env = dict(os.environ)
        env.pop('UA_RUN_ID', None)
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--root', str(self.root), *map(str, args)],
                                capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def assert_rejected(self, *args):
        code, result = self.run_checker(*args)
        self.assertEqual(code, 2, result)
        self.assertFalse(result['checkpoint1_prefix_valid'])
        self.assertFalse(result['output_written'])
        return result

    def test_valid_prefix_is_read_only_and_preserves_full_counter_value(self):
        before = {str(p.relative_to(self.root)): review.sha(p) for p in self.root.rglob('*') if p.is_file()}
        code, result = self.run_checker()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['baseline_evaluation'][0]['resolved'], 6)
        after = {str(p.relative_to(self.root)): review.sha(p) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(list(self.root.rglob('__pycache__')))

    def test_private_output_is_complete_and_never_overwrites(self):
        path = self.root / 'results/operator/prefix.json'
        code, result = self.run_checker('--output', path)
        self.assertEqual(code, 0, result)
        data = json.loads(path.read_text())
        self.assertEqual(data['saved_rank_proofs'][0]['scheduler_scalars']['num_steps'], 256)
        self.assertEqual(len(data['append_only_prefixes']), 9)
        self.assertEqual(data['archived_stop_request']['snapshot']['after_step'], 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        before = path.read_bytes()
        self.assert_rejected('--output', path)
        self.assertEqual(before, path.read_bytes())
        self.assertFalse(list(path.parent.glob('.*.tmp')))

    def test_active_locks_fail_immediately_without_output(self):
        output = self.root / 'results/operator/never.json'
        for relative in ('results/supervision/supervisor.lock', 'results/trainer.lock'):
            with self.subTest(lock=relative), (self.root / relative).open('rb') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self.assert_rejected('--output', output)
                self.assertIn('Run still holds', result['reason'])
                self.assertFalse(output.exists())

    def test_modified_dependency_is_rejected_before_import(self):
        source = self.root / 'code/supervise_run.py'
        source.write_text(source.read_text() + '\nraise RuntimeError("should never execute")\n')
        result = self.assert_rejected()
        self.assertIn('Frozen checker dependency changed', result['reason'])

    def test_bad_status_owner_and_live_process_are_rejected(self):
        path = self.root / 'results/supervision' / review.SESSION_ID / 'status.json'
        original = json.loads(path.read_text())
        variants = [('state', 'stop_pending'), ('exit_code', 1), ('session_id', 'stale'),
                    ('supervisor_pid', os.getpid())]
        for key, value in variants:
            row = copy.deepcopy(original)
            row[key] = value
            if key == 'supervisor_pid':
                row['supervisor_start_ticks'] = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
            write_json(path, row)
            with self.subTest(key=key):
                self.assert_rejected()
        write_json(path, original)

    def test_incomplete_checkpoint_and_changed_dataset_are_rejected(self):
        shard = self.root / 'checkpoints/global_step_1/actor/model/huggingface/model.safetensors'
        original = shard.read_bytes()
        shard.write_bytes(b'')
        self.assert_rejected()
        shard.write_bytes(original)
        (self.root / 'data/train.parquet').write_bytes(b'changed')
        self.assert_rejected()

    def test_changed_live_or_archived_config_is_rejected(self):
        for path in (self.root / 'configs/train.yaml', self.attempt / 'train.yaml'):
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(path=path):
                self.assert_rejected()
            path.write_bytes(original)

    def test_stale_or_malformed_saved_proof_is_rejected(self):
        path = self.root / 'results/reload-proof/save/rank-3.json'
        original = json.loads(path.read_text())
        variants = [lambda x: x.update(supervisor_attempt_id='stale'),
                    lambda x: x.update(time_unix=149),
                    lambda x: x['model_probes'][0].update(sample_count=769),
                    lambda x: x['model_probes'][0].update(sha256_float32='bad'),
                    lambda x: x['scheduler_scalars'].update(num_steps=0),
                    lambda x: x['optimizer'].update(step_counters={}),
                    lambda x: x['optimizer'].update(step_counters={'step': float('nan')})]
        for index, mutate in enumerate(variants):
            row = copy.deepcopy(original)
            mutate(row)
            write_json(path, row)
            with self.subTest(variant=index):
                self.assert_rejected()
        write_json(path, original)

    def test_incomplete_or_wrong_actor_hotpatch_proof_is_rejected(self):
        original = json.loads(self.observer.read_text())
        variants = [lambda x: x['patch_acknowledgements'].pop(),
                    lambda x: x['patch_acknowledgements'][0].update(actor_id='another'),
                    lambda x: x['patch_acknowledgements'][0].update(other_module_bindings_changed=['bad']),
                    lambda x: x['patch_acknowledgements'][0].update(replacement_code_equal_after=False),
                    lambda x: x['patch_acknowledgements'][0].update(live_source_file_modified=True)]
        for index, mutate in enumerate(variants):
            row = copy.deepcopy(original)
            mutate(row)
            write_json(self.observer, row)
            with self.subTest(variant=index):
                self.assert_rejected()

    def test_foreign_optimizer_row_duplicate_metric_and_partial_prefix_rejected(self):
        path = self.root / 'results/optimizer/rank-0.jsonl'
        original = path.read_bytes()
        row = json.loads(original)
        row['supervisor_attempt_id'] = 'foreign'
        with path.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        self.assert_rejected()
        path.write_bytes(original.rstrip(b'\n'))
        self.assert_rejected()
        path.write_bytes(original)
        metrics = self.root / 'results/train-metrics.jsonl'
        with metrics.open('a') as stream:
            stream.write(json.dumps({'step': 1, 'data': {'training/global_step': 1}}) + '\n')
        self.assert_rejected()

    def test_wrong_evaluation_owner_reward_or_budget_rejected(self):
        path = self.root / 'results/sessions/session-0/result.json'
        original = json.loads(path.read_text())
        for key, value in [('supervisor_attempt_id', 'foreign'), ('reward', 0)]:
            row = copy.deepcopy(original)
            row[key] = value
            write_json(path, row)
            with self.subTest(key=key):
                self.assert_rejected()
        write_json(path, original)
        self.change('results/budgets/validation_episodes.json', lambda x: x.update(submitted=0, attempted_by_reason={}))
        self.assert_rejected()

    def test_output_cannot_enter_runtime_source(self):
        target = self.root / 'code/audit.json'
        self.assert_rejected('--output', target)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
