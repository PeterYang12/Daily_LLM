"""Uni-Agent OpenSandbox provider using native file APIs and Bash sessions."""
import asyncio,base64,contextlib,json,os,re,shlex,sys,time,uuid
from datetime import timedelta
from pathlib import Path
sys.path.insert(0,'/lab/envs/opensandbox-sdk')
from opensandbox import Sandbox as SDK
from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import ExecutionHandlers,RunCommandOpts
from opensandbox.models.filesystem import WriteEntry
from uni_agent.sandbox.base import Sandbox,ExecResult
from uni_agent.sandbox.registry import register_sandbox

def settings(path='/lab/secrets/local-opensandbox/container.env'):
    values=dict(line.split('=',1) for line in Path(path).read_text().splitlines() if line and not line.startswith('#'))
    return ConnectionConfig(domain=values['OPENSANDBOX_DOMAIN'],protocol=values['OPENSANDBOX_PROTOCOL'],api_key=values['OPENSANDBOX_API_KEY'],use_server_proxy=True,request_timeout=timedelta(seconds=30))

class NativeShell:
    def __init__(self,owner,session_id):self.owner=owner;self.session_id=session_id;self.closed=False
    async def run(self,command,*,timeout=120):
        if self.closed:raise RuntimeError('OpenSandbox shell session is closed')
        return await self.owner._execute(command,timeout=timeout,session_id=self.session_id)
    async def close(self):
        if self.closed:return
        if self.owner.sb:await self.owner.sb.commands.delete_session(self.session_id)
        self.closed=True

