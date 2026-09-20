"""SWE tasks with profiled E2B lifecycle and a fresh, independent verifier."""
import asyncio,dataclasses,re,time
from pathlib import Path
from pydantic import Field
from uni_agent.tasks.base import Task,TaskConfig,TaskResult
from uni_agent.tasks.registry import register_task
from uni_agent.tasks.swe_rebench.task import _clean_git_history
from .common import CURRENT,save
from .provider import checked

def source_patch(patch):
    kept=[];excluded=[]
    for block in re.split(r'(?=^diff --git )',patch,flags=re.M):
        if not block.strip():continue
        m=re.search(r'^\+\+\+ b/(.+)$',block,re.M) or re.search(r'^--- a/(.+)$',block,re.M)
        path=m.group(1) if m else ''
        if re.search(r'(^|/)(tests?|testing)(/|$)|(^|/)(test_[^/]*|[^/]*_test)\.py$|(^|/)conftest\.py$',path):excluded.append(path)
        else:kept.append(block)
    return ''.join(kept),excluded

async def prepare(sb,meta,tools=False):
    await checked(sb,['git','config','--global','--add','safe.directory','/testbed'])
    await _clean_git_history(sb,meta['base_commit'])
    await checked(sb,['git','config','core.fileMode','false'],workdir='/testbed')
    await checked(sb,['bash','-c',"getent ahostsv4 localhost >/dev/null || printf '\\n127.0.0.1 localhost\\n' >> /etc/hosts"])
    if tools:
        archive=Path('/lab/local-e2b-20260916/results/uni-agent-flask/assets/tmux.tar.gz')
        await sb.write_file('/tmp/ua-tmux.tar.gz',archive.read_bytes())
        await checked(sb,['bash','-c','mkdir -p /opt; tar -xzf /tmp/ua-tmux.tar.gz -C /opt; ln -sf /opt/ua-tmux/tmux /usr/local/bin/tmux; rm /tmp/ua-tmux.tar.gz; tmux -V'])

async def reward(family,meta,sb,timeout):
    if family=='swe_rebench':from uni_agent.tasks.swe_rebench.reward import compute_reward
    else:from uni_agent.tasks.swe_bench.reward import compute_reward
    p=CURRENT.get();original=sb.exec_shell
    async def timed(script,**kwargs):
        marker='__UA_BODY_TIME_716672__'
        measured='__ua_start=$EPOCHREALTIME\n'+script+'\n__ua_rc=$?\n'+f"printf '\\n{marker} %s %s\\n' \"$__ua_start\" \"$EPOCHREALTIME\" >&2\n"+'exit "$__ua_rc"'
        with p.span('verifier_exec') as event:
            r=await original(measured,**kwargs)
            match=re.search(r'\n'+marker+r' ([0-9.]+) ([0-9.]+)\n?',r.stderr)
            if match:
                event['body_s']=float(match.group(2))-float(match.group(1));r.stderr=r.stderr[:match.start()]+r.stderr[match.end():]
            return r
    if p:sb.exec_shell=timed
    try:return await compute_reward(meta,sb,eval_timeout=timeout)
    finally:sb.exec_shell=original

class Config(TaskConfig):
    eval_timeout:float=300
    agent_timeout:float=1200

@register_task('rl_swe_bench')
@register_task('rl_swe_rebench')
class ProfiledSWE(Task):
    config_model=Config
    async def run(self):
        cfg=self.config;meta=cfg.metadata;p=CURRENT.get();family=cfg.name.removeprefix('rl_')
        sb=self.build_sandbox();detail={'instance_id':meta['instance_id'],'family':family};agent_result=None
        try:
            with p.in_phase('agent_provision'):
                await sb.__aenter__();await prepare(sb,meta,tools=True)
            with p.in_phase('agent'):
                agent_result=await asyncio.wait_for(self.build_agent().run(sandbox=sb,messages=cfg.prompt,workdir='/testbed'),timeout=cfg.agent_timeout)
                save(p.folder/'agent-result.json',dataclasses.asdict(agent_result))
            with p.in_phase('capture_patch'):
                await checked(sb,['git','add','-N','--','.'],workdir='/testbed')
                raw=(await checked(sb,['git','-c','core.fileMode=false','diff','--binary',meta['base_commit']],workdir='/testbed')).stdout
                patch,excluded=source_patch(raw)
                (p.folder/'candidate-source-only.patch').write_text(patch)
                detail.update(patch_bytes=len(patch.encode()),excluded_test_paths=excluded)
            with p.in_phase('agent_cleanup'):await sb.stop()
            sb=self.build_sandbox()
            with p.in_phase('verifier_provision'):
                await sb.__aenter__();await prepare(sb,meta)
                if patch:
                    await sb.write_file('/tmp/candidate.patch',patch)
                    await checked(sb,['git','apply','--whitespace=fix','/tmp/candidate.patch'],workdir='/testbed')
            with p.in_phase('verify'):
                score=await reward(family,meta,sb,cfg.eval_timeout)
                save(p.folder/'verifier.json',score);detail.update(score)
            result=TaskResult(reward=float(score['resolved']),accuracy=float(score['resolved']),finished=agent_result.finished,extra_info=detail)
        except Exception as exc:
            detail['error']=f'{type(exc).__name__}: {exc}'
            # Infrastructure failures remain errors; never relabel them as failed code repairs.
            raise
        finally:
            with p.in_phase('cleanup'):
                await sb.stop();detail['cleanup_completed']=True
            save(p.folder/'task-result.json',detail)
        detail['profile_metrics']=p.metrics()
        return result
