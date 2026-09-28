"""Serial API pause/connect timings with disk AND live-process state validation.

Default measured control operations are raw HTTP calls, using the exact SDK API
contracts: POST /sandboxes/{id}/pause {"memory":true} -> 204, then
POST /sandboxes/{id}/connect {"timeout":600} -> 201 when a paused VM is restored.
The third measured stage is an SDK command that checks a live in-memory token,
process PID, guest boot ID, incrementing counter and a per-round file marker.

SDK create, file injection, marker updates, paused-state listing and cleanup are
separately labeled setup/validation/cleanup. A paired --adapter sdk mode uses
public SDK pause/connect with the same state probe. No daemon settings change.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time
import uuid

import httpx
from packaging.version import Version
from e2b import Sandbox, SandboxQuery, SandboxState
from e2b.connection_config import ConnectionConfig


PURPOSE = 'e2b-benchmark-20260920'
CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')
VM_SSH = '/data2/yuhanya/e2b-local-20260916/code/vm-ssh'
PORT = 18187

# The token is generated inside the resident process and never written to disk.
# A disk-only reboot/restart cannot preserve it. The same process serves all
# rounds; every response increments a private counter once.
STATE_SERVER = r'''import http.server,json,os,secrets,sys
from pathlib import Path
token=secrets.token_hex(24)
counter=0
marker=Path(sys.argv[2])
boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  global counter
  counter+=1
  payload=json.dumps({'token':token,'counter':counter,'pid':os.getpid(),
                      'boot_id':boot_id,'marker':json.loads(marker.read_text())}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(payload)))
  self.end_headers();self.wfile.write(payload)
 def log_message(self,*args):pass
http.server.HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
'''
PROBE_CODE = f"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{PORT}/',timeout=10).read().decode())"
PROBE_COMMAND = shlex.join(['python3', '-c', PROBE_CODE])


class InvalidResult(RuntimeError):
    pass


class ForeignWorkload(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def classify(error):
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return 'cancelled'
    if isinstance(error, InvalidResult):
        return 'invalid'
    if isinstance(error, (httpx.TimeoutException, TimeoutError)) or 'timeout' in type(error).__name__.lower():
        return 'timeout'
    response = getattr(error, 'response', None)
    code = getattr(response, 'status_code', getattr(error, 'status_code', None))
    return 'rejected' if code in (429, 503) else 'error'


def validate_state(value, previous, marker):
    """Pure validation helper, also used by offline checks."""
    for field in ('token', 'pid', 'boot_id'):
        if not value.get(field) or value.get(field) != previous.get(field):
            raise InvalidResult('Live process state changed after resume: ' + field)
    if value.get('counter') != previous.get('counter', -1) + 1:
        raise InvalidResult('In-memory counter was not preserved across resume')
    if value.get('marker') != marker:
        raise InvalidResult('File marker was not preserved across resume')
    return {
        'state_check': True, 'memory_preserved': True, 'file_preserved': True,
        'guest_process_pid': value['pid'], 'counter': value['counter'],
        'token_sha256': hashlib.sha256(value['token'].encode()).hexdigest(),
    }


def handle_from_connect(data, config):
    """Mirror SDK connect's local construction without making another API call."""
    sid = data['sandboxID']
    token = data.get('envdAccessToken')
    headers = {'E2b-Sandbox-Id': sid, 'E2b-Sandbox-Port': str(ConnectionConfig.envd_port)}
    if token:
        headers['X-Access-Token'] = token
    connection = ConnectionConfig(extra_sandbox_headers=headers, request_timeout=20,
                                  retries=0, **config)
    return Sandbox(sandbox_id=sid, sandbox_domain=data.get('domain'),
                   envd_version=Version(data['envdVersion']),
                   envd_access_token=token, traffic_access_token=data.get('trafficAccessToken'),
                   connection_config=connection)


