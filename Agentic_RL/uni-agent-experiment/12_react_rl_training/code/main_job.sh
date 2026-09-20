#!/usr/bin/env bash
set -uo pipefail
RL_ROOT=/lab/rl-swe-e2b-20260916
export UA_RL_PHASE=main
export WANDB_RUN_ID=ua-qwen30b-e2b-20260916
bash "$RL_ROOT/code/train.sh" > "$RL_ROOT/logs/main-training.log" 2>&1
RL_EXIT_CODE=$?
/opt/venv/bin/python - "$RL_EXIT_CODE" <<'PY'
import json,sys,time
from pathlib import Path
p=Path('/lab/rl-swe-e2b-20260916/results/main-exit.json')
p.write_text(json.dumps({'exit_code':int(sys.argv[1]),'time_unix':time.time()},indent=2)+'\n')
PY
exit "$RL_EXIT_CODE"
