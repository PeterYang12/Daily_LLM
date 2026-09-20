#!/usr/bin/env bash
set -euo pipefail
export UA_RL_PHASE=pilot
export WANDB_RUN_ID=ua-qwen30b-e2b-pilot-20260916
exec bash /lab/rl-swe-e2b-20260916/code/train.sh \
  trainer.total_training_steps=2 \
  trainer.save_freq=-1 trainer.test_freq=-1 trainer.val_before_train=false \
  'trainer.logger=[console,file]' \
  trainer.experiment_name=pilot-qwen30b-react-e2b \
  trainer.default_local_dir=/lab/rl-swe-e2b-20260916/pilot-checkpoints \
  actor_rollout_ref.actor.optim.lr_warmup_steps=0 \
  data.train_files=/lab/rl-swe-e2b-20260916/data/pilot-train.parquet \
  data.val_files=/lab/rl-swe-e2b-20260916/data/pilot-val.parquet \
  actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.max_concurrent_sessions=4 \
  actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.runner_kwargs.task_config_path=/lab/rl-swe-e2b-20260916/configs/tasks.yaml \
  "$@"