@register_sandbox('opensandbox')
class OpenSandboxProvider(Sandbox):
    supports_shell=True
    @classmethod
    def from_config(cls,config):
        kwargs=dict(config.sandbox_kwargs);key=kwargs.pop('profile_key',None);kwargs.pop('slot',None)
        profile=None
        if key:
            from providers import PROFILES
            profile=PROFILES[key]
        return cls(config.image,timeout=config.runtime_timeout,profile=profile,**kwargs)
    def __init__(self,image,*,timeout=3600,profile=None,instance_label='ua-opensandbox',cpu='4',memory='8Gi',shell_init='',env_file='/lab/secrets/local-opensandbox/container.env'):
        self.image=image;self.timeout=timeout;self.profile=profile;self.label=instance_label
        self.cpu=cpu;self.memory=memory;self.shell_init=shell_init;self.env_file=env_file;self.sb=None;self.shells=[]
    def span(self,name,**kwargs):
        return self.profile.span(name,provider='opensandbox',**kwargs) if self.profile else contextlib.nullcontext({})
    async def start(self):
        with self.span('sandbox_create'):
            self.sb=await SDK.create(self.image,timeout=timedelta(seconds=self.timeout),ready_timeout=timedelta(seconds=240),resource={'cpu':self.cpu,'memory':self.memory},metadata={'experiment':self.label},connection_config=settings(self.env_file))
            if self.profile:
                (self.profile.out/f'sandbox-{self.sb.id}.json').write_text(json.dumps({'sandbox_id':self.sb.id,'image':self.image,'created_unix':time.time()})+'\n')
    async def stop(self):
        if not self.sb:return
        sb=self.sb
        with self.span('sandbox_destroy'):
            await sb.destroy()
            self.sb=None
            for shell in self.shells:shell.closed=True
            self.shells.clear()
    async def is_alive(self):
        try:return self.sb is not None and await asyncio.wait_for(self.sb.is_healthy(),5)
        except Exception:return False
    async def _execute(self,command,*,timeout=120,workdir=None,env=None,session_id=None):
        timeout=timeout if timeout is not None else 120
        marker='UA_CAPTURE_'+uuid.uuid4().hex
        out='/tmp/'+marker+'.out';err='/tmp/'+marker+'.err'
        # Execd's SSE text events omit line delimiters. Frame base64 output in
        # the same command so Unix output bytes survive without extra read RPCs.
        capture=(f'eval {shlex.quote(command)} > {out} 2> {err}\n'
                 +'__ua_capture_rc=$?\n'
                 +f"printf '\\n{marker}_BEGIN %s\\n' \"$__ua_capture_rc\"\n"
                 +f'/usr/bin/base64 {out}\n'
                 +f"printf '\\n{marker}_STDERR\\n'\n"
                 +f'/usr/bin/base64 {err}\n'
                 +f"printf '\\n{marker}_END\\n'\n"
                 +f'/bin/rm -f {out} {err}\n'
                 +'(exit "$__ua_capture_rc")')
        execution_id=None
        async def initialized(event):
            nonlocal execution_id
            execution_id=event.id
        handlers=ExecutionHandlers(on_init=initialized)
        with self.span('sandbox_exec',command_preview=command[:1200],session=bool(session_id)) as event:
            try:
                if session_id:
                    call=self.sb.commands.run_in_session(session_id,capture,timeout=timedelta(seconds=timeout),handlers=handlers)
                else:
                    call=self.sb.commands.run(capture,opts=RunCommandOpts(working_directory=workdir,envs=env,uid=0,timeout=timedelta(seconds=timeout)),handlers=handlers)
                result=await asyncio.wait_for(call,timeout+10)
            except (asyncio.TimeoutError,asyncio.CancelledError):
                if execution_id:
                    with contextlib.suppress(Exception):await asyncio.wait_for(asyncio.shield(self.sb.commands.interrupt(execution_id)),10)
                with contextlib.suppress(Exception):await asyncio.wait_for(asyncio.shield(self.sb.files.delete_files([out,err])),10)
                raise
            raw='\n'.join(x.text for x in result.logs.stdout)
            pattern=re.escape(marker)+r'_BEGIN (-?\d+)\n(.*?)'+re.escape(marker)+r'_STDERR\n(.*?)'+re.escape(marker)+r'_END'
            match=re.search(pattern,raw,re.S)
            if match:
                code=int(match[1]);stdout=base64.b64decode(match[2]).decode('utf-8',errors='replace');stderr=base64.b64decode(match[3]).decode('utf-8',errors='replace')
            elif result.exit_code==-1:
                with contextlib.suppress(Exception):await self.sb.files.delete_files([out,err])
                raise TimeoutError(f'OpenSandbox command exceeded {timeout}s')
            else:
                detail=f'{result.error.name}: {result.error.value}' if result.error else 'No exit code returned'
                if any(x in detail.lower() for x in ['timeout','timed out','deadline']):raise TimeoutError(detail)
                raise RuntimeError('Incomplete command output frame: '+detail)
            event.update(exit_code=code,stdout_bytes=len(stdout.encode()),stderr_bytes=len(stderr.encode()))
            if self.profile and 'eval_script_' in command:(self.profile.out/'verifier-raw.log').write_text(stdout+stderr)
            return ExecResult(code,stdout,stderr)
    async def _exec(self,argv,*,timeout=None,workdir=None,env=None):
        return await self._execute(shlex.join(argv),timeout=timeout,workdir=workdir,env=env)
    async def read_file(self,path):
        with self.span('file_read',path=path) as event:
            data=await self.sb.files.read_bytes(path);event['bytes']=len(data);return data
    async def write_file(self,path,content):
        with self.span('file_write',path=path,bytes=len(content.encode() if isinstance(content,str) else content)):
            await self.sb.files.write_files([WriteEntry(path=path,data=content,mode=0o644)])
    async def open_shell(self,*,cwd=None,env=None):
        sid=await self.sb.commands.create_session(working_directory=cwd)
        shell=NativeShell(self,sid);self.shells.append(shell)
        try:
            if self.shell_init:
                r=await shell.run(self.shell_init)
                if r.exit_code:raise RuntimeError('Shell initialization failed: '+r.stderr)
            if env:
                for key in env:
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key):raise ValueError('Invalid environment variable name')
                script='\n'.join('export '+key+'='+shlex.quote(str(value)) for key,value in env.items())
                r=await shell.run(script)
                if r.exit_code:raise RuntimeError('Shell environment initialization failed: '+r.stderr)
            return shell
        except BaseException:
            await shell.close();raise
