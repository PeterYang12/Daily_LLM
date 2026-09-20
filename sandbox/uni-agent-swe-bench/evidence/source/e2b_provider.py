import asyncio
import os
import shlex
import time

from e2b import AsyncSandbox, CommandExitException, TimeoutException
from uni_agent.sandbox.base import Sandbox, ExecResult
from uni_agent.sandbox.registry import register_sandbox
from runtime_common import CURRENT, ROOT, RUN_ID, api_options, event, load_secrets


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
        if self.phase == 'train':
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

    async def start(self):
        if self.client is not None:
            return
        start = time.monotonic()
        if self.gate is not None:
            await self.gate.acquire_create.remote()
        try:
            self.client = await AsyncSandbox.create(template=self.template, timeout=self.runtime_timeout,
                request_timeout=120, metadata={'experiment': RUN_ID, 'phase': self.phase,
                    **{k: str(v) for k, v in self.context.items() if k != 'phase'}}, **api_options())
            self.log('created', seconds=time.monotonic()-start)
            # Only retry this harmless readiness operation. Agent commands are never blindly retried.
            for attempt in range(3):
                try:
                    await self.client.commands.run('/bin/true', user='root', timeout=30)
                    break
                except Exception as exc:
                    self.log('readiness_error', attempt=attempt, error_type=type(exc).__name__)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))
            await self.ensure_tools()
        except BaseException:
            if self.client is not None:
                await self.stop()
            raise
        finally:
            if self.gate is not None:
                await self.gate.release_create.remote()

    async def ensure_tools(self):
        try:
            await self.client.commands.run('command -v tmux', user='root', timeout=30)
            return
        except CommandExitException as exc:
            if exc.exit_code != 1:
                raise
        os_release = str(await self.client.files.read('/etc/os-release', user='root'))
        values = dict(line.split('=', 1) for line in os_release.splitlines() if '=' in line)
        key = values['ID'].strip('"') + '-' + values['VERSION_ID'].strip('"')
        archive = ROOT/'data/tools'/key/'tmux-debs.tgz'
        if not archive.is_file():
            raise RuntimeError('Missing cached tmux packages for ' + key)
        start = time.monotonic()
        await self.client.files.write('/tmp/ua-tmux-debs.tgz', archive.read_bytes(), user='root')
        await self.client.commands.run(
            'set -e; mkdir -p /tmp/ua-tmux-debs; tar -xzf /tmp/ua-tmux-debs.tgz -C /tmp/ua-tmux-debs; '
            'DEBIAN_FRONTEND=noninteractive dpkg -i /tmp/ua-tmux-debs/*.deb; tmux -V', user='root', timeout=90)
        self.log('tools_provisioned', seconds=time.monotonic()-start, os_key=key, bytes=archive.stat().st_size)

    async def stop(self):
        if self.client is None:
            return
        start = time.monotonic()
        for attempt in range(3):
            try:
                await self.client.kill(request_timeout=60)
                self.log('destroyed', seconds=time.monotonic()-start)
                self.client = None
                return
            except Exception as exc:
                self.log('destroy_error', attempt=attempt, error_type=type(exc).__name__)
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    async def is_alive(self):
        if self.client is None:
            return False
        try:
            return await self.client.is_running(request_timeout=10)
        except Exception:
            return False

    def _is_timeout_error(self, exc):
        return isinstance(exc, TimeoutException) or super()._is_timeout_error(exc)

    async def _exec(self, argv, *, timeout=None, workdir=None, env=None):
        if self.client is None:
            raise RuntimeError('Sandbox is not started')
        if self.gate is not None:
            await self.gate.acquire_exec.remote()
        start = time.monotonic()
        try:
            try:
                response = await self.client.commands.run(shlex.join(argv), cwd=workdir, envs=env or {},
                    user='root', timeout=120 if timeout is None else timeout)
            except CommandExitException as exc:
                response = exc
            self.log('exec', executable=argv[0] if argv else None, seconds=time.monotonic()-start,
                     exit_code=response.exit_code, stdout_bytes=len(response.stdout), stderr_bytes=len(response.stderr))
            return ExecResult(response.exit_code, response.stdout, response.stderr)
        except Exception as exc:
            self.log('exec_transport_error', seconds=time.monotonic()-start, error_type=type(exc).__name__)
            raise
        finally:
            if self.gate is not None:
                await self.gate.release_exec.remote()

    async def read_file(self, path):
        start = time.monotonic()
        result = bytes(await self.client.files.read(path, format='bytes', user='root'))
        self.log('file_read', seconds=time.monotonic()-start, size=len(result))
        return result

    async def write_file(self, path, content):
        start = time.monotonic()
        await self.client.files.write(path, content, user='root')
        self.log('file_write', seconds=time.monotonic()-start, size=len(content))
