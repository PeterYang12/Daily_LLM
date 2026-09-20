"""Coordinate guest resource sampling and a bounded real-template capacity test."""
import argparse,json,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-e2b-large']
parser=argparse.ArgumentParser();parser.add_argument('--name',default='concurrency-24');parser.add_argument('--n',type=int,default=24)
args=parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+',args.name),'Use a simple result-directory name'
target=ROOT/'results'/args.name
assert not target.exists(),'Use a new result directory for another measurement'
subprocess.run(['scp','-F',str(ROOT/'configs/ssh_config'),str(ROOT/'code/guest_metrics.py'),'ua-e2b-large:/home/e2b/guest_metrics.py'],check=True)
subprocess.run(SSH+['rm -f /tmp/ua-capacity-monitor-stop'],check=True)
metrics_path=ROOT/'results'/('guest-metrics.jsonl' if args.name=='concurrency-24' else args.name+'-guest-metrics.jsonl')
with metrics_path.open('w') as metrics,(ROOT/'logs'/(args.name+'-worker.log')).open('w') as log:
    monitor=subprocess.Popen(SSH+['python3 /home/e2b/guest_metrics.py --watch'],stdout=metrics)
    command=['docker','exec','ua-lab-cpu','/lab/envs/cpu/bin/python',f'/lab/{ROOT.name}/code/concurrency.py','--n',str(args.n),'--output',f'/lab/{ROOT.name}/results/{args.name}']
    run=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
    try:
        deadline=time.monotonic()+300
        while not (target/'barrier.json').exists():
            if run.poll() is not None:raise RuntimeError('Capacity run stopped before barrier; see logs/concurrency.log')
            if time.monotonic()>deadline:raise TimeoutError('Capacity creation timed out')
            time.sleep(1)
        snapshot=json.loads(subprocess.check_output(SSH+['python3 /home/e2b/guest_metrics.py'],text=True))
        barrier_path=ROOT/'results'/('concurrency-barrier-guest.json' if args.name=='concurrency-24' else args.name+'-barrier-guest.json')
        barrier_path.write_text(json.dumps(snapshot,indent=2)+'\n')
        subprocess.run(['docker','exec','ua-lab-cpu','touch',f'/lab/{ROOT.name}/results/{args.name}/release'],check=True)
        assert run.wait(timeout=420)==0,'See logs/concurrency.log and summary.json'
    finally:
        subprocess.run(SSH+['touch /tmp/ua-capacity-monitor-stop'],check=True)
        monitor.wait(timeout=15)
    summary=json.loads((target/'summary.json').read_text())
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'}))
