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
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates git python3-venv jq qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
sudo install -d -m 755 /etc/rancher/k3s
cat <<'EOF' | sudo tee /etc/rancher/k3s/config.yaml >/dev/null
node-name: ua-osb-node
write-kubeconfig-mode: "0600"
cluster-cidr: 10.72.0.0/16
service-cidr: 10.73.0.0/16
disable:
  - traefik
  - servicelb
  - metrics-server
EOF
curl -fL --retry 3 https://raw.githubusercontent.com/k3s-io/k3s/v1.36.4+k3s1/install.sh -o /home/osb/k3s-install.sh
if [ -s /home/osb/k3s ]; then
  printf '%s  %s\n' 835873f37245fc615f547a2fe2af9402a347875f13fa64a1f136de644955ea3f /home/osb/k3s | sha256sum -c -
  sudo install -m 755 /home/osb/k3s /usr/local/bin/k3s
  sudo env INSTALL_K3S_VERSION=v1.36.4+k3s1 INSTALL_K3S_SKIP_DOWNLOAD=true sh /home/osb/k3s-install.sh
else
  sudo env INSTALL_K3S_VERSION=v1.36.4+k3s1 sh /home/osb/k3s-install.sh
fi
for attempt in $(seq 1 60); do
  if sudo k3s kubectl get node ua-osb-node >/dev/null 2>&1; then break; fi
  sleep 2
done
sudo k3s kubectl wait --for=condition=Ready node/ua-osb-node --timeout=180s
sudo k3s kubectl get nodes -o wide
