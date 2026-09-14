#!/usr/bin/env bash
set -euo pipefail
if [ ! -x /lab/envs/cpu/bin/python ]; then python -m venv --system-site-packages /lab/envs/cpu; fi
/lab/envs/cpu/bin/pip install --no-deps -r /lab/configs/cpu-overlay.lock
/lab/envs/cpu/bin/pip install --no-deps -e /lab/src/uni-agent -e /lab/src/uni-agent/verl
git config --global --add safe.directory /lab/src/uni-agent
git config --global --add safe.directory /lab/src/uni-agent/verl
/lab/envs/cpu/bin/python -c 'import torch; from uni_agent.gateway.gateway import _GatewayActor; from e2b import AsyncSandbox; from uni_agent.tasks.swe_bench.reward import compute_reward; assert torch.version.hip; assert torch.cuda.device_count()==0; print("controller imports and CPU-only isolation passed")'
