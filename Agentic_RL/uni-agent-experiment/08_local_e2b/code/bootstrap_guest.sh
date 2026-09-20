#!/usr/bin/env bash
set -euo pipefail
sudo cloud-init status --wait
sudo env DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates iptables rsync e2fsprogs iproute2 git python3-venv jq qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent

# Reserve sandbox guest memory before kubelet inventories hugepages.
sudo install -d -m 755 /etc/rancher/k3s
printf 'vm.nr_hugepages=16384\n' | sudo tee /etc/sysctl.d/90-e2b.conf >/dev/null
sudo sysctl -p /etc/sysctl.d/90-e2b.conf
test "$(cat /proc/sys/vm/nr_hugepages)" -eq 16384

cat <<'EOF' | sudo tee /etc/rancher/k3s/config.yaml >/dev/null
node-name: ua-e2b-node
node-label:
  - e2b.dev/single-node=true
write-kubeconfig-mode: "0600"
cluster-cidr: 10.62.0.0/16
service-cidr: 10.63.0.0/16
disable:
  - traefik
  - servicelb
  - metrics-server
EOF
curl -fL --retry 3 https://raw.githubusercontent.com/k3s-io/k3s/v1.36.4+k3s1/install.sh -o /home/e2b/k3s-install.sh
sha256sum /home/e2b/k3s-install.sh
sudo env INSTALL_K3S_VERSION=v1.36.4+k3s1 sh /home/e2b/k3s-install.sh
for attempt in $(seq 1 60); do
  if sudo k3s kubectl get node ua-e2b-node >/dev/null 2>&1; then break; fi
  sleep 2
done
sudo k3s kubectl wait --for=condition=Ready node/ua-e2b-node --timeout=180s
sudo k3s kubectl get node ua-e2b-node -o jsonpath='{.status.capacity.hugepages-2Mi}'
printf '\nGuest Kubernetes ready\n'
