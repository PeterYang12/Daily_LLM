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
    helper = '04_e2b_sandbox/code/run_e2b_e2e.py'
    selected.append({'current': helper, 'export_path': 'scripts/run_e2b_e2e.py',
                     'sha256': hashlib.sha256((ROOT / helper).read_bytes()).hexdigest(),
                     'origin': 'E2B-only replay helper added after the recorded experiments'})
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
