"""Install the official OpenSandbox Helm chart on the dedicated VM."""
import base64,json,subprocess,shlex
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-osb']
def guest(args,stdin=None):
    return subprocess.run(SSH+[shlex.join(args)],input=stdin,text=True,check=True)
guest(['sudo','k3s','kubectl','wait','--for=condition=Ready','node/ua-osb-node','--timeout=180s'])
for name in ['opensandbox-system','opensandbox']:
    guest(['sudo','k3s','kubectl','apply','-f','-'],json.dumps({'apiVersion':'v1','kind':'Namespace','metadata':{'name':name}}))
key=Path('/home/yuhanya/uni-agent-lab/secrets/local-opensandbox/api-key').read_text().strip()
secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'opensandbox-api-key','namespace':'opensandbox-system'},'type':'Opaque','data':{'api-key':base64.b64encode(key.encode()).decode()}}
guest(['sudo','k3s','kubectl','apply','-f','-'],json.dumps(secret))
config={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'ua-opensandbox-workload-template','namespace':'opensandbox-system'},'data':{'batchsandbox-template.yaml':(ROOT/'configs/batchsandbox-template.yaml').read_text()}}
guest(['sudo','k3s','kubectl','apply','-f','-'],json.dumps(config))
guest(['sudo','k3s','kubectl','apply','-f','-'],(ROOT/'configs/api-service.yaml').read_text())
guest(['sudo','env','KUBECONFIG=/etc/rancher/k3s/k3s.yaml','helm','upgrade','--install','opensandbox','/home/osb/opensandbox-0.2.2.tgz','--namespace','opensandbox-system','--values','/home/osb/values.yaml','--wait','--timeout','10m'])
guest(['sudo','k3s','kubectl','-n','opensandbox-system','get','pods','-o','wide'])
