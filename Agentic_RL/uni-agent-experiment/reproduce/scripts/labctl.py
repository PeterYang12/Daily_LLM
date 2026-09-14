#!/usr/bin/env python3
"""Host-side lifecycle for this experiment; touches only explicitly named ua-lab resources."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
IMAGE='vllm/vllm-openai-rocm@sha256:91e381f072d6a44e1e4c97c82dce06e50e5189905cb3999a11471c5a8fc6a563'
DIND='docker@sha256:5efed980cba3fc126cf54e21a5a6ff8849d05b6e0623d6e7612f48e9cd6cd17e'
NAMES=['ua-lab-cpu','ua-lab-sandbox-daemon','ua-lab-model-tp1','ua-lab-model-128k','ua-lab-model-tp4','ua-lab-model-aiter','ua-lab-model-aiter-replica','ua-lab-janitor']
def call(args,check=True,capture=False):
 return subprocess.run(args,check=check,text=True,stdout=subprocess.PIPE if capture else None)
def docker(args,**kw):return call(['docker',*args],**kw)
def exists(kind,name):return docker([kind,'inspect',name],check=False,capture=True).returncode==0
def ensure_image(image):
 if not exists('image',image):docker(['pull',image])
def launch(name,args,image,command):
 if exists('container',name):
  state=json.loads(docker(['inspect',name],capture=True).stdout)[0]
  sources={str(Path(m['Source']).resolve()) for m in state.get('Mounts',[]) if m['Type']=='bind'}
  expected={str(ROOT.resolve()),str((ROOT/'models').resolve()),str((ROOT/'run').resolve())}
  if not sources & expected: raise RuntimeError(f'{name} belongs to a different lab root; refusing to reuse it for {ROOT}')
  if not state['State']['Running']:docker(['start',name])
  else:print(name,'already running')
  return
 docker(['run','-d','--name',name,*args,image,*command])
def up_controller():
 ensure_image(IMAGE);ensure_image(DIND)
 if not exists('network','ua-lab-net'):docker(['network','create','--subnet','172.30.90.0/24','ua-lab-net'])
 for n in ['run','models','cache','logs','results','secrets']: (ROOT/n).mkdir(exist_ok=True)
 launch('ua-lab-sandbox-daemon',['--privileged','--network','ua-lab-net','--ip','172.30.90.4','--cpus','24','--memory','128g','-e','DOCKER_TLS_CERTDIR=','-v','ua-lab-sandbox-data:/var/lib/docker','-v',f'{ROOT}/run:/run/ua','-v',f'{ROOT}/cache:{ROOT}/cache:ro','--entrypoint','/usr/local/bin/dind'],DIND,['dockerd','--host','unix:///run/ua/docker.sock','--bip','172.31.90.1/24','--default-address-pool','base=172.31.92.0/22,size=24'])
 launch('ua-lab-cpu',['--network','ua-lab-net','--ip','172.30.90.2','--cpus','12','--memory','48g','--shm-size','4g','-e','DOCKER_HOST=unix:///lab/run/docker.sock','-e','HF_HOME=/lab/cache/hf','-e','PYTHONPATH=/lab/src/uni-agent:/lab/src/uni-agent/verl','-e',f'UNI_AGENT_LAB_HOST_ROOT={ROOT}','-v',f'{ROOT}:/lab','-v','/usr/bin/docker:/usr/local/bin/docker:ro','--entrypoint','sleep'],IMAGE,['infinity'])
 ensure_image('python@sha256:97490e383c4cffb12825431fa24e3d2b70e39fd691a8e33c46bf4c18edca3998')
 launch('ua-lab-janitor',['--init','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','0.25','--memory','64m','-v',f'{ROOT}/run:/run/ua','-v',f'{ROOT}/scripts/sandbox_janitor.py:/janitor.py:ro','--entrypoint','python'],'python@sha256:97490e383c4cffb12825431fa24e3d2b70e39fd691a8e33c46bf4c18edca3998',['/janitor.py','--delete','--interval','30'])
def model(which):
 options={'tp1':('0','172.30.90.3',18082,'serve_model.sh','24','192g'),'128k':('1','172.30.90.5',18083,'serve_model_128k.sh','24','192g'),'tp4':('4,5,6,7','172.30.90.6',18084,'serve_model_tp4.sh','48','256g'),'aiter':('2','172.30.90.7',18085,'serve_model_aiter.sh','24','192g'),'aiter-replica':('3','172.30.90.8',18086,'serve_model_aiter.sh','24','192g')}
 gpu,ip,port,script,cpu,mem=options[which]
 ensure_image(IMAGE)
 launch('ua-lab-model-'+which,['--network','ua-lab-net','--ip',ip,'-p',f'127.0.0.1:{port}:8000','--device','/dev/kfd','--device','/dev/dri','--group-add','video','--group-add','render','--ipc','private','--shm-size','32g','--cpus',cpu,'--memory',mem,'--ulimit','memlock=-1','--ulimit','stack=67108864','-e',f'HIP_VISIBLE_DEVICES={gpu}','-e','OMP_NUM_THREADS=8','-v',f'{ROOT}/models:/models:ro','-v',f'{ROOT}/scripts/{script}:/serve_model.sh:ro',*(['-e','VLLM_ROCM_USE_AITER=1'] if which in ('aiter','aiter-replica') else []),'--entrypoint','bash'],IMAGE,['/serve_model.sh'])
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['up','model','status','stop-models','stop']);p.add_argument('--which',choices=['tp1','128k','tp4','aiter','aiter-replica'],default='128k');a=p.parse_args()
 if a.action=='up':up_controller()
 elif a.action=='model':model(a.which)
 elif a.action=='status':
  docker(['ps','-a','--filter','name=ua-lab-','--format','{{.Names}}\t{{.Status}}\t{{.Image}}'])
  call([sys.executable,str(ROOT/'scripts/status.py')])
 else:
  names=[n for n in NAMES if n.startswith('ua-lab-model-')] if a.action=='stop-models' else list(reversed(NAMES))
  for n in names:
   if exists('container',n):docker(['stop','--time','30',n])
if __name__=='__main__': main()
