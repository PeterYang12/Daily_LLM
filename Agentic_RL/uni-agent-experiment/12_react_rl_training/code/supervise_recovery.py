"""Bounded host supervisor for this dedicated training container only.

Each retry preserves the attempt, checks GPU arithmetic, and resumes the last
completed checkpoint. Never resumes after a failed numerical preflight.
"""
import fcntl,json,os,shutil,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CROOT='/lab/rl-swe-e2b-20260916'
CONTAINER='ua-rl-gpu'

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)

def event(kind,**kwargs):
    row={'time_unix':time.time(),'event':kind,**kwargs}
    print(json.dumps(row),flush=True)
    with (ROOT/'results/recovery-events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')

def read_rows(path):
    rows=[]
    if path.exists():
        for line in path.read_text().splitlines():
            try:rows.append(json.loads(line))
            except json.JSONDecodeError:pass
    return rows

def execute(args,log,timeout=600):
    with log.open('a') as f:
        return subprocess.run(args,stdout=f,stderr=subprocess.STDOUT,timeout=timeout).returncode

def docker_python(script,*args):
    return ['docker','exec','-e',f'PYTHONPATH={CROOT}/code',CONTAINER,
            '/opt/venv/bin/python',f'{CROOT}/code/{script}',*args]

def prepare(attempt):
    marker=ROOT/'checkpoints/latest_checkpointed_iteration.txt'
    step=int(marker.read_text())
    assert step in (100,200),step
    folder=ROOT/f'checkpoints/global_step_{step}'
    assert (folder/'actor/ckpt_contents.json').exists()
    assert (folder/'data.pt').exists()
    previous=ROOT/f'results/attempts/attempt-{attempt-1}'
    previous.mkdir(parents=True,exist_ok=True)
    for name in ('main-metrics.jsonl','main-exit.json','main-launch.json'):
        p=ROOT/'results'/name
        if p.exists() and not (previous/name).exists():shutil.copy2(p,previous/name)
    metrics=ROOT/'results/main-metrics.jsonl'
    kept=[r for r in read_rows(metrics) if r['step']<=step]
    metrics.write_text(''.join(json.dumps(r)+'\n' for r in kept))
    for p in (ROOT/'results/main/optimizer').glob('rank-*.jsonl'):
        archive=previous/'optimizer'/p.name;archive.parent.mkdir(exist_ok=True)
        if not archive.exists():shutil.copy2(p,archive)
        retained=[r for r in read_rows(p) if r.get('global_optimizer_step',r['optimizer_step_index'])<=step]
        p.write_text(''.join(json.dumps(r)+'\n' for r in retained))
    for p in (ROOT/'results/rollouts').glob('*.jsonl'):
        if int(p.stem)>step:
            target=previous/f'rolled-back-step-{p.name}'
            if not target.exists():shutil.copy2(p,target)
            p.unlink()
    (ROOT/'results/main-exit.json').unlink(missing_ok=True)
    return step

