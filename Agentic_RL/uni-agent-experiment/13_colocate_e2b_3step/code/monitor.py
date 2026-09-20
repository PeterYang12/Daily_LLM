import asyncio
import json
import subprocess
import time
from runtime_common import ROOT, list_owned, load_secrets


async def main():
    load_secrets()
    while not (ROOT/'monitor.stop').exists():
        row = {'time_unix': time.time()}
        try:
            boxes = await list_owned('train')
            row['active_training_sandboxes'] = len(boxes)
            row['sandbox_ids'] = [b.sandbox_id for b in boxes]
            if len(boxes) > 128:
                row['concurrency_violation'] = True
        except Exception as exc:
            row['sandbox_error'] = str(exc)
        try:
            result = subprocess.run(['rocm-smi', '--showuse', '--showmeminfo', 'vram', '--json'],
                                    text=True, capture_output=True, timeout=15)
            start = result.stdout.find('{')
            row['gpu'] = json.loads(result.stdout[start:]) if start >= 0 else {'error': result.stderr[-300:]}
        except Exception as exc:
            row['gpu_error'] = str(exc)
        with (ROOT/'results/telemetry.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
        await asyncio.sleep(10)


if __name__ == '__main__':
    asyncio.run(main())
