#!/usr/bin/env python3
"""Inspect, or explicitly patch only one checkpoint-observer function on pinned workers.

Default is read-only inspection. --apply requires a previously reviewed target
manifest. No model/optimizer methods are invoked; no live source file is edited.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

ADDRESS = '127.0.0.1:38983'
NAMESPACE = 'ua-e2b-pathfinding-20260920-r2'
ATTEMPT = '20260920T184935Z-3c9aeb9d-attempt-01'
FROZEN_SHA = '13cc5ca25ed1ce1d77dd6a0dae11d1b3509833e3b9b0748b5db36a43fb666f61'
FUNCTION = '_optimizer_checkpoint_scalars'
COMPILE_NAME = '<reviewed-checkpoint-scalar-observer-v2>'


def inspect_worker(worker):
    import os, sys, hashlib, marshal
    from pathlib import Path
    import ray
    attrs = vars(worker); workers = attrs.get('worker_dict', {})
    roles = [key for key in workers if key in ('actor_rollout', 'actor_rollout_ref')]
    if len(roles) != 1: raise RuntimeError('Unexpected WorkerDict roles')
    inner = workers[roles[0]]
    training = vars(inner).get('actor')
    engine = vars(training).get('engine') if training is not None else None
    if engine is None: raise RuntimeError('Training engine missing')
    optimizer = vars(engine).get('optimizer')
    children = vars(optimizer).get('chained_optimizers') if optimizer is not None else None
    module = sys.modules.get('runtime_observe')
    function = vars(module).get('_optimizer_checkpoint_scalars') if module else None
    context = ray.get_runtime_context()
    return {'actor_id': context.get_actor_id(), 'namespace': context.namespace, 'job_id': context.get_job_id(), 'node_id': context.get_node_id(),
        'pid': os.getpid(), 'pid_start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
        'rank': attrs.get('_rank'), 'world_size': attrs.get('_world_size'), 'rank_env': os.environ.get('RANK'),
        'run_id': os.environ.get('UA_RUN_ID'), 'attempt_id': os.environ.get('UA_SUPERVISOR_ATTEMPT_ID'),
        'experiment_root': os.environ.get('UA_EXPERIMENT_ROOT'), 'is_actor': vars(inner).get('_is_actor'),
        'role': roles[0], 'engine_class': type(engine).__name__, 'optimizer_class': type(optimizer).__name__,
        'optimizer_children': len(children) if isinstance(children, (list, tuple)) else None,
        'observed_optimizer_calls': vars(engine).get('_ua_observed_updates', 0),
        'active_training_global_step': vars(engine).get('_ua_training_global_step'),
        'observer_module_loaded': module is not None,
        'function_code_sha256': hashlib.sha256(marshal.dumps(function.__code__)).hexdigest() if function else None,
        'observer_file_sha256': hashlib.sha256(Path('/experiment/code/runtime_observe.py').read_bytes()).hexdigest()}


def apply_one(worker, expected, source, source_sha):
    import ast, hashlib, importlib, marshal, sys, types
    from pathlib import Path
    current = inspect_worker(worker)
    for key in ('actor_id', 'namespace', 'job_id', 'node_id', 'pid', 'pid_start_ticks', 'rank', 'world_size', 'run_id', 'attempt_id', 'experiment_root', 'is_actor', 'role', 'engine_class', 'optimizer_class', 'optimizer_children'):
        if current[key] != expected[key]: raise RuntimeError('Worker identity changed: ' + key)
    if current['observed_optimizer_calls'] != 0 or current['active_training_global_step'] not in (None, 0):
        raise RuntimeError('Worker has begun a training update; idle-rollout patch refused')
    if current['observer_file_sha256'] != expected['observer_file_sha256']:
        raise RuntimeError('Frozen observer source differs')
    if hashlib.sha256(source.encode()).hexdigest() != source_sha:
        raise RuntimeError('Reviewed replacement source digest differs')
    parsed = ast.parse(source)
    if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.FunctionDef) or parsed.body[0].name != '_optimizer_checkpoint_scalars':
        raise RuntimeError('Replacement must define exactly the scalar observer function')
    if parsed.body[0].decorator_list: raise RuntimeError('Replacement decorators are not allowed')
    # This module's top level defines functions and imports standard metadata helpers;
    # it performs no tensor, model, optimizer, distributed or device operation.
    module = importlib.import_module('runtime_observe')
    target = '_optimizer_checkpoint_scalars'
    old = vars(module)[target]
    file_tree = ast.parse(Path(module.__file__).read_text())
    old_node = next(node for node in file_tree.body if isinstance(node, ast.FunctionDef) and node.name == target)
    old_namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[old_node], type_ignores=[])), module.__file__, 'exec'), old_namespace)
    expected_old_hash = hashlib.sha256(marshal.dumps(old_namespace[target].__code__)).hexdigest()
    scratch = {'__name__': module.__name__}
    exec(compile(source, '<reviewed-checkpoint-scalar-observer-v2>', 'exec'), scratch)
    replacement = types.FunctionType(scratch[target].__code__, vars(module), target)
    replacement.__module__ = module.__name__
    new_hash = hashlib.sha256(marshal.dumps(replacement.__code__)).hexdigest()
    old_hash = hashlib.sha256(marshal.dumps(old.__code__)).hexdigest()
    frozen_code_equal = old.__code__ == old_namespace[target].__code__
    replacement_code_equal = old.__code__ == replacement.__code__
    # marshal records interning/reference-sharing details; its digest is provenance,
    # not canonical code identity across separately compiled/imported functions.
    if not (frozen_code_equal or replacement_code_equal):
        raise RuntimeError('Loaded observer function is neither the frozen nor reviewed version')
    before_other = {key: id(value) for key, value in vars(module).items() if key != target}
    # The only assignment to the live module. No model/optimizer fields are accessed or changed.
    vars(module)[target] = replacement
    after_other = {key: id(value) for key, value in vars(module).items() if key != target}
    if before_other != after_other: raise RuntimeError('Unexpected change to other observer-module bindings')
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != current['observer_file_sha256']:
        raise RuntimeError('Live source file changed during the patch')
    return {**current, 'observer_module_loaded': True, 'old_function_code_sha256': old_hash,
        'new_function_code_sha256': new_hash, 'expected_frozen_function_code_sha256': expected_old_hash,
        'replacement_source_sha256': source_sha, 'other_module_bindings_changed': [],
        'frozen_code_equal_before': frozen_code_equal, 'replacement_code_equal_before': replacement_code_equal,
        'replacement_code_equal_after': vars(module)[target].__code__ == replacement.__code__,
        'already_applied': replacement_code_equal, 'applied': True,
        'patch_scope': 'runtime_observe._optimizer_checkpoint_scalars binding only',
        'model_optimizer_device_methods_called': False, 'live_source_file_modified': False}


def validate_workers(rows):
    if len(rows) != 8 or {row['rank'] for row in rows} != set(range(8)):
        raise RuntimeError('Expected exactly8 distinct worker ranks')
    if len({row['node_id'] for row in rows}) != 1 or not rows[0]['node_id']:
        raise RuntimeError('Unexpected worker node identities')
    if len({row['actor_id'] for row in rows}) != 8 or len({row['pid'] for row in rows}) != 8:
        raise RuntimeError('Duplicate actor/process identity')
    for row in rows:
        if row['namespace'] != NAMESPACE or row['run_id'] != NAMESPACE or row['attempt_id'] != ATTEMPT:
            raise RuntimeError('Worker ownership differs from the reviewed R2 attempt')
        if row['is_actor'] is not True or row['experiment_root'] != '/experiment': raise RuntimeError('Worker actor/root scope differs')
        if row['world_size'] != 8 or row['rank_env'] != str(row['rank']): raise RuntimeError('Worker rank identity differs')
        if row['optimizer_class'] != 'ChainedOptimizer' or row['optimizer_children'] != 2:
            raise RuntimeError('Unexpected optimizer structure')
        if row['observer_file_sha256'] != FROZEN_SHA: raise RuntimeError('Frozen observer file digest differs')
        if row['observed_optimizer_calls'] != 0 or row['active_training_global_step'] not in (None, 0):
            raise RuntimeError('Worker is no longer idle before its first update')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--targets', type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).with_name('scalar_observer_v2.py'))
    parser.add_argument('--output', type=Path, default=Path(__file__).parent)
    args = parser.parse_args(); os.umask(0o077)
    if args.apply and args.targets is None: parser.error('--apply requires a reviewed --targets manifest')
    source = args.source.read_text(); digest = hashlib.sha256(source.encode()).hexdigest()
    import ray
    ray.init(address=ADDRESS, namespace=NAMESPACE, logging_level='ERROR')
    try:
        table = ray._private.state.actors()
        names = sorted(row['Name'] for row in table.values() if row.get('State') == 'ALIVE'
                       and 'WorkerDict' in row.get('ActorClassName', ''))
        if len(names) != 8: raise RuntimeError('Cluster does not have exactly8 live WorkerDict actors')
        handles = [ray.get_actor(name, namespace=NAMESPACE) for name in names]
        rows = ray.get([actor.__ray_call__.remote(inspect_worker) for actor in handles], timeout=20)
        for name, row in zip(names, rows): row['actor_name'] = name
        validate_workers(rows)
        result = {'schema_version': 1, 'time_unix': time.time(), 'address': ADDRESS, 'namespace': NAMESPACE,
                  'attempt_id': ATTEMPT, 'replacement_source_sha256': digest, 'workers': rows, 'applied': False}
        if args.apply:
            target = json.loads(args.targets.read_text())
            if target['replacement_source_sha256'] != digest: raise RuntimeError('Replacement changed since review')
            expected = {row['actor_name']: row for row in target['workers']}
            if set(expected) != set(names): raise RuntimeError('Named worker set changed since review')
            for row in rows:
                prior = expected[row['actor_name']]
                for key in ('actor_id', 'pid', 'pid_start_ticks', 'rank', 'attempt_id', 'observer_file_sha256'):
                    if row[key] != prior[key]: raise RuntimeError('Worker changed since inspection: ' + key)
            references = [actor.__ray_call__.remote(apply_one, expected[name], source, digest)
                          for name, actor in zip(names, handles)]
            acknowledgements = []; errors = []
            for name, reference in zip(names, references):
                try: acknowledgements.append(ray.get(reference, timeout=20))
                except Exception as exc: errors.append({'actor_name': name, 'error_type': type(exc).__name__, 'error': str(exc)})
            result.update(patch_acknowledgements=acknowledgements, errors=errors,
                          applied=not errors and len(acknowledgements) == 8)
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / (('apply-' if args.apply else 'inspection-') + str(time.time_ns()) + '.json')
        path.write_text(json.dumps(result, indent=2) + '\n'); path.chmod(0o600)
        print(json.dumps({'evidence': str(path), 'worker_count': len(rows), 'applied': result['applied'],
                          'replacement_source_sha256': digest, 'error_count': len(result.get('errors', []))}))
        return 0 if not args.apply or result['applied'] else 1
    finally:
        ray.shutdown()


if __name__ == '__main__': raise SystemExit(main())
