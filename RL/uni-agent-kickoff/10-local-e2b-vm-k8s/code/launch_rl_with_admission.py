#!/usr/bin/env python3
"""Check admission settings for a new RL container; launch only with --execute."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

FROZEN_ROOT = Path('/data2/yuhanya/e2b-rl-pathfinding-20260920-r2')
FROZEN_RUN_ID = 'ua-e2b-pathfinding-20260920-r2'
ADMISSION_KEYS = {
    'create_permits': 'UA_CREATE_PERMITS',
    'startup_concurrency': 'SANDBOX_STARTUP_CONCURRENCY',
    'exec_permits': 'UA_EXEC_PERMITS',
}


def admission_environment(path: Path) -> dict[str, str]:
    config = json.loads(path.read_text())
    if not isinstance(config, dict) or set(config) != {'schema_version', *ADMISSION_KEYS}:
        raise ValueError('Admission config must contain exactly the schema and three settings')
    if type(config['schema_version']) is not int or config['schema_version'] != 1:
        raise ValueError('Unsupported admission config schema_version')
    result = {}
    for key, variable in ADMISSION_KEYS.items():
        value = config[key]
        if type(value) is not int or not 1 <= value <= 65536:
            raise ValueError(f'{key} must be an integer between 1 and 65536')
        result[variable] = str(value)
    return result


def validate_target(root: Path, run_id: str, container: str) -> Path:
    root = root.resolve(strict=True)
    if root == FROZEN_ROOT or root.is_relative_to(FROZEN_ROOT):
        raise ValueError('The frozen R2 experiment root cannot be used')
    if run_id == FROZEN_RUN_ID:
        raise ValueError('The frozen R2 run identity cannot be used')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', run_id):
        raise ValueError('Invalid new run identity')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', container):
        raise ValueError('Invalid Docker container name or ID')
    if (root / 'code').is_symlink():
        raise ValueError('The new experiment code directory cannot be a symbolic link')
    for name in ('launch.sh', 'configure.py', 'runtime_launch.py'):
        entry = root / 'code' / name
        if entry.is_symlink():
            raise ValueError(f'Entry point cannot be a symbolic link: code/{name}')
        if not entry.is_file():
            raise ValueError(f'Missing entry point: code/{name}')
    for name in ('configs', 'results', 'checkpoints', 'logs', 'ray'):
        directory = root / name
        if directory.is_symlink():
            raise ValueError(f'Output directory cannot be a symbolic link: {name}')
        if directory.exists() and not directory.is_dir():
            raise ValueError(f'Output path must be a directory: {name}')
        # Existing child links can redirect launch.lock/config writes into an
        # older run. Hard-linked files copied with cp -al share its writes too.
        for path in directory.rglob('*'):
            if path.is_symlink():
                raise ValueError(f'Output path cannot be a symbolic link: {path.relative_to(root)}')
            if path.is_file() and path.stat().st_nlink > 1:
                raise ValueError(f'Output file cannot have hard links: {path.relative_to(root)}')
    return root


def inspect_target(container: str, root: Path) -> dict:
    form = ('{"id":{{json .Id}},"running":{{json .State.Running}},'
            '"mounts":{{json .Mounts}}}')
    result = subprocess.run(
        ['docker', 'inspect', '--format', form, container],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=True)
    info = json.loads(result.stdout)
    if not info.get('running'):
        raise ValueError('Target container must already be running')
    matches = [m for m in info.get('mounts', []) if m.get('Destination') == '/experiment']
    if len(matches) != 1:
        raise ValueError('Exactly one /experiment mount is required')
    mount = matches[0]
    if mount.get('Type') != 'bind' or not mount.get('RW'):
        raise ValueError('/experiment must be a writable bind mount')
    if Path(mount['Source']).resolve(strict=True) != root:
        raise ValueError('Docker /experiment mount differs from the specified new root')
    if any(m.get('Destination', '').startswith('/experiment/') for m in info.get('mounts', [])):
        raise ValueError('Nested /experiment mounts are not supported')
    return info


def launch_command(container_id: str, run_id: str, admission: dict[str, str]) -> list[str]:
    if set(admission) != set(ADMISSION_KEYS.values()):
        raise ValueError('All three admission environment variables are required')
    environment = {**admission, 'UA_EXPERIMENT_ROOT': '/experiment',
                   'UA_RUN_ID': run_id, 'PYTHONDONTWRITEBYTECODE': '1'}
    command = ['docker', 'exec', '-w', '/experiment']
    for key, value in sorted(environment.items()):
        command += ['-e', f'{key}={value}']
    command += [container_id, 'bash', '/experiment/code/launch.sh']
    # Literal quotes in argv make Hydra retain strings, as required by Ray.
    command += [f'++ray_kwargs.ray_init.runtime_env.env_vars.{key}="{value}"'
                for key, value in sorted(admission.items())]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--config', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'configs/client-admission.json')
    parser.add_argument('--execute', action='store_true',
                        help='Run the checked command; otherwise only inspect and print')
    args = parser.parse_args(argv)
    try:
        admission = admission_environment(args.config)
        root = validate_target(args.root, args.run_id, args.container)
        target = inspect_target(args.container, root)
        command = launch_command(target['id'], args.run_id, admission)
        print(json.dumps({'mode': 'execute' if args.execute else 'check',
                          'experiment_root': str(root), 'container_id': target['id'],
                          'run_id': args.run_id, 'driver_admission_environment': admission,
                          'ray_worker_environment_overrides': admission,
                          'command_argv': command, 'command': shlex.join(command)}, indent=2), flush=True)
        if args.execute:
            return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Admission launch refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
