"""Build an isolated, ordinary-page local template for the official SDK workload.

The recipe uses the pinned upstream server unchanged, Python and JavaScript
kernels, and the existing local base rootfs. It is not the full hosted template.
No service settings or existing template aliases are changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import time

import httpx
from e2b import Template, wait_for_url


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')
SERVER_COMMIT = 'f56a1edf750e20a96f847df57e0063eeeb5e13c3'
JS_COMMIT = '79cb7d56dcfe0df9ff55eff0ca4dc920a6dcc361'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def connection(config_path):
    value = json.loads(config_path.read_text())
    return {key: value[key] for key in ('api_key', 'api_url', 'sandbox_url')}


def foreign_instances(config):
    response = httpx.get(config['api_url'] + '/sandboxes',
                         headers={'X-API-KEY': config['api_key']},
                         timeout=30, trust_env=False)
    response.raise_for_status()
    result = response.json()
    return [entry for entry in result if not (
        str((entry.get('metadata') or {}).get('purpose', '')).startswith('benchmark')
        and (entry.get('metadata') or {}).get('run_id') == 'session-20260920')]


def recipe(base_template='base'):
    template = (Template(file_context_path=ROOT)
        .from_template(base_template)
        .set_user('root').set_workdir('/root')
        .set_envs({'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_DEFAULT_TIMEOUT': '120'})
        .run_cmd('apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv python3-dev build-essential nodejs npm curl ca-certificates git && rm -rf /var/lib/apt/lists/*')
        .copy('configs/minimal-requirements.txt', '/opt/benchmark-requirements.txt')
        .run_cmd('python3 -m venv /opt/benchmark-ci && /opt/benchmark-ci/bin/pip install --no-cache-dir -r /opt/benchmark-requirements.txt')
        .run_cmd('/opt/benchmark-ci/bin/python -m ipykernel install --name python --prefix /opt/benchmark-ci && /opt/benchmark-ci/bin/python -m ipykernel install --name python3 --prefix /opt/benchmark-ci')
        .run_cmd('npm install -g git+https://github.com/e2b-dev/ijavascript.git#' + JS_COMMIT)
        .run_cmd('PATH=/opt/benchmark-ci/bin:$PATH ijsinstall --install=global')
        .copy('vendor/code-interpreter/template/server', '/root/.server')
        .copy('vendor/code-interpreter/template/jupyter_server_config.py', '/root/.jupyter/jupyter_server_config.py')
        .copy('vendor/code-interpreter/template/jupyter-healthcheck.sh', '/root/.jupyter/jupyter-healthcheck.sh', mode=0o755)
        .copy('vendor/code-interpreter/template/ipython_kernel_config.py', '/root/.ipython/profile_default/ipython_kernel_config.py')
        .copy('vendor/code-interpreter/template/startup_scripts', '/root/.ipython/profile_default/startup')
        .copy('configs/jupyter.service', '/etc/systemd/system/jupyter.service')
        .copy('configs/code-interpreter.service', '/etc/systemd/system/code-interpreter.service')
        .run_cmd('/opt/benchmark-ci/bin/pip freeze > /opt/benchmark-ci/installed-python.txt && npm list -g --json > /opt/benchmark-ci/installed-node.json && /opt/benchmark-ci/bin/jupyter kernelspec list --json > /opt/benchmark-ci/kernels.json')
        .set_user('user').set_workdir('/home/user')
        .set_start_cmd('sudo systemctl start jupyter', wait_for_url('http://localhost:49999/health')))
    return template


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--alias', default='benchmark-ci-minimal-20260920')
    parser.add_argument('--base-template', default='base')
    parser.add_argument('--report', type=Path, default=ROOT / 'results/template-build.json')
    parser.add_argument('--build', action='store_true', help='Actually register and build the new template')
    args = parser.parse_args(argv)
    if not args.build:
        print(json.dumps({'status': 'prepared', 'alias': args.alias,
                          'instruction': 'Pass --build only in the coordinated preparation window.'}))
        return 0
    if args.report.exists():
        raise SystemExit('Report already exists; inspect its state before a new build. Do not blindly retry an ambiguous POST.')
    config = connection(args.config)
    foreign = foreign_instances(config)
    if foreign:
        raise SystemExit('Foreign sandbox present; preparation paused without starting a build.')
    manifest = json.loads((ROOT / 'configs/upstream-sources.json').read_text())
    record = {
        'status': 'building', 'started_utc': utc_now(), 'alias': args.alias,
        'base_template': args.base_template, 'cpu_count': 2, 'memory_mb': 4096,
        'expected_page_mode': 'ordinary-4KiB', 'server_commit': SERVER_COMMIT,
        'ijavascript_commit': JS_COMMIT,
        'client_versions': {key: importlib.metadata.version(key) for key in ('e2b', 'e2b-code-interpreter')},
        'source_manifest_sha256': hashlib.sha256((ROOT / 'configs/upstream-sources.json').read_bytes()).hexdigest(),
        'source_file_count': len(manifest['files']),
        'adaptation': 'Existing local base rootfs; Python and JS only; pinned upstream server unchanged; venv/systemd paths adjusted; full hosted scientific/R/Java/bash package set omitted.',
    }
    save(args.report, record)
    log_path = args.report.with_suffix('.log.jsonl')
    def log(entry):
        message = str(entry).replace(config['api_key'], '<redacted>')
        identity = re.search(r'Template created with ID: ([^, ]+), Build ID: ([^\s]+)', message)
        if identity:
            record.update(template_id=identity.group(1), build_id=identity.group(2))
            save(args.report, record)
        with log_path.open('a') as handle:
            handle.write(json.dumps({'utc': utc_now(), 'message': message}) + '\n')
        print(message, flush=True)
    began = time.monotonic()
    try:
        info = Template.build(recipe(args.base_template), args.alias,
                              cpu_count=2, memory_mb=4096,
                              on_build_logs=log, api_key=config['api_key'],
                              api_url=config['api_url'], request_timeout=120, retries=0)
        record.update(status='ready', template_id=info.template_id, build_id=info.build_id)
    except BaseException as error:
        record.update(status='failed', error_type=type(error).__name__,
                      error=str(error).replace(config['api_key'], '<redacted>'))
        raise
    finally:
        record.update(finished_utc=utc_now(), wall_seconds=round(time.monotonic() - began, 3))
        save(args.report, record)
        print(json.dumps(record), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
