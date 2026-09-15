"""Run follow-up scheduling and serving studies only after isolated task pairs end."""
import argparse,json,subprocess,time
from pathlib import Path

def main(root,controls_tag):
    while not (root/'main-optimized-summary.json').exists():time.sleep(5)
    result=json.loads((root/'main-optimized-summary.json').read_text())
    if len(result['results'])!=result['jobs']:raise RuntimeError('Primary batch is incomplete')
    stages=[]
    commands=[]
    for provider,concurrency in [('docker',1),('e2b',4),('e2b',1),('docker',4)]:
        tag=f'scheduling-{provider}-c{concurrency}'
        command=['/lab/envs/cpu/bin/python','/lab/scripts/swe_sandbox_profile/run.py','--root',str(root),
                 '--mode','react','--tag',tag,'--controls-tag',controls_tag,'--providers',provider,
                 '--instances','pallets__flask-5014','--repeats','4','--concurrency',str(concurrency),
                 '--base-url','http://172.30.90.9:8000/v1']
        commands.append((tag,command))
    commands.append(('inference-benchmark',['/lab/envs/cpu/bin/python','/lab/scripts/swe_sandbox_profile/inference_bench.py','--root',str(root)]))
    for name,command in commands:
        if name=='inference-benchmark' and (root/name/'COMPLETE').exists():continue
        if (root/(name+'-summary.json')).exists():continue
        record={'stage':name,'start_unix':time.time(),'command':command}
        print('Starting',name,flush=True)
        with (root/(name+'.log')).open('w') as output:
            proc=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT)
        record.update(exit_code=proc.returncode,end_unix=time.time());stages.append(record)
        (root/'followup-stages.json').write_text(json.dumps(stages,indent=2)+'\n')
        print('Finished',name,proc.returncode,flush=True)
        if proc.returncode:raise RuntimeError('Stage failed: '+name)
    (root/'FOLLOWUP_COMPLETE').write_text(str(time.time())+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--controls-tag',default='controls-final')
    args=p.parse_args();main(args.root,args.controls_tag)
