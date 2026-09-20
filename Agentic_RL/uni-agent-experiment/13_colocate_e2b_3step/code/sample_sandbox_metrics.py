import asyncio
import dataclasses
import datetime
import json
import time
from e2b import AsyncSandbox
from runtime_common import ROOT, api_options, list_owned, load_secrets, save


async def main():
    load_secrets()
    boxes = await list_owned('train')
    chosen = boxes[::max(1, len(boxes)//8)][:8]
    slots = asyncio.Semaphore(2)
    rows = []
    since = datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=2)
    async def one(box):
        async with slots:
            row = {'sandbox_id': box.sandbox_id, 'metadata': box.metadata}
            try:
                opts = api_options(); opts.pop('sandbox_url')
                values = await AsyncSandbox.get_metrics(sandbox_id=box.sandbox_id, start=since,
                                                         request_timeout=15, **opts)
                row['metrics'] = [dataclasses.asdict(v) if dataclasses.is_dataclass(v)
                                  else v.model_dump() if hasattr(v, 'model_dump') else vars(v) for v in values]
            except Exception as exc:
                row['error'] = type(exc).__name__+': '+str(exc)
            rows.append(row)
    await asyncio.gather(*(one(box) for box in chosen))
    path = ROOT/'results'/f'sandbox-resource-sample-{int(time.time())}.json'
    save(path, {'active_at_selection': len(boxes), 'samples': rows,
                'note': 'Sample of live sandbox metrics, not a whole-node resource measurement.'})
    print(json.dumps({'selected': len(chosen), 'nonempty': sum(bool(r.get('metrics')) for r in rows),
                      'errors': [r['error'] for r in rows if 'error' in r], 'path': str(path)}))


if __name__ == '__main__':
    asyncio.run(main())
