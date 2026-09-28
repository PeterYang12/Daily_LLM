#!/bin/bash
set -euo pipefail
test "$(id -u)" -eq 0
cd /opt/e2b-local
for binary in api orchestrator migrator; do
  test -x "/opt/e2b-local/custom-runtime/$binary"
done
test -d /opt/e2b-local/custom-runtime/migrations
test -f /opt/e2b-local/custom-runtime/host-setup.sh
install -m 0644 configs/91-e2b-network-capacity.conf /etc/sysctl.d/91-e2b-network-capacity.conf
sysctl -q -p /etc/sysctl.d/91-e2b-network-capacity.conf
python3 - <<'PY'
from pathlib import Path
import os, json, fcntl
report = {'cpus': os.cpu_count(), 'kernel': os.uname().release}
report['memory'] = {k: v.strip() for k, v in
    (x.split(':', 1) for x in Path('/proc/meminfo').read_text().splitlines())
    if k in ['MemTotal', 'MemAvailable', 'HugePages_Total', 'HugePages_Free', 'Hugepagesize']}
with open('/dev/kvm', 'rb', buffering=0) as device:
    report['kvm_api_version'] = fcntl.ioctl(device, 0xAE00, 0)
    vm = fcntl.ioctl(device, 0xAE01, 0)
    os.close(vm)
    report['nested_kvm_create_vm'] = True
expected_cpus = int(os.environ.get('E2B_EXPECTED_VCPUS', '192'))
assert expected_cpus >= 16, 'The included Kubernetes resource settings require at least 16 vCPUs'
assert report['cpus'] == expected_cpus and report['kvm_api_version'] == 12
assert int(report['memory']['HugePages_Total']) == 0, 'This deployment uses ordinary 4 KiB pages'
Path('/opt/e2b-local/results').mkdir(exist_ok=True)
Path('/opt/e2b-local/results/guest-preflight.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report))
PY
install -m 0644 configs/guest-network.service /etc/systemd/system/e2b-local-network.service
systemctl daemon-reload
systemctl enable --now e2b-local-network.service
if ! command -v k3s >/dev/null 2>&1; then
  INSTALL_K3S_VERSION='v1.36.4+k3s1' \
  INSTALL_K3S_EXEC='server --disable traefik --disable servicelb --node-name e2b-vm --node-label e2b.dev/single-node=true --write-kubeconfig-mode 600' \
    sh vendor/install-k3s.sh
fi
for attempt in $(seq 1 60); do
  if k3s kubectl get node e2b-vm >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
k3s kubectl wait --for=condition=Ready node/e2b-vm --timeout=180s
k3s kubectl get node e2b-vm -o json > results/kubernetes-node.json
k3s kubectl create namespace e2b --dry-run=client -o yaml | k3s kubectl apply -f -
python3 - <<'PY'
import json,secrets,subprocess
exists = subprocess.run(['k3s','kubectl','-n','e2b','get','secret','e2b-api'],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
if exists.returncode != 0:
    obj = {'apiVersion':'v1','kind':'Secret','metadata':{'name':'e2b-api','namespace':'e2b'},
           'type':'Opaque','stringData':{'ADMIN_TOKEN':secrets.token_hex(32),
                                       'SANDBOX_ACCESS_TOKEN_HASH_SEED':secrets.token_hex(32)}}
    subprocess.run(['k3s','kubectl','apply','-f','-'],input=json.dumps(obj),text=True,check=True)
PY
k3s kubectl kustomize configs/kubernetes > results/rendered.yaml
python3 - <<'PY'
from pathlib import Path
import yaml
upstream = yaml.safe_load(Path('vendor/e2b/embed/kubernetes/statefulset.yaml').read_text())
rendered = list(yaml.safe_load_all(Path('results/rendered.yaml').read_text()))
actual = next(x for x in rendered if x.get('kind') == 'StatefulSet' and x['metadata']['name'] == 'e2b')
expected_order = [x['name'] for x in upstream['spec']['template']['spec']['initContainers']]
actual_order = [x['name'] for x in actual['spec']['template']['spec']['initContainers']]
assert actual_order == expected_order, 'Capacity overlay changed the required startup order'
orchestrator = next(x for x in actual['spec']['template']['spec']['initContainers'] if x['name'] == 'orchestrator')
assert 'hugepages-2Mi' not in orchestrator['resources'].get('requests', {})
assert 'hugepages-2Mi' not in orchestrator['resources'].get('limits', {})
env = {entry['name']: entry.get('value') for entry in orchestrator['env']}
assert env['E2B_SANDBOX_HUGE_PAGES'] == 'false'
assert env['E2B_MAX_SANDBOXES_PER_NODE'] == '512'
assert env['E2B_MAX_STARTING_INSTANCES_PER_NODE'] == '512'
print('UPSTREAM_INIT_ORDER_AND_4K_RUNTIME_CONFIG_VERIFIED')
PY
k3s kubectl apply -f results/rendered.yaml
k3s kubectl -n e2b rollout status statefulset/e2b --timeout=20m
k3s kubectl -n e2b rollout status deployment/e2b-gateway --timeout=3m
k3s kubectl -n e2b get pods -o wide
