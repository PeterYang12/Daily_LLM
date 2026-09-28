"""The official four-operation Python SDK baseline with durable benchmark records.

Source: e2b-dev/E2B packages/code-interpreter-python/tests/performance.py at
ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b. Each iteration creates a sandbox,
runs curl against its code-interpreter health route, executes Hello World twice,
then deletes the sandbox. Unlike upstream this adapter checks results, uses a
monotonic clock, records errors, and cleans up in finally. It never creates a
template or changes the service configuration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import signal
import time
import uuid

import httpx
from e2b import SandboxQuery, SandboxState
from e2b_code_interpreter import Sandbox


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = 'https://github.com/e2b-dev/E2B/blob/ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b/packages/code-interpreter-python/tests/performance.py'
CODE = "print('Hello, world!')"
HEALTH_COMMAND = 'curl http://0.0.0.0:49999/health'


class InvalidResult(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def error_status(error):
    code = getattr(error, 'status_code', None)
    if isinstance(error, InvalidResult):
        return 'invalid'
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return 'cancelled'
    if 'timeout' in type(error).__name__.lower() or isinstance(error, TimeoutError):
        return 'timeout'
    if code in (429, 503):
        return 'rejected'
    return 'error'


def load_config(path):
    values = json.loads(path.read_text())
    return {key: values[key] for key in ('api_key', 'api_url', 'sandbox_url')}


def check_foreign(config, permitted_run_prefix='session-20260920'):
    response = httpx.get(config['api_url'] + '/sandboxes',
                        headers={'X-API-KEY': config['api_key']}, timeout=20,
                        trust_env=False)
    response.raise_for_status()
    foreign = [entry for entry in response.json() if not (
        str((entry.get('metadata') or {}).get('purpose', '')).startswith('benchmark')
        and str((entry.get('metadata') or {}).get('run_id', '')).startswith(permitted_run_prefix))]
    if foreign:
        raise RuntimeError('Foreign sandbox present; baseline paused before starting workloads.')


def run_baseline(config, template, output, *, iterations=20, repeat=1,
                 run_id=None, memory_mib=4096, runtime='local-4k-512-20260920',
                 session_id='session-20260920', event_sink=None,
                 sandbox_class=Sandbox):
    """Run a serial closed-loop baseline. Config values are never written.

    sandbox_class and event_sink allow offline lifecycle/schema verification.
    Per-call create retries are disabled; uncertain creates are reconciled only
    by this run's metadata, without retrying create.
    """
    if iterations < 1:
        raise ValueError('iterations must be positive')
    run_id = run_id or f'{session_id}-ci-{uuid.uuid4().hex[:12]}'
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    run_started_utc = utc_now()
    common = {
        'run_id': run_id, 'phase': 'phase2-sdk',
        'scenario': 'official_code_interpreter_hello', 'template': template,
        'memory_mib': memory_mib, 'concurrency': 1, 'mode': 'closed_loop',
        'instrumentation': 'minimal', 'repeat': repeat, 'endpoint': config['api_url'],
        'measurement_kind': 'finite_batch', 'resident_count': 1,
        'run_started_utc': run_started_utc,
        'sdk': {name: importlib.metadata.version(name) for name in ('e2b', 'e2b-code-interpreter', 'httpx')},
        'runtime': runtime, 'page_mode': 'ordinary-4KiB', 'upstream_source': UPSTREAM,
        'workload': 'create -> curl /health -> run_code Hello World twice -> kill',
    }
    metadata = {'purpose': 'benchmark-phase2-sdk', 'run_id': run_id,
                'benchmark_session': session_id}
    operations = []
    cleanup_records = []
    stop_reason = 'completed'
    uncertain_create = False
    measurement_start = None
    measurement_end = None
    opts = {**config, 'request_timeout': 90, 'retries': 0}
    output_handle = output.open('x')

    def emit(record):
        output_handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        output_handle.flush()
        if event_sink:
            event_sink(record)

    def operate(name, iteration, function, sbx=None, validate=None):
        nonlocal measurement_start, measurement_end
        start = time.monotonic()
        if measurement_start is None:
            measurement_start = start - began
        record = {**common, 'record_type': 'operation', 'operation': name,
                  'operation_id': f'{run_id}:{iteration}:{name}',
                  'iteration': iteration, 'status': 'success',
                  'window': 'measurement', 'attempt_count': 1,
                  'start_offset_s': start - began, 'utc': utc_now()}
        if sbx is not None:
            record['sandbox_id'] = sbx.sandbox_id
        try:
            result = function()
            returned = time.monotonic()
            if validate:
                record.update(validate(result))
                record['state_check'] = True
            if name == 'create':
                if not getattr(result, 'sandbox_id', None):
                    raise InvalidResult('Create returned no sandbox ID')
                record['sandbox_id'] = result.sandbox_id
                record['state_check'] = True
            return result
        except BaseException as error:
            returned = time.monotonic()
            record.update(status=error_status(error), error_type=type(error).__name__,
                          error_class=type(error).__name__,
                          error=str(error).replace(config['api_key'], '<redacted>')[:1500])
            if isinstance(error, InvalidResult):
                record['state_check'] = False
            raise
        finally:
            record.update(end_offset_s=returned - began,
                          duration_ms=(returned - start) * 1000)
            measurement_end = returned - began
            operations.append(record)
            emit(record)

    def validate_health(value):
        if value.exit_code != 0 or value.stdout.strip() not in ('"OK"', 'OK'):
            raise InvalidResult('Code-interpreter /health did not return OK with exit code 0')
        return {'exit_code': value.exit_code, 'stdout_bytes': len(value.stdout.encode())}

    def validate_code(value):
        if value.error is not None:
            raise InvalidResult('run_code returned an execution error: ' + str(value.error))
        text = ''.join(value.logs.stdout)
        if text != 'Hello, world!\n':
            raise InvalidResult('run_code stdout did not match the expected Hello World line')
        return {'stdout_bytes': len(text.encode()), 'execution_count': value.execution_count}

    def cleanup(sbx, iteration):
        start = time.monotonic()
        record = {**common, 'record_type': 'cleanup', 'iteration': iteration,
                  'operation': 'kill', 'sandbox_id': sbx.sandbox_id,
                  'window': 'cleanup', 'status': 'success', 'start_offset_s': start - began}
        try:
            sbx.kill(request_timeout=30)
        except BaseException as error:
            record.update(status=error_status(error), error_type=type(error).__name__,
                          error=str(error).replace(config['api_key'], '<redacted>')[:1500])
        record.update(end_offset_s=time.monotonic() - began,
                      duration_ms=(time.monotonic() - start) * 1000)
        cleanup_records.append(record)
        emit(record)
        return record['status'] == 'success'

    emit({**common, 'record_type': 'run_start', 'started_utc': utc_now(),
          'iterations': iterations, 'cleanup_metadata': metadata})
    try:
        for iteration in range(1, iterations + 1):
            sbx = None
            try:
                uncertain_create = True
                sbx = operate('create', iteration, lambda: sandbox_class.create(
                    template=template, timeout=600, metadata=metadata, **opts))
                uncertain_create = False
                operate('health_check', iteration, lambda: sbx.commands.run(
                    HEALTH_COMMAND, timeout=30, request_timeout=20), sbx, validate_health)
                operate('run_code_first', iteration, lambda: sbx.run_code(
                    CODE, timeout=60, request_timeout=30), sbx, validate_code)
                operate('run_code_second', iteration, lambda: sbx.run_code(
                    CODE, timeout=60, request_timeout=30), sbx, validate_code)
            finally:
                if sbx is not None and not cleanup(sbx, iteration):
                    raise RuntimeError('Sandbox cleanup failed; no further measurement iterations')
    except BaseException as error:
        stop_reason = error_status(error) + ': ' + type(error).__name__
    finally:
        # Do not resend an ambiguous create. Its metadata permits cleanup after
        # the server's own create deadline; final list also verifies no residue.
        deadline = time.monotonic() + (150 if uncertain_create else 10)
        owned_remaining = None
        cleanup_ok = all(row['status'] == 'success' for row in cleanup_records)
        while True:
            try:
                paginator = sandbox_class.list(query=SandboxQuery(metadata=metadata,
                    state=[SandboxState.RUNNING]), limit=100, **{**opts, 'request_timeout': 20})
                owned = []
                while paginator.has_next:
                    owned.extend(item for item in paginator.next_items()
                                 if all((item.metadata or {}).get(k) == v for k, v in metadata.items()))
                for info in owned:
                    sandbox_class.kill(info.sandbox_id, **{**opts, 'request_timeout': 30})
                owned_remaining = len(owned)
                if not uncertain_create and not owned:
                    break
            except BaseException as error:
                cleanup_ok = False
                emit({**common, 'record_type': 'cleanup_error',
                      'error_type': type(error).__name__,
                      'error': str(error).replace(config['api_key'], '<redacted>')[:1500]})
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        cleanup_ok = cleanup_ok and owned_remaining == 0
        final = {**common, 'record_type': 'run', 'finished_utc': utc_now(),
                 'measurement_start_offset_s': measurement_start,
                 'measurement_end_offset_s': measurement_end,
                 'scheduled_count': iterations * 4, 'dispatched_count': len(operations),
                 'completed_count': len(operations),
                 'successful_count': sum(row['status'] == 'success' for row in operations),
                 'failed_count': sum(row['status'] != 'success' for row in operations),
                 'cleanup_ok': cleanup_ok, 'owned_remaining': owned_remaining,
                 'stop_reason': stop_reason, 'iterations': iterations,
                 'measurement_window_note': 'All four operations; final end is before final cleanup. Between-iteration cleanup gaps remain in wall window.',
                 'wall_seconds_including_cleanup': time.monotonic() - began}
        emit(final)
        output_handle.close()
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/home/yuhanya/.config/e2b-local-20260916/client.json'))
    parser.add_argument('--template', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=20)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--run-id')
    parser.add_argument('--memory-mib', type=int, default=4096)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    check_foreign(config)
    def stop(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, stop)
    result = run_baseline(config, args.template, args.output,
                          iterations=args.iterations, repeat=args.repeat,
                          run_id=args.run_id, memory_mib=args.memory_mib)
    print(json.dumps(result), flush=True)
    return 0 if result['stop_reason'] == 'completed' and result['cleanup_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
