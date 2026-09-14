"""Evidence wrappers; agents, task prompts and SWE verifier are upstream Uni-Agent."""
import asyncio, dataclasses, hashlib, json, tempfile, time
from pathlib import Path
from uni_agent.sandbox.docker import DockerSandbox

def dump(path, data):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(data,indent=2,ensure_ascii=False,default=str)+'\n')

class EvidenceDocker(DockerSandbox):
 def __init__(self, *, evidence_dir, **kw):
  super().__init__(**kw); self.evidence_dir=Path(evidence_dir); self.evidence_dir.mkdir(parents=True,exist_ok=True)
 async def _exec(self, argv, **kw):
  start=time.monotonic()
  try:
   result=await super()._exec(argv,**kw)
   record={'argv':argv,'workdir':kw.get('workdir'),'elapsed_s':time.monotonic()-start,**dataclasses.asdict(result)}
  except BaseException as e:
   record={'argv':argv,'elapsed_s':time.monotonic()-start,'exception':repr(e)}
   raise
  finally:
   with (self.evidence_dir/'exec.jsonl').open('a') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')
  return result
 async def write_file(self,path,content):
  # Docker's upstream base64-in-argv floor hits Linux MAX_ARG_STRLEN for large patches.
  # Its native upload_file uses docker cp and preserves bytes without argv size limits.
  data=content.encode() if isinstance(content,str) else content
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'payload'; p.write_bytes(data)
   await self.upload_file(p,path)
 async def stop(self):
  if self._container_name:
   try:
    r=await self._run_docker('exec',self._container_name,'cat','/sys/fs/cgroup/memory.events')
    (self.evidence_dir/'memory-events.txt').write_text(r.stdout+r.stderr)
   except Exception: pass
  await super().stop()
 async def start(self):
  await super().start()
  r=await self._run_docker('inspect',self._require_container())
  dump(self.evidence_dir/'container-inspect.json',json.loads(r.stdout))

async def checked(sandbox,argv,**kw):
 r=await sandbox.exec(argv,**kw)
 if r.exit_code: raise RuntimeError(f'command failed ({r.exit_code}): {argv!r}: {r.stderr[-1500:]} {r.stdout[-1500:]}')
 return r
