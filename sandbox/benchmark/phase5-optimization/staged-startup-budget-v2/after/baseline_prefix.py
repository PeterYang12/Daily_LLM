"""Immutable baseline-step1 lineage for an operator-reviewed continuation.

No model loading, GPU work, or E2B requests occur in this module. Capture is
allowed only after the old supervisor, trainer and owned processes have exited.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

PREFIX_REL = Path('results/baseline-prefix/checkpoint-1.json')
REVIEW_REL = Path('configs/startup-variant-review.json')
RESUME_PATHS = {
    ('trainer', 'resume_mode'), ('trainer', 'val_before_train'),
    ('ray_kwargs', 'ray_init', 'runtime_env', 'env_vars', 'UA_PAUSE_AFTER_STEP'),
    ('ray_kwargs', 'ray_init', 'runtime_env', 'env_vars', 'UA_SUPERVISOR_ATTEMPT_ID'),
    ('ray_kwargs', 'ray_init', 'runtime_env', 'env_vars', 'SANDBOX_STARTUP_TIMEOUT'),
    ('ray_kwargs', 'ray_init', 'runtime_env', 'env_vars', 'UA_TIMING_VARIANT'),
}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_prefix_sha(path, size):
    digest = hashlib.sha256(); remaining = size
    with Path(path).open('rb') as stream:
        while remaining:
            block = stream.read(min(remaining, 1024 * 1024))
            if not block: raise ValueError('File became shorter than its captured baseline prefix')
            digest.update(block); remaining -= len(block)
    return digest.hexdigest()


def reference(root, path, api, byte_prefix=False):
    path = Path(path).resolve(); root = Path(root).resolve()
    if not path.is_relative_to(root): raise api.SupervisorError('Evidence path leaves the experiment')
    result = {'path': str(path.relative_to(root)), 'sha256': api.sha(path)}
    if byte_prefix: result['prefix_bytes'] = path.stat().st_size
    return result


def validate_reference(root, record, api):
    path = (Path(root) / record['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()): raise api.SupervisorError('Prefix evidence path escapes root')
    digest = file_prefix_sha(path, record['prefix_bytes']) if 'prefix_bytes' in record else api.sha(path)
    if digest != record['sha256']: raise api.SupervisorError('Changed baseline evidence: ' + record['path'])
    return path


def find_prior_attempt(root, attempt_id, api):
    matches = []
    for path in (Path(root) / 'results/supervision').glob('*/attempt-*/source-and-config.json'):
        row = api.read(path)
        if row and row.get('attempt_id') == attempt_id: matches.append((path.parent, row))
    if len(matches) != 1: raise api.SupervisorError('Expected exactly one archived baseline attempt')
    return matches[0]


def prior_exit_check(status, outcome, attempt_id, api, check_process=True):
    if status.get('state') != 'paused' or status.get('exit_code') != 0 or not status.get('ended_utc'):
        raise api.SupervisorError('Prior supervisor has not finalized a clean paused exit')
    attempts = [row for row in status.get('attempts', []) if row.get('attempt_id') == attempt_id]
    if len(attempts) != 1 or attempts[0].get('exit_code') != 0:
        raise api.SupervisorError('Prior Docker training attempt did not exit zero')
    if outcome.get('supervisor_attempt_id') != attempt_id or outcome.get('status') != 'paused_after_checkpoint' or outcome.get('exit_code') != 0:
        raise api.SupervisorError('Prior native trainer did not finalize its owned checkpoint pause')
    if check_process:
        for pid, tick in [(status.get('supervisor_pid'), status.get('supervisor_start_ticks')),
                          (attempts[0].get('docker_client_pid'), attempts[0].get('docker_client_start_ticks'))]:
            if not pid or not tick: raise api.SupervisorError('Prior process ownership identity is missing')
            try: live_tick = api.process_start(int(pid))
            except (FileNotFoundError, ProcessLookupError): continue
            if live_tick == tick: raise api.SupervisorError('A prior supervisor/Docker client is still alive')
    return attempts[0]


def capture(root, api, baseline_recipe, check_process=True):
    """Return a complete prefix contract, independently of old acceptance-file existence."""
    root = Path(root); saved = api.checkpoint(root, 1)
    metrics = api.metric_snapshot(root)
    if metrics['observed_step_numbers'] != [1] or metrics['duplicate_step_numbers']:
        raise api.SupervisorError('Baseline hold must contain exactly completed native step1')
    raw_proofs = [api.read(root / f'results/reload-proof/save/rank-{rank}.json') for rank in range(8)]
    owners = {row.get('supervisor_attempt_id') for row in raw_proofs if row}
    if len(owners) != 1 or len([row for row in raw_proofs if row]) != 8 or not next(iter(owners)):
        raise api.SupervisorError('Saved checkpoint1 rank proofs have mixed or missing owners')
    old_id = next(iter(owners)); directory, snapshot = find_prior_attempt(root, old_id, api)
    status_path = directory.parent / 'status.json'; outcome_path = directory / 'trainer-outcome.json'
    status, outcome = api.read(status_path), api.read(outcome_path)
    old_attempt = prior_exit_check(status, outcome, old_id, api, check_process)
    proofs = api.reload_proof(root, 'save', old_id, old_attempt['started_unix'])
    if not proofs: raise api.SupervisorError('Missing all-rank saved checkpoint1 proof')
    ended = old_attempt.get('ended_unix')
    if not ended or any(row.get('time_unix', ended + 1) > ended for row in raw_proofs):
        raise api.SupervisorError('Saved proof timestamp lies outside the prior attempt')
    cleanup_path = directory / 'cleanup.json'; cleanup = api.read(cleanup_path)
    if not cleanup or cleanup.get('remaining') != [] or cleanup.get('errors') != [] or cleanup.get('supervisor_attempt_id') != old_id:
        raise api.SupervisorError('Prior attempt lacks clean, exact-owner sandbox cleanup')
    settings = snapshot['public_launch_settings']
    expected = {'UA_DATA_VARIANT': 'qualified', 'UA_RESUME_MODE': 'disable', 'UA_PAUSE_AFTER_STEP': '1',
                'UA_EVAL_BEFORE_TRAIN': '1', 'UA_PROFILE_SEPARATE_CREATE_GATE': '0', 'UA_WARMUP_BATCHES': '0',
                'WANDB_RUN_ID': api.RUN_ID, 'WANDB_RESUME': 'allow'}
    if any(settings.get(key) != value for key, value in expected.items()):
        raise api.SupervisorError('Prior attempt is not the intended baseline recipe')
    if int(settings.get('SANDBOX_STARTUP_TIMEOUT', '600')) != 600:
        raise api.SupervisorError('Baseline startup budget was not600 seconds')
    optimizer = api.optimizer_audit(root, [1], {old_id})
    evaluations = api.validate_evaluations(root)
    eval_paths = []
    for path in sorted((root / 'results/sessions').glob('*/result.json')):
        row = api.read(path)
        if row.get('phase') == 'validation' and row.get('global_step') == 0:
            if row.get('supervisor_attempt_id') != old_id:
                raise api.SupervisorError('Baseline evaluation includes a different attempt owner')
            eval_paths.append(path)
    if len(eval_paths) != 16: raise api.SupervisorError('Baseline evaluation must contain exactly16 owned sessions')
    immutable = [status_path, outcome_path, cleanup_path, directory / 'source-and-config.json',
                 directory / 'train.yaml', directory / 'task_config.yaml', directory / 'training-plan.json',
                 directory / 'source-provenance.json', root / 'results/validation/0.jsonl'] + eval_paths
    immutable += [root / f'results/reload-proof/save/rank-{rank}.json' for rank in range(8)]
    append_only = [root / 'results/train-metrics.jsonl'] + [root / f'results/optimizer/rank-{rank}.jsonl' for rank in range(8)]
    budgets = {}
    for kind in ('train_prompts', 'validation_episodes'):
        path = root / f'results/budgets/{kind}.json'; value = api.read(path)
        if not value or value.get('run_id') != api.RUN_ID: raise api.SupervisorError('Missing cumulative baseline budget')
        if value.get('submitted') != sum(value.get('attempted_by_reason', {}).values()):
            raise api.SupervisorError('Baseline submission accounting is inconsistent')
        budgets[kind] = {'reference': reference(root, path, api), 'value': value}
    if budgets['validation_episodes']['value']['submitted'] != 16 or budgets['validation_episodes']['value']['limit'] != 80:
        raise api.SupervisorError('Baseline validation budget does not represent16 of80')
    small_checkpoint_files = [root / 'checkpoints/global_step_1/actor/ckpt_contents.json',
                              root / 'checkpoints/global_step_1/checkpoint-record.json',
                              root / 'checkpoints/latest_checkpointed_iteration.txt']
    archived_checkpoint = {str(path.relative_to(root)): {'sha256': api.sha(path), 'text': path.read_text()}
                           for path in small_checkpoint_files}
    return {'schema_version': 1, 'run_id': api.RUN_ID, 'baseline_attempt_id': old_id,
            'baseline_supervisor_session': status['session_id'], 'baseline_attempt_directory': str(directory.relative_to(root)),
            'startup_timeout_seconds': 600, 'timing_variant': 'baseline-v1-600',
            'checkpoint': saved, 'archived_checkpoint_metadata': archived_checkpoint,
            'checkpoint_state_sha256': {'checkpoints/global_step_1/data.pt': api.sha(root / 'checkpoints/global_step_1/data.pt')},
            'saved_rank_proofs': proofs, 'step1_optimizer': optimizer, 'evaluations': evaluations,
            'immutable_files': [reference(root, path, api) for path in immutable],
            'append_only_prefixes': [reference(root, path, api, True) for path in append_only],
            'cumulative_budgets': budgets, 'baseline_recipe': baseline_recipe,
            'baseline_recipe_sha256': canonical_sha(baseline_recipe),
            'baseline_source_sha256': snapshot['source_sha256'],
            'scope': 'Step1 and eval0 retained; checkpoint1 payload required before resume, archived metadata retained after pruning'}


def verify(root, api, require_checkpoint=False):
    root = Path(root); path = root / PREFIX_REL; prefix = api.read(path)
    if not prefix or prefix.get('schema_version') != 1 or prefix.get('run_id') != api.RUN_ID:
        raise api.SupervisorError('Missing immutable baseline prefix contract')
    for ref in prefix['immutable_files'] + prefix['append_only_prefixes']: validate_reference(root, ref, api)
    if canonical_sha(prefix['baseline_recipe']) != prefix['baseline_recipe_sha256']:
        raise api.SupervisorError('Baseline recipe digest differs')
    old_id = prefix['baseline_attempt_id']
    proofs = api.reload_proof(root, 'save', old_id)
    if not proofs: raise api.SupervisorError('Baseline rank proofs changed owner or disappeared')
    baseline_rows = [row for row in api.jsonlines(root / 'results/train-metrics.jsonl')
                     if row.get('data', {}).get('training/global_step') == 1]
    if len(baseline_rows) != 1: raise api.SupervisorError('Baseline native step1 metrics duplicated or missing')
    api.optimizer_audit(root, [1], {old_id})
    if api.validate_evaluations(root) != prefix['evaluations']:
        raise api.SupervisorError('Baseline evaluation result changed')
    for path in (root / 'results/sessions').glob('*/result.json'):
        row = api.read(path)
        if row.get('phase') == 'validation' and row.get('global_step') == 0 and row.get('supervisor_attempt_id') != old_id:
            raise api.SupervisorError('Baseline evaluation owner changed')
    for kind, captured in prefix['cumulative_budgets'].items():
        value = api.read(root / captured['reference']['path']); old = captured['value']
        if value.get('limit') != old['limit'] or value.get('run_id') != api.RUN_ID:
            raise api.SupervisorError('Cumulative budget identity/limit changed')
        if value.get('submitted', -1) < old['submitted'] or any(value.get('attempted_by_reason', {}).get(key, -1) < count
                for key, count in old['attempted_by_reason'].items()):
            raise api.SupervisorError('Cumulative budget was reset below the accepted baseline')
        if require_checkpoint and api.sha(root / captured['reference']['path']) != captured['reference']['sha256']:
            raise api.SupervisorError('Budget changed before the reviewed resume began')
    if require_checkpoint:
        saved = api.checkpoint(root, 1)
        if saved['native_manifest_sha256'] != prefix['checkpoint']['native_manifest_sha256']:
            raise api.SupervisorError('Actual checkpoint1 differs from the captured baseline')
        for relative, digest in prefix['checkpoint_state_sha256'].items():
            if api.sha(root / relative) != digest: raise api.SupervisorError('Checkpoint1 dataloader state changed')
        directory = root / prefix['baseline_attempt_directory']
        prior_exit_check(api.read(directory.parent / 'status.json'), api.read(directory / 'trainer-outcome.json'),
                         old_id, api, check_process=True)
    return prefix


def validate_review(root, api, prefix, startup_timeout):
    review = api.read(Path(root) / REVIEW_REL)
    if not review or review.get('review_status') != 'accepted' or review.get('run_id') != api.RUN_ID:
        raise api.SupervisorError('Missing operator-reviewed startup variant manifest')
    if review.get('baseline_prefix_sha256') != api.sha(Path(root) / PREFIX_REL):
        raise api.SupervisorError('Reviewed variant is bound to a different baseline prefix')
    if review.get('startup_timeout_seconds') != startup_timeout:
        raise api.SupervisorError('Requested startup timeout differs from the reviewed variant')
    expected = dict(prefix['baseline_source_sha256'])
    for name, record in review['changed_source_files'].items():
        if expected.get(name) != record.get('before_sha256'):
            raise api.SupervisorError('Reviewed source base differs: ' + name)
        expected[name] = record['after_sha256']
    if api.source_hashes(Path(root)) != expected:
        raise api.SupervisorError('Current source differs from the exact reviewed-after source map')
    return review


def _remove(data, path):
    node = data
    for key in path[:-1]:
        if key not in node: return
        node = node[key]
    node.pop(path[-1], None)


def compare_recipe(baseline, candidate, startup_timeout, api):
    """Allow only resume bookkeeping and the explicitly reviewed startup budget."""
    expected = copy.deepcopy(baseline); actual = copy.deepcopy(candidate)
    env = actual['ray_kwargs']['ray_init']['runtime_env']['env_vars']
    if actual['trainer']['resume_mode'] != 'auto' or actual['trainer']['val_before_train'] is not False:
        raise api.SupervisorError('Continuation must restore checkpoint and skip repeated baseline evaluation')
    if env.get('SANDBOX_STARTUP_TIMEOUT') != str(startup_timeout) or env.get('UA_PAUSE_AFTER_STEP') != '0':
        raise api.SupervisorError('Continuation startup/pause settings differ from the reviewed values')
    if env.get('UA_TIMING_VARIANT') != f'startup-budget-v2-{startup_timeout}':
        raise api.SupervisorError('Continuation timing variant label is missing')
    for path in RESUME_PATHS: _remove(expected, path); _remove(actual, path)
    if expected != actual:
        raise api.SupervisorError('Unapproved semantic config change: model/data/objective/budgets/gate must remain equal')
    return {'baseline_recipe_sha256': canonical_sha(baseline), 'candidate_recipe_sha256': canonical_sha(candidate),
            'startup_timeout_seconds_before': 600, 'startup_timeout_seconds_after': startup_timeout,
            'approved_changed_paths': ['.'.join(path) for path in sorted(RESUME_PATHS)],
            'model_data_objective_unchanged': True, 'timing_comparison_is_not_rl_speedup_evidence': True}


def final_ownership_audit(root, api, prefix, resumed_attempt_id):
    old_id = prefix['baseline_attempt_id']; expected = {old_id: {1}, resumed_attempt_id: set(range(2, 101))}
    for rank in range(8):
        for row in api.jsonlines(Path(root) / f'results/optimizer/rank-{rank}.jsonl'):
            owner = row.get('supervisor_attempt_id'); step = row.get('global_step')
            if owner not in expected or step not in expected[owner]:
                raise api.SupervisorError(f'Optimizer owner/step mismatch on rank{rank}')
    optimizer = api.optimizer_audit(root, list(range(1, 101)), set(expected))
    evaluations = api.validate_evaluations(root, final=True)
    for path in (Path(root) / 'results/sessions').glob('*/result.json'):
        row = api.read(path)
        if row.get('phase') != 'validation': continue
        expected_owner = old_id if row.get('global_step') == 0 else resumed_attempt_id
        if row.get('supervisor_attempt_id') != expected_owner:
            raise api.SupervisorError('Evaluation round belongs to an unapproved attempt')
    train = api.read(Path(root) / 'results/budgets/train_prompts.json')
    val = api.read(Path(root) / 'results/budgets/validation_episodes.json')
    reasons = train.get('attempted_by_reason', {})
    if reasons.get('planned_batch') != 3200 or reasons.get('warmup', 0) != 0 or train['submitted'] != sum(reasons.values()):
        raise api.SupervisorError('Final training budget lost its baseline32 draws or changed the planned100 batches')
    if train['submitted'] > train['limit'] or val['submitted'] != 80 or val['limit'] != 80:
        raise api.SupervisorError('Final cumulative training/evaluation budget check failed')
    return {'optimizer': optimizer, 'evaluations': evaluations,
            'attempt_step_ownership': {old_id: [1], resumed_attempt_id: list(range(2, 101))}}
