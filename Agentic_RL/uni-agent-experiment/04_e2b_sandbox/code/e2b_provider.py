"""Lab extension: E2B-compatible SDK implementation of Uni-Agent's Sandbox interface.
Upstream Uni-Agent at the pinned revision has no native E2B provider.
"""
from pathlib import Path
import shlex
from e2b import AsyncSandbox, CommandExitException
from uni_agent.sandbox.base import Sandbox, ExecResult
from uni_agent.sandbox.registry import register_sandbox

@register_sandbox('e2b_compat')
class E2BCompatSandbox(Sandbox):
 def __init__(self,template='testlab-python-node',timeout=600):
  self.template=template; self.timeout=timeout; self._sb=None
 @classmethod
 def from_config(cls,config):
  return cls(template=config.image or 'testlab-python-node',timeout=int(config.runtime_timeout or 600))
 async def start(self):
  self._sb=await AsyncSandbox.create(template=self.template,timeout=self.timeout,metadata={'experiment':'uni-agent-rocm-lab'},request_timeout=30)
 async def stop(self):
  sb,self._sb=self._sb,None
  if sb: await sb.kill()
 async def is_alive(self):
  return bool(self._sb) and await self._sb.is_running()
 async def _exec(self,argv,*,timeout=None,workdir=None,env=None):
  try:
   r=await self._sb.commands.run(shlex.join(argv),timeout=timeout or 120,cwd=workdir,envs=env or {})
  except CommandExitException as e: r=e
  return ExecResult(exit_code=r.exit_code,stdout=r.stdout,stderr=r.stderr)
 async def read_file(self,path):
  return bytes(await self._sb.files.read(path,format='bytes'))
 async def write_file(self,path,content):
  await self._sb.files.write(path,content)
 async def upload_file(self,local_file,remote_file):
  await self.write_file(remote_file,Path(local_file).read_bytes())
 async def download_file(self,remote_file,local_file):
  p=Path(local_file); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(await self.read_file(remote_file))
