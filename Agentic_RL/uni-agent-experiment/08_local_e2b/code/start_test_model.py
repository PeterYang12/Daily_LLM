"""One temporary, memory-limited serving replica for local Uni-Agent acceptance."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
NAME='ua-e2b-test-model'
IMAGE='vllm/vllm-openai-rocm@sha256:91e381f072d6a44e1e4c97c82dce06e50e5189905cb3999a11471c5a8fc6a563'
inspection=subprocess.run(['docker','inspect',NAME],capture_output=True,text=True)
if inspection.returncode==0:
    state=json.loads(inspection.stdout)[0]
    assert state['Config']['Labels'].get('ua-local-e2b.owner')=='20260916','Name belongs to another container'
    if not state['State']['Running']:subprocess.run(['docker','start',NAME],check=True)
    print('Existing acceptance model ready or starting');sys.exit(0)
cmd=['docker','run','-d','--name',NAME,'--label','ua-local-e2b.owner=20260916',
 '--network','ua-lab-net','--ip','172.30.90.10','-p','127.0.0.1:18088:8000',
 '--device','/dev/kfd','--device','/dev/dri','--group-add','video','--group-add','render',
 '--ipc','private','--shm-size','16g','--cpus','16','--memory','128g',
 '-e','HIP_VISIBLE_DEVICES=1','-e','OMP_NUM_THREADS=8','-e','VLLM_ROCM_USE_AITER=1',
 '-v','/home/yuhanya/uni-agent-lab/models:/models:ro',
 '--entrypoint','python',IMAGE,'-m','vllm.entrypoints.openai.api_server',
 '--host','0.0.0.0','--port','8000','--model','/models/Qwen3-Coder-30B-A3B-Instruct',
 '--served-model-name','Qwen3-Coder-30B-A3B-Instruct','--dtype','bfloat16',
 '--max-model-len','131072','--gpu-memory-utilization','0.40','--max-num-seqs','4',
 '--max-num-batched-tokens','8192','--enable-prefix-caching','--trust-remote-code']
identifier=subprocess.check_output(cmd,text=True).strip()
(ROOT/'results/test-model.json').write_text(json.dumps({'container':NAME,'id':identifier,'command':cmd},indent=2)+'\n')
print('Started temporary model',identifier[:12])
