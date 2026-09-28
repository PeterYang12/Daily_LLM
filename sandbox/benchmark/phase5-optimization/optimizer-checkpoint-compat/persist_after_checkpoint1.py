#!/usr/bin/env python3
"""Persist the reviewed observer/scheduler fixes after R2's accepted checkpoint-1 hold."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/data2/yuhanya/e2b-rl-pathfinding-20260920-r2')
RUN = 'ua-e2b-pathfinding-20260920-r2'
SESSION = '20260920T184935Z-3c9aeb9d'
ATTEMPT = SESSION + '-attempt-01'
CONTAINER = '207252d9935048da3e3b94e01a6bdafcfd90533e581d65a4fdb8cbc0c2553983'
EXPECTED = {
    'runtime_observe.py': ('13cc5ca25ed1ce1d77dd6a0dae11d1b3509833e3b9b0748b5db36a43fb666f61',
                           '250764790ac26db7f0446827221cc33817b37e7fbd2a3e353cd9bb1b3839cb9e'),
    'configure.py': ('7b6cf6f8d09bd0baf514f6e103f88e0533d146b65b2bdd695405f167a02a83d4',
                     '165729ecbd8814ccd340fd97ee7c5e97aa8c704d1f2649c60c500f217ed49cd7'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(value, message):
    if not value:
        raise RuntimeError(message)


def write_new(path, data, mode=0o600):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    stage = Path(__file__).resolve().parent
    audit_path = ROOT / 'results/operator' / f'checkpoint1-prefix-{SESSION}.json'
    audit_bytes = audit_path.read_bytes()
    audit = json.loads(audit_bytes)
    require(audit.get('checkpoint1_prefix_valid') is True and audit.get('run_id') == RUN
            and audit.get('baseline_attempt_id') == ATTEMPT, 'Checkpoint-1 prefix has not been accepted')
    with contextlib.ExitStack() as stack:
        for relative in ('results/supervision/supervisor.lock', 'results/trainer.lock', 'results/launch.lock'):
            stream = stack.enter_context((ROOT / relative).open('rb'))
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        status = json.loads((ROOT / 'results/supervision' / SESSION / 'status.json').read_text())
        require(status.get('state') == 'paused' and status.get('exit_code') == 0, 'Original supervisor is not paused cleanly')
        require(json.loads((ROOT / 'results/latest-checkpoint.json').read_text())['global_step'] == 1, 'Checkpoint pointer changed')
        for evidence in audit['append_only_prefixes']:
            data = (ROOT / evidence['path']).read_bytes()
            require(len(data) == evidence['bytes'] and digest(data) == evidence['sha256'], 'Training evidence changed since prefix review')
        preserved = audit['immutable_evidence'] + list(audit['dataset_evidence'].values())
        preserved += [entry['file'] for entry in audit['budget_snapshots'].values()]
        preserved += [audit['checkpoint_dataloader'], audit['baseline_resolved_config_file'], audit['archived_stop_request']['file']]
        for evidence in preserved:
            path = (ROOT / evidence['path']).resolve()
            require(path.is_relative_to(ROOT), 'Preserved evidence leaves the experiment')
            data = path.read_bytes()
            require(len(data) == evidence['bytes'] and digest(data) == evidence['sha256'], 'Accepted evidence changed: ' + evidence['path'])
        for relative, evidence in audit['archived_checkpoint_metadata'].items():
            require(digest((ROOT / relative).read_bytes()) == evidence['sha256'], 'Checkpoint metadata changed: ' + relative)
        for relative, expected in audit['baseline_config_sha256'].items():
            require(digest((ROOT / relative).read_bytes()) == expected, 'Baseline configuration changed: ' + relative)
        for relative, expected in audit['baseline_source_sha256'].items():
            require(digest((ROOT / relative).read_bytes()) == expected, 'Frozen source changed: ' + relative)
        program = '''import json,os,sys
from pathlib import Path
token=b'UA_SUPERVISOR_ATTEMPT_ID='+sys.argv[1].encode();rows=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid():continue
 try:
  if token not in (p/'environ').read_bytes().split(b'\\0'):continue
  s=(p/'stat').read_text().rsplit(')',1)[1].split()
  if s[0] not in ('Z','X'):rows.append({'pid':int(p.name),'start_ticks':s[19],'state':s[0]})
 except (FileNotFoundError,ProcessLookupError):pass
print(json.dumps(rows))
'''
        response = subprocess.run(['docker', 'exec', '-i', CONTAINER, '/opt/venv/bin/python', '-', ATTEMPT],
                                  input=program, text=True, capture_output=True, timeout=30, check=True)
        processes = json.loads(response.stdout)
        require(processes == [], 'Residual owned training processes remain: ' + json.dumps(processes))
        prepared = {}
        for name, (before_sha, after_sha) in EXPECTED.items():
            before = (ROOT / 'code' / name).read_bytes()
            after = (stage / name.replace('.py', '.after.py')).read_bytes()
            require(digest(before) == before_sha and digest(after) == after_sha, 'Reviewed source hash differs: ' + name)
            compile(after, name, 'exec')
            prepared[name] = (before, after)
        stop_path = ROOT / 'results/stop-after-checkpoint.request'
        stop_bytes = stop_path.read_bytes()
        stop = json.loads(stop_bytes)
        require(stop.get('supervisor_session') == SESSION and stop.get('after_step') == 1, 'Checkpoint stop request owner differs')
        record = {'run_id': RUN, 'baseline_attempt_id': ATTEMPT, 'time_unix': time.time(),
                  'prefix_audit_sha256': digest(audit_bytes), 'residual_owned_processes': processes,
                  'source_changes': {name: {'before': hashes[0], 'after': hashes[1]} for name, hashes in EXPECTED.items()},
                  'cleared_stop_request': stop, 'applied': args.apply}
        if args.apply:
            destination = ROOT / 'results/operator/checkpoint1-persistence'
            destination.mkdir(mode=0o700)
            write_new(destination / 'stop-after-checkpoint.request', stop_bytes)
            for name, (before, after) in prepared.items():
                write_new(destination / (name + '.before'), before)
                write_new(destination / (name + '.after'), after)
                temporary = ROOT / 'code' / (name + '.reviewed-tmp')
                write_new(temporary, after, (ROOT / 'code' / name).stat().st_mode & 0o777)
                os.replace(temporary, ROOT / 'code' / name)
            sync_directory(ROOT / 'code')
            sync_directory(destination)
            sync_directory(destination.parent)
            require(stop_path.read_bytes() == stop_bytes, 'Checkpoint request changed during persistence')
            stop_path.unlink()
            write_new(destination / 'applied.json', (json.dumps(record, indent=2) + '\n').encode())
            sync_directory(destination)
            sync_directory(destination.parent)
            sync_directory(ROOT / 'results')
        print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
