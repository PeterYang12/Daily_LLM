"""Freeze 64/16 validated tasks and launch the authorized 300-step job once."""
import argparse,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--train-tasks',type=int,default=64);args=parser.parse_args()
assert json.loads((ROOT/'results/pilot-proof.json').read_text())['success']
assert json.loads((ROOT/'results/native-exec-acceptance/summary.json').read_text())['success']
assert not (ROOT/'results/main-launch.json').exists(),'Main launch already recorded'
while True:
    progress=json.loads((ROOT/'results/qualification-progress.json').read_text())
    if progress['counts']['training']>=args.train_tasks and progress['counts']['validation']>=16:break
    time.sleep(10)
# Interrupt only the qualification process. Its finally blocks clean active sandboxes;
# any server-side template builds already submitted can finish independently.
stop_code="""import os,signal
from pathlib import Path
for p in Path('/proc').glob('[0-9]*/cmdline'):
 try:a=p.read_bytes().split(b'\\0')
 except OSError:continue
 if len(a)>1 and a[1]==b'/lab/rl-swe-e2b-20260916/code/qualify_data.py':os.kill(int(p.parent.name),signal.SIGINT)
"""
subprocess.run(['docker','exec','ua-rl-train','python3','-c',stop_code],check=True)
time.sleep(5)
subprocess.run(['docker','exec','ua-rl-train','/lab/rl-swe-e2b-20260916/env/bin/python','/lab/rl-swe-e2b-20260916/code/finalize_data.py','--min-train',str(args.train_tasks)],check=True)
with (ROOT/'configs/final-resolved.yaml').open('w') as out,(ROOT/'logs/final-config-check.log').open('w') as err:
    subprocess.run(['docker','exec','ua-rl-gpu','bash','/lab/rl-swe-e2b-20260916/code/train.sh','--cfg','job','--resolve'],stdout=out,stderr=err,check=True)
monitor_log=(ROOT/'logs/main-monitor.log').open('w')
monitor=subprocess.Popen(['python3',str(ROOT/'code/monitor.py'),'--phase','main'],stdout=monitor_log,stderr=subprocess.STDOUT,start_new_session=True)
command=['docker','exec','-d','-e','UA_RL_PHASE=main','ua-rl-gpu','bash','/lab/rl-swe-e2b-20260916/code/main_job.sh']
subprocess.run(command,check=True)
record={'launched_unix':time.time(),'command':command,'monitor_pid':monitor.pid,'wandb_url':'https://wandb.ai/yuhanya-amd/uni-agent-swe-rl-rocm/runs/ua-qwen30b-e2b-20260916','target_steps':300,'train_prompts_per_step':4,'responses_per_prompt':4,'rollout_concurrency':16,'train_tasks':args.train_tasks,'validation_tasks':16,'save_freq':100,'checkpoints_to_keep':1}
(ROOT/'results/main-launch.json').write_text(json.dumps(record,indent=2)+'\n')
state=json.loads((ROOT/'state.json').read_text());state.update(status='main_initializing',main_training_steps_completed=0,main_launch=record)
(ROOT/'state.json').write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(record),flush=True)
