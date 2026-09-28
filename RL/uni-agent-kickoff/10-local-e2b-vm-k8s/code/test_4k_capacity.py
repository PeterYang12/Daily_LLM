"""Opt-in lightweight E2B capacity check; never fills the advertised guest RAM.

Run with client-env/bin/python and an explicitly selected ordinary-page template.
The JSON report is also the recovery journal: after a crash, use the same
--output with --cleanup-only to discover and delete only this run's sandboxes.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import uuid

from e2b import AsyncSandbox, SandboxQuery, SandboxState
from e2b.exceptions import SandboxNotFoundException


ROOT = Path(__file__).resolve().parents[1]
CLIENT_CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')
PURPOSE = 'e2b-ordinary-page-capacity-check'
MILESTONES = (32, 64, 128, 256, 512)
GIB = 1024 ** 3


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class CheckFailed(Exception):
    pass


def quota_rejection(error, limit=512):
    """A generic 429 or a node's 503 is not evidence of the team quota."""
    return getattr(error, 'status_code', None) == 429 and bool(re.search(
        rf'maximum number of concurrent E2B sandboxes\s*\({limit}\)',
        str(error), re.IGNORECASE))


def ambiguous_create(error):
    status = getattr(error, 'status_code', None)
    return status is None or status == 408 or (status >= 500 and status != 503)


