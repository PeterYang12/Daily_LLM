#!/usr/bin/env python3
"""Apply a reviewed staging bundle only after a clean checkpoint1 hold.

Without an explicit action this prints the staged plan and writes nothing.
Root must confirm the live trainer/supervisor have exited before invoking an
apply/capture action. No process is signalled or sandbox created by this helper.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

STAGE = Path(__file__).resolve().parent
ROOT = Path('/data2/yuhanya/e2b-rl-pathfinding-20260920')
sys.path.insert(0, str(STAGE / 'after'))
import supervise_run as api
import baseline_prefix as bp


def checked_bundle(root):
    manifest = json.loads((STAGE / 'staged-manifest.json').read_text())
    for name, item in manifest['files'].items():
        after = STAGE / 'after' / Path(name).name
        if api.sha(after) != item['after_sha256']: raise RuntimeError('Staged file changed: ' + name)
        path = root / name
        before = api.sha(path) if path.exists() else None
        if before != item['before_sha256']: raise RuntimeError('Live frozen source differs from staged base: ' + name)
    return manifest


@contextlib.contextmanager
def held_quiescence_locks(root):
    handles = []
    try:
        for relative in ('results/supervision/supervisor.lock', 'results/trainer.lock', 'results/launch.lock'):
            path = root / relative
            if not path.is_file(): raise RuntimeError('Expected existing launch ownership lock: ' + relative)
            handle = path.open('r+')
            try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close(); raise RuntimeError('Runtime is active; apply refused: ' + relative)
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles): handle.close()


def no_owned_processes(container_id, attempt_id):
    program = '''import json,os,sys
from pathlib import Path
token=b'UA_SUPERVISOR_ATTEMPT_ID='+sys.argv[1].encode(); found=[]
for path in Path('/proc').iterdir():
 if not path.name.isdigit() or int(path.name)==os.getpid():continue
 try:
  if token in (path/'environ').read_bytes().split(b'\\0'):
   found.append({'pid':int(path.name),'start_ticks':(path/'stat').read_text().rsplit(')',1)[1].split()[19]})
 except (FileNotFoundError,ProcessLookupError):pass
print(json.dumps({'matching_processes':found}))
'''
    result = subprocess.run(['docker', 'exec', '-i', container_id, '/opt/venv/bin/python', '-', attempt_id],
                            input=program, text=True, capture_output=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    if data['matching_processes']: raise RuntimeError('Old attempt still owns live container processes; apply refused')
    return data


def read_old_recipe(container_id, path, root):
    relative = path.resolve().relative_to(root.resolve())
    program = 'import json,sys,yaml\nprint(json.dumps(yaml.safe_load(open(sys.argv[1]))))\n'
    result = subprocess.run(['docker', 'exec', '-i', container_id, '/opt/venv/bin/python', '-', '/experiment/' + str(relative)],
                            input=program, text=True, capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


def capture_prefix(root, container):
    existing = root / bp.PREFIX_REL
    if existing.exists():
        prefix = bp.verify(root, api, require_checkpoint=True)
        no_owned_processes(container['id'], prefix['baseline_attempt_id'])
        return prefix
    first = api.read(root / 'results/reload-proof/save/rank-0.json')
    if not first: raise RuntimeError('Checkpoint1 save proof does not exist yet; apply refused')
    directory, _ = bp.find_prior_attempt(root, first['supervisor_attempt_id'], api)
    no_owned_processes(container['id'], first['supervisor_attempt_id'])
    recipe = read_old_recipe(container['id'], directory / 'train.yaml', root)
    prefix = bp.capture(root, api, recipe, check_process=True)
    # Preserve small and append-only evidence privately; model weights are never duplicated.
    archive = root / 'results/baseline-prefix/evidence'; archive.mkdir(parents=True, exist_ok=True, mode=0o700)
    for record in prefix['immutable_files'] + prefix['append_only_prefixes']:
        source = root / record['path']; target = archive / record['path']
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with source.open('rb') as incoming, target.open('wb') as outgoing:
            if 'prefix_bytes' in record:
                remaining = record['prefix_bytes']
                while remaining:
                    block = incoming.read(min(remaining, 1024 * 1024))
                    if not block: raise RuntimeError('Baseline prefix shortened during capture')
                    outgoing.write(block); remaining -= len(block)
            else: shutil.copyfileobj(incoming, outgoing)
            outgoing.flush(); os.fsync(outgoing.fileno())
        target.chmod(0o600)
    for relative in prefix['checkpoint_state_sha256']:
        target = archive / relative; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(root / relative, target); target.chmod(0o600)
    for kind, record in prefix['cumulative_budgets'].items():
        api.atomic(archive / f'budgets/{kind}.json', record['value'])
    api.atomic(existing, prefix)
    return prefix


def apply(root, manifest, prefix, note):
    if api.source_hashes(root) != prefix['baseline_source_sha256']:
        raise RuntimeError('Current source is not the archived baseline; this patch cannot be applied blindly')
    review_path = root / bp.REVIEW_REL
    if review_path.exists(): raise RuntimeError('A prior variant review exists; do not overwrite it implicitly')
    applied = []
    try:
        for name, item in manifest['files'].items():
            source = STAGE / 'after' / Path(name).name
            api.atomic(root / name, source.read_text(), mode=int(item['after_mode'], 8))
            applied.append(name)
        expected = dict(prefix['baseline_source_sha256'])
        for name, item in manifest['files'].items(): expected[name] = item['after_sha256']
        if api.source_hashes(root) != expected: raise RuntimeError('Applied source differs from reviewed exact-after map')
        review = {'schema_version': 1, 'run_id': api.RUN_ID, 'review_status': 'accepted',
            'operator_review_note': note, 'operator_confirmed_clean_stop': True,
            'baseline_prefix_sha256': api.sha(root / bp.PREFIX_REL),
            'baseline_attempt_id': prefix['baseline_attempt_id'], 'startup_timeout_seconds': 1800,
            'timing_variant': 'startup-budget-v2-1800', 'changed_source_files': manifest['files'],
            'staged_manifest_sha256': api.sha(STAGE / 'staged-manifest.json'),
            'model_data_objective_recipe_unchanged': True,
            'tokio_setting_changed': False, 'create_gate_changed': False,
            'timing_comparison_is_not_rl_speedup_evidence': True}
        api.atomic(root / bp.REVIEW_REL, review)
        # A consumed stop request is retained for audit, then removed only as part of explicit apply.
        stop = root / 'results/stop-after-checkpoint.request'
        if stop.exists():
            api.atomic(root / 'results/baseline-prefix/consumed-stop-request.json', api.read(stop))
            stop.unlink()
    except BaseException:
        review_path.unlink(missing_ok=True)
        for name in reversed(applied):
            item = manifest['files'][name]
            if item['before_sha256'] is None: (root / name).unlink(missing_ok=True)
            else: api.atomic(root / name, (STAGE / 'before' / Path(name).name).read_text(), mode=int(item['before_mode'], 8))
        raise
    return {'applied': True, 'changed_files': applied, 'baseline_prefix_sha256': review['baseline_prefix_sha256'],
            'startup_timeout_seconds': 1800, 'review_manifest': str(root / bp.REVIEW_REL)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    action = p.add_mutually_exclusive_group()
    action.add_argument('--capture-prefix-after-clean-stop', action='store_true')
    action.add_argument('--apply-after-clean-stop', action='store_true')
    p.add_argument('--review-note')
    args = p.parse_args(); root = args.root.resolve()
    manifest = checked_bundle(root)
    if not args.capture_prefix_after_clean_stop and not args.apply_after_clean_stop:
        print(json.dumps({'status': 'staged_only', 'files': manifest['files'], 'applied': False}, indent=2)); return 0
    if os.geteuid() != 0: p.error('Capture/apply needs root to inspect private old-process evidence; use sudo -n')
    if args.apply_after_clean_stop and not args.review_note: p.error('--review-note is required for explicit apply')
    with held_quiescence_locks(root):
        container = api.inspect_container(root)
        prefix = capture_prefix(root, container)
        if args.capture_prefix_after_clean_stop:
            result = {'captured': True, 'applied': False, 'baseline_attempt_id': prefix['baseline_attempt_id'],
                      'baseline_prefix_sha256': api.sha(root / bp.PREFIX_REL)}
        else: result = apply(root, manifest, prefix, args.review_note)
    print(json.dumps(result, indent=2)); return 0

if __name__ == '__main__': raise SystemExit(main())
