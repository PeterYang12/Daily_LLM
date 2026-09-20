"""Prepare a new comparison directory and start/reuse its pinned serving replica."""
import argparse,json,shutil,subprocess
from pathlib import Path
LAB=Path('/home/yuhanya/uni-agent-lab')
def main(root):
    root=root.resolve();root.mkdir(parents=True,exist_ok=False)
    old=LAB/'results/swe-sandbox-profile-20260914T144308Z';local=LAB/'local-e2b-20260916/results/uni-agent-flask'
    manifest=json.loads((old/'manifest.json').read_text());manifest['tasks']=[t for t in manifest['tasks'] if t['instance_id']=='pallets__flask-5014'];variants={}
    for label in ['docker','local_e2b','remote_e2b']:
        p=root/(label+'-'+root.name);(p/'assets').mkdir(parents=True)
        (p/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        templates=json.loads(((local if label=='local_e2b' else old)/'templates.json').read_text())
        (p/'templates.json').write_text(json.dumps([x for x in templates if x['instance_id']=='pallets__flask-5014'],indent=2)+'\n')
        shutil.copy2(old/'assets/tmux.tar.gz',p/'assets/tmux.tar.gz');variants[label]='/lab/'+str(p.relative_to(LAB))
    p={'root':str(root),'container_root':'/lab/'+str(root.relative_to(LAB)),'variants':variants,'task':'pallets__flask-5014',
       'trials_per_variant':3,'order':[['docker','local_e2b','remote_e2b'],['local_e2b','remote_e2b','docker'],['remote_e2b','docker','local_e2b']],
       'model_base_url':'http://172.30.90.9:8000/v1','model':'Qwen3-Coder-30B-A3B-Instruct',
       'sandbox_configured_resources':'4 CPU / 8 GiB','sampling':{'temperature':0.2,'top_p':0.9,'max_tokens':4096,'explicit_seed':None},
       'max_agent_steps':100,'runtime_timeout_s':3600,'agent_deadline_s':1800,'evaluation_timeout_s':600}
    (root/'protocol.json').write_text(json.dumps(p,indent=2)+'\n')
    name='ua-threeway-model';check=subprocess.run(['docker','inspect',name],capture_output=True,text=True)
    if check.returncode==0:
        c=json.loads(check.stdout)[0];assert c['Config']['Labels'].get('ua-threeway.owner','').startswith('sandbox-three-way-')
        if not c['State']['Running']:subprocess.run(['docker','start',name],check=True)
        info={'name':name,'id':c['Id'],'reused':True}
    else:
        cmd=['docker','run','-d','--name',name,'--label','ua-threeway.owner='+root.name,'--network','ua-lab-net','--ip','172.30.90.9','-p','127.0.0.1:18087:8000','--device','/dev/kfd','--device','/dev/dri','--group-add','video','--group-add','render','--ipc','private','--shm-size','32g','--cpus','24','--memory','192g','--ulimit','memlock=-1','--ulimit','stack=67108864','-e','HIP_VISIBLE_DEVICES=2','-e','OMP_NUM_THREADS=8','-e','VLLM_ROCM_USE_AITER=1','-v',str(LAB/'models')+':/models:ro','-v',str(LAB/'scripts/swe_sandbox_profile/serve_optimized_128k.sh')+':/serve_model.sh:ro','--entrypoint','bash','vllm/vllm-openai-rocm@sha256:91e381f072d6a44e1e4c97c82dce06e50e5189905cb3999a11471c5a8fc6a563','/serve_model.sh']
        info={'name':name,'id':subprocess.check_output(cmd,text=True).strip(),'command':cmd}
    (root/'model-service.json').write_text(json.dumps(info,indent=2)+'\n');print(p['container_root'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);main(p.parse_args().root)
