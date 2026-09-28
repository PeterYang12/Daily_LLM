"""Supplementary four-process load-generator headroom check.

Imports the existing runner unchanged. Each process owns C=16/N=16 exec_true
work and its own run ID. Provisioning is serialized; a common host-monotonic
barrier starts all measurement windows. Only exact, predeclared cohort members
are allowed by the guard. The coordinator reconciles only those members.

This scenario is named separately from the ordinary single-process curves.
Use --dry-run for a request-free plan. No service calls occur at import time.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from e2b import AsyncSandbox, SandboxQuery, SandboxState


RUNNER_PATH = Path(__file__).with_name('runner.py')
SAMPLER_PATH = Path(__file__).resolve().parents[2] / 'phase0-environment/sample_resources.py'
RUNNER_SHA256 = hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('frozen_service_runner', RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
GENERATOR_COUNT = 4
PER_GENERATOR_CONCURRENCY = 16
PER_GENERATOR_RESIDENTS = 16


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(temp, 0o600)
    temp.replace(path)


def member_ids(cohort_id):
    return [f'{cohort_id}-g{index}' for index in range(GENERATOR_COUNT)]


def belongs(metadata, cohort_id, members):
    metadata = metadata or {}
    return (metadata.get('purpose') == runner.PURPOSE
            and metadata.get('cohort_id') == cohort_id
            and metadata.get('run_id') in members)


def sampler_command(output_dir, pids):
    command = [sys.executable, str(SAMPLER_PATH), '--output', str(Path(output_dir)/'resources.jsonl'),
               '--interval', '2', '--samples', '120',
               '--stop-file', str(Path(output_dir)/'sample.stop')]
    for pid in pids:
        command.extend(['--pid', str(pid)])
    return command


async def stop_sampler(process, output_dir):
    """Use the sampler's stop protocol first; terminate only this child if stuck."""
    (Path(output_dir)/'sample.stop').write_text('stop\n')
    try:
        await asyncio.wait_for(process.wait(), timeout=15)
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
    return process.returncode


async def all_sandboxes(config):
    page = AsyncSandbox.list(query=SandboxQuery(state=[SandboxState.RUNNING, SandboxState.PAUSED]),
                             limit=100, request_timeout=20, retries=0, **config)
    result = []
    while page.has_next:
        result.extend(await page.next_items())
    return result


