#!/usr/bin/env python3
"""Read a bounded status snapshot from an existing experiment; never launch work."""
import argparse
import collections
import datetime
import json
from pathlib import Path


def read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def last_row(path):
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 262144))
            rows = stream.read().splitlines()
        for row in reversed(rows):
            try:
                value = json.loads(row)
                if value.get('record_type') == 'sample':
                    return value
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    return {}


def snapshot(root):
    results = root / 'results'
    latest = read(results / 'supervision/latest.json') or {}
    session = latest.get('session_id')
    status = read(results / 'supervision' / session / 'status.json') if session else None
    status = status or {}
    counts = collections.Counter()
    for path in (results / 'sessions').glob('*/result.json'):
        row = read(path)
        if row:
            counts[f"{row.get('phase', 'unknown')}/{row.get('status', 'unknown')}"] += 1
    admission = read(results / 'admission-state.json') or {}
    host = last_row(results / 'resources-host.jsonl')
    vm = last_row(results / 'resources-vm.jsonl').get('guest', {})
    latest_checkpoint = read(results / 'latest-checkpoint.json')
    return {
        'snapshot_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'experiment_root': str(root),
        'run_id': status.get('run_id'),
        'supervisor_session': session,
        'supervisor_state': status.get('state'),
        'heartbeat_utc': status.get('heartbeat_utc'),
        'native_steps': status.get('progress', {}).get('completed_native_global_step'),
        'remaining_seconds_estimate': status.get('progress', {}).get('remaining_seconds_estimate'),
        'checkpoint_step': latest_checkpoint.get('global_step') if latest_checkpoint else None,
        'episode_results': dict(sorted(counts.items())),
        'admission_observed_unix': admission.get('observed_unix'),
        'admission': {name: {key: admission.get(name, {}).get(key) for key in
                            ('capacity', 'active', 'queued', 'consistent', 'capacity_progress_age_s', 'max_active_age_s')}
                      for name in ('create', 'exec')},
        'host_sample_utc': host.get('utc'),
        'gpu_busy_percent': [gpu.get('gfx_busy_percent') for gpu in host.get('gpus', [])],
        'gpu_vram_used_gib': [round(gpu['vram_used_bytes'] / 2**30, 1) if gpu.get('vram_used_bytes') is not None else None
                              for gpu in host.get('gpus', [])],
        'sandbox_vm_sample_utc': vm.get('utc'),
        'sandbox_vm_cpu': vm.get('cpu'),
        'sandbox_vm_processes': vm.get('processes', {}).get('live_process_counts'),
        'sandbox_vm_cgroups': vm.get('cgroup_summary'),
        'supervisor_error': status.get('error'),
        'scope': 'Episode results and live process counts are not native training steps. VM counts may include other clients.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.root.resolve()), indent=2))
