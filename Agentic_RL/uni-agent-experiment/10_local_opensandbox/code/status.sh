#!/usr/bin/env bash
set -euo pipefail
OSB_ROOT=/home/yuhanya/uni-agent-lab/local-opensandbox-20260916
sudo virsh dominfo ua-opensandbox-local-20260916
systemctl is-active ua-opensandbox-local-tunnel.service
curl -fsS --max-time 5 http://127.0.0.1:18094/health
printf '\n'
python3 "$OSB_ROOT/code/vmctl.py" status
