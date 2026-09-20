"""Capture final deployment state and validate the complete Uni-Agent acceptance."""
import json,math,subprocess,time,urllib.error,urllib.request
from pathlib import Path
from vmctl import command
ROOT=Path(__file__).resolve().parents[1]
def get(args):
    p=command(args)
    if p.returncode:raise RuntimeError(p.stderr)
    return json.loads(p.stdout)
def main():
    smoke=json.loads((ROOT/'results/provider-smoke-final.json').read_text());assert smoke['success'] and all(smoke['checks'].values())
    restart=json.loads((ROOT/'results/restart-probe.json').read_text());assert restart['success'] and restart['cleanup_completed']
    case=ROOT/'results/uni-agent-flask/react/opensandbox/pallets__flask-5014'
    result=json.loads((case/'result.json').read_text());assert all(result[k] for k in ['resolved','finished','eval_completed','trajectory_valid','cleanup_completed'])
    trajectories=[]
    for p in case.glob('sessions/*/trajectories.jsonl'):
        for line in p.read_text().splitlines():
            t=json.loads(line)['trajectory'];assert len(t['response_ids'])==len(t['response_mask'])==len(t['response_logprobs'])
            assert all(m in [0,1] for m in t['response_mask']) and all(math.isfinite(x) for x in t['response_logprobs']) and t['reward_score']==1.0
            trajectories.append({'turns':t['num_turns'],'generated_tokens':sum(t['response_mask'])})
    assert len(trajectories)==1
    controls={}
    for mode in ['baseline','oracle']:
        p=ROOT/'results/uni-agent-flask/controls/opensandbox/pallets__flask-5014'/mode/'result.json'
        r=json.loads(p.read_text());assert r['eval_completed'] and r['resolved']==(mode=='oracle');controls[mode]=r['resolved']
    deadline=time.monotonic()+60
    while True:
        pods=get(['sudo','k3s','kubectl','-n','opensandbox','get','pods','-o','json'])['items']
        sandboxes=get(['sudo','k3s','kubectl','-n','opensandbox','get','batchsandboxes','-o','json'])['items']
        if not pods and not sandboxes:break
        if time.monotonic()>deadline:raise RuntimeError('Sandbox resources still present')
        time.sleep(2)
    services=get(['sudo','k3s','kubectl','-n','opensandbox-system','get','deployments','-o','json'])['items']
    assert all(x['status'].get('availableReplicas',0)==x['spec']['replicas']==1 for x in services)
    node=get(['sudo','k3s','kubectl','get','node','ua-osb-node','-o','json'])
    images=[]
    for p in get(['sudo','k3s','kubectl','-n','opensandbox-system','get','pods','-o','json'])['items']:
        for c in p['status'].get('containerStatuses',[]):images.append({'pod':p['metadata']['name'],'image':c['image'],'image_id':c['imageID'],'ready':c['ready']})
    try:urllib.request.urlopen('http://127.0.0.1:18094/sandboxes',timeout=5);unauth=200
    except urllib.error.HTTPError as exc:unauth=exc.code
    assert unauth==401
    assert urllib.request.urlopen('http://127.0.0.1:18094/health',timeout=5).status==200
    assert urllib.request.urlopen('http://127.0.0.1:18090/health',timeout=5).status==200
    report={'time_unix':time.time(),'success':True,'outer_kernel':subprocess.check_output(['uname','-r'],text=True).strip(),
            'node_info':node['status']['nodeInfo'],'node_capacity':node['status']['capacity'],'images':images,
            'provider_smoke_checks':smoke['checks'],'restart_preserves_session':restart['session_survived'],
            'restart_preserves_file':restart['file_survived'],'active_sandbox_pods':0,'active_batchsandboxes':0,
            'unauthenticated_status':unauth,'controls':controls,'trajectories':trajectories,
            'e2e':{k:result[k] for k in ['reward','wall_seconds','finished','resolved','eval_completed','trajectory_valid','cleanup_completed']},
            'existing_e2b_health':True}
    (ROOT/'results/deployment-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
