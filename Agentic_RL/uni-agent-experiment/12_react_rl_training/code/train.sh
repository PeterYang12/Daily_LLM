#!/usr/bin/env bash
set -euo pipefail
RL_ROOT=/lab/rl-swe-e2b-20260916
export PATH="/opt/venv/bin:$PATH"
export PYTHONPATH="$RL_ROOT/code:$RL_ROOT/src/uni-agent:$RL_ROOT/src/uni-agent/verl:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1
export MAX_JOBS=16
# Validated after an ECC reset: ROCm 7.14 lazy RCCL initialization produces
# incorrect first reductions with expandable_segments enabled in this image.
export PYTORCH_ALLOC_CONF=expandable_segments:False
export UA_RL_DISABLE_EXPANDABLE=1
export TORCH_NCCL_AVOID_RECORD_STREAMS=1
export CUDA_DEVICE_MAX_CONNECTIONS=1
export VLLM_ROCM_USE_AITER=1
export VLLM_ROCM_USE_AITER_MOE=0
export WANDB_ENTITY=yuhanya-amd
export WANDB_PROJECT=uni-agent-swe-rl-rocm
export WANDB_RUN_ID=${WANDB_RUN_ID:-ua-qwen30b-e2b-20260916}
export WANDB_RESUME=allow
export WANDB_DIR="$RL_ROOT/wandb"
export HYDRA_FULL_ERROR=1
export UA_RL_OBSERVE=1
export UA_RL_PHASE=${UA_RL_PHASE:-main}
export VERL_FILE_LOGGER_PATH="$RL_ROOT/results/${UA_RL_PHASE}-metrics.jsonl"
cd "$RL_ROOT/src/uni-agent"
exec python -m verl.trainer.main_ppo --config-dir "$RL_ROOT/configs" --config-name train "$@"
