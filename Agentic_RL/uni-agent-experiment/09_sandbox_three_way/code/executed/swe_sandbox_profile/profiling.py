"""Low-overhead in-memory spans for Uni-Agent rollout components."""
import contextlib,contextvars,functools,json,os,time,uuid
from pathlib import Path

CURRENT=contextvars.ContextVar('swe_profile',default=None)
PARENT=contextvars.ContextVar('swe_span_parent',default=None)
SESSIONS={}

class Profile:
    def __init__(self,key,out):
        self.key=key;self.out=Path(out);self.events=[];self.phase_name='setup';self.start_ns=time.time_ns()
    @contextlib.contextmanager
    def active(self):
        token=CURRENT.set(self)
        try:yield self
        finally:CURRENT.reset(token)
    @contextlib.contextmanager
    def phase(self,name):
        old=self.phase_name;self.phase_name=name
        try:
            with self.span('phase',name=name):yield
        finally:self.phase_name=old
    @contextlib.contextmanager
    def span(self,category,**fields):
        event={'id':len(self.events),'category':category,'phase':self.phase_name,
               'start_ns':time.time_ns(),'parent':PARENT.get(),**fields}
        self.events.append(event);token=PARENT.set(event['id']);start=time.perf_counter_ns()
        try:yield event
        except BaseException as exc:
            event['error_type']=type(exc).__name__;raise
        finally:
            event['duration_ns']=time.perf_counter_ns()-start;PARENT.reset(token)
    def save(self):
        self.out.mkdir(parents=True,exist_ok=True)
        payload={'key':self.key,'start_ns':self.start_ns,'events':self.events}
        text=json.dumps(payload,ensure_ascii=False)
        secret=os.environ.get('E2B_API_KEY')
        if secret:text=text.replace(secret,'[REDACTED]')
        (self.out/'spans.json').write_text(text+'\n')

def wrap_async(cls,name,category,describe=None,returned=None):
    original=getattr(cls,name)
    @functools.wraps(original)
    async def wrapped(self,*args,**kwargs):
        profile=CURRENT.get()
        if profile is None:return await original(self,*args,**kwargs)
        fields=describe(self,args,kwargs) if describe else {}
        with profile.span(category,**fields) as event:
            result=await original(self,*args,**kwargs)
            if returned:event.update(returned(result))
            return result
    setattr(cls,name,wrapped)

def wrap_sync(cls,name,category):
    original=getattr(cls,name)
    @functools.wraps(original)
    def wrapped(self,*args,**kwargs):
        profile=CURRENT.get()
        if profile is None:return original(self,*args,**kwargs)
        with profile.span(category,operation=name):return original(self,*args,**kwargs)
    setattr(cls,name,wrapped)

def install():
    import httpx
    from uni_agent.agents.react.model import OpenAICompatibleChatModel
    from uni_agent.tools import Toolbox
    from uni_agent.gateway.gateway import _GatewayActor
    from uni_agent.gateway.session.codec import MessageCodec
    wrap_async(OpenAICompatibleChatModel,'query','model_roundtrip',returned=lambda r:{
        'prompt_tokens':r[2].get('prompt_tokens'),'completion_tokens':r[2].get('completion_tokens'),
        'tool_calls':len(r[1]),'finish_reason':r[2].get('finish_reason')})
    wrap_async(Toolbox,'call','tool_call',describe=lambda s,a,k:{'tool':a[0] if a else k.get('name')},
               returned=lambda r:{'status':r.status,'observation_chars':len(r.text)})
    wrap_async(Toolbox,'__aenter__','tool_init')
    wrap_async(Toolbox,'close','tool_close')
    for method in ['build_initial_tokens','merge_context_tokens','merge_assistant_tokens']:
        wrap_sync(MessageCodec,method,'codec')
    wrap_async(MessageCodec,'decode_response','codec',describe=lambda s,a,k:{'operation':'decode_response'})
    original=_GatewayActor._handle_openai_chat_completions
    @functools.wraps(original)
    async def handle(self,*args,**kwargs):
        sid=kwargs.get('session_id') or args[0];profile=SESSIONS.get(sid)
        if profile is None:return await original(self,*args,**kwargs)
        with profile.active(),profile.span('gateway_http'):
            return await original(self,*args,**kwargs)
    _GatewayActor._handle_openai_chat_completions=handle
    def describe_http(client,args,kwargs):
        request=args[0] if args else kwargs['request'];url=request.url
        path=url.path
        if '/sessions/' in path:target='gateway'
        elif path.endswith('/completions'):target='vllm'
        elif '/process.Process/' in path or '/filesystem.Filesystem/' in path or path.endswith(('/files','/health')):target='e2b_execution'
        else:target='e2b_management'
        return {'method':request.method,'path':path,'target':target,'stream':kwargs.get('stream',False)}
    wrap_async(httpx.AsyncClient,'send','http_send',describe=describe_http,returned=lambda r:{'http_status':r.status_code})
