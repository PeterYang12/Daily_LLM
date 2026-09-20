#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sudo -n virsh dominfo ua-e2b-96c512g-20260916
systemctl is-active ua-e2b-local-tunnel.service
ssh -F "$root/configs/ssh_config" ua-e2b-large 'sudo k3s kubectl -n e2b get pods; grep -E "MemTotal|MemAvailable|HugePages|Hugepagesize" /proc/meminfo'
curl --fail --max-time 10 http://127.0.0.1:18090/health
