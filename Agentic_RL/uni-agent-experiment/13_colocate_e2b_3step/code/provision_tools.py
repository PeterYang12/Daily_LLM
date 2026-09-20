import asyncio
import json
import time
from e2b import AsyncSandbox
from runtime_common import ROOT, RUN_ID, api_options, event, load_secrets, save


async def main():
    load_secrets()
    task = json.loads((ROOT/'data/scheduled-tasks.json').read_text())[0]
    iid = task['metadata']['instance_id']
    template = json.loads((ROOT/'data/reusable-templates.json').read_text())[iid]['template_id']
    box = await AsyncSandbox.create(template=template, timeout=900, request_timeout=120,
        metadata={'experiment': RUN_ID, 'phase': 'tool-provision'}, **api_options())
    result = {'sandbox_id': box.sandbox_id, 'template_id': template}
    try:
        info = await box.commands.run('cat /etc/os-release; uname -m; test -d /testbed/.git; command -v tmux || true',
                                      user='root', timeout=30)
        print(info.stdout, flush=True)
        result['environment'] = info.stdout
        os_release = str(await box.files.read('/etc/os-release', user='root'))
        values = dict(line.split('=', 1) for line in os_release.splitlines() if '=' in line)
        key = values['ID'].strip('"') + '-' + values['VERSION_ID'].strip('"')
        folder = ROOT/'data/tools'/key
        folder.mkdir(parents=True, exist_ok=True)
        command = ('set -e; export DEBIAN_FRONTEND=noninteractive; '
                   'apt-get update -qq; apt-get --download-only -y install tmux; '
                   'tar -czf /tmp/ua-tmux-debs.tgz -C /var/cache/apt/archives --exclude=partial --exclude=lock .; '
                   'apt-get install -y tmux; tmux -V')
        response = await box.commands.run(command, user='root', timeout=600)
        result['install_stdout'] = response.stdout
        result['install_stderr'] = response.stderr
        print(response.stdout[-2500:], flush=True)
        bundle = await box.files.read('/tmp/ua-tmux-debs.tgz', format='bytes', user='root')
        (folder/'tmux-debs.tgz').write_bytes(bytes(bundle))
        result.update(os_key=key, bundle_bytes=len(bundle), success=response.exit_code == 0)
    except Exception as exc:
        result.update(error=type(exc).__name__+': '+str(exc), stdout=getattr(exc,'stdout',''),
                      stderr=getattr(exc,'stderr',''))
        print('tool_provision_failed', json.dumps(result)[-4000:], flush=True)
    finally:
        await box.kill(request_timeout=60)
        save(ROOT/'results/tool-provision.json', result)
    if not result.get('success'):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
