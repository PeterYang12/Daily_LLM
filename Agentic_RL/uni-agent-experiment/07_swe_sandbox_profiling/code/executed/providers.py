"""Profiled Docker and native E2B environments built from matched SWE images."""
import asyncio,dataclasses,json,os,shlex,sys,tempfile,time
from pathlib import Path
from e2b import AsyncSandbox,CommandExitException,TimeoutException
from uni_agent.sandbox.base import Sandbox,ExecResult
from uni_agent.sandbox.docker import DockerSandbox
from uni_agent.sandbox.registry import register_sandbox
from profiling import CURRENT

PROFILES={}

def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(data,indent=2,ensure_ascii=False,default=str)
    key=os.environ.get('E2B_API_KEY')
    if key:text=text.replace(key,'[REDACTED]')
    path.write_text(text+'\n')

@register_sandbox('profile_docker')
class ProfileDocker(DockerSandbox):
    @classmethod
    def from_config(cls,config):
        kw=dict(config.sandbox_kwargs);key=kw.pop('profile_key');label=kw.pop('instance_label')
        slot=int(kw.pop('slot',0));start=8+slot*4
        p=PROFILES[key]
        return cls(profile=p,image=config.image,pull_policy='never',start_timeout=90,
            run_args=['--cpus','4','--cpuset-cpus',f'{start}-{start+3}','--memory','8g','--pids-limit','512',
                      '--security-opt','no-new-privileges','--cap-drop','ALL','--cap-add','CHOWN',
                      '--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','SETGID','--cap-add','SETUID',
                      '--dns','10.145.80.1','--label','ua-lab.owner=uni-agent-rocm-lab',
                      '--label','ua-lab.ttl-seconds=5400','--label','ua-swe-profile='+label])
    def __init__(self,*,profile,**kwargs):
        self.profile=profile;super().__init__(**kwargs)
    async def _run_docker(self,*args,**kwargs):
        with self.profile.span('docker_cli',operation=args[0],argv_preview=shlex.join(args)[:1200]) as event:
            result=await super()._run_docker(*args,**kwargs)
            event.update(exit_code=result.exit_code,stdout_bytes=len(result.stdout.encode()),stderr_bytes=len(result.stderr.encode()))
            if any('eval_script_' in a for a in args):
                (self.profile.out/'verifier-raw.log').write_text(result.stdout+result.stderr)
            return result
    async def start(self):
        with self.profile.span('sandbox_create',provider='docker'):
            await super().start()
    async def stop(self):
        with self.profile.span('sandbox_destroy',provider='docker'):
            await super().stop()
    async def read_file(self,path):
        with self.profile.span('file_read',provider='docker',path=path) as event:
            data=await super().read_file(path);event['bytes']=len(data);return data
    async def write_file(self,path,content):
        data=content.encode() if isinstance(content,str) else content
        with self.profile.span('file_write',provider='docker',path=path,bytes=len(data)):
            with tempfile.TemporaryDirectory() as directory:
                local=Path(directory)/'payload';local.write_bytes(data)
                await super().upload_file(local,path)

