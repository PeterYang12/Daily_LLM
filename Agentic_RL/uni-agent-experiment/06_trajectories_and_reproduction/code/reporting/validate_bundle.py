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
               '04_e2b_sandbox', '05_inference_performance', '06_trajectories_and_reproduction',
               '07_swe_sandbox_profiling', '08_local_e2b', '09_sandbox_three_way', '10_local_opensandbox', '11_local_e2b_capacity',
               '12_react_rl_training']
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

    profile = EXPERIMENTS[6] + '/results/'
    profile_records = load(profile + 'source-manifest.json')['copied_files']
    for record in profile_records:
        path = ROOT / EXPERIMENTS[6] / record['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], str(path)
    counts = load(profile + 'experiment-counts.json')
    assert counts == {'primary_rollouts': 24, 'accepted_controls': 48, 'fixed_operation_samples': 200,
                      'scheduling_attempts': 16, 'serving_batches': 54, 'logprob_batches': 24,
                      'verifier_cpu_runs': 12}, counts
    cases = load(profile + 'cases.json')
    assert len(cases) == 24
    for provider in ['docker', 'e2b']:
        group = [r for r in cases if r['provider'] == provider]
        assert len(group) == 12 and sum(r['resolved'] for r in group) == 9
        assert all(r['eval_completed'] and r['trajectory_valid'] and not r['error'] for r in group)
        for row in group:
            path = profile + 'cases/' + provider + '/' + row['instance_id'] + '/'
            raw = load(path + 'result.json')
            assert raw['resolved'] == row['resolved'] and raw['cleanup_completed']
            assert load(path + 'trajectory-audit.json')['valid']
        assert math.isclose(sum(r['wall_seconds'] for r in group) / 12,
                            load(profile + 'summary.json')['providers'][provider]['mean_wall_s'])
    controls = load(profile + 'controls.json')
    assert len(controls) == 48
    assert all(r['eval_completed'] and r['resolved'] == (r['mode'] == 'oracle') for r in controls)
    for iid in {r['instance_id'] for r in cases}:
        states = [r['initial_state'] for r in controls if r['instance_id'] == iid and r['mode'] == 'baseline']
        assert len(states) == 2 and states[0]['tree'] == states[1]['tree'] and states[0]['head'] == states[1]['head']
    serving = load(profile + 'inference/raw.json')
    assert len(serving) == 54
    for stat in load(profile + 'inference/summary.json'):
        group = [r for r in serving if all(r[k] == stat[k] for k in ['backend', 'input_length', 'concurrency'])]
        assert len(group) == 3 and all(r['output_tokens'] == r['concurrency'] * 256 for r in group)
        assert math.isclose(sum(r['output_tokens_per_s'] for r in group) / 3, stat['output_tokens_per_s'])
    overhead = load(profile + 'inference/logprob-overhead.json')['results']
    assert len(overhead) == 24
    assert len({(r['backend'], r['concurrency'], r['repeat'], r['logprobs']) for r in overhead}) == 24
    probe = load(profile + 'verifier-cpu-probe.json')
    assert len(probe) == 12 and all(r['resolved'] and r['resources'] for r in probe)
    assert not load(profile + 'service-audit.json')['owned_running_after']

    local_e2b = EXPERIMENTS[7] + '/results/'
    local_records = load(local_e2b + 'source-manifest.json')['copied_files']
    for record in local_records:
        path = ROOT / EXPERIMENTS[7] / record['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], str(path)
    deployment = load(local_e2b + 'deployment-audit.json')
    assert deployment['success'] and deployment['pod_ready'] and deployment['active_sandboxes'] == 0
    assert all(not r['reachable'] for r in deployment['internal_port_reachability'])
    for name in ['sdk-smoke-before-restart', 'sdk-smoke-after-restart']:
        checked = load(local_e2b + name + '.json')
        assert checked['success'] and all(checked['checks'].values())
        assert len(checked['concurrent']) == 4 and all(r['passed'] for r in checked['concurrent'])
    local_case = load(local_e2b + 'uni-agent/result.json')
    assert all(local_case[k] for k in ['resolved', 'finished', 'eval_completed', 'trajectory_valid', 'cleanup_completed'])
    assert not load(local_e2b + 'controls/baseline.json')['resolved']
    assert load(local_e2b + 'controls/oracle.json')['resolved']
    for line in (ROOT / local_e2b / 'uni-agent/trajectories.jsonl').read_text().splitlines():
        t = json.loads(line)['trajectory']
        assert len(t['response_ids']) == len(t['response_mask']) == len(t['response_logprobs'])
        assert all(math.isfinite(x) for x in t['response_logprobs']) and t['reward_score'] == 1.0

    comparison = EXPERIMENTS[8] + '/results/'
    comparison_records = load(comparison + 'source-manifest.json')['copied_files']
    for record in comparison_records:
        path = ROOT / EXPERIMENTS[8] / record['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], str(path)
    comp = load(comparison + 'summary.json');comp_cases = load(comparison + 'cases.json')
    assert comp['complete'] and len(comp_cases) == 9
    for variant in ['docker', 'local_e2b', 'remote_e2b']:
        rows = [r for r in comp_cases if r['variant'] == variant]
        assert len(rows) == 3 and all(r['eval_completed'] and r['trajectory_valid'] for r in rows)
        assert sum(r['resolved'] for r in rows) == comp['groups'][variant]['resolved']
        assert math.isclose(sum(r['wall_seconds'] for r in rows) / 3, comp['groups'][variant]['means']['wall_seconds'])
    assert sum(x['n'] for x in comp['microbench']) == 300
    assert load(comparison + 'controls-audit.json')['matched']
    assert load(comparison + 'cleanup-audit.json')['success']

    osb = EXPERIMENTS[9] + '/results/'
    osb_records = load(osb + 'source-manifest.json')['copied_files']
    for record in osb_records:
        path = ROOT / EXPERIMENTS[9] / record['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], str(path)
    osb_audit = load(osb + 'deployment-audit.json')
    assert osb_audit['success'] and osb_audit['active_sandbox_pods'] == osb_audit['active_batchsandboxes'] == 0
    assert osb_audit['unauthenticated_status'] == 401 and all(osb_audit['provider_smoke_checks'].values())
    assert osb_audit['restart_preserves_session'] and osb_audit['restart_preserves_file']
    assert osb_audit['e2e']['reward'] == 1.0 and osb_audit['e2e']['trajectory_valid']
    assert not load(osb + 'controls/baseline.json')['resolved'] and load(osb + 'controls/oracle.json')['resolved']
    for line in (ROOT / osb / 'uni-agent/trajectories.jsonl').read_text().splitlines():
        t = json.loads(line)['trajectory']
        assert len(t['response_ids']) == len(t['response_mask']) == len(t['response_logprobs'])
        assert all(math.isfinite(x) for x in t['response_logprobs']) and t['reward_score'] == 1.0

    large = EXPERIMENTS[10] + '/results/'
    large_records = load(large + 'source-manifest.json')['copied_files']
    for record in large_records:
        path = ROOT / EXPERIMENTS[10] / record['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], str(path)
    large_audit = load(large + 'deployment-audit.json')
    assert large_audit['success'] and large_audit['vm_vcpus'] == 96 and large_audit['vm_memory_gib'] == 512
    assert large_audit['old_vm_state'] == 'shut off' and large_audit['active_sandboxes'] == 0
    assert large_audit['reboot']['success'] and large_audit['api_secret_preserved']
    concurrent = load(large + 'concurrency-24/summary.json')
    assert concurrent['success'] and concurrent['n'] == concurrent['simultaneously_live'] == concurrent['passed'] == 24
    assert concurrent['all_cleaned'] and len(concurrent['rows']) == 24
    for row in concurrent['rows']:
        assert row['cleanup_verified'] and row['eval_completed'] and row['passed']
        assert row['resolved'] == (row['mode'] == 'oracle')
    assert load(large + 'concurrency-barrier-guest.json')['firecracker_count'] == 24
    assert load(large + 'project-limits.json')['after']['concurrent_sandboxes'] == 64

    rl = EXPERIMENTS[11] + '/results/'
    rl_records = load(rl + 'source-manifest.json')['copied_files']
    for record in rl_records:
        path = ROOT / EXPERIMENTS[11] / record['destination']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], str(path)
    rl_status = load(rl + 'status.json')
    assert rl_status['status'] in {'running', 'paused', 'complete'} and rl_status['target_steps'] == 300
    rl_launch = load(rl + 'main-launch.json')
    assert (rl_launch['train_prompts_per_step'], rl_launch['responses_per_prompt'], rl_launch['rollout_concurrency']) == (4, 4, 16)
    assert (rl_launch['save_freq'], rl_launch['checkpoints_to_keep']) == (100, 1)
    assert load(rl + 'pilot-proof.json')['success'] and load(rl + 'native-exec-comparison.json')['success']
    frozen = load(rl + 'frozen-data.json')
    assert frozen['train']['n'] == 62 and frozen['val']['n'] == 16
    assert not set(frozen['train']['instance_ids']) & set(frozen['val']['instance_ids'])
    for name, record in frozen.items():
        assert hashlib.sha256((ROOT / EXPERIMENTS[11] / 'configs/data' / (name + '.parquet')).read_bytes()).hexdigest() == record['sha256']
    if rl_status['status'] == 'complete':
        final_rl = load(rl + 'training-summary.json')
        assert final_rl['step_sequence_complete'] and final_rl['batch_shape_valid']
        assert final_rl['last_step'] == 300 and final_rl['consumed_trajectories'] == 4800
        assert final_rl['checkpoint_dirs'] == ['global_step_300'] and final_rl['exit']['exit_code'] == 0
        assert not load(rl + 'trajectory-audit.json')['errors']

    markdown = list(ROOT.rglob('*.md'))
    assert len(markdown) <= 35, 'Keep the authored documentation compact.'
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
    secret = re.compile(rb'(?:e2b_[A-Za-z0-9]{40,}|osb_[A-Za-z0-9_-]{30,}|sk-ant-[A-Za-z0-9_-]{30,}|hf_[A-Za-z0-9]{30,}|wandb_v1_[A-Za-z0-9_-]{20,})')
    for path in ROOT.rglob('*'):
        if path.is_file():
            assert not secret.search(path.read_bytes()), str(path)
    report = {'experiments': len(EXPERIMENTS), 'markdown_files': len(markdown),
              'immutable_artifacts_verified': len(immutable), 'pinned_source_records_verified': len(pinned),
              'agent_results': 84, 'qualified_tasks': 28, 'performance_batches_recomputed': 54,
              'python_files_parsed': len(code), 'shell_examples_checked': shell_blocks,
              'english_diagrams': len(diagrams), 'missing_links': missing,
              'profiling_artifacts_verified': len(profile_records), 'profiling_experiments': counts,
              'local_e2b_artifacts_verified': len(local_records), 'local_e2b_acceptance': deployment['success'],
              'three_way_artifacts_verified': len(comparison_records), 'three_way_rollouts': len(comp_cases),
              'opensandbox_artifacts_verified': len(osb_records), 'opensandbox_e2e': osb_audit['success'],
              'large_e2b_artifacts_verified': len(large_records), 'large_e2b_capacity_verified': concurrent['simultaneously_live'],
              'rl_artifacts_verified': len(rl_records), 'rl_status': rl_status['status'],
              'rl_last_observed_step': rl_status['last_observed_step'],
              'scope': 'Offline documentation checks; no model, training, or sandbox execution.'}
    (PROVENANCE / 'bundle-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
