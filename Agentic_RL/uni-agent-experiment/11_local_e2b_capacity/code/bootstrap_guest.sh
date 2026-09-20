#!/usr/bin/env bash
set -euo pipefail
sudo cloud-init status --wait
cat <<'EOF' | sudo tee /etc/apt/apt.conf.d/99-ua-lab-network >/dev/null
Acquire::ForceIPv4 "true";
Acquire::http::Pipeline-Depth "0";
Acquire::http::Timeout "30";
Acquire::https::Timeout "30";
Acquire::Retries "3";
Acquire::Languages "none";
EOF
sudo env DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates iptables rsync e2fsprogs iproute2 git python3-venv jq qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent

# Reserve sandbox guest memory before kubelet inventories hugepages.
sudo install -d -m 755 /etc/rancher/k3s
printf 'vm.nr_hugepages=196608\n' | sudo tee /etc/sysctl.d/90-e2b.conf >/dev/null
sudo sysctl -p /etc/sysctl.d/90-e2b.conf
test "$(cat /proc/sys/vm/nr_hugepages)" -eq 196608

cat <<'EOF' | sudo tee /etc/rancher/k3s/config.yaml >/dev/null
node-name: ua-e2b-large
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
sudo install -m 755 /home/e2b/k3s /usr/local/bin/k3s
sudo env INSTALL_K3S_VERSION=v1.36.4+k3s1 INSTALL_K3S_SKIP_DOWNLOAD=true sh /home/e2b/k3s-install.sh
for attempt in $(seq 1 60); do
  if sudo k3s kubectl get node ua-e2b-large >/dev/null 2>&1; then break; fi
  sleep 2
done
sudo k3s kubectl wait --for=condition=Ready node/ua-e2b-large --timeout=180s
sudo k3s kubectl get node ua-e2b-large -o jsonpath='{.status.capacity.hugepages-2Mi}'
printf '\nGuest Kubernetes ready\n'
