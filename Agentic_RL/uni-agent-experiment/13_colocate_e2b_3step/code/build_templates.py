import argparse
import asyncio
import dataclasses
import hashlib
import json
import time

import httpx
from e2b import AsyncTemplate
from runtime_common import ROOT, api_options, load_secrets, save


async def pin_image(client, image):
    repository = image.removesuffix(':latest')
    response = await client.get('https://auth.docker.io/token', params={
        'service': 'registry.docker.io', 'scope': f'repository:{repository}:pull'})
    response.raise_for_status()
    headers = {'Authorization': 'Bearer ' + response.json()['token'],
               'Accept': 'application/vnd.docker.distribution.manifest.v2+json, '
                         'application/vnd.oci.image.manifest.v1+json, '
                         'application/vnd.docker.distribution.manifest.list.v2+json, '
                         'application/vnd.oci.image.index.v1+json'}
    response = await client.get(f'https://registry-1.docker.io/v2/{repository}/manifests/latest', headers=headers)
    response.raise_for_status()
    manifest = response.json()
    if 'manifests' in manifest:
        digest = next(m['digest'] for m in manifest['manifests']
                      if m.get('platform', {}).get('architecture') == 'amd64'
                      and m.get('platform', {}).get('os') == 'linux')
        response = await client.get(f'https://registry-1.docker.io/v2/{repository}/manifests/{digest}', headers=headers)
        response.raise_for_status()
        manifest = response.json()
    return repository + '@' + response.headers['Docker-Content-Digest'], sum(l['size'] for l in manifest.get('layers', []))


async def main(args):
    load_secrets()
    tasks = json.loads((ROOT / 'data/scheduled-tasks.json').read_text())[:args.limit]
    reused = json.loads((ROOT / 'data/reusable-templates.json').read_text())
    slots = asyncio.Semaphore(args.concurrency)
    records = {}

    async def one(task):
        iid = task['metadata']['instance_id']
        target = ROOT / 'results/templates' / iid / 'build.json'
        if target.exists():
            record = json.loads(target.read_text())
            if record.get('status') == 'ready':
                records[iid] = record
                return
        async with slots:
            started = time.time()
            record = {'instance_id': iid, 'source_image': task['sandbox']['image'], 'status': 'preparing'}
            try:
                if iid in reused and reused[iid].get('qualified'):
                    record.update(reused[iid])
                    record.update(status='ready', reused=True,
                                  note='Existing template will undergo current task and tmux checks before use.')
                    save(target, record)
                    records[iid] = record
                    print('reused', iid, record['template_id'], flush=True)
                    return
                async with httpx.AsyncClient(timeout=90, follow_redirects=True, trust_env=False) as client:
                    image, size = await pin_image(client, task['sandbox']['image'])
                    record.update(image_digest=image, compressed_image_bytes=size)
                    alias = 'ua-3step-' + hashlib.sha256(image.encode()).hexdigest()[:16]
                    template = AsyncTemplate().from_image(image).run_cmd(
                        'command -v tmux >/dev/null || '
                        '(apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y tmux)')
                    info = await AsyncTemplate.build_in_background(template, name=alias,
                        cpu_count=4, memory_mb=8192, request_timeout=120, **api_options())
                    record.update(dataclasses.asdict(info), alias=alias, status='building')
                    save(target, record)
                    print('building', iid, record['template_id'], flush=True)
                    deadline = time.monotonic() + 2400
                    while True:
                        options = api_options()
                        response = await client.get(options['api_url'].rstrip('/') +
                            f"/templates/{record['template_id']}/builds/{record['build_id']}/status",
                            headers={'X-API-Key': options['api_key']})
                        response.raise_for_status()
                        status = response.json()
                        save(target.parent / 'status.json', status)
                        state = status.get('status')
                        if state == 'ready':
                            record['status'] = 'ready'
                            break
                        if state in ['error', 'failed']:
                            raise RuntimeError('E2B build failed: ' + json.dumps(status)[:1000])
                        if time.monotonic() > deadline:
                            raise TimeoutError('E2B build exceeded 2400s')
                        await asyncio.sleep(5)
                print('ready', iid, round(time.time()-started, 1), flush=True)
            except Exception as exc:
                record.update(status='error', error=f'{type(exc).__name__}: {exc}')
                print('error', iid, type(exc).__name__, str(exc)[:200], flush=True)
            finally:
                record['elapsed_s'] = time.time() - started
                save(target, record)
                records[iid] = record
                save(ROOT / 'results/template-build-summary.json', {'target': len(tasks), 'records': records})

    await asyncio.gather(*(one(task) for task in tasks))
    result = {'target': len(tasks), 'records': records,
              'ready': sum(r['status'] == 'ready' for r in records.values()),
              'errors': sum(r['status'] == 'error' for r in records.values())}
    save(ROOT / 'results/template-build-summary.json', result)
    print(json.dumps({k: result[k] for k in ['target', 'ready', 'errors']}), flush=True)
    if result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=64)
    parser.add_argument('--concurrency', type=int, default=4)
    asyncio.run(main(parser.parse_args()))
