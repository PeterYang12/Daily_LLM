"""Check the experiment layout, evidence, immutable snapshots, and documentation."""
import ast
import csv
import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS = ['01_model_and_examples', '02_swe_bench_comparison', '03_docker_sandbox',
               '04_e2b_sandbox', '05_inference_performance', '06_trajectories_and_reproduction']
PROVENANCE = ROOT / EXPERIMENTS[5] / 'results/provenance'


def load(path):
    return json.loads((ROOT / path).read_text())


def main():
    assert {p.name for p in ROOT.iterdir()} == {'README.md', 'introduction.md', 'introduction.pdf', *EXPERIMENTS}
    for experiment in EXPERIMENTS:
        for item in ['README.md', 'STEPS.md', 'code', 'figures', 'results']:
            assert (ROOT / experiment / item).exists(), (experiment, item)
    records = load(EXPERIMENTS[5] + '/results/provenance/layout-map.json')['artifacts']
    immutable = [r for r in records if not r['previous'].startswith('assets/')]
    for record in immutable:
        assert hashlib.sha256((ROOT / record['current']).read_bytes()).hexdigest() == record['sha256'], record['current']
    pinned = load(EXPERIMENTS[5] + '/results/provenance/source-manifest.json')['copied_files']
    for record in pinned:
        assert hashlib.sha256((ROOT / record['destination']).read_bytes()).hexdigest() == record['destination_sha256'], record['destination']

    base = EXPERIMENTS[1] + '/results/'
    summary = load(base + 'final-summary.json')
    assert len(summary['rows']) == 84 and summary['status'] == 'complete'
    for agent, count in [('react', 17), ('claude', 11), ('mini', 12)]:
        info = summary['agents'][agent]
        assert (info['n'], info['resolved'], info['eval_completed'], info['errors']) == (28, count, 28, 0)
        results = [json.loads(p.read_text()) for p in (ROOT / base / 'cases' / agent).glob('*/result.json')]
        assert len(results) == 28 and sum(r['resolved'] for r in results) == count
    with (ROOT / base / 'per-case-results.csv').open() as stream:
        assert len(list(csv.DictReader(stream))) == 84
    baseline = {r['instance_id']: r for r in load(base + 'thirty-baseline-dns.json')['per_case']}
    oracle = {r['instance_id']: r for r in load(base + 'thirty-oracle-dns.json')['per_case']}
    qualified = {i for i in baseline if baseline[i]['eval_completed'] and not baseline[i]['resolved']
                 and oracle[i]['eval_completed'] and oracle[i]['resolved']}
    assert len(qualified) == 28 and qualified == set(load(base + 'validated-manifest.json')['instances'])
    for folder in ['tp', 'aiter', 'replicas']:
        base = EXPERIMENTS[4] + '/results/' + folder + '/'
        raw, stats = load(base + 'raw.json'), load(base + 'summary.json')['results']
        assert len(raw) == 18
        for row in stats:
            key = 'replicas' if 'replicas' in row else 'backend'
            group = [q for q in raw if q[key] == row[key] and q['concurrency'] == row['concurrency']]
            assert len(group) == 3 and all(q['output_tokens'] == q['concurrency'] * 256 for q in group)
            assert math.isclose(sum(q['aggregate_output_tok_s'] for q in group) / 3,
                                row['aggregate_output_tok_s'], rel_tol=1e-10)

    markdown = list(ROOT.rglob('*.md'))
    assert len(markdown) <= 20, 'Keep the authored documentation compact.'
    missing, non_english = [], []
    shell_blocks = 0
    for path in markdown:
        text = path.read_text()
        if re.search(r'[\u4e00-\u9fff]', text):
            non_english.append(str(path.relative_to(ROOT)))
        for url in re.findall(r'\]\(([^)]+)\)', text):
            parsed = urlsplit(url)
            if parsed.scheme or url.startswith('#'):
                continue
            if not (path.parent / unquote(parsed.path)).exists():
                missing.append((str(path.relative_to(ROOT)), url))
        for block in re.findall(r'```bash\n(.*?)\n```', text, re.S):
            checked = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True)
            assert checked.returncode == 0, (str(path), checked.stderr)
            shell_blocks += 1
    assert not missing, missing
    assert not non_english, non_english
    code = list(ROOT.glob('*/code/**/*.py'))
    for path in code:
        ast.parse(path.read_text(), filename=str(path))
    for path in ROOT.glob('*/code/**/*.sh'):
        checked = subprocess.run(['bash', '-n', str(path)], capture_output=True, text=True)
        assert checked.returncode == 0, (str(path), checked.stderr)
    diagrams = list(ROOT.glob('*/figures/*.mmd'))
    for path in diagrams:
        assert path.with_suffix('.svg').exists(), str(path)
    for path in ROOT.glob('*/figures/*.svg'):
        ET.parse(path)
        assert not re.search(r'[\u4e00-\u9fff]', path.read_text()), str(path)
    secret = re.compile(rb'(?:e2b_[A-Za-z0-9]{40,}|sk-ant-[A-Za-z0-9_-]{30,}|hf_[A-Za-z0-9]{30,})')
    for path in ROOT.rglob('*'):
        if path.is_file():
            assert not secret.search(path.read_bytes()), str(path)
    report = {'experiments': len(EXPERIMENTS), 'markdown_files': len(markdown),
              'immutable_artifacts_verified': len(immutable), 'pinned_source_records_verified': len(pinned),
              'agent_results': 84, 'qualified_tasks': 28, 'performance_batches_recomputed': 54,
              'python_files_parsed': len(code), 'shell_examples_checked': shell_blocks,
              'english_diagrams': len(diagrams), 'missing_links': missing,
              'scope': 'Offline documentation checks; no model, training, or sandbox execution.'}
    (PROVENANCE / 'bundle-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
