"""Replay the recorded merge_intervals task using only the E2B sandbox.

Uses the same BUG, TESTS, PROMPT and ReAct settings as the original paired run.
Run inside ua-lab-cpu. Does not use Gateway or perform training.
"""
import argparse
import asyncio
import dataclasses
import json
import os
import time
from pathlib import Path

BUG='''def merge_intervals(intervals):
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start < merged[-1][1]:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged
'''

TESTS='''import unittest
from interval_utils import merge_intervals
class Check(unittest.TestCase):
 def test_empty(self): self.assertEqual(merge_intervals([]), [])
 def test_single(self): self.assertEqual(merge_intervals([(1,2)]), [(1,2)])
 def test_unsorted(self): self.assertEqual(merge_intervals([(5,6),(1,3),(2,4)]),[(1,4),(5,6)])
 def test_touching(self): self.assertEqual(merge_intervals([(1,2),(2,3)]),[(1,3)])
 def test_nested(self): self.assertEqual(merge_intervals([(1,10),(2,3)]),[(1,10)])
 def test_negative(self): self.assertEqual(merge_intervals([(-5,-1),(-2,3)]),[(-5,3)])
 def test_duplicates(self): self.assertEqual(merge_intervals([(1,1),(1,1)]),[(1,1)])
 def test_mutation(self):
  x=[[5,6],[1,4],[2,3]]; old=[a[:] for a in x]; merge_intervals(x); self.assertEqual(x,old)
 def test_invalid(self):
  with self.assertRaises(ValueError): merge_intervals([(5,2)])
 def test_disjoint(self): self.assertEqual(merge_intervals([(5,7),(1,3)]),[(1,3),(5,7)])
unittest.main()
'''

PROMPT='''Fix /workspace/interval_utils.py. merge_intervals(intervals) must return a sorted list of tuples combining overlapping OR touching intervals. Nested intervals must not shrink the result. Handle empty input, duplicate/point intervals, negative boundaries, and unsorted inputs. Do not mutate either the caller's outer list or its inner lists. Raise ValueError for any interval with start > end. Use the existing Python standard library. Inspect and fix the function, test the behavior, then submit. Do not access any path outside /workspace except normal system tools.'''


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New result root; writes its e2b/ subdirectory')
    parser.add_argument('--env-file', default='/lab/secrets/e2b.env')
    parser.add_argument('--base-url', default='http://172.30.90.3:8000/v1')
    parser.add_argument('--template', default='testlab-python-node')
    return parser.parse_args()


def load_environment(path):
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key.strip()] = value
    for key in ['E2B_API_KEY', 'E2B_API_URL', 'E2B_SANDBOX_URL']:
        if not os.environ.get(key):
            raise ValueError('Missing configuration: ' + key)


def serialized(value):
    text = json.dumps(value, indent=2, ensure_ascii=False)
    key = os.environ.get('E2B_API_KEY')
    return text.replace(key, '[REDACTED]') if key else text


def dump(path, value):
    path.write_text(serialized(value) + '\n')


async def run(args):
    from uni_agent.agents import build_agent
    from uni_agent.agents.react.agent import ReActConfig
    from uni_agent.logging import sample_logging
    from e2b_provider import E2BCompatSandbox

    out = Path(args.output) / 'e2b'
    out.mkdir(parents=True, exist_ok=False)
    sandbox = E2BCompatSandbox(template=args.template, timeout=900)
    started = time.monotonic()
    result = {'provider': 'e2b', 'model': 'Qwen3-Coder-30B-A3B-Instruct',
              'base_url': args.base_url, 'gateway': False}
    try:
        print('[1/5] Creating remote E2B sandbox...', flush=True)
        async with sample_logging('e2b', log_path=str(out / 'task.log')), sandbox:
            result['sandbox_id'] = sandbox._sb.sandbox_id
            dump(out / 'sandbox.json', {'sandbox_id': result['sandbox_id']})
            print('[2/5] Preparing code and running baseline tests...', flush=True)
            await sandbox.exec(['mkdir', '-p', '/workspace'])
            await sandbox.write_file('/workspace/interval_utils.py', BUG)
            await sandbox.write_file('/workspace/heldout.py', TESTS)
            baseline = await sandbox.exec(['python3', '/workspace/heldout.py'], workdir='/workspace')
            dump(out / 'baseline.json', dataclasses.asdict(baseline))
            if 'Ran 10 tests' not in baseline.stderr:
                raise RuntimeError('Baseline did not execute all 10 tests; inspect baseline.json.')
            if baseline.exit_code == 0:
                raise RuntimeError('Baseline unexpectedly passed; task setup is not a valid negative control.')
            await sandbox.exec(['rm', '-f', '/workspace/heldout.py'])
            await sandbox.exec(['rm', '-rf', '/workspace/__pycache__'])
            config = ReActConfig(max_steps=30, model={
                'base_url': args.base_url, 'model_name': result['model'],
                'temperature': 0.2, 'top_p': 0.9,
                'max_total_tokens': 50000, 'max_tokens_per_turn': 2048,
            })
            print('[3/5] Running Uni-Agent ReAct with local Qwen and remote tools...', flush=True)
            agent_result = await asyncio.wait_for(build_agent(config).run(
                sandbox=sandbox, messages=[{'role': 'user', 'content': PROMPT}],
                workdir='/workspace'), timeout=600)
            dump(out / 'agent-result.json', dataclasses.asdict(agent_result))
            result['finished'] = agent_result.finished
            result['agent_info'] = agent_result.info
            (out / 'interval_utils.py').write_bytes(await sandbox.read_file('/workspace/interval_utils.py'))
            print('[4/5] Restoring independent tests and verifying the repair...', flush=True)
            await sandbox.write_file('/workspace/heldout.py', TESTS)
            verification = await sandbox.exec(['python3', '/workspace/heldout.py'], workdir='/workspace')
            dump(out / 'verifier.json', dataclasses.asdict(verification))
            result['resolved'] = verification.exit_code == 0
            print('[5/5] Destroying the remote sandbox...', flush=True)
        result['cleanup_completed'] = True
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['wall_seconds'] = time.monotonic() - started
        dump(out / 'result.json', result)
        print(serialized(result), flush=True)
    return result


if __name__ == '__main__':
    args = parse_args()
    load_environment(args.env_file)
    outcome = asyncio.run(run(args))
    raise SystemExit(0 if outcome.get('resolved') and outcome.get('cleanup_completed') else 1)
