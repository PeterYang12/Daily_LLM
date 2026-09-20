import asyncio,contextlib,dataclasses,fcntl,os,shlex,time
from e2b import AsyncSandbox,CommandExitException,TimeoutException
from uni_agent.sandbox.base import Sandbox,ExecResult
from uni_agent.sandbox.registry import register_sandbox
from .common import CURRENT,ROOT,load_e2b,save

@contextlib.asynccontextmanager
async def create_slot():
    directory=ROOT/'state/create-slots';directory.mkdir(parents=True,exist_ok=True)
    handles=[open(directory/str(i),'a') for i in range(3)];held=None
    try:
        while held is None:
            for h in handles:
                try:fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB);held=h;break
                except BlockingIOError:pass
            if held is None:await asyncio.sleep(.025)
        yield
    finally:
        for h in handles:h.close()

@register_sandbox('e2b_rl')
class E2BRL(Sandbox):
    @classmethod
    def from_config(cls,config):return cls(config.image,int(config.runtime_timeout or 3600),native_exec=bool(config.sandbox_kwargs.get('native_exec',False)))
    def __init__(self,template,timeout=3600,native_exec=False):
        self.template=template;self.timeout=min(timeout,3600);self.sb=None;self.profile=CURRENT.get();self.native_exec=native_exec;self.base_env={}
    def span(self,name,**fields):
        return self.profile.span(name,**fields) if self.profile else contextlib.nullcontext({})
    async def start(self):
        load_e2b()
        async with contextlib.AsyncExitStack() as stack:
            with self.span('sandbox_create_queue'):
                await stack.enter_async_context(create_slot())
            with self.span('sandbox_create',template=self.template) as event:
                self.sb=await AsyncSandbox.create(self.template,timeout=self.timeout,request_timeout=60,metadata={'experiment':'ua-rl-300','session':self.profile.key if self.profile else 'preflight'})
                event['sandbox_id']=self.sb.sandbox_id
                if self.profile:save(self.profile.folder/('sandbox-'+self.sb.sandbox_id+'.json'),{'sandbox_id':self.sb.sandbox_id,'created_unix':time.time(),'template':self.template})
        if self.native_exec:
            from .native_exec import execute
            with self.span('shell_environment_init'):
                result=await execute(self.sb,['/bin/bash','-l','-c','/usr/bin/env -0'])
                values=dict(pair.split('=',1) for pair in result.stdout.split('\0') if '=' in pair)
                self.base_env={k:v for k,v in values.items() if k in {'PATH','PYTHONPATH','LD_LIBRARY_PATH','VIRTUAL_ENV'} or k.startswith('CONDA_')}
    async def stop(self):
        if self.sb is None:return
        sb=self.sb
        if self.profile:
            with self.span('sandbox_metrics') as event:
                try:
                    metrics=await sb.get_metrics(request_timeout=4)
                    save(self.profile.folder/('resources-'+sb.sandbox_id+'.json'),{'sandbox_id':sb.sandbox_id,'samples':[dataclasses.asdict(m) if dataclasses.is_dataclass(m) else vars(m) for m in metrics]})
                    event['samples']=len(metrics)
                except Exception as exc:event['unavailable']=type(exc).__name__
        with self.span('sandbox_destroy',sandbox_id=sb.sandbox_id) as event:
            event['kill_ack']=await sb.kill(request_timeout=30)
            self.sb=None
    async def is_alive(self):
        return self.sb is not None and await self.sb.is_running(request_timeout=10)
    def _is_timeout_error(self,exc):return isinstance(exc,(TimeoutError,TimeoutException)) or super()._is_timeout_error(exc)
    async def _exec(self,argv,*,timeout=None,workdir=None,env=None):
        with self.span('sandbox_exec',argv=argv,workdir=workdir,exec_mode='native' if self.native_exec else 'sdk_login') as event:
            try:
                if self.native_exec:
                    from .native_exec import execute
                    result=await execute(self.sb,list(argv),timeout=timeout or 120,cwd=workdir,env={**self.base_env,**(env or {})})
                else:
                    result=await self.sb.commands.run(shlex.join(argv),timeout=timeout or 120,cwd=workdir,envs=env or {},user='root',request_timeout=30)
            except CommandExitException as exc:result=exc
            event.update(exit_code=result.exit_code,stdout_bytes=len(result.stdout.encode()),stderr_bytes=len(result.stderr.encode()))
            if self.profile and any('eval_script_' in str(a) for a in argv):
                (self.profile.folder/'verifier-raw.log').write_text(result.stdout+result.stderr)
            return ExecResult(result.exit_code,result.stdout,result.stderr)
    async def read_file(self,path):
        with self.span('file_read',path=path) as event:
            data=bytes(await self.sb.files.read(path,format='bytes',user='root',request_timeout=60));event['bytes']=len(data);return data
    async def write_file(self,path,content):
        with self.span('file_write',path=path,bytes=len(content.encode() if isinstance(content,str) else content)):
            await self.sb.files.write(path,content,user='root',request_timeout=60)

async def checked(sb,argv,**kwargs):
    result=await sb.exec(argv,**kwargs)
    if result.exit_code:raise RuntimeError(f'Command exit {result.exit_code}: {shlex.join(argv)[:150]}: {result.stderr[-1000:]}')
    return result