async def memory_guard():
    process = await asyncio.create_subprocess_exec(
        '/data2/yuhanya/e2b-local-20260916/code/vm-ssh',
        "awk '/MemAvailable/ {print $2}' /proc/meminfo",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(process.communicate(), 20)
    if process.returncode or int(out.strip()) < 128 * 1024 * 1024:
        raise runner.ForeignWorkload('VM memory floor or memory check failure')


class CohortBench(runner.Bench):
    def __init__(self, args):
        self.cohort_id = args.cohort_id
        self.worker_index = args.worker_index
        self.coordination_dir = Path(args.output_dir)
        self.members = frozenset(member_ids(self.cohort_id))
        self.abort_path = self.coordination_dir / 'abort.json'
        self.barrier_target = None
        self.coordinator_pid = os.getppid()
        self.prepare_deadline = time.monotonic() + args.prepare_timeout
        inherited = argparse.Namespace(
            adapter=args.adapter, phase=args.phase, scenario='exec_true',
            template=args.template, concurrency=PER_GENERATOR_CONCURRENCY,
            residents=PER_GENERATOR_RESIDENTS, count=1, duration=args.duration,
            repeat=args.repeat, bytes=0, mode='closed_loop', rate=1,
            run_id=member_ids(self.cohort_id)[self.worker_index],
            output=str(self.coordination_dir / f'worker-{self.worker_index}.jsonl'))
        super().__init__(inherited)
        self.metadata['cohort_id'] = self.cohort_id
        self.base.update(scenario='exec_true_multigenerator',
                         comparison_key='exec_true-four-processes-v1')

    def write(self, kind, **data):
        extra = {
            'cohort_id': self.cohort_id, 'generator_index': self.worker_index,
            'generator_count': GENERATOR_COUNT, 'total_concurrency': 64,
            'total_resident_count': 64,
            'per_generator_concurrency': PER_GENERATOR_CONCURRENCY,
            'per_generator_resident_count': PER_GENERATOR_RESIDENTS,
            'generator_origin_monotonic_s': self.started,
            'frozen_runner_sha256': RUNNER_SHA256,
        }
        if self.barrier_target is not None:
            extra['cohort_target_monotonic_s'] = self.barrier_target
        # Override the initial metadata record too, before __init__ returns.
        data.update(extra)
        self.base['scenario'] = 'exec_true_multigenerator'
        self.base['comparison_key'] = 'exec_true-four-processes-v1'
        super().write(kind, **data)

    async def guard(self):
        if os.getppid() != self.coordinator_pid:
            raise runner.ForeignWorkload('Cohort coordinator exited')
        if self.abort_path.exists():
            raise runner.ForeignWorkload('Cohort coordinator requested an abort')
        rows = await self.list_all()
        if any(not belongs(info.metadata, self.cohort_id, self.members) for info in rows):
            raise runner.ForeignWorkload('Sandbox outside this exact cohort found')
        await memory_guard()

    async def prepare(self):
        # Serialize SDK creates to avoid changing the node's three-start limit.
        lock = (self.coordination_dir / 'prepare.lock').open('a')
        held = False
        try:
            while not held:
                if self.stop.is_set() or self.abort_path.exists():
                    raise runner.ForeignWorkload('Stopped before cohort preparation')
                if time.monotonic() >= self.prepare_deadline:
                    raise TimeoutError('Worker preparation deadline exceeded')
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                except BlockingIOError:
                    await asyncio.sleep(.1)
            await super().prepare()
        finally:
            if held:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        atomic_json(self.coordination_dir / f'ready-{self.worker_index}.json', {
            'cohort_id': self.cohort_id, 'run_id': self.run_id,
            'generator_index': self.worker_index, 'resident_count': len(self.handles),
            'pid': os.getpid(), 'prepared_utc': utc_now(),
            'frozen_runner_sha256': RUNNER_SHA256})
        release = self.coordination_dir / 'release.json'
        while not release.exists():
            if self.stop.is_set() or self.abort_path.exists():
                raise runner.ForeignWorkload('Stopped while waiting for cohort barrier')
            if time.monotonic() >= self.prepare_deadline:
                raise TimeoutError('Worker shared-barrier deadline exceeded')
            await asyncio.sleep(.02)
        barrier = json.loads(release.read_text())
        if barrier['cohort_id'] != self.cohort_id:
            raise runner.InvalidResult('Cohort barrier identity mismatch')
        self.barrier_target = barrier['target_monotonic_s']
        while time.monotonic() < self.barrier_target:
            if self.stop.is_set() or self.abort_path.exists():
                raise runner.ForeignWorkload('Stopped before cohort barrier release')
            await asyncio.sleep(min(.02, self.barrier_target - time.monotonic()))
        self.write('barrier', window='setup', start_offset_s=self.offset(),
                   release_lateness_ms=max(0, time.monotonic()-self.barrier_target)*1000)
        # Base.run starts its unchanged closed-loop measurement immediately next.


def summarize_workers(output_dir, duration):
    """Pool terminal samples only inside the intersection of all four windows."""
    runs = []
    operations = []
    for index in range(GENERATOR_COUNT):
        path = Path(output_dir) / f'worker-{index}.jsonl'
        if not path.exists():
            return {'valid': False, 'reason': 'missing worker file', 'worker_index': index}
        final = None
        worker_ops = []
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record['record_type'] == 'run':
                final = record
            elif record['record_type'] == 'operation' and record.get('window') == 'measurement':
                worker_ops.append(record)
        if final is None:
            return {'valid': False, 'reason': 'missing worker final run record', 'worker_index': index}
        runs.append(final)
        operations.extend(worker_ops)
    starts = [r['generator_origin_monotonic_s'] + r['measurement_start_offset_s'] for r in runs]
    ends = [r['generator_origin_monotonic_s'] + r['measurement_end_offset_s'] for r in runs]
    common_start, common_end = max(starts), min(ends)
    seconds = max(0, common_end-common_start)
    included = [row for row in operations
                if row['generator_origin_monotonic_s'] + row['start_offset_s'] >= common_start
                and row['generator_origin_monotonic_s'] + row['end_offset_s'] <= common_end]
    success = [row for row in included if row['status'] == 'success']
    latencies = sorted(row['duration_ms'] for row in success)
    def percentile(p):
        if not latencies:
            return None
        at = (len(latencies)-1)*p
        low = int(at)
        high = min(low+1, len(latencies)-1)
        return latencies[low] + (latencies[high]-latencies[low])*(at-low)
    counts = {status: sum(row['status'] == status for row in included)
              for status in sorted({row['status'] for row in included})}
    valid = (all(r.get('cleanup_ok') and r.get('stop_reason') == 'completed' for r in runs)
             and seconds >= duration - .5 and max(starts)-min(starts) <= .1)
    return {
        'valid': valid, 'reason': None if valid else 'incomplete/failed worker or misaligned window',
        'common_start_monotonic_s': common_start, 'common_end_monotonic_s': common_end,
        'common_window_seconds': seconds, 'start_spread_ms': (max(starts)-min(starts))*1000,
        'completed_count': len(included), 'successful_count': len(success),
        'status_counts': counts, 'successful_ops_per_s': len(success)/seconds if seconds else None,
        'all_terminal_ops_per_s': len(included)/seconds if seconds else None,
        'pooled_latency_ms': {'n': len(latencies), 'p50': percentile(.5),
                              'p95': percentile(.95), 'p99': percentile(.99)},
        'boundary_excluded_operations': len(operations)-len(included),
        'count_scope': 'operations whose start and end are both within the four-worker window intersection',
        'worker_runs': [{k: run.get(k) for k in ('run_id','generator_index','scheduled_count',
            'completed_count','successful_count','cleanup_ok','stop_reason','event_loop_lag_ms')}
            for run in runs],
    }


async def reconcile(config, cohort_id, members, uncertain):
    """Never delete another cohort, even when it has the benchmark purpose."""
    began = time.monotonic()
    minimum_end = began + (150 if uncertain else 0)
    deadline = began + (180 if uncertain else 30)
    empty_count = 0
    removed = set()
    last_count = None
    foreign_count = None
    while time.monotonic() < deadline:
        rows = await all_sandboxes(config)
        owned = [row for row in rows if belongs(row.metadata, cohort_id, members)]
        foreign_count = len(rows)-len(owned)
        last_count = len(owned)
        if not owned:
            empty_count += 1
            if empty_count >= 2 and time.monotonic() >= minimum_end:
                return {'cleanup_ok': True, 'owned_remaining': 0,
                        'coordinator_removed_count': len(removed), 'foreign_count_after': foreign_count}
        else:
            empty_count = 0
            for row in owned:
                await AsyncSandbox.kill(row.sandbox_id, request_timeout=30, retries=0, **config)
                removed.add(row.sandbox_id)
        await asyncio.sleep(1)
    return {'cleanup_ok': False, 'owned_remaining': last_count,
            'coordinator_removed_count': len(removed), 'foreign_count_after': foreign_count}


async def coordinator(args):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(output_dir, 0o700)
    cohort_id = args.cohort_id or 'multigen-' + uuid.uuid4().hex
    members = member_ids(cohort_id)
    values = json.loads(runner.CONFIG.read_text())
    config = {k: values[k] for k in ('api_key', 'api_url', 'sandbox_url')}
    report = {'record_type': 'cohort', 'cohort_id': cohort_id, 'adapter': args.adapter,
              'phase': args.phase, 'scenario': 'exec_true_multigenerator',
              'generator_count': 4, 'total_concurrency': 64, 'total_resident_count': 64,
              'per_generator_concurrency': 16, 'per_generator_resident_count': 16,
              'duration_seconds': args.duration, 'repeat': args.repeat,
              'template': args.template, 'memory_mib': 4096, 'page_mode': '4KiB',
              'runtime_version': 'local-4k-512-20260920', 'endpoint': config['api_url'],
              'frozen_runner_sha256': RUNNER_SHA256, 'started_utc': utc_now(),
              'member_run_ids': members, 'status': 'precheck',
              'sample_resources': args.sample_resources}
    atomic_json(output_dir/'cohort.json', report)
    processes = []
    handles = []
    sampler = None
    sampler_log = None
    problem = None
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, cancel.set)
    try:
        if await all_sandboxes(config):
            raise runner.ForeignWorkload('Coordinator requires an empty running/paused sandbox list')
        await memory_guard()
        report['status'] = 'preparing'
        atomic_json(output_dir/'cohort.json', report)
        for index in range(GENERATOR_COUNT):
            log = (output_dir/f'worker-{index}.stdout.log').open('x')
            handles.append(log)
            command = [sys.executable, str(Path(__file__).resolve()), '--adapter', args.adapter,
                '--phase', args.phase, '--template', args.template, '--duration', str(args.duration),
                '--repeat', str(args.repeat), '--output-dir', str(output_dir),
                '--cohort-id', cohort_id, '--worker-index', str(index),
                '--expected-runner-sha256', RUNNER_SHA256]
            processes.append(await asyncio.create_subprocess_exec(*command, stdout=log,
                                                                   stderr=asyncio.subprocess.STDOUT))
        deadline = time.monotonic() + args.prepare_timeout
        while True:
            if cancel.is_set():
                raise RuntimeError('coordinator cancelled')
            if any(process.returncode is not None for process in processes):
                raise RuntimeError('worker exited before the shared start barrier')
            ready_paths = [output_dir/f'ready-{index}.json' for index in range(GENERATOR_COUNT)]
            if all(path.exists() for path in ready_paths):
                readiness = [json.loads(path.read_text()) for path in ready_paths]
                if any(row['run_id'] != members[index] or row['cohort_id'] != cohort_id
                       or row['resident_count'] != 16 or row['frozen_runner_sha256'] != RUNNER_SHA256
                       or row['pid'] != processes[index].pid
                       for index, row in enumerate(readiness)):
                    raise RuntimeError('invalid worker readiness record')
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('cohort preparation timed out')
            await asyncio.sleep(.1)
        if args.sample_resources:
            pids = [row['pid'] for row in readiness]
            sampler_log = (output_dir/'sampler.stdout.log').open('x')
            sampler = await asyncio.create_subprocess_exec(*sampler_command(output_dir, pids),
                stdout=sampler_log, stderr=asyncio.subprocess.STDOUT)
            report['resources'] = {
                'path': str(output_dir/'resources.jsonl'), 'host_pids': pids,
                'interval_seconds': 2, 'requested_samples': 120,
                'sampler_pid': sampler.pid, 'started_utc': utc_now(),
                'sampler_sha256': hashlib.sha256(SAMPLER_PATH.read_bytes()).hexdigest()}
            # Metadata is flushed after the sampler captures its initial host
            # counters, so the four-generator CPU baseline precedes the barrier.
            deadline = time.monotonic() + 20
            while True:
                if cancel.is_set():
                    raise RuntimeError('coordinator cancelled before sampling started')
                if sampler.returncode is not None:
                    raise RuntimeError('resource sampler exited before the measurement barrier')
                path = output_dir/'resources.jsonl'
                if path.exists():
                    lines = path.read_text().splitlines()
                    if lines:
                        try:
                            metadata = json.loads(lines[0])
                        except json.JSONDecodeError:
                            metadata = None
                        if metadata and metadata.get('host_pids') == pids:
                            break
                if time.monotonic() >= deadline:
                    raise TimeoutError('resource sampler did not initialize')
                await asyncio.sleep(.05)
        target = time.monotonic() + 1
        report.update(status='measuring', measurement_target_monotonic_s=target,
                      prepared_workers=readiness)
        atomic_json(output_dir/'cohort.json', report)
        atomic_json(output_dir/'release.json', {'cohort_id': cohort_id, 'target_monotonic_s': target})
        print(json.dumps({'status': 'barrier_released', 'cohort_id': cohort_id,
                          'generator_count': 4, 'total_concurrency': 64}), flush=True)
        deadline = target + args.duration + 180
        while any(process.returncode is None for process in processes):
            if cancel.is_set():
                raise RuntimeError('coordinator cancelled')
            if any(process.returncode not in (None, 0) for process in processes):
                raise RuntimeError('worker failed during measurement or cleanup')
            if sampler is not None and sampler.returncode is not None:
                raise RuntimeError('resource sampler exited before workers completed')
            if time.monotonic() >= deadline:
                raise TimeoutError('worker measurement/drain deadline exceeded')
            await asyncio.sleep(.1)
        if any(process.returncode != 0 for process in processes):
            raise RuntimeError('worker failed')
    except BaseException as error:
        problem = type(error).__name__ + ': ' + str(error)
        atomic_json(output_dir/'abort.json', {'cohort_id': cohort_id, 'reason': problem, 'utc': utc_now()})
    finally:
        if problem:
            for process in processes:
                if process.returncode is None:
                    process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.gather(*(p.wait() for p in processes)), timeout=180)
            except asyncio.TimeoutError:
                for process in processes:
                    if process.returncode is None:
                        process.kill()
                await asyncio.gather(*(p.wait() for p in processes))
        if sampler is not None:
            try:
                report['resources']['exit_code'] = await stop_sampler(sampler, output_dir)
                resource_rows = [json.loads(line) for line in (output_dir/'resources.jsonl').read_text().splitlines()]
                completions = [row for row in resource_rows if row.get('record_type') == 'completion']
                sample_count = sum(row.get('record_type') == 'sample' for row in resource_rows)
                report['resources'].update(sample_count=sample_count,
                    completion_status=completions[-1].get('status') if completions else None,
                    valid=(sampler.returncode == 0 and bool(completions)
                           and completions[-1].get('status') in ('complete', 'stopped')
                           and sample_count > 0))
            except BaseException as error:
                report.setdefault('resources', {}).update(valid=False, error_class=type(error).__name__)
            finally:
                if sampler_log is not None:
                    sampler_log.close()
        elif sampler_log is not None:
            sampler_log.close()
        for log in handles:
            log.close()
        # Empty precheck failure allocated no resources and needs no delayed
        # reconciliation. Once a child exists, its create may have been in flight.
        if processes:
            try:
                report.update(await reconcile(config, cohort_id, members, uncertain=bool(problem)))
            except BaseException as error:
                report.update(cleanup_ok=False, cleanup_error=type(error).__name__)
        else:
            report.update(cleanup_ok=True, owned_remaining=0)
        try:
            report['common_window'] = summarize_workers(output_dir, args.duration) if processes else {'valid': False}
        except BaseException as error:
            report['common_window'] = {'valid': False, 'reason': type(error).__name__}
        report.update(status='passed' if not problem and report['cleanup_ok']
                      and report['common_window']['valid']
                      and (not args.sample_resources or report.get('resources', {}).get('valid')) else 'failed',
                      stop_reason=problem or 'completed', finished_utc=utc_now(),
                      worker_exit_codes=[p.returncode for p in processes])
        atomic_json(output_dir/'cohort.json', report)
        print(json.dumps({'cohort_id': cohort_id, 'status': report['status'],
                          'cleanup_ok': report['cleanup_ok'], 'summary': str(output_dir/'cohort.json')}), flush=True)
    return 0 if report['status'] == 'passed' else 1


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', required=True, choices=['service', 'sdk'])
    parser.add_argument('--phase')
    parser.add_argument('--template', default='base-4k-4g')
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--cohort-id')
    parser.add_argument('--prepare-timeout', type=float, default=900)
    parser.add_argument('--expected-runner-sha256')
    parser.add_argument('--sample-resources', action='store_true',
                        help='Sample all four generator PIDs plus VM/host resources every 2 seconds')
    parser.add_argument('--worker-index', type=int, choices=range(4), help=argparse.SUPPRESS)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    args.phase = args.phase or ('phase1-service' if args.adapter == 'service' else 'phase2-sdk')
    if not 1 <= args.duration <= 120 or not 60 <= args.prepare_timeout <= 1800:
        parser.error('duration must be 1..120 seconds; prepare timeout 60..1800')
    if args.expected_runner_sha256 and args.expected_runner_sha256 != RUNNER_SHA256:
        parser.error('frozen runner hash does not match')
    if args.worker_index is not None and not args.cohort_id:
        parser.error('worker requires cohort ID')
    return args


async def main(argv=None):
    args = arguments(argv)
    if args.dry_run:
        print(json.dumps({'adapter': args.adapter, 'generator_count': 4,
            'total_concurrency': 64, 'total_resident_count': 64, 'duration': args.duration,
            'output_dir': args.output_dir, 'frozen_runner_sha256': RUNNER_SHA256,
            'sample_resources': args.sample_resources,
            'service_requests': 0}, indent=2))
        return 0
    if args.worker_index is not None:
        bench = CohortBench(args)
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, bench.stop.set)
        return await bench.run()
    return await coordinator(args)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