class LifecycleBench:
    def __init__(self, args):
        self.args = args
        values = json.loads(args.config.read_text())
        self.config = {k: values[k] for k in ('api_key', 'api_url', 'sandbox_url')}
        self.run_id = args.run_id or uuid.uuid4().hex
        self.started = time.monotonic()
        self.started_utc = utc_now()
        self.metadata = {'purpose': PURPOSE, 'run_id': self.run_id}
        self.common = {
            'run_id': self.run_id, 'phase': args.phase,
            'scenario': 'lifecycle_memory_roundtrip', 'comparison_key': 'lifecycle-memory-v1',
            'template': args.template, 'memory_mib': 4096, 'concurrency': 1,
            'resident_count': 1, 'payload_bytes': 0, 'mode': 'closed_loop',
            'instrumentation': 'minimal', 'repeat': args.repeat,
            'measurement_kind': 'finite_batch', 'run_started_utc': self.started_utc,
            'sdk_version': importlib.metadata.version('e2b'),
            'runtime_version': 'local-4k-512-20260920', 'page_mode': '4KiB',
            'adapter': args.adapter,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        self.file = args.output.open('x', buffering=1)
        os.chmod(args.output, 0o600)
        self.http = httpx.Client(base_url=self.config['api_url'], trust_env=False,
                                 timeout=args.request_timeout,
                                 transport=httpx.HTTPTransport(retries=0),
                                 headers={'X-API-Key': self.config['api_key']})
        self.sandbox = None
        self.known_id = None
        self.uncertain_until = 0.0
        self.rows = []
        self.stop = False
        self.write('metadata', endpoint=self.config['api_url'], iterations=args.iterations,
                   client_transport='raw-http-controls+sdk-state-probe' if args.adapter == 'service' else 'public-sdk',
                   measured_stages=['pause', 'resume_connect', 'ready_state_probe'],
                   state_scope='resident process token/PID/bootID/counter plus per-round file marker',
                   attempt_scope='one application attempt; no mutation retry', pid=os.getpid())

    def offset(self):
        return time.monotonic() - self.started

    def opts(self):
        return {**self.config, 'request_timeout': self.args.request_timeout, 'retries': 0}

    def write(self, kind, **fields):
        self.file.write(json.dumps({'record_type': kind, **self.common, **fields}) + '\n')

    def list_all(self):
        paginator = Sandbox.list(query=SandboxQuery(state=[SandboxState.RUNNING, SandboxState.PAUSED]),
                                 limit=100, **self.opts())
        rows = []
        while paginator.has_next:
            rows.extend(paginator.next_items())
        return rows

    def owns(self, info):
        return all((info.metadata or {}).get(key) == value for key, value in self.metadata.items())

    def guard(self, check_memory=False):
        if self.stop:
            raise KeyboardInterrupt('Stop requested')
        rows = self.list_all()
        if any(not self.owns(info) for info in rows):
            raise ForeignWorkload('Another user or benchmark sandbox is present; no further mutations')
        if check_memory:
            result = subprocess.run([VM_SSH, "awk '/MemAvailable/ {print $2}' /proc/meminfo"],
                                    text=True, capture_output=True, timeout=20, check=True)
            if int(result.stdout.strip()) < 128 * 1024 * 1024:
                raise ForeignWorkload('VM available memory is below 128 GiB')

    def measured(self, operation, iteration, function, validate=None):
        start = self.offset()
        row = {'operation': operation, 'operation_id': f'{self.run_id}:{iteration}:{operation}',
               'iteration': iteration, 'window': 'measurement', 'attempt_count': 1,
               'status': 'success', 'start_offset_s': start, 'sandbox_id': self.known_id}
        result = None
        try:
            result = function()
            end = self.offset()
            if validate:
                row.update(validate(result))
        except BaseException as error:
            end = self.offset()
            row.update(status=classify(error), state_check=False,
                       error_class=type(error).__name__,
                       error=str(error).replace(self.config['api_key'], '<redacted>')[:1000])
            response = getattr(error, 'response', None)
            if response is not None:
                row['http_status'] = response.status_code
            raise
        finally:
            row.update(end_offset_s=end, duration_ms=(end-start)*1000)
            self.rows.append(row)
            self.write('operation', **row)
        return result

    def probe(self):
        response = self.sandbox.commands.run(PROBE_COMMAND, user='root', timeout=30, request_timeout=20)
        if response.exit_code != 0:
            raise InvalidResult('State probe command failed')
        return json.loads(response.stdout)

    def setup(self):
        self.guard(check_memory=True)
        start = self.offset()
        self.uncertain_until = time.monotonic() + 150
        self.sandbox = Sandbox.create(self.args.template, timeout=600,
                                      metadata=self.metadata, **self.opts())
        self.known_id = self.sandbox.sandbox_id
        self.uncertain_until = 0
        info = self.sandbox.get_info(request_timeout=20)
        if info.memory_mb != 4096 or info.cpu_count != 2:
            raise InvalidResult('Template does not match the declared 2 vCPU / 4096 MiB fixture')
        self.marker_path = '/tmp/e2b-benchmark-lifecycle-' + self.run_id + '.json'
        self.script_path = '/tmp/e2b-benchmark-lifecycle-' + self.run_id + '.py'
        marker = {'run_id': self.run_id, 'round': 0}
        self.sandbox.files.write(self.marker_path, json.dumps(marker), user='root')
        self.sandbox.files.write(self.script_path, STATE_SERVER, user='root')
        self.sandbox.commands.run(shlex.join(['python3', self.script_path, str(PORT), self.marker_path]),
                                  background=True, user='root', timeout=0, request_timeout=20)
        deadline = time.monotonic() + 10
        while True:
            try:
                previous = self.probe()
                if previous['marker'] != marker or not previous['token'] or previous['counter'] != 1:
                    raise InvalidResult('Initial resident process state is invalid')
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        self.write('setup', operation='create_and_seed_process', sandbox_id=self.known_id,
                   start_offset_s=start, end_offset_s=self.offset(), state_check=True,
                   window='setup', guest_process_pid=previous['pid'],
                   token_sha256=hashlib.sha256(previous['token'].encode()).hexdigest())
        return previous

    def pause_raw(self):
        self.uncertain_until = time.monotonic() + 150
        response = self.http.post(f'/sandboxes/{self.known_id}/pause', json={'memory': True})
        response.raise_for_status()
        if response.status_code != 204:
            raise InvalidResult('Pause did not return HTTP 204')
        self.uncertain_until = 0
        return {'http_status': 204, 'state_check': True}

    def connect_raw(self):
        self.uncertain_until = time.monotonic() + 150
        response = self.http.post(f'/sandboxes/{self.known_id}/connect', json={'timeout': 600})
        response.raise_for_status()
        if response.status_code != 201:
            raise InvalidResult('Connect was not a paused-instance restore (expected HTTP 201)')
        data = response.json()
        if data.get('sandboxID') != self.known_id:
            raise InvalidResult('Connect returned a different sandbox ID')
        self.uncertain_until = 0
        return data

    def pause_sdk(self):
        self.uncertain_until = time.monotonic() + 150
        result = self.sandbox.pause(keep_memory=True, request_timeout=self.args.request_timeout, retries=0)
        if result is not True:
            raise InvalidResult('SDK pause did not report a new pause')
        self.uncertain_until = 0
        return {'state_check': True}

    def connect_sdk(self):
        self.uncertain_until = time.monotonic() + 150
        result = Sandbox.connect(self.known_id, timeout=600, **self.opts())
        if result.sandbox_id != self.known_id:
            raise InvalidResult('SDK connect returned a different sandbox ID')
        self.uncertain_until = 0
        return result

    def verify_paused(self, iteration):
        deadline = time.monotonic() + 5
        while True:
            owned = [info for info in self.list_all() if self.owns(info)]
            if len(owned) == 1 and owned[0].sandbox_id == self.known_id and owned[0].state == SandboxState.PAUSED:
                self.write('validation', window='validation', iteration=iteration,
                           operation='paused_state', state_check=True, end_offset_s=self.offset())
                return
            if time.monotonic() >= deadline:
                raise InvalidResult('Pause response succeeded but listing did not show exactly one paused sandbox')
            time.sleep(.1)

    def cleanup(self):
        start = self.offset()
        deadline = max(time.monotonic() + 10, self.uncertain_until)
        remaining = None
        while True:
            owned = [info for info in self.list_all() if self.owns(info)]
            for info in owned:
                Sandbox.kill(info.sandbox_id, **self.opts())
            remaining = len(owned)
            if not owned and time.monotonic() >= self.uncertain_until:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
        self.write('cleanup', window='cleanup', start_offset_s=start, end_offset_s=self.offset(),
                   cleanup_ok=remaining == 0, owned_remaining=remaining)
        return remaining == 0

    def run(self):
        start = end = self.offset()
        stop_reason = 'completed'
        scheduled = 0
        try:
            previous = self.setup()
            start = self.offset()
            for iteration in range(1, self.args.iterations+1):
                self.guard(check_memory=iteration % 10 == 0)
                marker = {'run_id': self.run_id, 'round': iteration}
                self.sandbox.files.write(self.marker_path, json.dumps(marker), user='root')
                self.write('setup', operation='round_marker_update', iteration=iteration,
                           window='setup', end_offset_s=self.offset())
                scheduled += 1
                self.measured('pause', iteration,
                              self.pause_raw if self.args.adapter == 'service' else self.pause_sdk,
                              validate=lambda result: result)
                self.verify_paused(iteration)
                scheduled += 1
                if self.args.adapter == 'service':
                    data = self.measured('resume_connect', iteration, self.connect_raw,
                        validate=lambda data: {'state_check': True, 'http_status': 201})
                    # Tokens remain in memory only; do not persist raw connect responses.
                    self.sandbox = handle_from_connect(data, self.config)
                else:
                    self.sandbox = self.measured('resume_connect', iteration, self.connect_sdk,
                                                  validate=lambda sb: {'state_check': True})
                scheduled += 1
                previous = self.measured('ready_state_probe', iteration, self.probe,
                    validate=lambda value: validate_state(value, previous, marker))
            end = self.offset()
        except BaseException as error:
            stop_reason = classify(error) + ': ' + type(error).__name__
            end = self.offset()
            self.write('run_error', error_class=type(error).__name__,
                       error=str(error).replace(self.config['api_key'], '<redacted>')[:1000],
                       end_offset_s=end)
        finally:
            try:
                cleanup_ok = self.cleanup()
            except BaseException as error:
                cleanup_ok = False
                self.write('cleanup_error', error_class=type(error).__name__,
                           error=str(error).replace(self.config['api_key'], '<redacted>')[:1000])
            self.write('run', measurement_start_offset_s=start, measurement_end_offset_s=end,
                       scheduled_count=scheduled, dispatched_count=len(self.rows), completed_count=len(self.rows),
                       successful_count=sum(row['status'] == 'success' for row in self.rows),
                       cleanup_ok=cleanup_ok, stop_reason=stop_reason,
                       endpoint=self.config['api_url'], iterations=self.args.iterations,
                       roundtrips_completed=sum(row['operation']=='ready_state_probe' and row['status']=='success' for row in self.rows),
                       throughput_scope='three-stage lifecycle rounds including between-stage validation/marker updates; stage latency excludes those gaps')
            self.http.close()
            self.file.close()
        result = {'run_id': self.run_id, 'rows': len(self.rows), 'cleanup_ok': cleanup_ok,
                  'stop_reason': stop_reason, 'output': str(self.args.output)}
        print(json.dumps(result), flush=True)
        return 0 if cleanup_ok and stop_reason == 'completed' else 1


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--template', default='base-4k-4g')
    parser.add_argument('--adapter', choices=['service', 'sdk'], default='service')
    parser.add_argument('--phase', default='phase1-service')
    parser.add_argument('--iterations', type=int, default=20)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--request-timeout', type=float, default=90)
    parser.add_argument('--run-id')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.iterations <= 100 or not 10 <= args.request_timeout <= 120:
        parser.error('iterations must be 1..100; request timeout must be 10..120 seconds')
    return args


def main(argv=None):
    benchmark = LifecycleBench(arguments(argv))
    def stop(signum, frame):
        benchmark.stop = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return benchmark.run()


if __name__ == '__main__':
    raise SystemExit(main())
