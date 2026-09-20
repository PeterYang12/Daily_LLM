#!/usr/bin/env bash
set -euo pipefail
sudo k3s kubectl create namespace e2b --dry-run=client -o yaml | sudo k3s kubectl apply -f -

# Generate the admin secrets in memory and send them directly to Kubernetes.
sudo python3 - <<'PY'
import base64,json,secrets,subprocess
existing=subprocess.run(['k3s','kubectl','-n','e2b','get','secret','e2b-api'],capture_output=True)
if existing.returncode:
    data={name:base64.b64encode(secrets.token_hex(32).encode()).decode() for name in ['ADMIN_TOKEN','SANDBOX_ACCESS_TOKEN_HASH_SEED']}
    resource={'apiVersion':'v1','kind':'Secret','metadata':{'name':'e2b-api','namespace':'e2b'},'type':'Opaque','data':data}
    subprocess.run(['k3s','kubectl','create','-f','-'],input=json.dumps(resource),text=True,check=True)
PY

cat <<'EOF' | sudo tee /usr/local/sbin/ua-e2b-ingress-guard >/dev/null
#!/bin/bash
set -euo pipefail
nic=$(ip -4 route show default | awk '{print $5; exit}')
ports=3003,5007,5008,5009,5010,5016,5017,5018,5109
iptables -C INPUT -i "$nic" -p tcp -m multiport --dports "$ports" -j DROP 2>/dev/null || iptables -I INPUT 1 -i "$nic" -p tcp -m multiport --dports "$ports" -j DROP
# E2B's guest image pins 8.8.8.8. This site permits its existing host resolver;
# translate only E2B-veth DNS traffic to that resolver, leaving other traffic intact.
resolver=$(ip -4 route show default | awk '{print $3; exit}')
for protocol in udp tcp; do
  iptables -t nat -C PREROUTING -i 'veth+' -d 8.8.8.8 -p "$protocol" --dport 53 -j DNAT --to-destination "$resolver:53" 2>/dev/null || iptables -t nat -I PREROUTING 1 -i 'veth+' -d 8.8.8.8 -p "$protocol" --dport 53 -j DNAT --to-destination "$resolver:53"
done
EOF
sudo chmod 755 /usr/local/sbin/ua-e2b-ingress-guard
cat <<'EOF' | sudo tee /etc/systemd/system/ua-e2b-ingress-guard.service >/dev/null
[Unit]
Description=Keep E2B internal control ports off the VM external interface
Wants=network-online.target
After=network-online.target
Before=k3s.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ua-e2b-ingress-guard
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now ua-e2b-ingress-guard
sudo k3s kubectl apply -k /home/e2b/deploy/overlay
sudo k3s kubectl -n e2b rollout status statefulset/e2b --timeout=20m
