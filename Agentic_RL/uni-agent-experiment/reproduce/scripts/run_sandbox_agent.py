"""Same local 30B ReAct agent; tools execute in local Docker or remote E2B-compatible sandbox."""
import asyncio,dataclasses,json,os,time,argparse
from pathlib import Path
from uni_agent.agents import build_agent
from uni_agent.agents.react.agent import ReActConfig
from uni_agent.logging import sample_logging
from lab_runtime import EvidenceDocker,dump
from e2b_provider import E2BCompatSandbox
os.environ.update(dict(line.split('=',1) for line in Path('/lab/secrets/e2b.env').read_text().splitlines()))
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

parser=argparse.ArgumentParser();parser.add_argument('--output',default='/lab/results/sandbox-agent');args=parser.parse_args()

async def one(provider):
 out=Path(args.output)/provider; out.mkdir(parents=True,exist_ok=False)
 s=E2BCompatSandbox(timeout=900) if provider=='e2b' else EvidenceDocker(image='ua-lab/demo:20260912',evidence_dir=out/'container',pull_policy='never',run_args=['--network','none','--cpus','1','--memory','1g','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges'])
 start=time.time(); result={'provider':provider,'model':'Qwen3-Coder-30B-A3B-Instruct'}
 try:
  async with sample_logging(provider,log_path=str(out/'task.log')),s:
   if provider=='e2b': result['sandbox_id']=s._sb.sandbox_id
   r=await s.exec_shell('cat /etc/os-release; uname -a; cat /sys/class/dmi/id/product_name 2>/dev/null || true')
   (out/'environment.txt').write_text(r.stdout)
   await s.exec(['mkdir','-p','/workspace']); await s.write_file('/workspace/interval_utils.py',BUG)
   # Negative control runs before agent. Remove hidden verifier before exposing environment.
   await s.write_file('/workspace/heldout.py',TESTS)
   r=await s.exec(['python3','/workspace/heldout.py'],workdir='/workspace'); dump(out/'baseline.json',dataclasses.asdict(r))
   await s.exec(['rm','-f','/workspace/heldout.py']); await s.exec_shell('rm -rf /workspace/__pycache__')
   config=ReActConfig(max_steps=30,model={'base_url':'http://172.30.90.3:8000/v1','model_name':result['model'],'temperature':0.2,'top_p':0.9,'max_total_tokens':50000,'max_tokens_per_turn':2048})
   ar=await asyncio.wait_for(build_agent(config).run(sandbox=s,messages=[{'role':'user','content':PROMPT}],workdir='/workspace'),timeout=600)
   dump(out/'agent-result.json',dataclasses.asdict(ar)); result['finished']=ar.finished; result['agent_info']=ar.info
   (out/'interval_utils.py').write_bytes(await s.read_file('/workspace/interval_utils.py'))
   await s.write_file('/workspace/heldout.py',TESTS)
   r=await s.exec(['python3','/workspace/heldout.py'],workdir='/workspace'); dump(out/'verifier.json',dataclasses.asdict(r))
   result['resolved']=r.exit_code==0
 except Exception as e: result['error']=repr(e)
 result['wall_seconds']=time.time()-start; dump(out/'result.json',result); print(json.dumps(result),flush=True)
async def main():
 # Sequential to avoid conflating remote execution overhead with shared-model contention.
 for provider in ['docker','e2b']: await one(provider)
asyncio.run(main())
