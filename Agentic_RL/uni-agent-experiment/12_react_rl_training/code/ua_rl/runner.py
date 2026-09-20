import functools,os,time
from .common import CURRENT,ROOT,Profile,save
from . import tasks

_installed=False
def install():
    global _installed
    if _installed:return
    _installed=True
    from uni_agent.agents.react.model import OpenAICompatibleChatModel
    from uni_agent.tools import Toolbox
    def wrap(cls,name,category):
        original=getattr(cls,name)
        @functools.wraps(original)
        async def call(self,*args,**kwargs):
            p=CURRENT.get()
            if p is None:return await original(self,*args,**kwargs)
            fields={'tool':args[0]} if category=='tool_call' and args else {}
            with p.span(category,**fields) as event:
                result=await original(self,*args,**kwargs)
                if category=='model_roundtrip':event['usage']=result[2]
                return result
        setattr(cls,name,call)
    wrap(OpenAICompatibleChatModel,'query','model_roundtrip');wrap(Toolbox,'call','tool_call')

async def run_task(**kwargs):
    install()
    from uni_agent.framework.task_runner import run_task as upstream
    session=kwargs['session'];key=session.session_id
    p=Profile(key,ROOT/'results/sessions'/key)
    with p.active():
        try:
            save(p.folder/'identity.json',{'session_id':key,'run_phase':os.environ.get('UA_RL_PHASE','main'),'attempt':int(os.environ.get('UA_RL_ATTEMPT','1')),'sample_index':kwargs.get('sample_index'),'instance_id':kwargs['tools_kwargs']['task']['metadata']['instance_id'],'started_unix':time.time()})
            result=await upstream(**kwargs)
            result.extra_info['profile_metrics']=p.metrics()
            save(p.folder/'result.json',{'reward':result.reward,'finished':result.finished,'extra_info':result.extra_info})
            return result
        except BaseException as exc:
            save(p.folder/'error.json',{'error_type':type(exc).__name__,'error':str(exc),'time_unix':time.time()})
            raise
        finally:p.save()

def score_from_runner_result(*,extra_info,**kwargs):
    result=extra_info['runner_reward_info']
    return {'score':float(result['reward']),**result['metrics'],**result.get('reward_context',{}).get('profile_metrics',{})}
