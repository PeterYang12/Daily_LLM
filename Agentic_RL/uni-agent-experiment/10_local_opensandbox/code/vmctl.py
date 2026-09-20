"""Run guest commands or inspect OpenSandbox without exposing generated API credentials."""
import argparse,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-osb']
def redact(text):return re.sub(r'(?:e2b|osb)_[A-Za-z0-9_-]{20,}','[API_KEY_REDACTED]',text)
def command(argv):
    p=subprocess.run(SSH+[shlex.join(argv)],text=True,capture_output=True)
    return p
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
    if a.args==['status']:
        r=command(['sudo','k3s','kubectl','-n','opensandbox-system','get','pods','-o','wide'])
    else:r=command(a.args)
    print(redact(r.stdout),end='');print(redact(r.stderr),end='');raise SystemExit(r.returncode)
