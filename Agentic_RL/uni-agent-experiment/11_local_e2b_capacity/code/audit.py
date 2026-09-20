"""Record capacity, migration, recovery, and service state without credentials."""
import json,socket,subprocess,time,urllib.request,xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from vmctl import command
ROOT=Path(__file__).resolve().parents[1]

def main():
    s=json.loads((ROOT/'state.json').read_text())
    pod=json.loads(command(['sudo','k3s','kubectl','-n','e2b','get','pod','e2b-0','-o','json']).stdout)
    node=json.loads(command(['sudo','k3s','kubectl','get','node','ua-e2b-large','-o','json']).stdout)
    guest=json.loads(command(['python3','/home/e2b/guest_metrics.py']).stdout)
    xml=ET.fromstring(subprocess.check_output(['sudo','-n','virsh','dumpxml',s['vm_name']],text=True))
    assert int(xml.findtext('vcpu'))==96 and xml.find('memory').get('unit')=='KiB' and int(xml.findtext('memory'))==512*1024*1024
    original=json.loads((ROOT.parent/'secrets/local-e2b-large/migrated-api-secret.json').read_text())
    restored=json.loads(command(['sudo','k3s','kubectl','-n','e2b','get','secret','e2b-api','-o','json']).stdout)
    assert original['data']==restored['data'],'Migration Secret mismatch'
    with urllib.request.urlopen('http://127.0.0.1:18094/health',timeout=5) as response:osb_health=json.load(response)
    env=dict(x.split('=',1) for x in (ROOT.parent/'secrets/local-e2b/host.env').read_text().splitlines())
    def get(path):
        req=urllib.request.Request(env['E2B_API_URL']+path,headers={'X-API-Key':env['E2B_API_KEY']})
        with urllib.request.urlopen(req,timeout=10) as response:return response.read()
    templates=json.loads(get('/templates'));boxes=json.loads(get('/sandboxes'))
    def reachable(port):
        try:
            with socket.create_connection((s['vm_ip'],port),timeout=1):return {'port':port,'reachable':True}
        except OSError:return {'port':port,'reachable':False}
    with ThreadPoolExecutor(max_workers=9) as pool:
        ports=list(pool.map(reachable,[3003,5007,5008,5009,5010,5016,5017,5018,5109]))
    domain=lambda name:subprocess.check_output(['sudo','-n','virsh','domstate',name],text=True).strip()
    ready=any(x['type']=='Ready' and x['status']=='True' for x in pod['status']['conditions'])
    capacity=json.loads((ROOT/'results/concurrency-24/summary.json').read_text())
    samples=[json.loads(x) for x in (ROOT/'results/guest-metrics.jsonl').read_text().splitlines()]
    busy=[]
    for a,b in zip(samples,samples[1:]):
        dt=b['time_unix']-a['time_unix']
        if not 0<dt<2:continue
        total=sum(b['cpu_ticks'][:8])-sum(a['cpu_ticks'][:8])
        idle=sum(b['cpu_ticks'][3:5])-sum(a['cpu_ticks'][3:5])
        busy.append((total-idle)/b['clock_ticks']/dt)
    transitions=sorted([(r['verify_started_unix'],1) for r in capacity['rows']]+[(r['verify_finished_unix'],-1) for r in capacity['rows']])
    active=0;peak=0
    for _,delta in transitions:active+=delta;peak=max(peak,active)
    report={'time_unix':time.time(),'vm_name':s['vm_name'],'vm_ip':s['vm_ip'],'vm_vcpus':96,'vm_memory_gib':512,'vm_disk_gib':500,
            'node_info':node['status']['nodeInfo'],'node_capacity':node['status']['capacity'],'guest':guest,
            'old_vm_state':domain(s['old_vm_name']),'new_vm_state':domain(s['vm_name']),
            'opensandbox_vm_state':domain('ua-opensandbox-local-20260916'),
            'opensandbox_tunnel':subprocess.check_output(['systemctl','is-active','ua-opensandbox-local-tunnel.service'],text=True).strip(),
            'opensandbox_health':osb_health,'api_secret_preserved':True,'domain_xml_resources_verified':True,
            'pod_ready':ready,'pod_uid':pod['metadata']['uid'],'api_health':get('/health').decode(),
            'templates':[ {k:t.get(k) for k in ['templateID','buildStatus','aliases','cpuCount','memoryMB']} for t in templates],
            'active_sandboxes':len(boxes),'internal_ports':ports,'original_api_key_works':True,
            'sdk_smoke':json.loads((ROOT/'results/sdk-smoke.json').read_text())['success'],
            'sdk_smoke_after_reboot':json.loads((ROOT/'results/sdk-smoke-after-reboot.json').read_text())['success'],
            'reboot':json.loads((ROOT/'results/reboot-check.json').read_text()),
            'concurrency_success':capacity['success'],'simultaneously_live':capacity['simultaneously_live'],
            'peak_overlapping_verifier_requests':peak,'guest_cpu_busy_cores_peak_1s':max(busy),
            'metrics_note':'Guest-wide /proc/stat sampled at ~1s, includes services. Samples include a gap at the host barrier. This is not a saturation benchmark.'}
    report['success']=all([ready,not boxes,report['sdk_smoke'],report['sdk_smoke_after_reboot'],capacity['success'],report['reboot']['success'],report['old_vm_state']=='shut off',report['new_vm_state']=='running',report['opensandbox_vm_state']=='running',guest['cpu_count']==96,guest['meminfo']['HugePages_Total']==196608,guest['nbd_devices']==256,node['status']['capacity']['hugepages-2Mi']=='384Gi',not any(p['reachable'] for p in ports)])
    (ROOT/'results/deployment-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    assert report['success']
    s.update(status='ready',completed_unix=time.time(),api_url='http://127.0.0.1:18090',sandbox_url='http://127.0.0.1:18091',team_concurrency_limit=64,verified_concurrency=24)
    (ROOT/'state.json').write_text(json.dumps(s,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['success','vm_name','pod_ready','active_sandboxes','old_vm_state','simultaneously_live','peak_overlapping_verifier_requests','guest_cpu_busy_cores_peak_1s']}))
if __name__=='__main__':main()
