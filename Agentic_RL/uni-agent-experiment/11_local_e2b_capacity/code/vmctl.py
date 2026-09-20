"""Run guest commands or inspect E2B without exposing generated API credentials."""
import argparse,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-e2b-large']
def redact(text):return re.sub(r'e2b_[A-Za-z0-9_-]{20,}','[E2B_KEY_REDACTED]',text)
def command(argv):
    p=subprocess.run(SSH+[shlex.join(argv)],text=True,capture_output=True)
    return p
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
    if a.args==['status']:
        r=command(['sudo','k3s','kubectl','-n','e2b','get','pod','e2b-0','-o','json'])
        if r.returncode:print(redact(r.stderr));raise SystemExit(r.returncode)
        d=json.loads(r.stdout)
        print(json.dumps({'phase':d['status']['phase'],'conditions':d['status'].get('conditions'),
              'containers':[{'name':x['name'],'ready':x.get('ready'),'restarts':x['restartCount'],'state':x['state']} for x in d['status'].get('initContainerStatuses',[])+d['status'].get('containerStatuses',[])]},indent=2))
    else:
        r=command(a.args);print(redact(r.stdout),end='');print(redact(r.stderr),end='');raise SystemExit(r.returncode)
