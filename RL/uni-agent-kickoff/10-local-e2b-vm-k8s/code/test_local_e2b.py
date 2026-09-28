"""Real SDK tests against this local deployment; no model or inference calls."""
import argparse
import asyncio
import importlib.metadata
import json
import time
from pathlib import Path
import uuid

import httpx
from e2b import Sandbox, AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException


def settings():
    config = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
    assert config['api_url'] == config['sandbox_url'] == 'http://10.145.64.67:8088'
    return config


def basic(config):
    checks = []
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(config['api_url'] + '/sandboxes', headers={'X-API-KEY': 'invalid-local-test-key'})
        assert response.status_code in (401, 403), response.status_code
        checks.append('invalid API key rejected')
    sb = Sandbox.create('base', timeout=180, metadata={'purpose': 'local-e2b-functional-test'},
                        request_timeout=60, **config)
    sid = sb.sandbox_id
    began = time.monotonic()
    try:
        result = sb.commands.run("printf 'local-e2b-ok\\n'; uname -s", timeout=20)
        assert result.exit_code == 0 and 'local-e2b-ok\nLinux' in result.stdout
        checks.append('real Linux command execution')
        payload = 'Hello E2B / 中文文件回读\n'
        path = '/tmp/e2b-unicode-' + uuid.uuid4().hex
        sb.files.write(path, payload)
        assert sb.files.read(path) == payload
        checks.append('UTF-8 file roundtrip')
        sb.files.write('/tmp/e2b-binary', bytes(range(256)))
        assert sb.files.read('/tmp/e2b-binary', format='bytes') == bytes(range(256))
        checks.append('binary file roundtrip')
        try:
            sb.commands.run('printf partial; printf expected >&2; exit 7', timeout=10)
        except CommandExitException as error:
            assert error.exit_code == 7 and error.stdout == 'partial' and error.stderr == 'expected'
        else:
            raise AssertionError('Nonzero exit did not preserve the SDK exception contract')
        checks.append('exit 7 and separate stdout/stderr preserved')
        result = sb.commands.run('printf "$E2B_TEST_VALUE"; pwd', cwd='/tmp', envs={'E2B_TEST_VALUE': 'env-ok\n'}, timeout=10)
        assert result.stdout == 'env-ok\n/tmp\n'
        checks.append('cwd and environment')
        sb.set_timeout(240)
        assert sb.is_running()
        checks.append('timeout renewal and liveness')
        background = sb.commands.run('python3 -m http.server 18080 --directory /tmp', background=True)
        try:
            with httpx.Client(timeout=15, trust_env=False) as client:
                for attempt in range(20):
                    response = client.get(config['sandbox_url'] + '/e2b-binary', headers={
                        'E2b-Sandbox-Id': sid, 'E2b-Sandbox-Port': '18080'})
                    if response.status_code == 200:
                        break
                    time.sleep(.25)
                assert response.status_code == 200 and response.content == bytes(range(256))
            checks.append('sandbox HTTP port through the same public endpoint')
        finally:
            background.kill()
    finally:
        sb.kill()
    assert not sb.is_running()
    checks.append('sandbox deleted and confirmed absent')
    return {'checks': checks, 'sandbox_id': sid, 'wall_seconds': time.monotonic() - began}


async def concurrent(config, count):
    sem = asyncio.Semaphore(2)
    sandboxes = []
    began = time.monotonic()
    try:
        async def create(index):
            async with sem:
                sb = await AsyncSandbox.create('base', timeout=180,
                    metadata={'purpose': 'local-e2b-concurrency', 'index': str(index)},
                    request_timeout=60, **config)
                sandboxes.append(sb)
                return sb
        items = await asyncio.gather(*(create(i) for i in range(count)), return_exceptions=True)
        failures = [type(x).__name__ for x in items if isinstance(x, BaseException)]
        if failures:
            raise RuntimeError('Concurrent create failed: ' + ', '.join(failures))
        assert len({x.sandbox_id for x in items}) == count
        async def run(index, sb):
            value = f'isolated-{index}-{uuid.uuid4().hex}'
            await sb.files.write('/tmp/isolation-marker', value)
            result = await sb.commands.run('sleep 2; cat /tmp/isolation-marker', timeout=20)
            assert result.stdout == value and result.exit_code == 0
            return {'sandbox_id': sb.sandbox_id, 'index': index, 'isolated': True}
        checks = await asyncio.gather(*(run(i, sb) for i, sb in enumerate(items)), return_exceptions=True)
        failures = [type(x).__name__ for x in checks if isinstance(x, BaseException)]
        if failures:
            raise RuntimeError('Concurrent command failed: ' + ', '.join(failures))
    finally:
        cleanups = await asyncio.gather(*(sb.kill() for sb in sandboxes), return_exceptions=True)
        assert not any(isinstance(x, BaseException) for x in cleanups), 'Cleanup failure'
    return {'concurrent_running_sandboxes': count, 'create_parallelism': 2,
            'isolated_results': checks, 'all_cleaned': True, 'wall_seconds': time.monotonic() - began}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = settings()
    report = {'sdk_version': importlib.metadata.version('e2b'), 'endpoint': config['api_url'],
              'scope': 'real sandbox service; no LLM inference or RL training', 'status': 'running'}
    try:
        report['basic'] = basic(config)
        report['concurrency'] = asyncio.run(concurrent(config, args.concurrency))
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
