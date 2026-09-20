"""Durably finalize this run only after the trainer and analyses have succeeded."""
import fcntl,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DEST=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment')

def run(command,timeout=1800):
    with (ROOT/'logs/publication.log').open('a') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)

def main():
    if (ROOT/'state/paused-by-user').exists():
        raise RuntimeError('Paused by user; automatic finalization is disabled')
    lock=(ROOT/'state/finalizer.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    while True:
        status=json.loads((ROOT/'state.json').read_text())['status']
        if status=='training_and_analysis_complete':break
        if status=='recovery_needs_investigation':
            print('Training recovery needs investigation; finalization stays pending.',flush=True)
        time.sleep(30)
    assert json.loads((ROOT/'results/training-summary.json').read_text())['last_step']==300
    # The dedicated trainer has exited. Clear its Ray actors before fresh load.
    run(['docker','exec','ua-rl-gpu','/opt/venv/bin/ray','stop','--force'],120)
    run(['docker','exec','-e','HIP_VISIBLE_DEVICES=0,1','-e','PYTORCH_ALLOC_CONF=expandable_segments:False',
         '-e','VLLM_ROCM_USE_AITER=1','-e','VLLM_ROCM_USE_AITER_MOE=0','ua-rl-gpu','/opt/venv/bin/python',
         '/lab/rl-swe-e2b-20260916/code/verify_saved_model.py'])
    run(['/usr/bin/python3',str(ROOT/'code/write_report.py')])
    # Root overview has a single live status entry; preserve other experiments.
    p=DEST/'README.md';text=p.read_text().replace('Experiment 12 is now running full RL training','Experiment 12 completed 300 steps of full RL training')
    text=text.replace('In progress: 300-step full parameter RL; 62 training tasks, 16 evaluation tasks, 16 concurrent rollouts, W&B metrics and sandbox profiling.',
                      'Completed 300 retained RL steps and 4,800 training trajectories; fixed evaluation, W&B, checkpoint recovery and sandbox bottleneck analysis.')
    p.write_text(text)
    run(['/usr/bin/python3',str(DEST/'06_trajectories_and_reproduction/code/reporting/validate_bundle.py')])
    (ROOT/'results/publication-complete.json').write_text(json.dumps({'complete':True,'published_unix':time.time(),'directory':str(DEST/'12_react_rl_training')},indent=2)+'\n')
    state=json.loads((ROOT/'state.json').read_text());state['status']='complete';(ROOT/'state.json').write_text(json.dumps(state,indent=2)+'\n')

if __name__=='__main__':main()
