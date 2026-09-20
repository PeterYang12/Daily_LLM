#!/usr/bin/env bash
set -euo pipefail
RUN_ROOT=/work/experiments/colocate-e2b-3step-20260920
export PYTHONPATH="$RUN_ROOT/code:$RUN_ROOT/src/uni-agent:$RUN_ROOT/src/uni-agent/verl"
export PYTHONNOUSERSITE=1
export PYTORCH_ALLOC_CONF=expandable_segments:False
export TORCH_NCCL_AVOID_RECORD_STREAMS=1
export CUDA_DEVICE_MAX_CONNECTIONS=1
export OMP_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export VLLM_ROCM_USE_AITER=1
export VLLM_ROCM_USE_AITER_MOE=0
export USE_ROCM_AITER_ROPE_BACKEND=0
export TRANSFER_QUEUE_ENABLE=1
export UA_PHASE=train
export UA_OBSERVE=1
export UA_MAX_SUBMITTED_PROMPTS=48
export HYDRA_FULL_ERROR=1
export VERL_FILE_LOGGER_PATH="$RUN_ROOT/results/train-metrics.jsonl"
cd "$RUN_ROOT/src/uni-agent"
set +e
python "$RUN_ROOT/code/runtime_launch.py" --config-dir "$RUN_ROOT/configs" --config-name train "$@"
TRAIN_EXIT_CODE=$?
python "$RUN_ROOT/code/mark_exit.py" "$TRAIN_EXIT_CODE"
exit "$TRAIN_EXIT_CODE"