def main():
    if (ROOT/'state/paused-by-user').exists():
        raise RuntimeError('Paused by user; explicit resume instruction is required')
    lock=(ROOT/'state/recovery-supervisor.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state=json.loads((ROOT/'state.json').read_text())
    monitor=None
    try:
        recorded=[int(p.parent.name.split('-')[-1]) for p in (ROOT/'results/attempts').glob('attempt-*/main-launch.json')]
        first_attempt=max(recorded,default=1)+1
        for attempt in range(first_attempt,first_attempt+3):
            attempt_dir=ROOT/f'results/attempts/attempt-{attempt}';attempt_dir.mkdir(parents=True,exist_ok=True)
            log=ROOT/f'logs/attempts/attempt-{attempt}-training.log'
            if attempt>2:
                execute(['docker','restart',CONTAINER],ROOT/'logs/recovery-supervisor.log',120)
            # All diagnostics finish before the training process starts.
            health_path=attempt_dir/'supervisor-health'
            rc=execute(['docker','exec','-e','PYTORCH_ALLOC_CONF=expandable_segments:False',CONTAINER,
                        '/opt/venv/bin/python','-m','torch.distributed.run','--standalone','--nproc-per-node=8',
                        f'{CROOT}/code/gpu_health.py','--output',str(health_path).replace(str(ROOT),CROOT)],
                       ROOT/f'logs/attempts/attempt-{attempt}-preflight.log',180)
            assert rc==0 and len(list(health_path.glob('gpu-health-rank-*.json')))==8,'GPU numerical preflight failed'
            assert shutil.disk_usage(ROOT).free>430*2**30,'Insufficient room for next checkpoint before pruning'
            execute(docker_python('cleanup_sessions.py','--phase','main'),ROOT/'logs/recovery-cleanup.log',120)
            step=prepare(attempt)
            (ROOT/'state/stop-monitor-main').unlink(missing_ok=True)
            if monitor is None or monitor.poll() is not None:
                monitor_log=(ROOT/'logs/recovery-monitor.log').open('a')
                monitor=subprocess.Popen([sys.executable,str(ROOT/'code/monitor.py'),'--phase','main'],stdout=monitor_log,stderr=subprocess.STDOUT)
            canonical=ROOT/'logs/main-training.log'
            if canonical.is_symlink():canonical.unlink()
            elif canonical.exists():
                archive=ROOT/'logs/attempts/attempt-1-training.log'
                if not archive.exists():shutil.copy2(canonical,archive)
                canonical.unlink()
            canonical.symlink_to(log.relative_to(canonical.parent))
            run_id=f'ua-qwen30b-e2b-20260916-r{attempt-1}'
            launch={'launched_unix':time.time(),'attempt':attempt,'resume_step':step,'target_steps':300,
                    'train_prompts_per_step':4,'responses_per_prompt':4,'rollout_concurrency':16,
                    'allocator_config':'expandable_segments:False',
                    'wandb_url':f'https://wandb.ai/yuhanya-amd/uni-agent-swe-rl-rocm/runs/{run_id}'}
            save(ROOT/'results/main-launch.json',launch);save(attempt_dir/'main-launch.json',launch)
            state.update(status='resuming',attempt=attempt,main_training_steps_completed=step,main_launch=launch)
            save(ROOT/'state.json',state);event('launch',**launch)
            with log.open('w') as out:
                process=subprocess.Popen(['docker','exec','-e',f'UA_RL_ATTEMPT={attempt}',
                    '-e',f'UA_RL_RESUME_STEP={step}',CONTAINER,'bash',f'{CROOT}/code/recovery_job.sh'],stdout=out,stderr=subprocess.STDOUT)
                started=time.time();last_step=step;last_progress=started
                while process.poll() is None:
                    time.sleep(15)
                    rows=read_rows(ROOT/'results/main-metrics.jsonl')
                    observed=max((r['step'] for r in rows),default=step)
                    if observed>last_step:
                        last_step=observed;last_progress=time.time()
                        state.update(status='training',main_training_steps_completed=observed)
                        save(ROOT/'state.json',state);event('progress',attempt=attempt,step=observed)
                    # Allow initialization/compilation, long SWE episodes and sync saves.
                    # A stalled attempt is restarted only after 90 min without an update.
                    if time.time()-last_progress>5400:
                        event('stalled',attempt=attempt,step=last_step)
                        execute(['docker','restart',CONTAINER],ROOT/'logs/recovery-actions.log',120)
                        process.wait(timeout=120)
                        break
                rc=process.wait()
            if not (ROOT/'results/main-exit.json').exists():
                save(ROOT/'results/main-exit.json',{'exit_code':rc,'time_unix':time.time(),'attempt':attempt})
            event('attempt_exit',attempt=attempt,exit_code=rc,step=last_step)
            if rc==0 and last_step==300:
                state.update(status='training_complete_validation_pending',main_training_steps_completed=300)
                save(ROOT/'state.json',state)
                for script,args in [('cleanup_sessions.py',['--phase','main']),('summarize_run.py',['--final']),
                                    ('audit_trajectories.py',['--final']),('analyze_sandbox.py',[]),('render_figures.py',[])]:
                    rc=execute(docker_python(script,*args),ROOT/'logs/final-analysis.log',1800)
                    assert rc==0,f'Final analysis failed: {script}'
                event('training_and_analysis_complete',step=300)
                state['status']='training_and_analysis_complete';save(ROOT/'state.json',state)
                return
        raise RuntimeError('Three recovery attempts exhausted; manual investigation required')
    except BaseException as exc:
        event('supervisor_stopped',error_type=type(exc).__name__,message=str(exc))
        state['status']='recovery_needs_investigation';save(ROOT/'state.json',state)
        raise
    finally:
        (ROOT/'state/stop-monitor-main').touch()
        if monitor:
            try:monitor.wait(timeout=45)
            except subprocess.TimeoutExpired:monitor.terminate()

if __name__=='__main__':main()
