import contextlib,contextvars,json,os,time
from pathlib import Path

ROOT=Path('/lab/rl-swe-e2b-20260916')
CURRENT=contextvars.ContextVar('ua_rl_profile',default=None)

def load_e2b():
    for line in Path('/lab/secrets/user-e2b-10-145-64-67.env').read_text().splitlines():
        k,v=line.split('=',1);os.environ[k]=v
    assert os.environ['E2B_API_URL']=='http://10.145.64.67:8088'

def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(data,indent=2,default=str,ensure_ascii=False)
    for name in ['E2B_API_KEY','WANDB_API_KEY']:
        if os.environ.get(name):text=text.replace(os.environ[name],'[REDACTED]')
    path.write_text(text+'\n')

class Profile:
    def __init__(self,key,folder):
        self.key=key;self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.events=[];self.phase='setup';self.started_unix=time.time()
    @contextlib.contextmanager
    def span(self,category,**fields):
        e={'category':category,'phase':self.phase,'start_unix':time.time(),**fields};self.events.append(e)
        t=time.monotonic()
        try:yield e
        except BaseException as exc:e['error_type']=type(exc).__name__;raise
        finally:e['duration_s']=time.monotonic()-t
    @contextlib.contextmanager
    def in_phase(self,name):
        before=self.phase;self.phase=name
        try:
            with self.span('phase',name=name):yield
        finally:self.phase=before
    @contextlib.contextmanager
    def active(self):
        token=CURRENT.set(self)
        try:yield self
        finally:CURRENT.reset(token)
    def save(self):save(self.folder/'spans.json',{'key':self.key,'started_unix':self.started_unix,'events':self.events})
    def metrics(self):
        out={}
        for name in ['sandbox_create_queue','sandbox_create','sandbox_destroy','sandbox_exec','file_read','file_write','model_roundtrip','tool_call','verifier_exec']:
            events=[e for e in self.events if e['category']==name]
            out[name+'_s']=sum(e.get('duration_s',0) for e in events);out[name+'_count']=len(events)
        for e in self.events:
            if e['category']=='phase':out[e['name']+'_s']=out.get(e['name']+'_s',0)+e.get('duration_s',0)
        return out
