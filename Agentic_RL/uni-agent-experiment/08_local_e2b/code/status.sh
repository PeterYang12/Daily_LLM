#!/usr/bin/env bash
set -euo pipefail
PROFILE_ROOT=/home/yuhanya/uni-agent-lab/local-e2b-20260916
sudo virsh dominfo ua-e2b-local-20260916
systemctl is-active ua-e2b-local-tunnel.service
curl --fail --max-time 5 http://127.0.0.1:18090/health
printf '\n'
python3 "$PROFILE_ROOT/code/vmctl.py" sudo k3s kubectl -n e2b get pods -o wide