@register_sandbox('profile_e2b')
class ProfileE2B(Sandbox):
    @classmethod
    def from_config(cls,config):
        kw=config.sandbox_kwargs
        return cls(PROFILES[kw['profile_key']],config.image,config.runtime_timeout,kw['instance_label'])
    def __init__(self,profile,template,timeout,label):
        self.profile=profile;self.template=template;self.timeout=int(timeout);self.label=label;self.sb=None
    async def start(self):
        with self.profile.span('sandbox_create',provider='e2b'):
            self.sb=await AsyncSandbox.create(template=self.template,timeout=self.timeout,
                metadata={'experiment':self.label,'purpose':'swe-bench-profile'},request_timeout=60)
            save(self.profile.out/('sandbox-'+self.sb.sandbox_id+'.json'),{'sandbox_id':self.sb.sandbox_id,'template':self.template,'created_unix':time.time()})
    async def stop(self):
        sb,self.sb=self.sb,None
        if sb:
            with self.profile.span('sandbox_destroy',provider='e2b'):
                killed=await sb.kill(request_timeout=30)
                save(self.profile.out/('cleanup-'+sb.sandbox_id+'.json'),{'sandbox_id':sb.sandbox_id,'kill_ack':killed,'finished_unix':time.time()})
    async def is_alive(self):
        try:return self.sb is not None and await self.sb.is_running(request_timeout=10)
        except Exception:return False
    def _is_timeout_error(self,exc):
        return isinstance(exc,(TimeoutError,TimeoutException)) or super()._is_timeout_error(exc)
    async def _exec(self,argv,*,timeout=None,workdir=None,env=None):
        with self.profile.span('sandbox_exec',provider='e2b',argv_preview=shlex.join(argv)[:1200],workdir=workdir) as event:
            try:
                result=await self.sb.commands.run(shlex.join(argv),timeout=timeout or 120,cwd=workdir,envs=env or {},user='root',request_timeout=30)
            except CommandExitException as exc:result=exc
            event.update(exit_code=result.exit_code,stdout_bytes=len(result.stdout.encode()),stderr_bytes=len(result.stderr.encode()))
            if any('eval_script_' in a for a in argv):
                (self.profile.out/'verifier-raw.log').write_text(result.stdout+result.stderr)
            return ExecResult(result.exit_code,result.stdout,result.stderr)
    async def read_file(self,path):
        with self.profile.span('file_read',provider='e2b',path=path) as event:
            result=bytes(await self.sb.files.read(path,format='bytes',user='root',request_timeout=60));event['bytes']=len(result);return result
    async def write_file(self,path,content):
        with self.profile.span('file_write',provider='e2b',path=path,bytes=len(content.encode() if isinstance(content,str) else content)):
            await self.sb.files.write(path,content,user='root',request_timeout=60)

async def checked(sandbox,argv,**kwargs):
    result=await sandbox.exec(argv,**kwargs)
    if result.exit_code:raise RuntimeError(f'Command failed ({result.exit_code}): {shlex.join(argv)[:200]}: {result.stderr[-1000:]} {result.stdout[-1000:]}')
    return result

async def prepare(sandbox,profile,archive,folder,expected_commit,tools=True):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    with profile.span('environment_prepare',tools=tools):
        loopback=await checked(sandbox,['bash','-c',"getent ahostsv4 localhost >/dev/null || printf '\\n127.0.0.1 localhost\\n' >> /etc/hosts; getent ahostsv4 localhost"])
        (folder/'loopback.txt').write_text(loopback.stdout)
        if tools:
            await sandbox.upload_file(archive,'/tmp/ua-tmux.tar.gz')
            await checked(sandbox,['bash','-c','mkdir -p /opt; tar -xzf /tmp/ua-tmux.tar.gz -C /opt; ln -sf /opt/ua-tmux/tmux /usr/local/bin/tmux; rm /tmp/ua-tmux.tar.gz'])
            tmux_version=await checked(sandbox,['tmux','-V'])
            (folder/'tmux-version.txt').write_text(tmux_version.stdout)
        await checked(sandbox,['git','config','--global','--add','safe.directory','/testbed'])
        await checked(sandbox,['git','config','core.fileMode','false'],workdir='/testbed')
        commit=(await checked(sandbox,['git','rev-parse','HEAD'],workdir='/testbed')).stdout.strip()
        # SWE images can contain setup commits after the dataset base. Preserve the
        # pinned image state and compare its initial tree across providers instead.
        await checked(sandbox,['git','cat-file','-e',expected_commit+'^{commit}'],workdir='/testbed')
        environment=await checked(sandbox,['bash','-c','uname -a; nproc; head -n 4 /etc/os-release; /opt/miniconda3/envs/testbed/bin/python --version; free -m; df -h /testbed'])
        (folder/'environment.txt').write_text(environment.stdout)
        baseline=(await checked(sandbox,['git','diff','--binary',expected_commit],workdir='/testbed')).stdout
        (folder/'image-baseline.patch').write_text(baseline)
        await checked(sandbox,['git','add','-A'],workdir='/testbed')
        tree=(await checked(sandbox,['git','write-tree'],workdir='/testbed')).stdout.strip()
        save(folder/'initial-state.json',{'head':commit,'tree':tree,'dataset_base_commit':expected_commit,'preexisting_patch_bytes':len(baseline.encode())})
        return tree
