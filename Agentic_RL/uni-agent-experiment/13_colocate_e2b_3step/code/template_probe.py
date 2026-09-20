import asyncio
import dataclasses
import json
import time
import httpx
from e2b import AsyncTemplate
from runtime_common import ROOT, api_options, load_secrets, save


async def main():
    load_secrets()
    iid = 'ettoreleandrotognoli__python-cdi-28'
    source = json.loads((ROOT / 'results/templates' / iid / 'build.json').read_text())
    template = AsyncTemplate().from_image(source['image_digest'])
    info = await AsyncTemplate.build_in_background(template, name='ua-3step-import-probe',
        cpu_count=4, memory_mb=8192, request_timeout=120, **api_options())
    result = {'instance_id': iid, 'image_digest': source['image_digest'], **dataclasses.asdict(info)}
    save(ROOT / 'results/template-import-probe.json', result)
    print('import_only_probe', json.dumps(result), flush=True)
    options = api_options()
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        deadline = time.monotonic() + 1200
        while True:
            response = await client.get(options['api_url'].rstrip('/') +
                f"/templates/{info.template_id}/builds/{info.build_id}/status", headers={'X-API-Key': options['api_key']})
            response.raise_for_status()
            status = response.json()
            save(ROOT / 'results/template-import-probe-status.json', status)
            if status.get('status') in ['ready', 'error', 'failed']:
                print('probe_result', status.get('status'), status.get('reason'), flush=True)
                for line in status.get('logEntries', [])[-6:]:
                    print(line.get('message', '')[:400], flush=True)
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Probe build deadline')
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
