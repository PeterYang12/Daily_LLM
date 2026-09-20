import asyncio
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path

import httpx

ROOT = Path('/work/experiments/colocate-e2b-3step-20260920')


def load_secrets():
    path = ROOT / 'secrets/e2b.env'
    path.chmod(0o600)
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.removeprefix('export ').split('=', 1)
            os.environ[key.strip()] = value.strip().strip('\"\'')


def save(name, data):
    text = json.dumps(data, indent=2, default=str)
    if os.environ.get('E2B_API_KEY'):
        text = text.replace(os.environ['E2B_API_KEY'], '[REDACTED]')
    (ROOT / 'results' / name).write_text(text + '\n')


async def main():
    load_secrets()
    packages = {}
    for name in ['torch', 'vllm', 'ray', 'verl', 'transformer-engine', 'megatron-core',
                 'mbridge', 'TransferQueue', 'e2b', 'swebench', 'datasets', 'pyarrow']:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    for name in ['uni_agent', 'verl', 'vllm', 'transfer_queue']:
        packages[name + '_import'] = importlib.util.find_spec(name).origin
    save('environment.json', {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'packages': packages})
    print('environment', json.dumps(packages), flush=True)
    endpoint = os.environ['E2B_API_URL'].rstrip('/')
    from e2b import AsyncSandbox
    paginator = AsyncSandbox.list(api_url=endpoint, api_key=os.environ['E2B_API_KEY'], limit=100)
    boxes = []
    while paginator.has_next:
        page = await paginator.next_items()
        boxes.extend({'sandbox_id': b.sandbox_id, 'template_id': b.template_id,
                      'metadata': b.metadata} for b in page)
    save('initial-sandboxes.json', {'endpoint': endpoint, 'sandboxes': boxes})
    print('initial_sandboxes', len(boxes), flush=True)
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.get(endpoint + '/templates', headers={'X-API-Key': os.environ['E2B_API_KEY']})
        response.raise_for_status()
        templates = response.json()
        save('initial-templates.json', templates)
        if isinstance(templates, list):
            print('template_count', len(templates), 'first', json.dumps(templates[:2], default=str), flush=True)
        else:
            print('template_response_keys', list(templates), flush=True)
    from datasets import Dataset
    path = ROOT / 'data/recommended-train-original.parquet'
    ds = Dataset.from_parquet(str(path))
    assert len(ds) == 1150, len(ds)
    row = ds[0]
    task = row['extra_info']['tools_kwargs']['task']
    save('input-data.json', {'n': len(ds), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'columns': ds.column_names, 'task_keys': list(task),
                             'metadata_keys': list(task.get('metadata', {}))})
    print('dataset', len(ds), 'task_keys', list(task), 'metadata_keys', list(task.get('metadata', {})), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
