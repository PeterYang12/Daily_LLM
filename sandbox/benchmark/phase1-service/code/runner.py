#!/usr/bin/env python3
"""Owned, bounded E2B service/SDK benchmarks; never changes daemon settings.

Service mode uses HTTPX REST and JSON Connect framing through the public gateway.
SDK mode uses the public AsyncSandbox methods. Sandbox provisioning for data-plane
runs uses the SDK outside the measurement window. Credentials stay in memory.
"""
import argparse
import asyncio
import base64
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import struct
import time
import uuid

import httpx
from e2b import AsyncSandbox, SandboxQuery, SandboxState

ROOT = Path('/data2/yuhanya/e2b-benchmark/session-20260920')
CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')
PURPOSE = 'e2b-benchmark-20260920'


class InvalidResult(Exception):
    pass


class ForeignWorkload(Exception):
    pass


class Frames:
    """Decode bounded Connect JSON envelopes, independent of HTTP chunking."""
    def __init__(self):
        self.buf = bytearray()
        self.ended = False

    def feed(self, chunk):
        self.buf.extend(chunk)
        result = []
        while len(self.buf) >= 5:
            flag, size = struct.unpack('>BI', self.buf[:5])
            if size > 32 * 1024 * 1024:
                raise InvalidResult('oversized Connect frame')
            if len(self.buf) < size + 5:
                break
            body = bytes(self.buf[5:5 + size])
            del self.buf[:5 + size]
            if self.ended or flag not in (0, 2):
                raise InvalidResult('invalid Connect frame order/flags')
            self.ended = flag == 2
            result.append((flag, json.loads(body)))
        return result

    def finish(self):
        if self.buf or not self.ended:
            raise InvalidResult('truncated Connect response')


