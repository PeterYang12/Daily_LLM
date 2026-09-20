"""Collect preserved experiment snapshots into their original runtime code layout."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    destination = args.output.expanduser().resolve()
    if destination.exists():
        parser.error('Output must not exist; existing files are never overwritten.')
    provenance = ROOT / '06_trajectories_and_reproduction/results/provenance/layout-map.json'
    records = json.loads(provenance.read_text())['artifacts']
    selected = [r for r in records if r['previous'].startswith('reproduce/')]
    for name in ['run_e2b_e2e.py', 'run_uni_agent_e2b.py']:
        helper = '04_e2b_sandbox/code/' + name
        selected.append({'current': helper, 'export_path': 'scripts/' + name,
                         'sha256': hashlib.sha256((ROOT / helper).read_bytes()).hexdigest(),
                         'origin': 'Manual replay helper added after the recorded experiments'})
    profiling = ROOT / '07_swe_sandbox_profiling'
    if profiling.exists():
        records = json.loads((profiling / 'results/source-manifest.json').read_text())['copied_files']
        for record in records:
            relative = record['destination']
            if not relative.startswith('code/'):
                continue
            selected.append({'current': '07_swe_sandbox_profiling/' + relative,
                             'export_path': 'scripts/swe_sandbox_profile/' + relative.removeprefix('code/'),
                             'sha256': record['sha256'],
                             'origin': '2026-09-14 SWE sandbox profiling study'})
    local_e2b = ROOT / '08_local_e2b'
    if local_e2b.exists():
        records = json.loads((local_e2b / 'results/source-manifest.json').read_text())['copied_files']
        for record in records:
            relative = record['destination']
            if not relative.startswith(('code/', 'configs/')) and relative != 'results/runtime-state.json':
                continue
            runtime_path = 'state.json' if relative == 'results/runtime-state.json' else relative
            selected.append({'current': '08_local_e2b/' + relative,
                             'export_path': 'local-e2b-20260916/' + runtime_path,
                             'sha256': record['sha256'],
                             'origin': '2026-09-16 local E2B deployment; credentials and VM disks excluded'})
    comparison = ROOT / '09_sandbox_three_way'
    if comparison.exists():
        records = json.loads((comparison / 'results/source-manifest.json').read_text())['copied_files']
        for record in records:
            relative = record['destination']
            if relative.startswith('code/'):
                selected.append({'current': '09_sandbox_three_way/' + relative,
                                 'export_path': 'scripts/sandbox_three_way/' + relative.removeprefix('code/'),
                                 'sha256': record['sha256'], 'origin': '2026-09-16 three-deployment comparison'})
    local_osb = ROOT / '10_local_opensandbox'
    if local_osb.exists():
        records = json.loads((local_osb / 'results/source-manifest.json').read_text())['copied_files']
        for record in records:
            relative = record['destination']
            if not relative.startswith(('code/', 'configs/')) and relative != 'results/runtime-state.json':
                continue
            runtime_path = 'state.json' if relative == 'results/runtime-state.json' else relative
            selected.append({'current': '10_local_opensandbox/' + relative,
                             'export_path': 'local-opensandbox-20260916/' + runtime_path,
                             'sha256': record['sha256'], 'origin': '2026-09-16 local OpenSandbox deployment; credentials excluded'})
    large_e2b = ROOT / '11_local_e2b_capacity'
    if large_e2b.exists():
        records = json.loads((large_e2b / 'results/source-manifest.json').read_text())['copied_files']
        for record in records:
            relative = record['destination']
            if not relative.startswith(('code/', 'configs/')) and relative != 'results/runtime-state.json':
                continue
            runtime_path = 'state.json' if relative == 'results/runtime-state.json' else relative
            selected.append({'current': '11_local_e2b_capacity/' + relative,
                             'export_path': 'local-e2b-96c512g-20260916/' + runtime_path,
                             'sha256': record['sha256'], 'origin': '2026-09-16 larger E2B VM; private backup, credentials, and disks excluded'})
    rl = ROOT / '12_react_rl_training'
    if rl.exists():
        records = json.loads((rl / 'results/source-manifest.json').read_text())['copied_files']
        for record in records:
            relative = record['destination']
            if relative.startswith('configs/data/'):
                runtime_path = 'data/' + relative.removeprefix('configs/data/')
            elif relative.startswith(('code/', 'configs/')):
                runtime_path = relative
            elif relative in ['results/uni-agent-changes.patch', 'results/verl-observability.patch', 'results/python-environment.lock']:
                runtime_path = relative
            else:
                continue
            selected.append({'current': '12_react_rl_training/' + relative,
                             'export_path': 'rl-swe-e2b-20260916/' + runtime_path,
                             'sha256': record['sha256'], 'origin': 'Full RL study; code/config/data snapshot; weights and credentials excluded'})
    # Validate the entire selection before creating an output directory.
    for record in selected:
        source = ROOT / record['current']
        if hashlib.sha256(source.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Snapshot hash mismatch: ' + record['current'])
    destination.mkdir(parents=True)
    for record in selected:
        relative = record.get('export_path') or record['previous'].removeprefix('reproduce/')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / record['current'], target)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == record['sha256']
    (destination / 'export-manifest.json').write_text(json.dumps(selected, indent=2) + '\n')
    print(json.dumps({'exported_files': len(selected), 'destination': str(destination),
                      'scope': 'Code/config snapshots only; no installed runtime or services.'}))


if __name__ == '__main__':
    main()
