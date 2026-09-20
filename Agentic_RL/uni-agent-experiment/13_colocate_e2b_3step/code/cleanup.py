import asyncio
import json
import httpx
from runtime_common import ROOT, api_options, event, list_owned, load_secrets, save


async def main():
    load_secrets()
    result = {'deleted': [], 'errors': []}
    options = api_options()
    slots = asyncio.Semaphore(16)
    async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
        async def delete(box):
            async with slots:
                for attempt in range(3):
                    try:
                        response = await client.delete(options['api_url'].rstrip('/') + '/sandboxes/' + box.sandbox_id,
                                                       headers={'X-API-Key': options['api_key']})
                        if response.status_code not in [200, 204, 404]:
                            response.raise_for_status()
                        result['deleted'].append(box.sandbox_id)
                        event('cleanup_deleted', sandbox_id=box.sandbox_id)
                        return
                    except Exception as exc:
                        if attempt == 2:
                            result['errors'].append({'sandbox_id': box.sandbox_id, 'error': str(exc)})
                        await asyncio.sleep(1)
        await asyncio.gather(*(delete(box) for box in await list_owned()))
    result['remaining'] = [b.sandbox_id for b in await list_owned()]
    save(ROOT / 'results/cleanup.json', result)
    print(json.dumps({'deleted': len(result['deleted']), 'remaining': result['remaining'], 'errors': result['errors']}))
    if result['remaining'] or result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