class Bench:
    def __init__(self, args):
        self.args = args
        self.cfg = json.loads(CONFIG.read_text())
        self.run_id = args.run_id or uuid.uuid4().hex
        self.started = time.monotonic()
        self.started_utc = datetime.now(timezone.utc).isoformat()
        self.stop = asyncio.Event()
        self.handles = {}
        self.raw_ids = set()
        self.written = {}
        self.uncertain_until = 0
        self.rows = []
        self.lags = []
        self.guard_error = None
        self.path = Path(args.output)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('x', buffering=1)
        os.chmod(self.path, 0o600)
        self.base = dict(run_id=self.run_id, phase=args.phase,
                         scenario='create_delete_churn' if args.scenario == 'create' else args.scenario,
                         template=args.template, memory_mib=4096,
                         payload_bytes=args.bytes,
                         concurrency=args.concurrency, resident_count=args.residents,
                         mode=args.mode, instrumentation='minimal', repeat=args.repeat,
                         arrival_rate_ops_s=args.rate if args.mode=='open_loop' else None,
                         measurement_kind='steady_state' if args.duration else 'finite_batch',
                         comparison_key=args.scenario + '-v1', page_mode='4KiB',
                         run_started_utc=self.started_utc,
                         sdk_version=importlib.metadata.version('e2b'),
                         runtime_version='local-4k-512-20260920')
        self.http = httpx.AsyncClient(base_url=self.cfg['api_url'], timeout=20,
                limits=httpx.Limits(max_connections=512, max_keepalive_connections=256),
                trust_env=False)
        self.metadata = {'purpose': PURPOSE, 'run_id': self.run_id}
        self.write('metadata', utc=datetime.now(timezone.utc).isoformat(),
                   endpoint=self.cfg['api_url'], parameters=vars(args),
                   runner_revision='v2-error-diagnostics',
                   client_transport='httpx-json-connect' if args.adapter == 'service' else 'e2b-public-sdk',
                   attempt_scope='application-level; SDK connection retries not separately instrumented',
                   resource_sampler='external finite sampler', pid=os.getpid())

    def offset(self):
        return time.monotonic() - self.started

    def write(self, kind, **data):
        self.file.write(json.dumps({'record_type': kind, **self.base, **data}) + '\n')

    def opts(self):
        return {**self.cfg, 'request_timeout': 20, 'retries': 0}

    def error_info(self, error):
        response = getattr(error, 'response', None)
        status = getattr(response, 'status_code', getattr(error, 'status_code', None))
        message = str(error)
        secrets = [self.cfg['api_key']]
        for handle in self.handles.values():
            secrets.extend([getattr(handle, '_envd_access_token', None),
                            getattr(handle, 'traffic_access_token', None)])
        for secret in secrets:
            if isinstance(secret, str) and secret:
                message = message.replace(secret, '[REDACTED]')
        code = getattr(error, 'code', None)
        return {'error_class': type(error).__name__, 'http_status': status,
                'rpc_code': str(getattr(code, 'value', code)) if code is not None else None,
                'error_message': message[:1500]}

    async def list_all(self):
        page = AsyncSandbox.list(query=SandboxQuery(state=[SandboxState.RUNNING, SandboxState.PAUSED]),
                                 limit=100, **self.opts())
        xs = []
        while page.has_next:
            xs.extend(await page.next_items())
        return xs

    async def guard(self):
        xs = await self.list_all()
        foreign = [x.sandbox_id for x in xs if (x.metadata or {}).get('purpose') != PURPOSE]
        if foreign:
            raise ForeignWorkload(f'{len(foreign)} foreign sandbox(es) detected')
        # The two benchmark adapters must not overlap each other either.
        others = [x for x in xs if (x.metadata or {}).get('purpose') == PURPOSE
                  and (x.metadata or {}).get('run_id') != self.run_id]
        if others:
            raise ForeignWorkload('another benchmark run is active')
        proc = await asyncio.create_subprocess_exec(
            '/data2/yuhanya/e2b-local-20260916/code/vm-ssh',
            "awk '/MemAvailable/ {print $2}' /proc/meminfo",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
        if proc.returncode or int(out.strip()) < 128 * 1024 * 1024:
            raise ForeignWorkload('VM memory floor or memory check failure')

    async def background(self):
        last_guard = 0
        while not self.stop.is_set():
            target = time.monotonic() + .1
            await asyncio.sleep(.1)
            self.lags.append(max(0, time.monotonic() - target) * 1000)
            if time.monotonic() - last_guard >= 10:
                last_guard = time.monotonic()
                try:
                    await self.guard()
                except Exception as e:
                    self.guard_error = type(e).__name__
                    self.stop.set()
                    return

    async def create_sdk(self):
        try:
            sb = await AsyncSandbox.create(self.args.template, timeout=1800,
                                           metadata=self.metadata, **self.opts())
        except Exception as error:
            detail = self.error_info(error)
            code = detail['http_status']
            if code is None or code == 408 or (code >= 500 and code != 503):
                self.uncertain_until = max(self.uncertain_until, time.monotonic()+60)
            self.write('create_error', created_handles=len(self.handles),
                       observed_offset_s=self.offset(), **detail)
            raise
        self.handles[sb.sandbox_id] = sb
        return sb

    async def create_raw(self):
        r = await self.http.post('/sandboxes', headers={'X-API-Key': self.cfg['api_key']},
            json={'templateID': self.args.template, 'timeout': 1800, 'secure': True,
                  'allowInternetAccess': True, 'envVars': {}, 'metadata': self.metadata})
        r.raise_for_status()
        d = r.json()
        sid = d['sandboxID']
        self.raw_ids.add(sid)
        return sid

    async def delete_raw(self, sid):
        r = await self.http.delete('/sandboxes/' + sid,
                                  headers={'X-API-Key': self.cfg['api_key']})
        if r.status_code != 404:
            r.raise_for_status()
        self.raw_ids.discard(sid)
        self.handles.pop(sid, None)

    async def exec_raw(self, sb, command):
        headers = dict(sb._envd_api.headers)
        headers.update({'Content-Type': 'application/connect+json', 'Connect-Protocol-Version': '1',
                        'Connect-Timeout-Ms': '15000', 'Authorization': 'Basic cm9vdDo='})
        body = json.dumps({'process': {'cmd': '/bin/bash', 'args': ['-l', '-c', command],
                                      'envs': {}, 'cwd': '/tmp'}, 'stdin': False}).encode()
        payload = struct.pack('>BI', 0, len(body)) + body
        decoder = Frames()
        output, error = bytearray(), bytearray()
        exit_code, first = None, None
        began = time.monotonic()
        async with self.http.stream('POST', self.cfg['sandbox_url'] + '/process.Process/Start',
                                     headers=headers, content=payload) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                for flag, item in decoder.feed(chunk):
                    if flag & 2:
                        if item.get('error'):
                            raise InvalidResult('Connect error: ' + str(item['error'].get('code')))
                        continue
                    event = item.get('event', {})
                    if 'data' in event:
                        for key, dest in [('stdout', output), ('stderr', error)]:
                            val = event['data'].get(key)
                            if val:
                                if first is None: first = (time.monotonic() - began) * 1000
                                dest.extend(base64.b64decode(val, validate=True))
                                if len(output) + len(error) > 1024 * 1024:
                                    raise InvalidResult('command output exceeds scenario bound')
                    if 'end' in event:
                        exit_code = event['end'].get('exitCode', 0)
        decoder.finish()
        if exit_code is None:
            raise InvalidResult('missing process completion event')
        return exit_code, bytes(output), bytes(error), first

    async def command(self, sb, command):
        if self.args.adapter == 'service':
            return await self.exec_raw(sb, command)
        began = time.monotonic()
        first = None
        def on_output(text):
            nonlocal first
            if text and first is None: first = (time.monotonic() - began) * 1000
        r = await sb.commands.run(command, user='root', cwd='/tmp', envs={},
                                  timeout=15, request_timeout=15,
                                  on_stdout=on_output, on_stderr=on_output)
        return r.exit_code, r.stdout.encode(), r.stderr.encode(), first

    async def write_file(self, sb, path, data):
        if self.args.adapter == 'sdk':
            await sb.files.write(path, data, user='root', request_timeout=15)
        else:
            r = await self.http.post(self.cfg['sandbox_url'] + '/files',
                headers=dict(sb._envd_api.headers), params={'username': 'root', 'path': path},
                files={'file': (path, data)}, timeout=15)
            r.raise_for_status()
            if not isinstance(r.json(), list): raise InvalidResult('invalid upload response')

    async def read_file(self, sb, path):
        if self.args.adapter == 'sdk':
            return bytes(await sb.files.read(path, format='bytes', user='root', request_timeout=15))
        r = await self.http.get(self.cfg['sandbox_url'] + '/files',
                headers=dict(sb._envd_api.headers), params={'path': path, 'username': 'root'}, timeout=15)
        r.raise_for_status()
        return r.content

    async def action(self, index, slot):
        name = self.args.scenario
        if name == 'create':
            if self.args.adapter == 'service': sid = await self.create_raw()
            else: sid = (await self.create_sdk()).sandbox_id
            return {'sandbox_id': sid, 'state_check': True}
        sb = list(self.handles.values())[slot % len(self.handles)]
        path = '/tmp/e2b-bench-' + self.run_id + '-' + str(slot)
        data = self.payload
        if name.startswith('exec_'):
            cmd = {'exec_true': 'true', 'exec_output_4k': "python3 -c \"print('x'*4095)\""}[name]
            code, out, err, first = await self.command(sb, cmd)
            expected = b'' if name == 'exec_true' else b'x' * 4095 + b'\n'
            if code != 0 or out != expected or err:
                raise InvalidResult('command output/exit mismatch')
            return {'state_check': True, 'output_bytes': len(out), 'first_output_ms': first,
                    'sandbox_id': sb.sandbox_id}
        if name.startswith('file_read'):
            out = await self.read_file(sb, '/tmp/e2b-bench-source-' + self.run_id)
            if out != data: raise InvalidResult('file content mismatch')
            return {'state_check': True, 'output_bytes': len(out), 'sandbox_id': sb.sandbox_id}
        if name.startswith('file_write'):
            prefix = f'{self.run_id}:{index}:'.encode()
            data = prefix + data[len(prefix):]
            await self.write_file(sb, path, data)
            self.written[(sb.sandbox_id, path)] = data
            # Post-window verification reads every worker's final payload.
            return {'state_check': None, 'input_bytes': len(data), 'sandbox_id': sb.sandbox_id,
                    'validation_scope': 'response plus post-window file content'}
        raise ValueError(name)

    async def measured(self, index, window='measurement', planned=None, slot=0):
        began = self.offset()
        data = {}
        status = 'success'
        try:
            data = await self.action(index, slot)
        except Exception as e:
            status = ('invalid' if isinstance(e, InvalidResult) else
                      'rejected' if getattr(e, 'status_code', None) in (429, 503) or
                       (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (429, 503)) else
                      'timeout' if isinstance(e, (TimeoutError, httpx.TimeoutException)) else 'error')
            data = {**self.error_info(e), 'state_check': False}
            if self.args.scenario == 'create' and status not in ('rejected', 'invalid'):
                self.uncertain_until = time.monotonic() + 60
        end = self.offset()
        row = dict(operation=self.args.scenario, operation_id=f'{self.run_id}:{index}',
                   status=status, start_offset_s=began, end_offset_s=end,
                   duration_ms=(end-began)*1000, window=window, attempt_count=1, worker_id=slot, **data)
        if planned is not None: row['planned_offset_s'] = planned
        self.write('operation', **row)
        if window == 'measurement': self.rows.append(row)
        if self.args.scenario == 'create' and 'sandbox_id' in data:
            cleanup_start = self.offset()
            cleanup_status = 'success'
            try: await self.delete_raw(data['sandbox_id'])
            except Exception: cleanup_status = 'pending'
            self.write('lifecycle_cleanup', duration_ms=(self.offset()-cleanup_start)*1000,
                       status=cleanup_status, operation_id=row['operation_id'])
        return row

    async def prepare(self):
        await self.guard()
        check_sb = await self.create_sdk()
        info = await AsyncSandbox.get_info(check_sb.sandbox_id, **self.opts())
        if info.memory_mb != self.base['memory_mib']:
            raise InvalidResult('template memory does not match experiment metadata')
        self.write('template_check', template_id=info.template_id, memory_mib=info.memory_mb,
                   cpu_count=info.cpu_count, envd_version=info.envd_version)
        await self.delete_raw(check_sb.sandbox_id)
        if self.args.scenario != 'create':
            # Serialized setup is excluded from throughput/latency windows.
            for _ in range(self.args.residents):
                if self.stop.is_set(): raise ForeignWorkload('stopped during setup')
                await self.create_sdk()
            self.payload = b'x' * self.args.bytes
            for sb in self.handles.values():
                await self.write_file(sb, '/tmp/e2b-bench-source-' + self.run_id, self.payload)
            warmup = await self.measured(0, 'warmup')
        else:
            warmup = await self.measured(-1, 'warmup')
        if warmup['status'] != 'success':
            raise InvalidResult('warmup failed; refusing performance measurement')
        await self.guard()

    async def cleanup(self):
        # Reconcile ambiguous creates by exact run metadata, not guessed IDs.
        ok = False
        consecutive_empty = 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            xs = await self.list_all()
            owned = [x for x in xs if (x.metadata or {}).get('run_id') == self.run_id
                     and (x.metadata or {}).get('purpose') == PURPOSE]
            if not owned:
                consecutive_empty += 1
                if consecutive_empty >= 2 and time.monotonic() >= self.uncertain_until:
                    ok = True; break
                await asyncio.sleep(1); continue
            consecutive_empty = 0
            for x in owned:
                with contextlib.suppress(Exception): await self.delete_raw(x.sandbox_id)
            await asyncio.sleep(1)
        return ok

    async def run(self):
        bg = asyncio.create_task(self.background())
        start = end = self.offset()
        problem = None
        post_validation = None
        scheduled = 0
        try:
            await self.prepare()
            start = self.offset()
            deadline = time.monotonic() + self.args.duration if self.args.duration else None
            counter = 0
            async def worker(worker_id):
                nonlocal counter, scheduled
                while not self.stop.is_set():
                    if deadline is not None and time.monotonic() >= deadline: break
                    if deadline is None and counter >= self.args.count: break
                    index = counter; counter += 1; scheduled += 1
                    await self.measured(index, slot=worker_id)
            if self.args.mode == 'closed_loop':
                await asyncio.gather(*(worker(i) for i in range(self.args.concurrency)))
            else:
                active = set()
                slots = asyncio.Queue()
                for slot in range(self.args.concurrency): slots.put_nowait(slot)
                target = time.monotonic()
                for index in range(self.args.count):
                    if self.stop.is_set(): break
                    await asyncio.sleep(max(0, target-time.monotonic()))
                    if self.stop.is_set(): break
                    scheduled += 1
                    planned = target-self.started
                    active = {t for t in active if not t.done()}
                    if len(active) >= self.args.concurrency:
                        self.write('generator_drop', planned_offset_s=planned, reason='inflight_cap')
                    else:
                        slot = slots.get_nowait()
                        async def dispatched(index=index, planned=planned, slot=slot):
                            try: await self.measured(index, planned=planned, slot=slot)
                            finally: slots.put_nowait(slot)
                        t = asyncio.create_task(dispatched()); active.add(t)
                    target += 1/self.args.rate
                await asyncio.gather(*active)
            end = self.offset()
            if self.args.scenario.startswith('file_write'):
                post_validation = False
                for (sid, path), expected in sorted(self.written.items()):
                    sb = self.handles[sid]
                    out = await self.read_file(sb, path)
                    if out != expected: raise InvalidResult('post-window upload validation failed')
                post_validation = True
        except Exception as e:
            problem = type(e).__name__
            end = self.offset()
            self.write('run_error', observed_offset_s=end, created_handles=len(self.handles),
                       **self.error_info(e))
        finally:
            self.stop.set(); bg.cancel()
            with contextlib.suppress(asyncio.CancelledError): await bg
            try: clean = await self.cleanup()
            except Exception: clean = False
            self.write('run', measurement_start_offset_s=start,
                       measurement_end_offset_s=min(end, start+self.args.duration) if self.args.duration else end,
                       drain_end_offset_s=end,
                       scheduled_count=scheduled, dispatched_count=len(self.rows), completed_count=len(self.rows),
                       successful_count=sum(r['status']=='success' for r in self.rows),
                       cleanup_ok=clean, stop_reason=problem or self.guard_error or 'completed',
                       endpoint=self.cfg['api_url'], event_loop_lag_ms={
                           'n':len(self.lags),'max':max(self.lags,default=0),
                           'mean':sum(self.lags)/max(1,len(self.lags))},
                       post_validation_ok=post_validation,
                       throughput_scope='create-delete churn' if self.args.scenario=='create' else 'standalone operation')
            await self.http.aclose(); self.file.close()
        print(json.dumps({'run_id':self.run_id,'path':str(self.path),'n':len(self.rows),
                          'success':sum(r['status']=='success' for r in self.rows),
                          'cleanup_ok':clean,'problem':problem or self.guard_error}),flush=True)
        return 0 if clean and not problem and not self.guard_error else 1


def arguments():
    p = argparse.ArgumentParser()
    p.add_argument('--adapter', choices=['service','sdk'], required=True)
    p.add_argument('--phase', required=True)
    p.add_argument('--scenario', choices=['create','exec_true','exec_output_4k','file_read_1k',
                    'file_write_1k','file_read_1m','file_write_1m'], required=True)
    p.add_argument('--template', default='base-4k-4g')
    p.add_argument('--concurrency', type=int, default=1)
    p.add_argument('--residents', type=int, default=1)
    p.add_argument('--count', type=int, default=100)
    p.add_argument('--duration', type=float, default=0)
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--bytes', type=int, default=1024)
    p.add_argument('--mode', choices=['closed_loop','open_loop'],default='closed_loop')
    p.add_argument('--rate', type=float, default=5)
    p.add_argument('--run-id')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if not 1 <= args.concurrency <= 256 or not 1 <= args.residents <= 256:
        p.error('concurrency/residents must be in [1,256]')
    if args.residents < args.concurrency and args.scenario.startswith('file_write'):
        p.error('file write validation requires residents >= concurrency')
    if args.count < 1 or args.duration < 0 or args.rate <= 0: p.error('invalid run size')
    if args.mode == 'open_loop' and args.duration:
        p.error('open_loop uses count/rate; duration is not allowed')
    if args.scenario.startswith('file_'):
        args.bytes = 1024*1024 if args.scenario.endswith('1m') else 1024
    return args


async def main():
    bench=Bench(arguments())
    for sig in (signal.SIGINT,signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig,bench.stop.set)
    return await bench.run()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