async def vm_available_memory(vm_ssh):
    # Fixed command: neither credentials nor user-controlled shell text is sent.
    proc = await asyncio.create_subprocess_exec(
        str(vm_ssh), "awk '/^MemAvailable:/ {print $2}' /proc/meminfo",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0 or not stdout.strip().isdigit():
        raise CheckFailed('VM MemAvailable read failed; refusing further creates')
    return int(stdout.strip()) * 1024


class CapacityCheck:
    def __init__(self, args, config, report=None, sandbox_api=AsyncSandbox,
                 memory_reader=vm_available_memory):
        self.args = args
        self.config = config
        self.api = sandbox_api
        self.memory_reader = memory_reader
        self.started = time.monotonic()
        self.stop = asyncio.Event()
        self.handles = {}
        self.markers = {}
        self.journal_failed = False
        self.uncertain_until = 0.0
        self.last_owned = None
        self.report = report or {
            'schema_version': 1,
            'purpose': PURPOSE,
            'run_id': uuid.uuid4().hex,
            'started_utc': utc_now(),
            'endpoint': config['api_url'],
            'sdk_version': importlib.metadata.version('e2b'),
            'template': args.template,
            'requested_count': args.count,
            'ttl_seconds': args.ttl,
            'create_inflight': args.inflight,
            'create_retries': args.create_retries,
            'hold_seconds': args.hold_seconds,
            'scope': 'Lightweight simultaneous liveness and isolated file/echo checks; '
                     'does not allocate or verify count * advertised guest RAM.',
            'status': 'running',
            'attempts': [],
            'retry_events': [],
            'sandboxes': {},
            'milestones': [],
            'memory_checks': [],
        }
        self.run_id = self.report['run_id']
        self.metadata = {'run_id': self.run_id, 'purpose': PURPOSE}
        self.marker_path = '/tmp/e2b-capacity-' + self.run_id + '.marker'
        if args.cleanup_only and any(
                item.get('state') == 'pending' or item.get('ambiguous', False)
                for item in self.report.get('attempts', [])):
            self.uncertain_until = time.monotonic() + args.reconcile_seconds

    def error_info(self, error):
        message = str(error)
        key = self.config.get('api_key')
        if key:
            message = message.replace(key, '[REDACTED]')
        message = re.sub(r'e2b_[A-Za-z0-9_-]+', '[REDACTED]', message)
        return {'type': type(error).__name__,
                'status_code': getattr(error, 'status_code', None),
                'message': message[:1500]}

    def event(self, kind, **values):
        try:
            print(json.dumps({'event': kind, 'run_id': self.run_id,
                              'elapsed_seconds': round(time.monotonic() - self.started, 3),
                              **values}, ensure_ascii=False), flush=True)
        except OSError:
            # A disconnected stdout consumer must not prevent sandbox cleanup.
            pass

    def save(self, strict=True):
        """Atomic, private journal; record identity before starting any create."""
        self.report['updated_utc'] = utc_now()
        temp = self.args.output.with_name('.' + self.args.output.name + f'.{os.getpid()}.tmp')
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                json.dump(self.report, file, ensure_ascii=False, indent=2)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.args.output)
        except OSError as error:
            self.journal_failed = True
            if strict:
                raise
            self.event('journal_write_failed', error_type=type(error).__name__)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError as error:
                self.journal_failed = True
                if strict:
                    raise
                self.event('journal_temp_cleanup_failed', error_type=type(error).__name__)

    def opts(self, timeout=None):
        # Disable SDK 429 retries so the quota probe has one observable request.
        return {**self.config, 'request_timeout': timeout or self.args.request_timeout,
                'retries': 0}

    def owns(self, info):
        metadata = info.metadata or {}
        return all(metadata.get(key) == value for key, value in self.metadata.items())

    def remember(self, info, recovered=False):
        sid = info.sandbox_id
        record = self.report['sandboxes'].setdefault(sid, {
            'sandbox_id': sid, 'first_seen_utc': utc_now(), 'recovered': recovered,
        })
        metadata = info.metadata or {}
        record.update(index=metadata.get('index'), phase=metadata.get('phase'),
                      state=getattr(info.state, 'value', str(info.state)),
                      cpu_count=info.cpu_count, memory_mb=info.memory_mb)
        return record

    async def owned_list(self, respect_stop=False):
        paginator = self.api.list(query=SandboxQuery(metadata=self.metadata),
                                  limit=100, **self.opts(timeout=20))
        owned = {}
        while paginator.has_next:
            if respect_stop:
                self.check_stop()
            for info in await paginator.next_items():
                # The local check is mandatory even if a server ignores filters.
                if self.owns(info):
                    owned[info.sandbox_id] = info
                    self.remember(info, recovered=info.sandbox_id not in self.handles)
        self.last_owned = owned
        return owned

    async def memory_guard(self, created):
        available = await self.memory_reader(self.args.vm_ssh)
        result = {'created': created, 'available_gib': round(available / GIB, 3),
                  'minimum_gib': self.args.min_mem_gib, 'checked_utc': utc_now()}
        self.report['memory_checks'].append(result)
        self.save()
        self.event('memory', **result)
        if available < self.args.min_mem_gib * GIB:
            raise CheckFailed('VM MemAvailable is below the safety floor; stopping creates')

    def check_stop(self):
        if self.stop.is_set():
            raise CheckFailed('Interrupted; no further sandboxes will be created')
        # No TTL extension: finish well before the earliest sandbox can expire.
        if time.monotonic() - self.started >= self.args.ttl - 120:
            raise CheckFailed('Approaching the earliest sandbox TTL; stopping creates')

    async def wait_interruptibly(self, seconds):
        self.check_stop()
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
        self.check_stop()

    async def create_one(self, index, phase='capacity'):
        # SDK 503 means the request was refused without creating a sandbox.
        # Unknown outcomes are never retried; cleanup reconciles their metadata.
        for retry_number in range(self.args.create_retries + 1):
            try:
                return await self.create_attempt(index, phase, retry_number + 1)
            except Exception as error:
                if (phase != 'capacity' or getattr(error, 'status_code', None) != 503
                        or retry_number >= self.args.create_retries):
                    raise
                delay = min(2 ** retry_number, 8)
                retry = {'index': index, 'phase': phase, 'retry_number': retry_number + 1,
                         'failed_attempt': retry_number + 1, 'delay_seconds': delay,
                         'scheduled_utc': utc_now(), 'state': 'scheduled',
                         'error': self.error_info(error)}
                self.report['retry_events'].append(retry)
                self.save()
                self.event('create_retry_scheduled', **retry)
                try:
                    await self.wait_interruptibly(delay)
                except BaseException:
                    retry.update(state='cancelled', finished_utc=utc_now())
                    self.save(strict=False)
                    raise
                retry.update(state='retrying', finished_utc=utc_now())
                self.save()
                self.event('create_retry', index=index, retry_number=retry_number + 1)
        raise AssertionError('unreachable create retry loop')

    async def create_attempt(self, index, phase, attempt_number):
        self.check_stop()
        attempt = {'index': index, 'phase': phase, 'state': 'pending',
                   'attempt_number': attempt_number, 'started_utc': utc_now()}
        self.report['attempts'].append(attempt)
        self.save()
        began = time.monotonic()
        try:
            sandbox = await asyncio.wait_for(self.api.create(
                self.args.template, timeout=self.args.ttl,
                metadata={**self.metadata, 'index': str(index), 'phase': phase},
                lifecycle={'on_timeout': 'kill'}, **self.opts()),
                timeout=self.args.request_timeout + 5)
        except BaseException as error:
            uncertain = ambiguous_create(error)
            attempt.update(state='error', ambiguous=uncertain, error=self.error_info(error),
                           finished_utc=utc_now(),
                           elapsed_seconds=round(time.monotonic() - began, 4))
            if uncertain:
                self.uncertain_until = max(self.uncertain_until,
                                          time.monotonic() + self.args.reconcile_seconds)
            self.save(strict=False)
            raise
        sid = sandbox.sandbox_id
        # Remember the ID in memory before journal I/O or any tool operation.
        self.handles[sid] = sandbox
        self.report['sandboxes'][sid] = {
            'sandbox_id': sid, 'index': str(index), 'phase': phase,
            'first_seen_utc': utc_now(), 'recovered': False,
            'create_seconds': round(time.monotonic() - began, 4),
        }
        attempt.update(state='created', sandbox_id=sid, finished_utc=utc_now(),
                       elapsed_seconds=round(time.monotonic() - began, 4))
        self.save()
        return sandbox

    async def initialize_marker(self, sandbox):
        self.check_stop()
        value = uuid.uuid4().hex
        self.markers[sandbox.sandbox_id] = value
        await sandbox.files.write(self.marker_path, value, request_timeout=20)
        self.check_stop()
        if await sandbox.files.read(self.marker_path, request_timeout=20) != value:
            raise CheckFailed('Initial marker file roundtrip failed')
        self.check_stop()
        result = await sandbox.commands.run('echo ' + value, timeout=20,
                                            request_timeout=25)
        if result.exit_code != 0 or result.stdout != value + '\n':
            raise CheckFailed('Sandbox echo check failed')
        self.report['sandboxes'][sandbox.sandbox_id]['initial_tool_check'] = True
        self.save()

    async def check_marker(self, sandbox):
        self.check_stop()
        value = self.markers[sandbox.sandbox_id]
        result = await sandbox.commands.run(
            f"cat {self.marker_path}; printf '\\n'; echo {value}",
            timeout=20, request_timeout=25)
        if result.exit_code != 0 or result.stdout != value + '\n' + value + '\n':
            raise CheckFailed('Isolation marker changed after other sandboxes wrote the same path')
        record = self.report['sandboxes'][sandbox.sandbox_id]
        record['final_isolation_check'] = True
        record['isolation_check_count'] = record.get('isolation_check_count', 0) + 1
        record['last_isolation_check_utc'] = utc_now()

    async def bounded(self, items, action, limit):
        semaphore = asyncio.Semaphore(limit)

        async def one(item):
            async with semaphore:
                return await action(item)

        results = await asyncio.gather(*(one(item) for item in items), return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise errors[0]
        return results

    async def checkpoint(self, expected, label='milestone'):
        owned = await self.owned_list(respect_stop=True)
        running = {sid for sid, info in owned.items() if info.state == SandboxState.RUNNING}
        result = {'target': expected, 'running': len(running), 'owned_total': len(owned),
                  'elapsed_seconds': round(time.monotonic() - self.started, 3),
                  'checked_utc': utc_now(), 'label': label}
        self.report['milestones'].append(result)
        self.save()
        self.event(label, **result)
        if len(running) != expected or set(self.handles) != running:
            raise CheckFailed(f'Liveness mismatch: expected {expected}, observed {len(running)}')
        return result

    async def hold(self):
        began = time.monotonic()
        deadline = began + self.args.hold_seconds
        holding = {'status': 'running', 'started_utc': utc_now(),
                   'requested_seconds': self.args.hold_seconds,
                   'check_interval_seconds': 10, 'checks': []}
        self.report['hold'] = holding
        self.save()
        self.event('hold_started', seconds=self.args.hold_seconds,
                   expected_running=self.args.count)
        next_check = min(began + 10, deadline)
        try:
            while time.monotonic() < deadline:
                await self.wait_interruptibly(max(0, next_check - time.monotonic()))
                result = await self.checkpoint(self.args.count, 'hold_liveness')
                holding['checks'].append(result)
                await self.memory_guard(self.args.count)
                next_check = min(next_check + 10, deadline)
            holding['status'] = 'passed'
        except BaseException as error:
            holding.update(status='failed', error=self.error_info(error))
            raise
        finally:
            holding.update(finished_utc=utc_now(),
                           elapsed_seconds=round(time.monotonic() - began, 3))
            self.save(strict=False)

    async def exercise(self):
        await self.memory_guard(0)
        created = 0
        boundaries = sorted(set(MILESTONES) | {self.args.count} |
                            set(range(64, self.args.count + 1, 64)))
        while created < self.args.count:
            self.check_stop()
            next_boundary = min(x for x in boundaries if x > created)
            batch_count = min(self.args.inflight, self.args.count - created,
                              next_boundary - created)
            batch = await self.bounded(range(created, created + batch_count),
                                       self.create_one, self.args.inflight)
            await self.bounded(batch, self.initialize_marker, self.args.inflight)
            created += batch_count
            if created % 64 == 0:
                await self.memory_guard(created)
            if created in MILESTONES or created == self.args.count:
                await self.checkpoint(created)

        # Re-read after EVERY sandbox has written its distinct marker to the same
        # pathname. Immediate per-sandbox roundtrips alone cannot prove isolation.
        await self.bounded(list(self.handles.values()), self.check_marker,
                           self.args.tool_inflight)
        await self.checkpoint(self.args.count, 'all_tools_verified')
        if self.args.count != 512:
            self.report['quota_probe'] = {'status': 'not_attempted_count_below_512'}
            return
        await self.hold()
        await self.bounded(list(self.handles.values()), self.check_marker,
                           self.args.tool_inflight)
        self.report['post_hold_tools'] = {'checked_count': len(self.handles),
                                         'completed_utc': utc_now()}
        await self.checkpoint(512, 'post_hold_tools_verified')
        self.check_stop()
        await self.memory_guard(512)
        try:
            sandbox = await self.create_one(512, phase='quota_probe')
        except Exception as error:
            self.report['quota_probe'] = {
                'status': 'expected_team_quota' if quota_rejection(error) else 'unexpected_error',
                'error': self.error_info(error),
            }
            self.save()
            if not quota_rejection(error):
                raise CheckFailed('513th create did not return the explicit 512-team-quota rejection') from error
        else:
            self.report['quota_probe'] = {'status': 'unexpected_success',
                                          'sandbox_id': sandbox.sandbox_id}
            self.save()
            raise CheckFailed('513th create unexpectedly succeeded; it will also be cleaned up')
        await self.checkpoint(512, 'quota_rejected_512_still_running')

    async def cleanup_one(self, sid):
        record = self.report['sandboxes'][sid]
        try:
            info = await self.api.get_info(sid, **self.opts(timeout=20))
        except SandboxNotFoundException:
            record['cleanup_verified_absent'] = True
            return
        if not self.owns(info):
            record['cleanup_ownership_mismatch'] = True
            raise CheckFailed('Refusing deletion: sandbox metadata does not match this run')
        self.remember(info)
        await self.api.kill(sid, **self.opts(timeout=20))
        record['kill_requested_utc'] = utc_now()
        # A successful DELETE alone is not the final liveness proof.
        record['cleanup_verified_absent'] = False

    async def cleanup(self):
        self.report['cleanup'] = {'status': 'running', 'started_utc': utc_now(), 'errors': []}
        cleanup = self.report['cleanup']
        deadline = time.monotonic() + self.args.cleanup_timeout
        empty_rounds = 0
        while time.monotonic() < deadline:
            listed = False
            try:
                await asyncio.wait_for(self.owned_list(), timeout=min(45, deadline - time.monotonic()))
                listed = True
            except Exception as error:
                cleanup['errors'].append(self.error_info(error))
            pending = [sid for sid, record in self.report['sandboxes'].items()
                       if not record.get('cleanup_verified_absent', False)
                       or (listed and sid in self.last_owned)]
            try:
                if pending:
                    await asyncio.wait_for(
                        self.bounded(pending, self.cleanup_one, self.args.cleanup_inflight),
                        timeout=max(0.01, deadline - time.monotonic()))
            except Exception as error:
                cleanup['errors'].append(self.error_info(error))
            pending = [sid for sid, record in self.report['sandboxes'].items()
                       if not record.get('cleanup_verified_absent', False)]
            current_zero = listed and not self.last_owned and not pending
            empty_rounds = empty_rounds + 1 if current_zero else 0
            cleanup.update(remaining_known_ids=pending,
                           last_owned_count=len(self.last_owned) if listed else None,
                           consecutive_empty_lists=empty_rounds)
            self.save(strict=False)
            # Keep discovering while an ambiguous create could still complete.
            if empty_rounds >= 2 and time.monotonic() >= self.uncertain_until:
                cleanup.update(status='verified_zero', finished_utc=utc_now())
                self.event('cleanup_verified_zero', discovered_ids=len(self.report['sandboxes']))
                return True
            await asyncio.sleep(min(2, max(0, deadline - time.monotonic())))
        cleanup.update(status='incomplete', finished_utc=utc_now())
        return False

    async def execute(self):
        passed = False
        try:
            self.save(strict=not self.args.cleanup_only)
            self.event('started', template=self.report['template'],
                       target=self.report['requested_count'], cleanup_only=self.args.cleanup_only)
            if not self.args.cleanup_only:
                await self.exercise()
                self.check_stop()
                passed = True
        except BaseException as error:
            self.report['test_error'] = self.error_info(error)
            self.event('check_failed', **self.error_info(error))
        finally:
            self.report['status'] = 'cleaning'
            self.save(strict=False)
            try:
                cleaned = await self.cleanup()
            except BaseException as error:
                cleaned = False
                self.report.setdefault('cleanup', {})['fatal_error'] = self.error_info(error)
            status = 'cleaned' if self.args.cleanup_only else 'passed' if passed else 'failed'
            self.report.update(status=status if cleaned else 'cleanup_failed',
                               finished_utc=utc_now(),
                               wall_seconds=round(time.monotonic() - self.started, 3))
            self.save(strict=False)
            if self.journal_failed:
                self.report['journal_write_failed'] = True
                self.report['status'] = 'journal_failed' if cleaned else 'cleanup_failed'
                self.save(strict=False)
            self.event('finished', status=self.report['status'],
                       cleaned=cleaned, report=str(self.args.output))
        return 0 if cleaned and not self.journal_failed and (passed or self.args.cleanup_only) else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', help='Explicit ordinary-page template name or ID')
    parser.add_argument('--count', type=int, default=512)
    parser.add_argument('--output', type=Path, required=True, help='Private JSON report and recovery journal')
    parser.add_argument('--inflight', type=int, default=2)
    parser.add_argument('--create-retries', type=int, default=8,
                        help='Additional attempts after explicit create HTTP 503; 0..8')
    parser.add_argument('--hold-seconds', type=float, default=60,
                        help='Hold all 512 alive, checking every 10 seconds before the quota probe')
    parser.add_argument('--tool-inflight', type=int, default=8)
    parser.add_argument('--cleanup-inflight', type=int, default=4)
    parser.add_argument('--ttl', type=int, default=3600)
    parser.add_argument('--request-timeout', type=float, default=90)
    parser.add_argument('--min-mem-gib', type=float, default=64)
    parser.add_argument('--cleanup-timeout', type=float, default=300)
    parser.add_argument('--reconcile-seconds', type=float, default=150,
                        help='Discovery window after an ambiguous create; minimum 120 seconds')
    parser.add_argument('--vm-ssh', type=Path, default=ROOT / 'code/vm-ssh')
    parser.add_argument('--config', type=Path, default=CLIENT_CONFIG)
    parser.add_argument('--cleanup-only', action='store_true',
                        help='Recover this output journal and only clean its metadata-matched instances')
    args = parser.parse_args(argv)
    if not args.cleanup_only and not args.template:
        parser.error('--template is required for a capacity run')
    if not 1 <= args.count <= 512 or not 1 <= args.inflight <= 3:
        parser.error('--count must be 1..512 and --inflight must be 1..3')
    if not 1 <= args.cleanup_inflight <= 16 or not 1 <= args.tool_inflight <= 16:
        parser.error('tool and cleanup concurrency must be 1..16')
    if not 300 <= args.ttl <= 3600 or not 1 <= args.request_timeout <= 120:
        parser.error('--ttl must be 300..3600; --request-timeout must be 1..120')
    if not 0 <= args.create_retries <= 8 or not 0 <= args.hold_seconds <= args.ttl - 120:
        parser.error('--create-retries must be 0..8; --hold-seconds must be 0..TTL minus 120')
    if not args.min_mem_gib >= 64 or not args.reconcile_seconds >= 120:
        parser.error('memory floor must be at least 64 GiB; reconciliation at least 120 seconds')
    if not args.cleanup_timeout >= args.reconcile_seconds + 60:
        parser.error('cleanup timeout must exceed reconciliation by at least 60 seconds')
    args.output = args.output.resolve()
    return args


async def async_main(args, config, report):
    check = CapacityCheck(args, config, report)
    loop = asyncio.get_running_loop()

    def stop_requested():
        check.stop.set()
        check.event('stop_requested', action='finish in-flight requests and clean this run')

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_requested)
    try:
        return await check.execute()
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main(argv=None):
    args = parse_args(argv)
    config = json.loads(args.config.read_text())
    # Pass only connection settings; the private file never enters the report.
    config = {name: config[name] for name in ('api_key', 'api_url', 'sandbox_url')}
    report = None
    if args.cleanup_only:
        report = json.loads(args.output.read_text())
        if (report.get('purpose') != PURPOSE
                or not re.fullmatch(r'[0-9a-f]{32}', report.get('run_id', ''))
                or report.get('endpoint') != config['api_url']):
            raise CheckFailed('Recovery journal identity or endpoint does not match')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite the only record of a previous run's sandbox IDs.
        descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    return asyncio.run(async_main(args, config, report))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # Setup failures may involve credentials: print the type, not raw config.
        print(json.dumps({'status': 'setup_failed', 'error_type': type(error).__name__}),
              file=sys.stderr, flush=True)
        sys.exit(1)
