"""Host launcher for the two pinned model configurations used in this study."""
import argparse,json,subprocess,sys
from pathlib import Path

IMAGE='vllm/vllm-openai-rocm@sha256:91e381f072d6a44e1e4c97c82dce06e50e5189905cb3999a11471c5a8fc6a563'
NAME='ua-profile-model-aiter-128k'

def main(root):
    subprocess.run([sys.executable,str(root/'scripts/labctl.py'),'up'],check=True)
    subprocess.run([sys.executable,str(root/'scripts/labctl.py'),'model','--which','128k'],check=True)
    inspection=subprocess.run(['docker','inspect',NAME],capture_output=True,text=True)
    if inspection.returncode==0:
        state=json.loads(inspection.stdout)[0]
        sources={m['Source'] for m in state.get('Mounts',[]) if m['Type']=='bind'}
        if str(root/'models') not in sources:raise RuntimeError('Existing optimized container belongs to a different lab root')
        if not state['State']['Running']:subprocess.run(['docker','start',NAME],check=True)
        print(NAME,'ready or starting');return
    command=['docker','run','-d','--name',NAME,'--label','ua-profile.owner=swe-sandbox-profile',
        '--network','ua-lab-net','--ip','172.30.90.9','-p','127.0.0.1:18087:8000',
        '--device','/dev/kfd','--device','/dev/dri','--group-add','video','--group-add','render',
        '--ipc','private','--shm-size','32g','--cpus','24','--memory','192g',
        '--ulimit','memlock=-1','--ulimit','stack=67108864',
        '-e','HIP_VISIBLE_DEVICES=2','-e','OMP_NUM_THREADS=8','-e','VLLM_ROCM_USE_AITER=1',
        '-v',str(root/'models')+':/models:ro',
        '-v',str(root/'scripts/swe_sandbox_profile/serve_optimized_128k.sh')+':/serve_model.sh:ro',
        '--entrypoint','bash',IMAGE,'/serve_model.sh']
    subprocess.run(command,check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--lab-root',type=Path,default=Path('/home/yuhanya/uni-agent-lab'));main(p.parse_args().lab_root.resolve())
