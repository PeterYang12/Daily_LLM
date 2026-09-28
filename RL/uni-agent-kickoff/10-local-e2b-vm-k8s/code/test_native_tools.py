"""Replay all nine former Uni-Agent/E2B tool checks against the local endpoint."""
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path[:0] = ['/data2/yuhanya/uni-agent-lab/src/uni-agent',
               '/data2/yuhanya/swe-e2b-rl-20260914/code']
from uni_agent.tools import Toolbox
from swe_e2b_sandbox import SWEE2BSandbox

ROOT = Path('/data2/yuhanya/e2b-local-20260916')

async def main():
    config = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
    assert config['api_url'] == config['sandbox_url'] == 'http://10.145.64.67:8088'
    started = time.monotonic()
    checks = []
    sb = SWEE2BSandbox(image='swebench/sweb.eval.x86_64.pallets_1776_flask-5014',
        runtime_timeout=300, template_map_path=str(ROOT / 'configs/template-map.json'),
        credentials_file='/home/yuhanya/.config/e2b-local-20260916/lab.json',
        api_url=config['api_url'], sandbox_url=config['sandbox_url'])
    async with sb:
        sid = sb._sandbox.sandbox_id
        result = await sb.exec(['printf', '%s', 'literal $(id); space \' "'])
        assert result.stdout == 'literal $(id); space \' "'
        checks.append('literal argv preserved')
        result = await sb.exec_shell('printf "$ADAPTER_CHECK"; pwd; id -u', env={'ADAPTER_CHECK': 'env-ok\n'})
        assert result.stdout == 'env-ok\n/testbed\n0\n'
        checks.append('root, cwd, and env')
        await sb.write_file('/tmp/adapter-binary.bin', bytes(range(256)))
        assert await sb.read_file('/tmp/adapter-binary.bin') == bytes(range(256))
        checks.append('binary file roundtrip')
        result = await sb.exec_shell('printf partial; printf expected >&2; exit 7')
        assert (result.exit_code, result.stdout, result.stderr) == (7, 'partial', 'expected')
        checks.append('nonzero exit and streams preserved')
        toolbox = Toolbox.from_specs([{'name': 'stateful_shell', 'command_timeout': 10},
                    {'name': 'str_replace_editor'}, {'name': 'submit'}], sandbox=sb)
        async with toolbox.entered(retry=1, timeout=60):
            result = await toolbox.call('shell', {'command': 'cd /tmp; export PERSISTENT_CHECK=stateful'})
            assert result.status == 'ok'
            result = await toolbox.call('shell', {'command': 'printf "$PERSISTENT_CHECK"; pwd'})
            assert 'stateful/tmp' in result.to_observation()
            checks.append('native tmux shell preserves cwd and env')
            result = await toolbox.call('str_replace_editor', {'command': 'create', 'path': '/tmp/adapter-editor.txt', 'file_text': 'old text\n'})
            assert result.status == 'ok'
            result = await toolbox.call('str_replace_editor', {'command': 'str_replace', 'path': '/tmp/adapter-editor.txt', 'old_str': 'old text', 'new_str': 'new text'})
            assert result.status == 'ok'
            assert await sb.read_file('/tmp/adapter-editor.txt') == b'new text\n'
            checks.append('native editor create and replacement')
            result = await toolbox.call('submit', {})
            assert result.status == 'ok'
            checks.append('native submit')
        assert await sb.is_alive()
        checks.append('liveness')
    assert sb._sandbox is None
    checks.append('cleanup')
    report = {'status': 'passed', 'scope': 'Original Uni-Agent tools and E2B adapter; no inference or RL',
              'endpoint': config['api_url'], 'sandbox_id': sid, 'checks': checks,
              'wall_seconds': time.monotonic() - started}
    (ROOT / 'results/native-tool-tests.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))

if __name__ == '__main__':
    asyncio.run(main())
