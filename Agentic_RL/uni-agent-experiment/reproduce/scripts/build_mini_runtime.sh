#!/usr/bin/env bash
set -euo pipefail
mkdir -p /lab/cache/mini
sha256sum /lab/cache/portable-python.tar.gz > /lab/results/portable-python.sha256
python -c "import hashlib; assert hashlib.sha256(open('/lab/cache/portable-python.tar.gz','rb').read()).hexdigest()=='9be5c21b78dbc371e739bc7faf3b007b8e607335f780bdd2e0dd44a6e3580d76'"
tar xzf /lab/cache/portable-python.tar.gz --strip-components=1 -C /lab/cache/mini
/lab/cache/mini/bin/python3 -m pip install --no-deps -r /lab/configs/mini.lock
cp /lab/src/uni-agent/examples/mini_swe_agent/run_agent.py /lab/cache/mini/bin/run_agent.py
/lab/cache/mini/bin/python3 -m pip freeze > /lab/results/mini-freeze.txt
/lab/cache/mini/bin/python3 -c 'from minisweagent.agents.default import DefaultAgent; import importlib.metadata; print(importlib.metadata.version("mini-swe-agent"))'
