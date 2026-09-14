#!/usr/bin/env bash
set -euo pipefail
exec python -m vllm.entrypoints.openai.api_server \
 --model /models/Qwen3-Coder-30B-A3B-Instruct \
 --served-model-name Qwen3-Coder-30B-A3B-Instruct \
 --host 0.0.0.0 --port 8000 --dtype bfloat16 \
 --tensor-parallel-size 1 --gpu-memory-utilization 0.65 \
 --max-model-len 65536 --max-num-seqs 16 \
 --max-num-batched-tokens 8192 \
 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
 --generation-config vllm --enable-prefix-caching \
 --no-enable-log-requests
