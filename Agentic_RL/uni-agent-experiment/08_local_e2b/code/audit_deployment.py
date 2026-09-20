"""Capture final, non-secret service state and acceptance evidence."""
import json,socket,subprocess,time,urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from vmctl import command
ROOT=Path(__file__).resolve().parents[1]

def main():
    state=json.loads((ROOT/'state.json').read_text())
    pod=json.loads(command(['sudo','k3s','kubectl','-n','e2b','get','pod','e2b-0','-o','json']).stdout)
    node=json.loads(command(['sudo','k3s','kubectl','get','node','ua-e2b-node','-o','json']).stdout)
    ready=next(x['status']=='True' for x in pod['status']['conditions'] if x['type']=='Ready')
    def reachable(port):
        try:
            with socket.create_connection((state['vm_ip'],port),timeout=1):return {'port':port,'reachable':True}
        except OSError:return {'port':port,'reachable':False}
    with ThreadPoolExecutor(max_workers=9) as pool:
        blocked=list(pool.map(reachable,[3003,5007,5008,5009,5010,5016,5017,5018,5109]))
    env={}
    for line in Path('/home/yuhanya/uni-agent-lab/secrets/local-e2b/host.env').read_text().splitlines():
        k,v=line.split('=',1);env[k]=v
    def get(path):
        req=urllib.request.Request(env['E2B_API_URL']+path,headers={'X-API-Key':env['E2B_API_KEY']})
        with urllib.request.urlopen(req,timeout=10) as response:return response.read()
    templates=json.loads(get('/templates'));sandboxes=json.loads(get('/sandboxes'))
    report={'time_unix':time.time(),'vm_name':state['vm_name'],'vm_ip':state['vm_ip'],
            'vm_vcpus':16,'vm_memory_gib':64,'vm_disk_gib':200,
            'outer_kernel':subprocess.check_output(['uname','-r'],text=True).strip(),
            'node_info':node['status']['nodeInfo'],'node_capacity':node['status']['capacity'],
            'pod_ready':ready,'pod_uid':pod['metadata']['uid'],
            'api_health':get('/health').decode(),'templates':[{
                k:t.get(k) for k in ['templateID','buildID','buildStatus','aliases','cpuCount','memoryMB']
                } for t in templates],
            'active_sandboxes':len(sandboxes),'internal_port_reachability':blocked,
            'tunnel_service':subprocess.check_output(['systemctl','is-active','ua-e2b-local-tunnel.service'],text=True).strip()}
    case=ROOT/'results/uni-agent-flask/react-local/e2b/pallets__flask-5014/result.json'
    data=json.loads(case.read_text());report['uni_agent']={k:data[k] for k in ['resolved','finished','eval_completed','trajectory_valid','wall_seconds','cleanup_completed']}
    for name in ['sdk-smoke-before-restart','sdk-smoke-after-restart']:
        report[name]=json.loads((ROOT/'results'/f'{name}.json').read_text())['success']
    report['success']=ready and report['active_sandboxes']==0 and not any(x['reachable'] for x in blocked) and all(report['uni_agent'][k] for k in ['resolved','finished','eval_completed','trajectory_valid','cleanup_completed']) and report['sdk-smoke-before-restart'] and report['sdk-smoke-after-restart']
    (ROOT/'results/deployment-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));assert report['success']

if __name__=='__main__':main()
