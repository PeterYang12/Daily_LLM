import asyncio
import json
import os
import shlex
import time
import uuid

from e2b import AsyncSandbox, CommandExitException, TimeoutException
from uni_agent.sandbox.base import Sandbox, ExecResult
from uni_agent.sandbox.registry import register_sandbox
from runtime_common import CURRENT, ROOT, RUN_ID, api_options, event, load_secrets
from profile_events import emit, span


async def _await_actor_ref(reference):
    return await reference


def _observe_task_exception(task):
    # A remote failure can arrive after its owning operation was cancelled.
    # Retrieve it to avoid an unhandled-task warning; later awaits still raise it.
    if not task.cancelled():
        task.exception()


async def _drain_shielded(task):
    """Drain a cleanup task despite repeated caller cancellation; report cancellation afterwards."""
    interrupted = None
    while True:
        try:
            return await asyncio.shield(task), interrupted
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            interrupted = exc


class AdmissionLeaseError(RuntimeError):
    pass


@register_sandbox('e2b')
class E2BSandbox(Sandbox):
    def __init__(self, template, runtime_timeout):
        load_secrets()
        self.template = template
        self.runtime_timeout = int(runtime_timeout)
        self.client = None
        self.context = dict(CURRENT.get())
        self.phase = self.context.get('phase', os.environ.get('UA_PHASE', 'train'))
        self.gate = None
        self.separate_create_gate = os.environ.get('UA_PROFILE_SEPARATE_CREATE_GATE', '0') == '1'
        if self.phase in ('train', 'validation'):
            import ray
            if ray.is_initialized():
                self.gate = ray.get_actor('e2b-admission', namespace=RUN_ID)

    @classmethod
    def from_config(cls, config):
        if not config.image:
            raise ValueError('E2B requires a mapped template ID')
        if '/' in config.image:
            raise ValueError('Unmapped E2B image: ' + config.image)
        return cls(config.image, config.runtime_timeout)

    def log(self, kind, **fields):
        event(kind, phase=self.phase, sandbox_id=self.client.sandbox_id if self.client else None,
              template_id=self.template, **{k: v for k, v in self.context.items() if k != 'phase'}, **fields)

    def profile(self, operation, category='sdk', **fields):
        metadata = {**self.context, 'template_id': self.template,
                    'sandbox_id': self.client.sandbox_id if self.client else None,
                    'gate_policy': 'create_only' if self.separate_create_gate else 'create_and_setup', **fields}
        return span(operation, category=category, **metadata)

    def _ticket_event(self, operation, lease, **fields):
        """Small run-owned lease ledger; logging errors cannot interrupt lease reconciliation."""
        row = {'schema_version': 1, 'run_id': RUN_ID, 'pid': os.getpid(), 'time_unix': time.time(),
               'monotonic_ns': time.monotonic_ns(), 'event': operation,
               'gate': lease['kind'], 'ticket': lease['ticket'], **fields}
        row.update({key: value for key, value in self.context.items() if key in
                    ('phase','session_id','episode_id','instance_id','sample_uid','rollout_index','global_step','supervisor_attempt_id')})
        try:
            path = ROOT/'results/gate-ticket-events.jsonl'; path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps(row, separators=(',', ':'))+'\n').encode()
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try: os.write(descriptor, data)
            finally: os.close(descriptor)
        except OSError:
            pass

    async def _sdk_command(self, operation, command, **kwargs):
        with self.profile(operation, timeout_s=kwargs.get('timeout')) as meta:
            try:
                response = await self.client.commands.run(command, **kwargs)
            except CommandExitException as exc:
                meta.update(exit_code=exc.exit_code, status='command_nonzero')
                raise
            meta.update(exit_code=response.exit_code,
                        stdout_bytes=len((response.stdout or '').encode()),
                        stderr_bytes=len((response.stderr or '').encode()))
            return response

    async def _acquire_gate(self, kind):
        if self.gate is None:
            return None
        lease = {'kind': kind, 'ticket': uuid.uuid4().hex, 'acquire_task': None,
                 'cleanup_task': None, 'retired': False}
        self._ticket_event('requested', lease)
        try:
            with self.profile(kind+'_gate_wait', category='gate', gate=kind):
                reference = getattr(self.gate, 'acquire_'+kind).remote(lease['ticket'])
                task = asyncio.create_task(_await_actor_ref(reference))
                task.add_done_callback(_observe_task_exception)
                lease['acquire_task'] = task
                # Shield the original RPC so cancellation cannot discard its eventual grant state.
                grant = await asyncio.shield(task)
                if not isinstance(grant, dict) or grant.get('ticket') != lease['ticket'] or grant.get('state') != 'granted':
                    raise AdmissionLeaseError('Admission ticket was not granted')
            self.log('admission_granted', gate=kind, ticket=lease['ticket'])
            self._ticket_event('granted', lease)
            return lease
        except BaseException as original:
            self._ticket_event('wait_cancelled' if isinstance(original, asyncio.CancelledError) else 'acquire_failed',
                               lease, error_type=type(original).__name__)
            # A cancelled local await does not cancel an async Ray actor method. Reconcile the
            # ticket even if the grant acknowledgement was lost or arrived concurrently.
            try:
                await self._finish_gate(lease, cancel=True, propagate_cancel=False)
            except BaseException as cleanup_error:
                self.log('admission_cleanup_failed', gate=kind, ticket=lease['ticket'],
                         error_type=type(cleanup_error).__name__)
                self._ticket_event('cleanup_failed_ticket_retained', lease, error_type=type(cleanup_error).__name__)
                if hasattr(original, 'add_note'):
                    original.add_note('Admission cancellation cleanup failed: '+type(cleanup_error).__name__)
            raise

    async def _finish_gate(self, lease, *, cancel=False, propagate_cancel=True):
        if lease is None or lease['retired']:
            return
        kind, ticket = lease['kind'], lease['ticket']
        if lease['cleanup_task'] is None:
            async def finish():
                action = 'cancel_' if cancel else 'release_'
                with self.profile(kind+'_gate_'+('cancel' if cancel else 'release'), category='gate', gate=kind):
                    acknowledgement = await _await_actor_ref(getattr(self.gate, action+kind).remote(ticket))
                    if not isinstance(acknowledgement, dict) or acknowledgement.get('ticket') != ticket or acknowledgement.get('state') not in ('released', 'cancelled', 'absent'):
                        raise AdmissionLeaseError('Admission cleanup was not acknowledged')
                self.log('admission_cancelled' if cancel else 'admission_released', gate=kind, ticket=ticket)
                self._ticket_event('cancel_acknowledged' if cancel else 'release_acknowledged', lease,
                                   actor_state=acknowledgement['state'])
                # Never retire an unresolved acquisition: its late arrival needs the tombstone.
                if lease['acquire_task'] is None:
                    return
                try:
                    acquired = await lease['acquire_task']
                    self._ticket_event('acquire_rpc_settled', lease, acquire_result_state=acquired.get('state') if isinstance(acquired, dict) else 'invalid')
                except BaseException:
                    self.log('admission_ticket_retained', gate=kind, ticket=ticket,
                             reason='acquire_rpc_failed_after_cleanup')
                    self._ticket_event('retained', lease, reason='acquire_rpc_failed_after_cleanup')
                    raise
                retired = await _await_actor_ref(getattr(self.gate, 'retire_'+kind).remote(ticket))
                if not isinstance(retired, dict) or retired.get('ticket') != ticket or retired.get('retired') is not True:
                    self.log('admission_ticket_retained', gate=kind, ticket=ticket,
                             reason='retirement_not_acknowledged')
                    self._ticket_event('retained', lease, reason='retirement_not_acknowledged')
                    raise AdmissionLeaseError('Admission ticket retirement was not acknowledged')
                lease['retired'] = True
                self._ticket_event('retired', lease)
            lease['cleanup_task'] = asyncio.create_task(finish())
            lease['cleanup_task'].add_done_callback(_observe_task_exception)
        _, interrupted = await _drain_shielded(lease['cleanup_task'])
        if interrupted is not None and propagate_cancel:
            raise interrupted

    async def start(self):
        if self.client is not None:
            return
        start = time.monotonic()
        create_lease = await self._acquire_gate('create')
        if self.gate is not None:
            emit('create_gate_acquired', category='gate', gate='create', **self.context)
        held = time.monotonic_ns()
        create_released = False
        async def release_create():
            nonlocal create_released
            if self.gate is not None and not create_released:
                await self._finish_gate(create_lease)
                create_released = True
                emit('create_gate_held', category='gate', gate='create', duration_ns=time.monotonic_ns()-held,
                     sandbox_id=self.client.sandbox_id if self.client else None,
                     gate_policy='create_only' if self.separate_create_gate else 'create_and_setup', **self.context)
        try:
            with self.profile('sdk_create') as meta:
                self.client = await AsyncSandbox.create(template=self.template, timeout=self.runtime_timeout,
                    request_timeout=120, metadata={'experiment': RUN_ID, 'phase': self.phase,
                        **{k: str(v) for k, v in self.context.items() if k != 'phase'}}, **api_options())
                meta['sandbox_id'] = self.client.sandbox_id
            self.log('created', seconds=time.monotonic()-start)
            if self.separate_create_gate:
                await release_create()
            # Only retry this harmless readiness operation. Agent commands are never blindly retried.
            for attempt in range(3):
                try:
                    with self.profile('readiness_attempt', category='setup', attempt=attempt + 1):
                        await self._sdk_command('sdk_readiness', '/bin/true', user='root', timeout=30)
                    break
                except Exception as exc:
                    self.log('readiness_error', attempt=attempt, error_type=type(exc).__name__)
                    if attempt == 2:
                        raise
                    with self.profile('readiness_backoff', category='retry', retry_delay_s=0.5 * (attempt + 1)):
                        await asyncio.sleep(0.5 * (attempt + 1))
            with self.profile('tools_setup', category='setup'):
                await self.ensure_tools()
        except BaseException:
            if self.client is not None:
                await self.stop()
            raise
        finally:
            await release_create()

    async def ensure_tools(self):
        try:
            await self._sdk_command('sdk_tools_probe', 'command -v tmux', user='root', timeout=30)
            emit('tools_cache', cached=True, category='setup', **self.context)
            return
        except CommandExitException as exc:
            if exc.exit_code != 1:
                raise
        emit('tools_cache', cached=False, category='setup', **self.context)
        with self.profile('sdk_tools_os_read'):
            os_release = str(await self.client.files.read('/etc/os-release', user='root'))
        values = dict(line.split('=', 1) for line in os_release.splitlines() if '=' in line)
        key = values['ID'].strip('"') + '-' + values['VERSION_ID'].strip('"')
        archive = ROOT/'data/tools'/key/'tmux-debs.tgz'
        if not archive.is_file():
            raise RuntimeError('Missing cached tmux packages for ' + key)
        start = time.monotonic()
        with self.profile('sdk_tools_upload', input_bytes=archive.stat().st_size):
            await self.client.files.write('/tmp/ua-tmux-debs.tgz', archive.read_bytes(), user='root')
        await self._sdk_command('sdk_tools_install',
            'set -e; mkdir -p /tmp/ua-tmux-debs; tar -xzf /tmp/ua-tmux-debs.tgz -C /tmp/ua-tmux-debs; '
            'DEBIAN_FRONTEND=noninteractive dpkg -i /tmp/ua-tmux-debs/*.deb; tmux -V', user='root', timeout=90)
        self.log('tools_provisioned', seconds=time.monotonic()-start, os_key=key, bytes=archive.stat().st_size)

    async def stop(self):
        if self.client is None:
            return
        start = time.monotonic()
        for attempt in range(3):
            try:
                with self.profile('sdk_kill', attempt=attempt + 1):
                    await self.client.kill(request_timeout=60)
                self.log('destroyed', seconds=time.monotonic()-start)
                self.client = None
                return
            except Exception as exc:
                self.log('destroy_error', attempt=attempt, error_type=type(exc).__name__)
                if attempt == 2:
                    raise
                with self.profile('kill_backoff', category='retry', retry_delay_s=0.5 * (attempt + 1)):
                    await asyncio.sleep(0.5 * (attempt + 1))

    async def is_alive(self):
        if self.client is None:
            return False
        try:
            with self.profile('sdk_is_running'):
                return await self.client.is_running(request_timeout=10)
        except Exception:
            return False

    def _is_timeout_error(self, exc):
        return isinstance(exc, TimeoutException) or super()._is_timeout_error(exc)

    async def _exec(self, argv, *, timeout=None, workdir=None, env=None):
        if self.client is None:
            raise RuntimeError('Sandbox is not started')
        exec_lease = await self._acquire_gate('exec')
        if self.gate is not None:
            emit('exec_gate_acquired', category='gate', gate='exec', **self.context)
        start = time.monotonic()
        try:
            with self.profile('sdk_exec', timeout_s=120 if timeout is None else timeout) as meta:
                try:
                    response = await self.client.commands.run(shlex.join(argv), cwd=workdir, envs=env or {},
                        user='root', timeout=120 if timeout is None else timeout)
                except CommandExitException as exc:
                    response = exc
                meta.update(exit_code=response.exit_code,
                            status='ok' if response.exit_code == 0 else 'command_nonzero',
                            stdout_bytes=len((response.stdout or '').encode()),
                            stderr_bytes=len((response.stderr or '').encode()))
            self.log('exec', executable=argv[0] if argv else None, seconds=time.monotonic()-start,
                     exit_code=response.exit_code, stdout_bytes=len(response.stdout), stderr_bytes=len(response.stderr))
            return ExecResult(response.exit_code, response.stdout, response.stderr)
        except Exception as exc:
            self.log('exec_transport_error', seconds=time.monotonic()-start, error_type=type(exc).__name__)
            raise
        finally:
            if self.gate is not None:
                await self._finish_gate(exec_lease)
                emit('exec_gate_held', category='gate', gate='exec',
                     duration_ns=int((time.monotonic()-start)*1e9), **self.context)

    async def read_file(self, path):
        start = time.monotonic()
        with self.profile('sdk_file_read') as meta:
            result = bytes(await self.client.files.read(path, format='bytes', user='root'))
            meta['output_bytes'] = len(result)
        self.log('file_read', seconds=time.monotonic()-start, size=len(result))
        return result

    async def write_file(self, path, content):
        start = time.monotonic()
        with self.profile('sdk_file_write', input_bytes=len(content.encode() if isinstance(content, str) else content)):
            await self.client.files.write(path, content, user='root')
        self.log('file_write', seconds=time.monotonic()-start, size=len(content))
