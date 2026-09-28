"""Read-only configuration evidence for the ordinary-page local deployment."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import httpx

ROOT = Path(__file__).resolve().parents[1]
VM_SCRIPT = r'''
import json, pathlib, subprocess
p = pathlib.Path
def cmd(argv):
    return subprocess.check_output(argv, text=True).strip()
mem = dict(line.split(':', 1) for line in p('/proc/meminfo').read_text().splitlines())
node = json.loads(cmd(['sudo','k3s','kubectl','get','node','e2b-vm','-o','json']))
pod = json.loads(cmd(['sudo','k3s','kubectl','-n','e2b','get','pod','e2b-0','-o','json']))
env = {}
for c in pod['spec']['initContainers']:
    if c['name'] in ('api','orchestrator','host-setup'):
        env[c['name']] = {x['name']:x.get('value') for x in c.get('env',[])
                         if x['name'] in ('E2B_SANDBOX_HUGE_PAGES','E2B_MAX_SANDBOXES_PER_NODE','HUGEPAGES','NBDS_MAX','NBD_POOL_SIZE')}
sql = "SELECT json_build_object('concurrent_sandboxes',concurrent_sandboxes,'max_ram_mb',max_ram_mb,'max_vcpu',max_vcpu) FROM public.team_limits WHERE id='0b8a3ded-4489-4722-afd1-1d82e64ec2d5';"
quota = json.loads(cmd(['sudo','k3s','kubectl','-n','e2b','exec','e2b-0','-c','postgres','--','psql','-U','postgres','-d','postgres','-XAt','-c',sql]))
postgres_pages = cmd(['sudo','k3s','kubectl','-n','e2b','exec','e2b-0','-c','postgres','--','psql','-U','postgres','-d','postgres','-XAt','-c','SHOW huge_pages;'])
orch = next(c for c in pod['spec']['initContainers'] if c['name']=='orchestrator')
result = {
 'boot_id':p('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'node_boot_id':node['status']['nodeInfo']['bootID'],
 'memory':{k:mem[k].strip() for k in ('MemTotal','MemAvailable','HugePages_Total','HugePages_Free','HugePages_Rsvd','HugePages_Surp','Hugetlb')},
 'hugetlb_pools':{d.name:{n:(d/n).read_text().strip() for n in ('nr_hugepages','nr_overcommit_hugepages','surplus_hugepages')}
                 for d in p('/sys/kernel/mm/hugepages').iterdir()},
 'hugepage_persistence':{str(f):[x for x in f.read_text().splitlines() if 'nr_hugepages' in x]
                         for f in p('/etc/sysctl.d').glob('*.conf') if 'nr_hugepages' in f.read_text()},
 'node_capacity':node['status']['capacity'], 'node_allocatable':node['status']['allocatable'],
 'nbd_devices':int(p('/sys/module/nbd/parameters/nbds_max').read_text()),
 'nbd_persistence':p('/etc/modprobe.d/e2b-nbd.conf').read_text().strip(),
 'neighbor_thresholds':{family:{f'gc_thresh{i}':int(p(f'/proc/sys/net/{family}/neigh/default/gc_thresh{i}').read_text()) for i in (1,2,3)} for family in ('ipv4','ipv6')},
 'neighbor_persistence':p('/etc/sysctl.d/91-e2b-network-capacity.conf').read_text(),
 'orchestrator_resources':orch['resources'], 'runtime_environment':env,
 'runtime_annotations':{k:v for k,v in pod['metadata'].get('annotations',{}).items() if k.startswith('local.e2b/')},
 'postgres_huge_pages':postgres_pages, 'team_quota':quota,
 'ready':any(c['type']=='Ready' and c['status']=='True' for c in pod['status']['conditions']),
}
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    raw = subprocess.check_output([str(ROOT / 'code/vm-ssh'), 'sudo python3 -'],
                                  input=VM_SCRIPT, text=True, timeout=60)
    report = json.loads(raw)
    qemu_pid = subprocess.check_output(['sudo','-n','systemctl','show','e2b-sandbox-vm.service','-p','MainPID','--value'], text=True).strip()
    qemu_limits = subprocess.check_output(['sudo','-n','cat',f'/proc/{qemu_pid}/limits'], text=True)
    nofile = next(line.split() for line in qemu_limits.splitlines() if line.startswith('Max open files'))
    report['qemu_nofile'] = {'pid':int(qemu_pid), 'soft':int(nofile[3]), 'hard':int(nofile[4])}
    report['checked_utc'] = datetime.now(timezone.utc).isoformat()
    config = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
    report['endpoint'] = config['api_url']
    with httpx.Client(timeout=20, trust_env=False) as client:
        health = client.get(config['api_url'] + '/health')
        report['health_http'] = health.status_code
        auth = client.get(config['api_url'] + '/v2/sandboxes',
                          params={'limit': 100}, headers={'X-API-Key':config['api_key']})
        report['authenticated_list_http'] = auth.status_code
        auth.raise_for_status()
        report['running_list_first_page_count'] = len(auth.json())
    report['ordinary_page_configuration_verified'] = (
        report['health_http'] == 200 and report['ready']
        and report['boot_id'] == report['node_boot_id']
        and report['team_quota']['concurrent_sandboxes'] == 512
        and report['nbd_devices'] == 1024
        and report['memory']['Hugetlb'] == '0 kB'
        and all(int(report['memory'][k]) == 0 for k in ('HugePages_Total','HugePages_Free','HugePages_Rsvd','HugePages_Surp'))
        and all(all(int(v) == 0 for v in values.values()) for values in report['hugetlb_pools'].values())
        and report['node_allocatable'].get('hugepages-2Mi','0') == '0'
        and all('hugepages-2Mi' not in report['orchestrator_resources'].get(kind,{})
                for kind in ('requests','limits'))
        and all(line.split('=',1)[1].strip() == '0'
                for lines in report['hugepage_persistence'].values() for line in lines)
        and report['postgres_huge_pages'] == 'off'
        and all(report['neighbor_thresholds'][family]['gc_thresh3'] == 16384 for family in ('ipv4','ipv6'))
        and report['qemu_nofile']['soft'] == 1048576
        and report['runtime_environment']['orchestrator']['E2B_MAX_SANDBOXES_PER_NODE'] == '512'
        and all(report['runtime_environment'][c]['E2B_SANDBOX_HUGE_PAGES'] == 'false'
                for c in ('api','orchestrator','host-setup'))
    )
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k:report[k] for k in ('health_http','authenticated_list_http','running_list_first_page_count','ordinary_page_configuration_verified')}))
    if not report['ordinary_page_configuration_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
