"""Read-only optimizer evidence and host-clock trainer phase traces."""
import contextlib,hashlib,json,os,statistics,time
from pathlib import Path
from .common import ROOT

def append(name,row):
    phase=os.environ.get('UA_RL_PHASE','main');path=ROOT/'results'/phase/name
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:f.write(json.dumps(row,default=float)+'\n')

def snapshot(engine):
    import torch
    params=[(name,p) for chunk in engine.module for name,p in chunk.named_parameters() if p.requires_grad and p.is_contiguous() and p.numel()>1024]
    selected=params[::max(1,len(params)//16)][:16]
    return [(name,p.detach().view(-1)[::max(1,p.numel()//1024)][:1024].float().cpu().clone()) for name,p in selected]

def record_optimizer(engine,before,grad_norm,successful):
    import torch
    after=snapshot(engine);count=getattr(engine,'_ua_optimizer_count',0)+1;engine._ua_optimizer_count=count
    changed=0;total=0;max_delta=0.0
    h0=hashlib.sha256();h1=hashlib.sha256()
    for (name,a),(name2,b) in zip(before,after):
        assert name==name2
        changed+=int((a!=b).sum());total+=a.numel();max_delta=max(max_delta,float((a-b).abs().max()))
        h0.update(a.numpy().tobytes());h1.update(b.numpy().tobytes())
    attempt=int(os.environ.get('UA_RL_ATTEMPT','1'))
    if count==1 and attempt>1:
        phase=os.environ.get('UA_RL_PHASE','main');rank=torch.distributed.get_rank()
        previous=json.loads((ROOT/'results'/phase/'optimizer'/f'rank-{rank}.jsonl').read_text().splitlines()[-1])
        restored=previous['sample_hash_after']==h0.hexdigest()
        prior_step=previous.get('global_optimizer_step',previous['optimizer_step_index'])
        scheduler_restored=int(engine.lr_scheduler.num_steps)==prior_step
        append(f'restore-checks/attempt-{attempt}-rank-{rank}.jsonl',{'attempt':attempt,'rank':rank,'checkpoint_step':prior_step,'sampled_model_restored':restored,'scheduler_restored':scheduler_restored,'scheduler_num_steps':int(engine.lr_scheduler.num_steps),'sample_hash_before':h0.hexdigest(),'expected_hash':previous['sample_hash_after']})
        assert restored and scheduler_restored,'Checkpoint model/scheduler restore verification failed'
    append('optimizer/rank-'+str(torch.distributed.get_rank())+'.jsonl',{'time_unix':time.time(),'optimizer_step_index':count,'global_optimizer_step':int(engine.lr_scheduler.num_steps)+1,'attempt':int(os.environ.get('UA_RL_ATTEMPT','1')),'grad_norm':float(grad_norm),'update_successful':bool(successful),'sampled_parameters':total,'sampled_changed':changed,'sampled_max_abs_delta':max_delta,'sample_hash_before':h0.hexdigest(),'sample_hash_after':h1.hexdigest()})

@contextlib.contextmanager
def marked_timer(name,timing_raw,*args,**kwargs):
    from verl.utils.debug import marked_timer as original
    t=time.time()
    try:
        with original(name,timing_raw,*args,**kwargs):yield
    finally:
        if os.environ.get('UA_RL_OBSERVE')=='1':append('trainer-phases-'+str(os.getpid())+'.jsonl',{'name':name,'start_unix':t,'end_unix':time.time()})

_seen=None
def enrich_metrics(data,step):
    """Add observational metrics. Windowed session stats are explicitly labelled."""
    global _seen
    enriched=dict(data);enriched['system/logged_unix']=time.time()
    enriched['system/attempt']=int(os.environ.get('UA_RL_ATTEMPT','1'))
    paths=set((ROOT/'results/sessions').glob('*/result.json'))
    if _seen is None:_seen=paths
    new=paths-_seen;metrics=[]
    for p in new:
        try:
            result=json.loads(p.read_text());metrics.append(result.get('extra_info',{}).get('profile_metrics',{}));_seen.add(p)
        except (OSError,json.JSONDecodeError):continue
    enriched['sandbox/window_completed_sessions']=len(metrics)
    for key in sorted({k for m in metrics for k in m}):
        values=[float(m[key]) for m in metrics if key in m]
        enriched['sandbox/window_mean/'+key]=statistics.mean(values)
        enriched['sandbox/window_max/'+key]=max(values)
    phase=os.environ.get('UA_RL_PHASE','main');observed=[]
    for p in (ROOT/'results'/phase/'optimizer').glob('rank-*.jsonl'):
        try:observed.append(json.loads(p.read_text().splitlines()[-1]))
        except (OSError,IndexError,json.JSONDecodeError):continue
    if observed:
        enriched['weights/observed_ranks']=len(observed)
        enriched['weights/sample_changed_fraction']=sum(x['sampled_changed'] for x in observed)/sum(x['sampled_parameters'] for x in observed)
        enriched['weights/sample_max_abs_delta']=max(x['sampled_max_abs_delta'] for x in observed)
        enriched['weights/optimizer_successful_ranks']=sum(x['update_successful'] for x in observed)
    telemetry=ROOT/'results'/phase/'telemetry.jsonl'
    if telemetry.exists():
        try:
            with telemetry.open('rb') as f:
                f.seek(max(0,telemetry.stat().st_size-65536));lines=f.read().splitlines()
            sample=json.loads(lines[-1])
            enriched['system/telemetry_age_s']=time.time()-sample['time_unix']
            if 'owned_sandbox_count' in sample:enriched['sandbox/active_at_log_time']=sample.get('rollout_sandbox_count',sample['owned_sandbox_count'])
            gpu=[v for k,v in sample.get('gpu',{}).items() if k.startswith('card')]
            if gpu:
                enriched['system/gpu_mean_utilization_pct']=statistics.mean(float(g['GPU use (%)']) for g in gpu)
                enriched['system/gpu_max_memory_gib']=max(int(g['VRAM Total Used Memory (B)'])/2**30 for g in gpu)
        except (OSError,ValueError,KeyError,IndexError):pass
    append('metrics-timestamped.jsonl',{'step':step,'data':enriched})
    return enriched
