#!/usr/bin/env bash
set -uo pipefail
RL_ROOT=/lab/rl-swe-e2b-20260916
: "${UA_RL_ATTEMPT:?}" "${UA_RL_RESUME_STEP:?}"
export UA_RL_PHASE=main
export WANDB_RUN_ID="ua-qwen30b-e2b-20260916-r$((UA_RL_ATTEMPT-1))"
export WANDB_RUN_GROUP=ua-qwen30b-e2b-20260916
export VERL_FILE_LOGGER_APPEND=1
bash "$RL_ROOT/code/train.sh" \
  trainer.resume_mode=resume_path \
  "trainer.resume_from_path=$RL_ROOT/checkpoints/global_step_$UA_RL_RESUME_STEP" \
  trainer.val_before_train=false \
  actor_rollout_ref.actor.optim.use_checkpoint_opt_param_scheduler=true \
  "trainer.experiment_name=qwen3-coder-30b-react-e2b-300-r$((UA_RL_ATTEMPT-1))"
RL_EXIT_CODE=$?
/opt/venv/bin/python - "$RL_EXIT_CODE" <<'PY'
import json,os,sys,time
from pathlib import Path
p=Path('/lab/rl-swe-e2b-20260916/results/main-exit.json')
p.write_text(json.dumps({'exit_code':int(sys.argv[1]),'time_unix':time.time(),'attempt':int(os.environ['UA_RL_ATTEMPT'])},indent=2)+'\n')
PY
exit "$RL_EXIT_CODE"
