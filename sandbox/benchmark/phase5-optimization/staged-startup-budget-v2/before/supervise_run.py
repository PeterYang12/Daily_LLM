#!/usr/bin/env python3
"""Host supervisor: checkpoint-1 acceptance, one verified resume, then 100 native steps.

Default invocation launches only after the operator's immutable ready contract.
Use --check to inspect prerequisites without starting any training or analysis.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid

RUN_ID = 'ua-e2b-pathfinding-20260920'
CONTAINER = 'yuhanya-e2b-rl-pathfinding-20260920'
IMAGE = 'sha256:1da23791184cb601e0e661dbcae6d689c3949cf041f9c3888f2574a56372ef52'
TERMINAL = {'completed', 'paused', 'failed', 'blocked', 'interrupted'}
TRANSITIONS = {
    'created': {'checking'},
    'checking': {'acceptance_running', 'resume_running', 'blocked', 'paused'},
    'acceptance_running': {'acceptance_validating', 'stop_pending', 'failed'},
    'acceptance_validating': {'resume_running', 'paused', 'failed'},
    'resume_running': {'running', 'final_validating', 'stop_pending', 'failed'},
    'running': {'final_validating', 'stop_pending', 'failed'},
    'stop_pending': {'paused', 'final_validating', 'failed'},
    'final_validating': {'completed', 'failed'},
}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic(path: Path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, str) else json.dumps(value, indent=2, default=str) + '\n'
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def read(path, default=None):
    try: return json.loads(Path(path).read_text())
    except FileNotFoundError: return default


def jsonlines(path):
    try:
        with Path(path).open() as stream:
            for line in stream:
                if line.strip(): yield json.loads(line)
    except FileNotFoundError: return


def process_start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


class SupervisorError(RuntimeError): pass


class RotatingLog:
    """Bounded private raw training logs; rotation/lost-prefix byte counts are explicit."""
    def __init__(self, directory, limit=32 * 1024 * 1024, keep=8):
        self.directory, self.limit, self.keep = directory, limit, keep
        self.total = self.rotations = self.evicted_bytes = 0
        self.path = directory / 'trainer-current.log'
        self.stream = self.path.open('ab', buffering=0); os.chmod(self.path, 0o600)
        self.size = self.path.stat().st_size

    def write(self, data):
        self.total += len(data)
        while data:
            if self.size >= self.limit:
                self.stream.close()
                oldest = self.directory / f'trainer-{self.keep - 1}.log'
                if oldest.exists(): self.evicted_bytes += oldest.stat().st_size; oldest.unlink()
                for number in range(self.keep - 2, 0, -1):
                    source = self.directory / f'trainer-{number}.log'
                    if source.exists(): source.replace(self.directory / f'trainer-{number + 1}.log')
                self.path.replace(self.directory / 'trainer-1.log')
                self.stream = self.path.open('wb', buffering=0); os.chmod(self.path, 0o600)
                self.size = 0; self.rotations += 1
            part, data = data[:self.limit - self.size], data[self.limit - self.size:]
            self.stream.write(part); self.size += len(part)

    def flush(self):
        os.fsync(self.stream.fileno())
        atomic(self.directory / 'log-retention.json', {'received_bytes': self.total,
            'rotation_count': self.rotations, 'evicted_prefix_bytes': self.evicted_bytes,
            'max_files': self.keep, 'max_bytes_per_file': self.limit})

    def close(self): self.flush(); self.stream.close()


def inspect_container(root):
    form = '{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},"mounts":{{json .Mounts}}}'
    result = subprocess.run(['docker', 'inspect', '--format', form, CONTAINER],
                            capture_output=True, text=True, timeout=30, check=True)
    container = json.loads(result.stdout)
    if not container['running'] or container['image'] != IMAGE:
        raise SupervisorError('Dedicated container is stopped or has a different image')
    mounts = {item['Destination']: item['Source'] for item in container['mounts']}
    if Path(mounts.get('/experiment', '/__missing__')).resolve() != root:
        raise SupervisorError('Dedicated container experiment mount differs from this root')
    return {'id': container['id'], 'image': container['image'], 'name': CONTAINER,
            'experiment_mount': str(root)}


def check_prerequisites(root):
    contract_path = root / 'configs/supervisor-prerequisites.json'
    contract = read(contract_path)
    if not contract or contract.get('ready') is not True or contract.get('run_id') != RUN_ID:
        raise SupervisorError('Missing root-reviewed ready contract: configs/supervisor-prerequisites.json')
    evidence = contract.get('evidence', {})
    required = {'phase3', 'phase5', 'capacity256', 'qualification'}
    if not required <= evidence.keys(): raise SupervisorError('Ready contract is missing required evidence labels')
    records = {}
    for label in sorted(required):
        item = evidence[label]
        path = Path(item['path'])
        if not path.is_absolute(): path = root / path
        if item.get('review_status') != 'accepted' or not path.is_file() or sha(path) != item.get('sha256'):
            raise SupervisorError(f'Prerequisite {label} is unreviewed, missing or changed')
        if label in {'phase3', 'phase5'} and item.get('cleanup_confirmed') is not True:
            raise SupervisorError(f'Prerequisite {label} lacks an explicit reviewed cleanup confirmation')
        records[label] = {'path': str(path), 'sha256': item['sha256'],
                          'review_status': item['review_status'], 'review_note': item.get('review_note', '')}
    capacity = read(Path(records['capacity256']['path']))
    if capacity.get('target') != 256 or capacity.get('remaining_ids') != []:
        raise SupervisorError('Capacity diagnostic must target256 and confirm zero remaining owned sandboxes')
    records['capacity256']['diagnostic_success'] = capacity.get('success')
    records['capacity256']['observed_simultaneous_running'] = capacity.get('observed_simultaneous_running')
    qualification = read(Path(records['qualification']['path']))
    manifest = read(root / 'configs/datasets-qualified.json')
    for split, filename in [('train', 'train.parquet'), ('val', 'val.parquet')]:
        if sha(root / 'data/qualified' / filename) != manifest[split]['sha256']:
            raise SupervisorError(f'Qualified {split} data changed after its frozen manifest')
    selected = set(manifest['train']['instance_ids']) | set(manifest['val']['instance_ids'])
    cases = qualification.get('cases', {})
    if len(selected) != 78 or any(cases.get(iid, {}).get('status') != 'passed' for iid in selected):
        raise SupervisorError('Every selected62/16 task must pass live reward/environment qualification')
    if qualification.get('status') != 'passed' or qualification.get('remaining_owned_sandbox_ids') != []:
        raise SupervisorError('Qualification reports incomplete cleanup')
    for iid in selected:
        if any(cases[iid].get('modes', {}).get(mode, {}).get('cleanup_ok') is not True
               for mode in ('baseline', 'oracle')):
            raise SupervisorError(f'Qualification task {iid} lacks successful cleanup for both controls')
    gpu = []
    for rank in range(8):
        health_path = root / f'results/gpu-health/gpu-health-rank-{rank}.json'
        mesh_path = root / f'results/gpu-mesh/rank-{rank}.json'
        health, mesh = read(health_path), read(mesh_path)
        if not health or health.get('rank') != rank or health.get('checks', 0) < 26 or not all(
                health.get(key) is True for key in ('matmul', 'all_reduce', 'all_to_all')):
            raise SupervisorError(f'GPU rank{rank} numerical/collective evidence is missing or failed')
        if health.get('allocator_config') != 'expandable_segments:False':
            raise SupervisorError(f'GPU rank{rank} allocator preflight differs from the launch')
        if (not mesh or mesh.get('rank') != rank or mesh.get('success') is not True
                or len(mesh['groups']['context']) != 4 or rank not in mesh['groups']['context']
                or set(mesh['groups']['expert']) != set(range(8))):
            raise SupervisorError(f'GPU rank{rank} CP4/EP8 evidence is missing or failed')
        gpu.append({'rank': rank, 'health_path': str(health_path), 'health_sha256': sha(health_path),
                    'checks': health['checks'], 'health_time_unix': health.get('time_unix'),
                    'mesh_path': str(mesh_path), 'mesh_sha256': sha(mesh_path)})
    return {'contract_sha256': sha(contract_path), 'evidence': records, 'gpu_ranks': gpu,
            'selected_train_tasks': 62, 'selected_eval_tasks': 16}


def source_hashes(root):
    result = {}
    for directory in (root / 'code', root / 'src'):
        for current, dirs, files in os.walk(directory):
            dirs[:] = [name for name in dirs if name not in {'.git', '__pycache__', 'node_modules'}]
            for name in files:
                path = Path(current) / name
                if path.suffix in {'.py', '.sh', '.yaml', '.yml', '.toml'} and not path.is_symlink():
                    result[str(path.relative_to(root))] = sha(path)
    return result


def checkpoint(root, expected=None):
    sys.path.insert(0, str(root / 'code'))
    os.environ['UA_EXPERIMENT_ROOT'] = str(root)
    from verify_checkpoint import inspect_checkpoint
    pointer = read(root / 'results/latest-checkpoint.json')
    if not pointer or pointer.get('run_id') != RUN_ID: raise SupervisorError('Missing run-owned checkpoint record')
    step = pointer.get('global_step')
    if expected is not None and step != expected: raise SupervisorError(f'Expected checkpoint{expected}, found{step}')
    folder = root / f'checkpoints/global_step_{step}'
    record = inspect_checkpoint(folder)
    if not record['complete'] or record['native_manifest_sha256'] != pointer.get('native_manifest_sha256'):
        raise SupervisorError('Checkpoint completion manifest or required payloads failed validation')
    if int((root / 'checkpoints/latest_checkpointed_iteration.txt').read_text().strip()) != step:
        raise SupervisorError('Native checkpoint pointer differs from completed record')
    return record


def reload_proof(root, phase, attempt_id=None, started=None):
    rows = []
    for rank in range(8):
        path = root / f'results/reload-proof/{phase}/rank-{rank}.json'
        row = read(path)
        if not row or row.get('global_step') != 1 or row.get('rank') != rank: return None
        if attempt_id and row.get('supervisor_attempt_id') != attempt_id: return None
        if started and row.get('time_unix', 0) < started: return None
        if not row.get('model_probes') or not row.get('scheduler_scalars'):
            raise SupervisorError(f'Rank{rank} checkpoint probe lacks model or scheduler evidence')
        if phase == 'load':
            saved = root / f'results/reload-proof/save/rank-{rank}.json'
            if row.get('matched') is not True or row.get('mismatches') or row.get('saved_evidence_sha256') != sha(saved):
                raise SupervisorError(f'Rank{rank} checkpoint reload probes do not match')
        rows.append({'rank': rank, 'sha256': sha(path), 'model_probe_count': len(row['model_probes']),
                     'optimizer_counter_count': len(row.get('optimizer', {}).get('step_counters', {})),
                     'optimizer_unavailable': row.get('optimizer', {}).get('unavailable', []),
                     'matched': row.get('matched'), 'time_unix': row.get('time_unix')})
    return rows


def metric_snapshot(root):
    rows = list(jsonlines(root / 'results/train-metrics.jsonl'))
    by_step, duplicates = {}, []
    for row in rows:
        data = row.get('data', {})
        step = data.get('training/global_step')
        if step is None: continue
        step = int(step)
        if step in by_step: duplicates.append(step)
        by_step[step] = data
    contiguous = 0
    while contiguous + 1 in by_step: contiguous += 1
    durations = []
    for step in sorted(by_step)[-5:]:
        data = by_step[step]
        value = data.get('timing_s/step')
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
            durations.append(value + float(data.get('timing_s/testing', 0) or 0))
    eta = ((100 - contiguous) * sum(durations) / len(durations)) if durations and contiguous < 100 else 0 if contiguous == 100 else None
    return {'completed_native_global_step': contiguous, 'observed_step_numbers': sorted(by_step),
            'duplicate_step_numbers': duplicates, 'remaining_seconds_estimate': eta,
            'eta_basis': 'Mean of last 5 completed native step timers, adding testing when present; setup/future faults unknown',
            'last_step_metrics': by_step.get(contiguous, {})}


def optimizer_audit(root, expected_steps, attempt_ids):
    result = {}
    for rank in range(8):
        rows = [row for row in jsonlines(root / f'results/optimizer/rank-{rank}.jsonl')
                if row.get('supervisor_attempt_id') in attempt_ids]
        by_step = collections.Counter()
        for row in rows:
            step = row.get('global_step')
            if step not in expected_steps or not row.get('successful') or not row.get('gradient_finite'):
                raise SupervisorError(f'Rank{rank} optimizer evidence has a missing step, failure or nonfinite gradient')
            by_step[int(step)] += 1
        if set(by_step) != set(expected_steps):
            raise SupervisorError(f'Rank{rank} does not have optimizer evidence for every accepted native step')
        result[str(rank)] = {'native_optimizer_calls': len(rows), 'calls_by_native_global_step': dict(by_step)}
    return result


def validate_evaluations(root, final=False):
    expected_steps = [0, 25, 50, 75, 100] if final else [0]
    manifest = read(root / 'configs/datasets-qualified.json')
    expected_ids = set(manifest['val']['instance_ids'])
    sessions = collections.defaultdict(list)
    for path in (root / 'results/sessions').glob('*/result.json'):
        row = read(path)
        if row.get('phase') == 'validation': sessions[row.get('global_step')].append(row)
    result = []
    for step in expected_steps:
        path = root / f'results/validation/{step}.jsonl'
        rows = list(jsonlines(path)); episode_rows = sessions.get(step, [])
        if len(rows) != 16 or any(row.get('step') != step for row in rows):
            raise SupervisorError(f'Evaluation step{step} does not have exactly16 native rows')
        if len(episode_rows) != 16 or {row.get('instance_id') for row in episode_rows} != expected_ids:
            raise SupervisorError(f'Evaluation step{step} does not cover each of the16 expected task IDs exactly once')
        by_uid = {}
        for row in episode_rows:
            if row.get('status') != 'completed' or row.get('extra_info', {}).get('eval_completed') is not True:
                raise SupervisorError(f'Evaluation step{step} has an unfinished or unverifiable session')
            key = (row.get('sample_uid'), int(row.get('rollout_index', -1)))
            if not key[0] or key in by_uid: raise SupervisorError('Missing or duplicate evaluation sample identity')
            by_uid[key] = row
        seen = set(); scores = []
        for row in rows:
            parts = str(row.get('uid', '')).rsplit('_', 2)
            if len(parts) != 3: raise SupervisorError('Native validation dump lacks its trajectory UID')
            key = (parts[0], int(parts[1])); episode = by_uid.get(key)
            score = row.get('score')
            if key in seen or episode is None or score != episode.get('reward'):
                raise SupervisorError(f'Evaluation step{step} trajectory/session/reward join failed')
            if not isinstance(score, (int, float)) or not math.isfinite(score) or score not in (0, 1):
                raise SupervisorError(f'Evaluation step{step} contains an invalid binary score')
            seen.add(key); scores.append(score)
        if seen != set(by_uid): raise SupervisorError('Validation sample coverage mismatch')
        result.append({'global_step': step, 'tasks': 16, 'resolved': int(sum(scores)), 'sha256': sha(path),
                       'task_session_reward_join': 'passed'})
    if final and (set(sessions) != set(expected_steps) or sum(map(len, sessions.values())) != 80):
        raise SupervisorError('Unexpected validation rounds or session count beyond the planned80')
    return result


class Supervisor:
    def __init__(self, args):
        self.args, self.root = args, args.root.resolve()
        self.session_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        self.directory = self.root / 'results/supervision' / self.session_id
        self.directory.mkdir(parents=True, mode=0o700)
        self.state = {'schema_version': 1, 'run_id': RUN_ID, 'session_id': self.session_id, 'state': 'created',
            'supervisor_pid': os.getpid(), 'supervisor_start_ticks': process_start(os.getpid()),
            'supervisor_cpu_affinity': sorted(os.sched_getaffinity(0)),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'started_utc': utc(), 'started_unix': time.time(), 'attempts': [], 'requested_native_global_steps': 100}
        self.stop_requested = False; self.stop_reason = None; self.analysis = None; self.last_analysis = 0
        self.active_attempt = None
        self.checks = None; self.container = None; self.metric_error = None
        for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, self.signal_stop)

    def signal_stop(self, signum, frame):
        self.stop_requested = True; self.stop_reason = signal.Signals(signum).name

    def transition(self, state, **fields):
        old = self.state['state']
        if state != old and state not in TRANSITIONS.get(old, set()):
            raise SupervisorError(f'Invalid supervisor transition: {old}->{state}')
        self.state.update(state=state, updated_utc=utc(), **fields)
        self.write_status()

    def write_status(self):
        try:
            metrics = metric_snapshot(self.root); self.state['progress'] = metrics; self.metric_error = None
        except (ValueError, OSError) as exc:
            self.metric_error = type(exc).__name__; self.state['metrics_read_error'] = self.metric_error
        self.state['heartbeat_utc'] = utc(); self.state['heartbeat_unix'] = time.time()
        self.state['latest_checkpoint'] = read(self.root / 'results/latest-checkpoint.json')
        atomic(self.directory / 'status.json', self.state)
        atomic(self.root / 'results/supervision/latest.json', {'session_id': self.session_id, 'status_file': str(self.directory / 'status.json')})
        progress = self.state.get('progress', {}); step = progress.get('completed_native_global_step', 0)
        cp = self.state.get('latest_checkpoint') or {}; eta = progress.get('remaining_seconds_estimate')
        eta_text = 'not yet estimable from completed steps' if eta is None else f'{eta / 3600:.1f} hours, based on completed-step timers'
        text = f'# RL execution status\n\nUpdated {utc()}. State: **{self.state["state"]}**.\n\n'
        text += f'Completed native trainer steps: **{step}/100**. Latest recorded checkpoint: **{cp.get("global_step", "none")}**. '
        text += 'Rollout and agent-turn counts are not training steps.\n\n'
        text += 'Training uses the explicit 62-task SWE-rebench subset and 16-task SWE-bench Verified subset. This is not a full 500-task score.\n\n'
        text += f'Estimated remaining time: {eta_text}. This estimate does not guarantee overnight completion.\n\n'
        if self.state.get('error'): text += f'Issue: {self.state["error"]}\n\n'
        if self.state.get('final_analysis_error'):
            text += f'Analysis issue: {self.state["final_analysis_error"]}\n\n'
        if self.state.get('latest_analysis'):
            text += f'Latest metadata analysis exit code: {self.state["latest_analysis"]["exit_code"]}.\n\n'
        if self.state.get('reload_acceptance'): text += 'Checkpoint1 model/scheduler and available optimizer probes matched after native reload on all 8 ranks.\n\n'
        if self.state.get('evaluations'):
            text += '| Evaluation step | Resolved | Tasks |\n|---|---:|---:|\n'
            for row in self.state['evaluations']: text += f'| {row["global_step"]} | {row["resolved"]} | {row["tasks"]} |\n'
            text += '\n'
        text += f'Run: `{RUN_ID}`. Private execution evidence: `{self.directory}`. '
        text += f'[W&B run](https://wandb.ai/yuhanya-amd/e2b-rl-pathfinding/runs/{RUN_ID}).\n'
        atomic(self.args.report, text, mode=0o644)

    def request_checkpoint_stop(self):
        path = self.root / 'results/stop-after-checkpoint.request'
        existing = read(path)
        if existing is not None: return
        cp = read(self.root / 'results/latest-checkpoint.json', {})
        atomic(path, {'after_step': max(1, int(cp.get('global_step', 0)) + 1),
                      'supervisor_session': self.session_id, 'reason': self.stop_reason or 'operator_request', 'time_utc': utc()})

    def paused(self):
        if self.state['state'] in {'acceptance_running', 'resume_running', 'running'}:
            self.transition('stop_pending', stop_reason=self.stop_reason)
        self.transition('paused')

    def terminate_owned_attempt(self, attempt_id, signum):
        # The token is public ownership metadata. No environment contents are printed.
        program = '''import json,os,signal,sys
from pathlib import Path
token=b'UA_SUPERVISOR_ATTEMPT_ID='+sys.argv[1].encode();sig=int(sys.argv[2]);rows=[]
for path in Path('/proc').iterdir():
 if not path.name.isdigit() or int(path.name)==os.getpid():continue
 try:
  before=(path/'stat').read_text().rsplit(')',1)[1].split()[19]
  if token not in (path/'environ').read_bytes().split(b'\\0'):continue
  descriptor=os.pidfd_open(int(path.name))
  try:
   if (path/'stat').read_text().rsplit(')',1)[1].split()[19]!=before:continue
   signal.pidfd_send_signal(descriptor,sig)
   rows.append({'pid':int(path.name),'start_ticks':before,'signal':sig})
  finally:os.close(descriptor)
 except (FileNotFoundError,ProcessLookupError,PermissionError):continue
print(json.dumps({'signalled_owned_processes':rows}))
'''
        command = ['docker', 'exec', '-i', self.container['id'], '/opt/venv/bin/python', '-', attempt_id, str(signum)]
        response = subprocess.run(command, input=program, text=True, capture_output=True, timeout=30)
        result = {'time_utc': utc(), 'signal': signum, 'docker_exit_code': response.returncode}
        if response.returncode == 0: result.update(json.loads(response.stdout))
        atomic(self.directory / f'owned-termination-{time.time_ns()}.json', result)
        return result

    def abort_active_after_error(self):
        if self.active_attempt is None: return
        attempt_id, proc = self.active_attempt
        errors = []
        for signum in (signal.SIGTERM, signal.SIGKILL):
            try: self.terminate_owned_attempt(attempt_id, signum)
            except Exception as exc: errors.append(type(exc).__name__ + ': ' + str(exc))
            if signum == signal.SIGTERM: time.sleep(2)
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)
        if errors: self.state['owned_stop_errors'] = errors

    def cleanup_owned_after_error(self):
        if self.active_attempt is None or self.container is None: return
        attempt_id, _ = self.active_attempt
        prior = read(self.root / 'results/cleanup.json')
        if prior is not None: atomic(self.directory / 'cleanup-before-supervisor.json', prior)
        env = {'UA_EXPERIMENT_ROOT': '/experiment', 'UA_RUN_ID': RUN_ID,
               'UA_SUPERVISOR_ATTEMPT_ID': attempt_id,
               'PYTHONPATH': '/experiment/code:/experiment/src/uni-agent:/experiment/src/uni-agent/verl'}
        command = self.docker_command(env, ['/opt/venv/bin/python', '/experiment/code/cleanup.py'])
        with (self.directory / 'supervisor-owned-cleanup.log').open('wb') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        self.state['supervisor_owned_cleanup'] = {'exit_code': result.returncode,
                                                  'result': read(self.root / 'results/cleanup.json')}

    def analyze_tick(self, force=False, allow_start=True):
        now = time.monotonic()
        if self.analysis:
            proc, started, output, log = self.analysis
            if proc.poll() is None and now - started > self.args.analysis_timeout:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
            if proc.poll() is not None:
                log.close()
                atomic(output / 'analysis-exit.json', {'exit_code': proc.returncode, 'ended_utc': utc()})
                self.state['latest_analysis'] = {'output': str(output), 'exit_code': proc.returncode}
                if proc.returncode == 0:
                    atomic(self.root / 'results/supervision/latest-analysis.json', self.state['latest_analysis'])
                    self.state['last_successful_analysis'] = str(output)
                self.analysis = None
                completed = sorted((self.directory / 'analysis').glob('run-*'))
                for old in completed[:-2]:
                    if str(old) != self.state.get('last_successful_analysis'): shutil.rmtree(old)
        if allow_start and self.analysis is None and (force or now - self.last_analysis >= self.args.analysis_interval):
            output = self.directory / 'analysis' / f'run-{time.time_ns()}'
            output.mkdir(parents=True, mode=0o700)
            log = (output / 'analyzer.log').open('wb'); os.chmod(output / 'analyzer.log', 0o600)
            command = [sys.executable, str(self.root / 'code/profile_analyze.py'), '--root', str(self.root),
                       '--output', str(output), '--no-plots', '--phase', 'train']
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            self.analysis = proc, now, output, log; self.last_analysis = now

    def finish_analysis(self):
        # Complete or terminate a prior analysis, then run one final snapshot only.
        for final in (False, True):
            if final: self.analyze_tick(force=True)
            deadline = time.monotonic() + self.args.analysis_timeout + 10
            while self.analysis and time.monotonic() < deadline:
                self.analyze_tick(allow_start=False)
                self.write_status(); time.sleep(min(5, self.args.poll_interval))
            if self.analysis:
                proc, _, output, log = self.analysis
                if proc.poll() is None:
                    proc.kill(); proc.wait(timeout=10)
                log.close(); self.analysis = None
                atomic(output / 'analysis-exit.json', {'exit_code': proc.returncode, 'forced_timeout': True})

    def environment(self, acceptance, attempt_id):
        return {'UA_EXPERIMENT_ROOT': '/experiment', 'UA_RUN_ID': RUN_ID, 'UA_DATA_VARIANT': 'qualified',
                'UA_PAUSE_AFTER_STEP': '1' if acceptance else '0', 'UA_RESUME_MODE': 'disable' if acceptance else 'auto',
                'UA_EVAL_BEFORE_TRAIN': '1' if acceptance else '0', 'UA_EVAL_ENABLED': '1', 'UA_EVAL_FREQ': '25',
                'UA_SAVE_FREQ': '1', 'UA_KEEP_CHECKPOINTS': '2', 'UA_PROFILE_SEPARATE_CREATE_GATE': '0',
                'UA_WARMUP_BATCHES': '0', 'UA_SUPERVISOR_ATTEMPT_ID': attempt_id,
                'WANDB_RUN_ID': RUN_ID, 'WANDB_RESUME': 'allow', 'WANDB_PROJECT': 'e2b-rl-pathfinding'}

    def docker_command(self, env, command):
        args = ['docker', 'exec']
        for key, value in sorted(env.items()): args += ['-e', f'{key}={value}']
        return args + [self.container['id']] + command

    def attempt(self, acceptance):
        number = len(self.state['attempts']) + 1
        attempt_id = self.session_id + f'-attempt-{number:02d}'
        directory = self.directory / f'attempt-{number:02d}'; directory.mkdir(mode=0o700)
        env = self.environment(acceptance, attempt_id)
        actual_container = inspect_container(self.root)
        if actual_container['id'] != self.container['id']: raise SupervisorError('Dedicated container identity changed')
        prepare = self.docker_command(env, ['/opt/venv/bin/python', '/experiment/code/configure.py',
                    '--root', '/experiment', '--runtime-root', '/experiment', '--data-variant', 'qualified'])
        with (directory / 'configure.log').open('wb') as log:
            prepared = subprocess.run(prepare, stdout=log, stderr=subprocess.STDOUT, timeout=90)
        if prepared.returncode: raise SupervisorError('Attempt configuration preparation failed')
        files = {str(p.relative_to(self.root)): sha(p) for p in (self.root / 'configs').glob('*') if p.is_file()}
        snapshot = {'attempt_id': attempt_id, 'created_utc': utc(), 'source_sha256': source_hashes(self.root),
                    'config_sha256': files, 'public_launch_settings': env, 'container': self.container}
        atomic(directory / 'source-and-config.json', snapshot)
        for name in ('train.yaml', 'task_config.yaml', 'training-plan.json', 'source-provenance.json'):
            shutil.copyfile(self.root / 'configs' / name, directory / name); os.chmod(directory / name, 0o600)
        command = self.docker_command(env, ['bash', '/experiment/code/launch.sh'])
        started = time.time(); log = RotatingLog(directory)
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        self.active_attempt = attempt_id, proc
        os.set_blocking(proc.stdout.fileno(), False)
        selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
        item = {'attempt_id': attempt_id, 'kind': 'checkpoint1_acceptance' if acceptance else 'owned_resume',
                'started_unix': started, 'started_utc': utc(), 'docker_client_pid': proc.pid,
                'docker_client_start_ticks': process_start(proc.pid), 'directory': str(directory), 'exit_code': None}
        self.state['attempts'].append(item)
        self.transition('acceptance_running' if acceptance else 'resume_running')
        heartbeat = 0; fault_tail = b''; eof = False
        fatal_error = None; fatal_since = None; termination_stage = 0; stage_time = None
        try:
            while proc.poll() is None or not eof:
                try:
                    for key, _ in selector.select(timeout=self.args.poll_interval):
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj); eof = True; continue
                        log.write(data)
                        diagnostic = (fault_tail + data).lower(); fault_tail = diagnostic[-256:]
                        if any(token in diagnostic for token in (b'hw exception by gpu', b'uncorrectable errevent', b'gpu hang')):
                            item['gpu_fault_observed'] = True
                    if self.stop_requested:
                        self.request_checkpoint_stop()
                        if self.state['state'] != 'stop_pending': self.transition('stop_pending', stop_reason=self.stop_reason)
                    if not acceptance and 'reload_acceptance' not in self.state and fatal_error is None:
                        proof = reload_proof(self.root, 'load', attempt_id, started)
                        if proof:
                            self.state['reload_acceptance'] = proof
                            atomic(directory / 'reload-acceptance.json', proof)
                            if not self.stop_requested: self.transition('running')
                    if time.monotonic() - heartbeat >= self.args.heartbeat_interval:
                        self.write_status(); log.flush(); heartbeat = time.monotonic()
                    self.analyze_tick()
                except Exception as exc:
                    if fatal_error is None:
                        fatal_error = exc; fatal_since = time.monotonic()
                        item['supervisor_error'] = type(exc).__name__ + ': ' + str(exc)
                        self.stop_requested = True; self.stop_reason = 'supervisor_failure'
                        try: self.request_checkpoint_stop()
                        except Exception: pass
                    # Keep draining/logging and owning the child while requesting a checkpoint stop.
                    time.sleep(min(1, self.args.poll_interval))
                now = time.monotonic()
                if fatal_error is not None and proc.poll() is None:
                    if termination_stage == 0 and now - fatal_since >= self.args.fatal_stop_grace:
                        self.terminate_owned_attempt(attempt_id, signal.SIGTERM)
                        termination_stage = 1; stage_time = now
                    elif termination_stage == 1 and now - stage_time >= 15:
                        self.terminate_owned_attempt(attempt_id, signal.SIGKILL)
                        termination_stage = 2; stage_time = now
                    elif termination_stage == 2 and now - stage_time >= 15:
                        # The container-owned token processes were signalled first. Reap only this CLI.
                        proc.terminate()
                        try: proc.wait(timeout=10)
                        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)
                if proc.poll() is not None and not selector.get_map(): eof = True
            proc.wait()
        except BaseException:
            # Never detach from an owned running trainer on an unexpected supervisor exception.
            if proc.poll() is None:
                self.terminate_owned_attempt(attempt_id, signal.SIGTERM)
                time.sleep(2)
                self.terminate_owned_attempt(attempt_id, signal.SIGKILL)
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)
            raise
        finally:
            selector.close(); proc.stdout.close(); log.close()
        item.update(exit_code=proc.returncode, ended_unix=time.time(), ended_utc=utc())
        outcome = read(self.root / 'results/main-exit.json', {})
        item['outcome'] = outcome
        atomic(directory / 'trainer-outcome.json', outcome)
        self.write_status()
        if fatal_error is not None:
            raise SupervisorError('Supervisor failure stopped the owned attempt: ' + str(fatal_error))
        if proc.returncode != 0 or outcome.get('supervisor_attempt_id') != attempt_id:
            raise SupervisorError(f'Trainer attempt exited{proc.returncode}; no automatic retry. Inspect its private logs')
        # Raw agent/tool output is untrusted; a text marker alone cannot override native completion.
        if outcome.get('status') == 'failed':
            raise SupervisorError('Trainer/GPU failure recorded; operator diagnosis required, no automatic restart')
        cleanup = read(self.root / 'results/cleanup.json', {})
        if cleanup.get('remaining') != [] or cleanup.get('errors') != []:
            raise SupervisorError('Trainer did not confirm complete run-owned sandbox cleanup')
        atomic(directory / 'cleanup.json', cleanup)
        return item, outcome, snapshot

    def execute(self):
        self.transition('checking')
        self.checks = check_prerequisites(self.root); self.container = inspect_container(self.root)
        self.state['prerequisites'] = self.checks; self.state['container'] = self.container
        atomic(self.directory / 'prerequisites.json', self.checks)
        if self.stop_requested: self.paused(); return 0
        if not self.args.resume_owned:
            if (self.root / 'results/train-metrics.jsonl').exists() or (self.root / 'results/latest-checkpoint.json').exists():
                raise SupervisorError('Existing training evidence requires explicit --resume-owned; fresh start refused')
            item, outcome, first_snapshot = self.attempt(True)
            if self.stop_requested:
                checkpoint(self.root); self.paused(); return 0
            self.transition('acceptance_validating')
            if outcome.get('status') != 'paused_after_checkpoint': raise SupervisorError('Acceptance did not pause cleanly')
            saved = checkpoint(self.root, 1)
            if metric_snapshot(self.root)['completed_native_global_step'] != 1:
                raise SupervisorError('Checkpoint1 lacks completed native step1 metrics')
            proof = reload_proof(self.root, 'save', item['attempt_id'], item['started_unix'])
            if not proof: raise SupervisorError('Checkpoint1 lacks all8 saved rank fingerprints')
            first_optimizer = optimizer_audit(self.root, [1], {item['attempt_id']})
            self.state['evaluations'] = validate_evaluations(self.root)
            atomic(self.directory / 'checkpoint1-acceptance.json', {'checkpoint': saved, 'saved_rank_proofs': proof,
                                                                   'evaluations': self.state['evaluations'], 'optimizer': first_optimizer})
            if source_hashes(self.root) != first_snapshot['source_sha256']:
                raise SupervisorError('Source changed during acceptance; controlled automatic resume refused')
            current_checks = check_prerequisites(self.root)
            if current_checks != self.checks:
                raise SupervisorError('Reviewed prerequisites changed during acceptance; automatic resume refused')
        else:
            checkpoint(self.root, 1)
            if not reload_proof(self.root, 'save'): raise SupervisorError('Explicit resume lacks checkpoint1 saved proofs')
        request = self.root / 'results/stop-after-checkpoint.request'
        if request.exists(): raise SupervisorError('Remove reviewed stop-after-checkpoint.request before resuming')
        if self.stop_requested: self.paused(); return 0
        item, outcome, _ = self.attempt(False)
        if self.stop_requested and outcome.get('status') == 'paused_after_checkpoint':
            checkpoint(self.root); self.paused(); return 0
        self.transition('final_validating')
        if outcome.get('status') != 'completed': raise SupervisorError('Resumed trainer did not report target completion')
        final = checkpoint(self.root, 100)
        metrics = metric_snapshot(self.root)
        if metrics['observed_step_numbers'] != list(range(1, 101)) or metrics['duplicate_step_numbers']:
            raise SupervisorError('Native training metrics are not exactly contiguous steps1..100')
        proof = reload_proof(self.root, 'load', item['attempt_id'], item['started_unix'])
        if not proof: raise SupervisorError('Final acceptance lacks all8 matching checkpoint1 reload proofs')
        first_attempt = read(self.root / 'results/reload-proof/save/rank-0.json')['supervisor_attempt_id']
        optimizer = optimizer_audit(self.root, list(range(1, 101)), {first_attempt, item['attempt_id']})
        self.state['evaluations'] = validate_evaluations(self.root, final=True)
        self.state['final_acceptance'] = {'checkpoint': final, 'native_training_steps': 100,
                                         'reload_proof': proof, 'optimizer': optimizer, 'evaluations': self.state['evaluations']}
        self.transition('completed'); return 0

    def run(self):
        code = 1
        try:
            code = self.execute()
        except (Exception, KeyboardInterrupt) as exc:
            self.abort_active_after_error()
            try: self.cleanup_owned_after_error()
            except Exception as cleanup_error:
                self.state['supervisor_owned_cleanup_error'] = type(cleanup_error).__name__ + ': ' + str(cleanup_error)
            target = 'blocked' if self.state['state'] == 'checking' else 'failed'
            if self.state['state'] not in TERMINAL:
                self.transition(target, error=f'{type(exc).__name__}: {exc}')
            code = 2 if target == 'blocked' else 1
        finally:
            try:
                self.finish_analysis()
            except Exception as exc:
                self.state['final_analysis_error'] = type(exc).__name__ + ': ' + str(exc)
                if self.analysis:
                    proc, _, _, log = self.analysis
                    if proc.poll() is None: proc.kill(); proc.wait(timeout=10)
                    log.close(); self.analysis = None
            self.state['ended_utc'] = utc(); self.state['exit_code'] = code; self.write_status()
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report', type=Path, default=Path('/home/yuhanya/Daily_LLM/sandbox/benchmark/phase4-rl/STATUS.md'))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--resume-owned', action='store_true', help='Explicit operator recovery; never used automatically after failure')
    parser.add_argument('--no-elevate', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--poll-interval', type=float, default=5)
    parser.add_argument('--heartbeat-interval', type=float, default=15)
    parser.add_argument('--analysis-interval', type=float, default=600)
    parser.add_argument('--analysis-timeout', type=float, default=120)
    parser.add_argument('--fatal-stop-grace', type=float, default=120)
    args = parser.parse_args()
    if not 1 <= args.poll_interval <= 60 or not 5 <= args.heartbeat_interval <= 60:
        parser.error('Polling must be1..60 seconds and heartbeat5..60 seconds')
    if args.analysis_interval < 60 or not 10 <= args.analysis_timeout <= 600 or args.fatal_stop_grace < 10:
        parser.error('Analysis interval>=60, analysis timeout10..600, fatal-stop grace>=10 seconds required')
    if os.geteuid() != 0 and not args.no_elevate:
        # Required because the dedicated container writes mode0600 root-owned artifacts.
        os.execvp('sudo', ['sudo', '-n', sys.executable, '-u', str(Path(__file__).resolve()), *sys.argv[1:], '--no-elevate'])
    os.umask(0o077)
    affinity = os.sched_getaffinity(0) & set(range(192))
    if not affinity: raise SystemExit('No permitted CPU in the training-side0–191 allocation')
    os.sched_setaffinity(0, affinity)
    if args.check:
        try:
            print(json.dumps({'prerequisites': check_prerequisites(args.root.resolve()),
                              'container': inspect_container(args.root.resolve())}, indent=2))
            return 0
        except Exception as exc:
            print(json.dumps({'ready': False, 'error_type': type(exc).__name__, 'error': str(exc)})); return 2
    directory = args.root / 'results/supervision'; directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'supervisor.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('This experiment already has a host supervisor')
        return Supervisor(args).run()


if __name__ == '__main__':
    raise SystemExit(main())
