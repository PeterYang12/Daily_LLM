import json
import os
import time
import contextvars
from pathlib import Path

ROOT = Path('/work/experiments/colocate-e2b-3step-20260920')
RUN_ID = 'ua-colocate-e2b-3step-20260920'
CURRENT = contextvars.ContextVar('ua_run_context', default={})


def load_secrets():
    for line in (ROOT / 'secrets/e2b.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.removeprefix('export ').split('=', 1)
            os.environ[key.strip()] = value.strip().strip('\"\'')


def api_options():
    return {'api_key': os.environ['E2B_API_KEY'], 'api_url': os.environ['E2B_API_URL'],
            'sandbox_url': os.environ['E2B_SANDBOX_URL']}


def save(path, data):
    path = Path(path)
    text = json.dumps(data, indent=2, default=str)
    for key in ['E2B_API_KEY', 'WANDB_API_KEY']:
        if os.environ.get(key):
            text = text.replace(os.environ[key], '[REDACTED]')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text + '\n')
    tmp.replace(path)


async def list_owned(phase=None):
    from e2b import AsyncSandbox
    paginator = AsyncSandbox.list(api_key=os.environ['E2B_API_KEY'],
                                  api_url=os.environ['E2B_API_URL'], limit=100)
    result = []
    while paginator.has_next:
        for box in await paginator.next_items():
            metadata = box.metadata or {}
            if metadata.get('experiment') == RUN_ID and (phase is None or metadata.get('phase') == phase):
                result.append(box)
    return result


def event(kind, **fields):
    line = json.dumps({'time_unix': time.time(), 'event': kind, **fields}, default=str) + '\n'
    path = ROOT / 'results/sandbox-events.jsonl'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
