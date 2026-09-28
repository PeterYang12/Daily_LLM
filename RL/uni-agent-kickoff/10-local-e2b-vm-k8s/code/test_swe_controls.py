"""Replay one real SWE verifier baseline/oracle pair; no model is called."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import pyarrow.parquet as pq

sys.path[:0] = ['/data2/yuhanya/uni-agent-lab/src/uni-agent',
               '/data2/yuhanya/swe-e2b-rl-20260914/code']
from uni_agent.tasks.swe_bench import reward as native_reward
from swe_e2b_sandbox import SWEE2BSandbox, CURRENT_EPISODE, EpisodeObservation, redact
from validate_swe_controls import source_content_diff, verdict

ROOT = Path('/data2/yuhanya/e2b-local-20260916')
LAB = Path('/data2/yuhanya/uni-agent-lab')
INSTANCE = 'pallets__flask-5014'


async def phase(mode, row, config):
    folder = ROOT / 'results/swe-controls' / mode
    folder.mkdir(parents=True, exist_ok=False)
    task = row['extra_info']['tools_kwargs']['task']
    metadata = task['metadata']
    observation = EpisodeObservation()
    token = CURRENT_EPISODE.set(observation)
    began = time.monotonic()
    result = {'instance_id': INSTANCE, 'phase': mode}
    try:
        async with SWEE2BSandbox(image=task['sandbox']['image'], runtime_timeout=900,
                template_map_path=str(ROOT / 'configs/template-map.json'),
                credentials_file='/home/yuhanya/.config/e2b-local-20260916/lab.json',
                api_url=config['api_url'], sandbox_url=config['sandbox_url']) as sandbox:
            info = await sandbox._sandbox.get_info()
            assert (info.cpu_count, info.memory_mb) == (4, 8192)
            result['resources'] = {'vcpus': info.cpu_count, 'memory_mb': info.memory_mb}
            ancestor = await sandbox.exec(['git', 'merge-base', '--is-ancestor', metadata['base_commit'], 'HEAD'])
            assert ancestor.exit_code == 0
            initial = await sandbox.exec(['git', '-c', 'core.fileMode=false', 'diff', '--binary', metadata['base_commit']])
            prior = LAB / 'results/swe-expanded-v1/baseline' / INSTANCE / 'candidate.content.patch'
            if not prior.exists():
                prior = LAB / 'results/swe-baseline-six' / INSTANCE / 'candidate.patch'
            expected = prior.read_text() if prior.exists() else ''
            assert source_content_diff(initial.stdout) == source_content_diff(expected)
            result['initial_source_matches_prior_baseline'] = True
            if mode == 'oracle':
                await sandbox.write_file('/tmp/oracle.patch', metadata['patch'])
                applied = await sandbox.exec(['git', 'apply', '--whitespace=fix', '/tmp/oracle.patch'])
                assert applied.exit_code == 0
            scoring_metadata = dict(metadata)
            scoring_metadata.pop('patch', None)
            extra = await native_reward.compute_reward(scoring_metadata, sandbox, eval_timeout=300)
            result.update(reward=float(extra['resolved']), extra_info=extra)
    except Exception as error:
        result.update(error_type=type(error).__name__, error=redact(str(error)))
    finally:
        CURRENT_EPISODE.reset(token)
    result['wall_seconds'] = time.monotonic() - began
    result['sandbox_ids'] = [e['sandbox_id'] for e in observation.events if e['kind'] == 'sandbox_started']
    result['cleanup_sandbox_ids'] = [e['sandbox_id'] for e in observation.events if e['kind'] == 'sandbox_stopped']
    result['infrastructure_errors'] = observation.infrastructure_errors
    for i, run in enumerate(observation.verifier_runs):
        (folder / f'verifier-{i}.stdout').write_text(redact(run.get('stdout', '')))
        (folder / f'verifier-{i}.stderr').write_text(redact(run.get('stderr', '')))
    (folder / 'result.json').write_text(json.dumps(redact(result), indent=2))
    print(json.dumps({k: result.get(k) for k in ['phase', 'reward', 'error_type', 'wall_seconds']}), flush=True)
    return result


async def main():
    config = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
    assert config['api_url'] == config['sandbox_url'] == 'http://10.145.64.67:8088'
    rows = pq.read_table(LAB / 'results/swe-data/verified-six.parquet').to_pylist()
    row = next(x for x in rows if x['extra_info']['tools_kwargs']['task']['metadata']['instance_id'] == INSTANCE)
    baseline, oracle = await asyncio.gather(*(phase(mode, row, config) for mode in ['baseline', 'oracle']))
    check = verdict(baseline, oracle)
    assert baseline['sandbox_ids'] == baseline['cleanup_sandbox_ids']
    assert oracle['sandbox_ids'] == oracle['cleanup_sandbox_ids']
    report = {'status': 'passed' if check['usable'] else 'failed', 'checks': check,
        'scope': 'Official native SWE verifier controls; oracle uses supplied gold patch, no model execution',
        'endpoint': config['api_url'], 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'native_verifier_sha256': hashlib.sha256(Path(native_reward.__file__).read_bytes()).hexdigest(),
        'baseline': baseline, 'oracle': oracle}
    (ROOT / 'results/swe-controls/summary.json').write_text(json.dumps(redact(report), indent=2))
    assert check['usable'], check


if __name__ == '__main__':
    asyncio.run(main())
