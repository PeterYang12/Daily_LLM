"""Build the 100-step recipe without importing GPU libraries or contacting services."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import yaml

RUN_ID = 'ua-e2b-pathfinding-20260920-r2'
HISTORICAL_ROOT = '/work/experiments/colocate-e2b-3step-20260920'


def flag(name, default):
    value = os.environ.get(name, str(int(default)))
    if value not in {'0', '1'}:
        raise ValueError(f'{name} must be 0 or 1')
    return value == '1'


def relocate(value, runtime_root):
    if isinstance(value, dict):
        return {k: relocate(v, runtime_root) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v, runtime_root) for v in value]
    if isinstance(value, str):
        return value.replace(HISTORICAL_ROOT, str(runtime_root))
    return value


def build(root, runtime_root, variant):
    cfg = relocate(yaml.safe_load((root / 'configs/historical/train.yaml').read_text()), runtime_root)
    model = os.environ.get('UA_MODEL_PATH', '/models/Qwen3-Coder-30B-A3B-Instruct')
    manifest_path = root / f'configs/datasets-{variant}.json'
    manifest = json.loads(manifest_path.read_text())
    mapping_path = root / 'configs/datasets-template-map.json'
    mapping = json.loads(mapping_path.read_text())
    train_rows, val_rows = manifest['train']['rows'], manifest['val']['rows']
    steps, batch, n = 100, 32, 8
    if train_rows < batch:
        raise ValueError('Dataset must contain at least one 32-prompt training batch')
    warmup = int(os.environ.get('UA_WARMUP_BATCHES', '0'))
    if warmup < 0:
        raise ValueError('UA_WARMUP_BATCHES must be nonnegative')
    eval_enabled = flag('UA_EVAL_ENABLED', True)
    resuming = os.environ.get('UA_RESUME_MODE', 'disable') != 'disable'
    eval_before = flag('UA_EVAL_BEFORE_TRAIN', not resuming) and eval_enabled
    eval_freq = int(os.environ.get('UA_EVAL_FREQ', '25')) if eval_enabled else -1
    if eval_enabled and eval_freq <= 0:
        raise ValueError('Enabled evaluation requires a positive UA_EVAL_FREQ; final evaluation is included')
    eval_rounds = (int(eval_before) + steps // eval_freq + int(steps % eval_freq != 0)) if eval_enabled else 0
    validation_budget = val_rows * (eval_rounds + int(resuming and eval_enabled and not eval_before))
    refill_budget = int(os.environ.get('UA_PROMPT_REFILL_ALLOWANCE', '320'))
    if refill_budget < 0:
        raise ValueError('UA_PROMPT_REFILL_ALLOWANCE must be nonnegative')
    prompt_budget = steps * batch + warmup * batch + refill_budget
    save_freq = int(os.environ.get('UA_SAVE_FREQ', '1'))
    retention = int(os.environ.get('UA_KEEP_CHECKPOINTS', '2'))
    if not 1 <= save_freq <= 100 or retention < 1:
        raise ValueError('Checkpoint frequency must be 1..100 and retention positive')
    startup_timeout = int(os.environ.get('SANDBOX_STARTUP_TIMEOUT', '1800'))
    if startup_timeout != 1800:
        raise ValueError('This reviewed corrected run uses startup timeout1800 seconds')
    if os.environ.get('TOKIO_WORKER_THREADS'):
        raise ValueError('This corrected run retains default Tokio workers')
    run_id = os.environ.get('UA_RUN_ID', RUN_ID)
    runtime_data = runtime_root / 'data' / variant
    cfg['data'].update(train_files=str(runtime_data / 'train.parquet'), val_files=str(runtime_data / 'val.parquet'),
                       train_batch_size=batch, val_batch_size=min(batch, val_rows), shuffle=True,
                       validation_shuffle=False, seed=20260920)
    cfg['actor_rollout_ref']['model']['path'] = model
    cfg['actor_rollout_ref']['actor']['ppo_mini_batch_size'] = batch
    cfg['actor_rollout_ref']['actor']['checkpoint']['load_contents'] = ['model', 'optimizer', 'extra']
    cfg['actor_rollout_ref']['rollout']['n'] = n
    framework = cfg['actor_rollout_ref']['rollout']['custom']['agent_framework']
    framework['agent_runners']['task']['max_concurrent_sessions'] = batch * n
    cfg['trainer'].update(total_training_steps=steps, total_epochs=steps + 1, save_freq=save_freq,
                          max_actor_ckpt_to_keep=retention, max_critic_ckpt_to_keep=retention,
                          resume_mode=os.environ.get('UA_RESUME_MODE', 'disable'), test_freq=eval_freq,
                          val_before_train=eval_before, project_name='e2b-rl-pathfinding', experiment_name=run_id,
                          logger=['console', 'file', 'wandb'], log_val_generations=0,
                          validation_data_dir=str(runtime_root / 'results/validation'),
                          checkpoint_callback_class='verify_checkpoint.CheckpointRecord')
    cfg['trainer']['v1']['colocate_async']['num_warmup_batches'] = warmup
    env = cfg['ray_kwargs']['ray_init']['runtime_env']['env_vars']
    env.update(UA_EXPERIMENT_ROOT=str(runtime_root), UA_RUN_ID=run_id, UA_PROFILE='1',
               SANDBOX_STARTUP_TIMEOUT=str(startup_timeout), UA_TIMING_VARIANT='ticket-admission-v2-startup1800',
               UA_SUPERVISOR_ATTEMPT_ID=os.environ.get('UA_SUPERVISOR_ATTEMPT_ID', 'manual'),
               UA_RUNNER_CPUS=os.environ.get('UA_RUNNER_CPUS', '0.5'),
               UA_PROFILE_SEPARATE_CREATE_GATE=os.environ.get('UA_PROFILE_SEPARATE_CREATE_GATE', '0'),
               UA_MAX_SUBMITTED_PROMPTS=str(prompt_budget), UA_MAX_VALIDATION_EPISODES=str(validation_budget),
               UA_PLANNED_TRAIN_PROMPTS=str(steps * batch),
               UA_PROMPT_REFILL_ALLOWANCE=str(refill_budget),
               UA_PAUSE_AFTER_STEP=os.environ.get('UA_PAUSE_AFTER_STEP', '0'),
               UA_APPEND_METRICS='1', UA_DURABLE_METRICS='1',
               WANDB_PROJECT='e2b-rl-pathfinding', WANDB_RUN_ID=run_id,
               WANDB_RESUME='allow', WANDB_DIR=str(runtime_root / 'results/wandb'),
               WANDB_MODE=os.environ.get('WANDB_MODE', 'online'),
               RAY_TMPDIR=str(runtime_root / 'ray'), RAY_ADDRESS='local')
    cfg['ray_kwargs']['ray_init'].update(namespace=run_id, num_cpus=int(os.environ.get('UA_RAY_CPUS', '160')),
                                         num_gpus=8, address='local', _temp_dir=str(runtime_root / 'ray'))
    cfg['ray_kwargs']['ray_init']['runtime_env']['worker_process_setup_hook'] = 'runtime_common.worker_setup'
    for key in ('E2B_API_KEY', 'WANDB_API_KEY'):
        assert key not in env
    base_task = yaml.safe_load((root / 'configs/historical/task_config.yaml').read_text())[0]
    tasks = []
    for name in ('swe_rebench', 'swe_bench'):
        task = copy.deepcopy(base_task)
        task.update(name=name, run_oracle_solution=False, eval_timeout=600)
        task['sandbox'] = {'provider': 'e2b', 'runtime_timeout': 3600, 'image_map': mapping['image_map']}
        tasks.append(task)
    assert all(t['agent']['max_steps'] == 200 and t['agent']['model']['max_total_tokens'] == 131072 for t in tasks)
    decisions = {'run_id': run_id, 'dataset_variant': variant, 'training_steps': steps,
                 'startup_timeout_seconds': startup_timeout,
                 'admission_protocol': 'uuid-ticket-cancel-release-retire-v2',
                 'timing_variant': 'ticket-admission-v2-startup1800',
                 'initial_weights': 'original base model; failed previous run had zero updates',
                 'previous_failed_run_id': 'ua-e2b-pathfinding-20260920',
                 'timing_comparison_is_not_rl_speedup_evidence': True,
                 'prompt_batch': batch, 'rollouts_per_prompt': n, 'concurrent_sessions': batch * n,
                 'optimizer_minibatch_prompts': batch, 'train_rows': train_rows, 'validation_rows': val_rows,
                 'ray_runner_cpu_reservation': float(os.environ.get('UA_RUNNER_CPUS', '0.5')),
                 'expected_consumed_training_trajectories': steps * batch * n,
                 'train_prompt_submission_limit': prompt_budget, 'warmup_prefetch_prompts': warmup * batch,
                 'automatic_failed_group_refill_allowance': refill_budget, 'validation_rounds': eval_rounds,
                 'validation_episode_submission_limit': validation_budget,
                 'eval_before_train': eval_before, 'eval_frequency': eval_freq, 'eval_final': eval_enabled,
                 'prior_baseline_round_allowance_on_resume': int(resuming and eval_enabled and not eval_before),
                 'checkpoint_frequency': save_freq, 'checkpoint_retention': retention,
                 'checkpoint_contents': ['model', 'optimizer', 'extra', 'dataloader'],
                 'queue_checkpoint': 'Native only if installed TransferQueue >=0.1.9 exposes save/load APIs; warmup defaults to zero',
                 'dataset_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                 'template_map_sha256': hashlib.sha256(mapping_path.read_bytes()).hexdigest(),
                 'creation_permits': int(os.environ.get('UA_CREATE_PERMITS', '3')),
                 'exec_permits': int(os.environ.get('UA_EXEC_PERMITS', '16')),
                 'separate_create_gate': os.environ.get('UA_PROFILE_SEPARATE_CREATE_GATE', '0'),
                 'note': '100 is the requested optimizer/global-step target, not an overnight completion guarantee.'}
    return cfg, tasks, decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('UA_EXPERIMENT_ROOT', '/experiment')))
    parser.add_argument('--runtime-root', type=Path, default=Path('/experiment'))
    parser.add_argument('--data-variant', choices=['qualified', 'full'], default=os.environ.get('UA_DATA_VARIANT', 'qualified'))
    args = parser.parse_args()
    cfg, tasks, decisions = build(args.root.resolve(), args.runtime_root, args.data_variant)
    output = args.root / 'configs'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'train.yaml').write_text(yaml.safe_dump(cfg, sort_keys=False))
    (output / 'train-recipe.json').write_text(json.dumps(cfg, indent=2) + '\n')
    (output / 'task_config.yaml').write_text(yaml.safe_dump(tasks, sort_keys=False))
    (output / 'training-plan.json').write_text(json.dumps(decisions, indent=2) + '\n')
    sources = {}
    for label, repository in [('uni-agent', args.root / 'src/uni-agent'),
                              ('verl', args.root / 'src/uni-agent/verl')]:
        command = ['git', '-c', f'safe.directory={repository}', '-C', str(repository)]
        revision = subprocess.check_output(command + ['rev-parse', 'HEAD'], text=True).strip()
        names = subprocess.check_output(command + ['ls-files', '-m', '-o', '--exclude-standard'], text=True).splitlines()
        changed = {name: hashlib.sha256((repository / name).read_bytes()).hexdigest()
                   for name in names if (repository / name).is_file()}
        diff = subprocess.check_output(command + ['diff', '--binary', '--ignore-submodules=all'], text=True)
        (output / f'{label}-reconstruction.patch').write_text(diff)
        sources[label] = {'revision': revision, 'modified_or_added_file_sha256': changed}
    sources['runtime_code_sha256'] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((args.root / 'code').glob('*')) if path.suffix in {'.py', '.sh'} and path.is_file()
    }
    (output / 'source-provenance.json').write_text(json.dumps(sources, indent=2) + '\n')
    print(json.dumps(decisions, indent=2))


if __name__ == '__main__':
    main()
