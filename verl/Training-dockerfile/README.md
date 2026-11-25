# `Dockerfile.rocm.primus-qat`

verl training image for AMD Instinct MI350 / MI355 (gfx950): Megatron / FSDP training with vLLM rollout.

## What is in the image

| Component | Version | Source |
|---|---|---|
| Base | `rocm/primus:v26.9` (ROCm 10.2, torch 2.16.0a0) | Docker Hub |
| vLLM | `v0.34.1rc0`, built from source, no patches | `VLLM_TAG` |
| verl | upstream `main`, installed editable in `/workspace/verl`, no patches | `VERL_REPO`, `VERL_REF` |
| transformers | 5.18.0 | `TRANSFORMERS_VERSION` |
| Megatron-Core / Megatron-Bridge | `core_v0.19.2` / `v0.6.2` | |
| MoRI-IO (`amd_mori`) | 1.2.5 | |
| torchcodec | 0.17.0+cpu | PyTorch CPU index |

The commits of verl and vLLM in a built image are in `/workspace/.manifest/`.

Two versions differ from what verl locks, because vLLM 0.34 requires `huggingface_hub >= 2.0`:
verl locks vLLM 0.32.0 and transformers 5.16.1 (`< 5.17`). pip reports this as a conflict during the build; it
does not fail the build.

## Build

The Dockerfile clones vLLM and verl itself and reads nothing from the build context.

```bash
cd /home/yuhanya/Daily_LLM/verl/Training-dockerfile
DOCKER_BUILDKIT=1 docker build --network=host --progress=plain \
  -f Dockerfile.rocm.primus-qat \
  -t yangyuhanintel/verl-qat:primus-v26.9-vllm0.34.1rc0 .

docker push yangyuhanintel/verl-qat:primus-v26.9-vllm0.34.1rc0
```

Build arguments (`--build-arg NAME=value`):

| Name | Default | Meaning |
|---|---|---|
| `VERL_REF` | `main` | verl branch, tag or commit. A rebuild reuses the cached clone; pass a commit to move to a newer one |
| `VERL_REPO` | `https://github.com/verl-project/verl.git` | verl repository |
| `VLLM_TAG` | `v0.34.1rc0` | vLLM git ref |
| `TRANSFORMERS_VERSION` | `5.18.0` | transformers release |

On a cluster node, run the build detached so it does not depend on the login session. The log is written to
`<log dir>` (under `/home`); `BUILD_EXIT=0` at its end means the build succeeded.

```bash
source /etc/profile.d/spur.sh

# build
srun --jobid=<job id> --overlap docker run -d --rm --name qat_build \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /home/yuhanya/Daily_LLM/verl/Training-dockerfile:/df:ro \
  -v <log dir>:/log \
  docker:cli sh -c "cd /df && DOCKER_BUILDKIT=1 docker build --network=host --progress=plain \
    -f Dockerfile.rocm.primus-qat -t yangyuhanintel/verl-qat:primus-v26.9-vllm0.34.1rc0 . \
    > /log/build.log 2>&1; echo BUILD_EXIT=\$? >> /log/build.log"

# push (needs a Docker Hub login in ~/.docker/config.json)
srun --jobid=<job id> --overlap docker run -d --rm --name qat_push \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $HOME/.docker:/root/.docker:ro \
  -v <log dir>:/log \
  docker:cli sh -c "docker push yangyuhanintel/verl-qat:primus-v26.9-vllm0.34.1rc0 > /log/push.log 2>&1; \
    echo PUSH_EXIT=\$? >> /log/push.log"
```

## Run

```bash
docker run -d -it --name verl --network host --ipc host \
  --device /dev/kfd --device /dev/dri --device /dev/infiniband \
  --group-add video --group-add render --cap-add IPC_LOCK --cap-add SYS_PTRACE \
  --security-opt seccomp=unconfined --security-opt label=disable \
  -v /shared_nfs/yuhan:/work \
  yangyuhanintel/verl-qat:primus-v26.9-vllm0.34.1rc0 /bin/bash
```

To use a local verl checkout instead of the one in the image, mount it over `/workspace/verl`:

```bash
  -v /home/yuhanya/verl:/workspace/verl
```

No reinstall is needed: verl is installed editable, so Python reads the mounted code. Run from
`/workspace/verl`. If the local checkout needs a package the image does not have, install that package alone;
`pip install -e .` would move transformers back below 5.17.

## Tested (2026-11-25, node with 8 × MI355X)

GRPO on GSM8K, 3 training steps, FSDP2 training, vLLM rollout (aiter off unless noted).

| Model | verl | Rollout | Result |
|---|---|---|---|
| Qwen2.5-0.5B | image (`main`) | plain, 2 GPUs | completes |
| Qwen2.5-0.5B | mounted checkout | plain, 2 GPUs | completes |
| Qwen2.5-0.5B | mounted checkout with MoRI-IO PD | PD READ 1P1D | completes |
| Qwen3-30B-A3B-Instruct-2507 | image (`main`) | plain, 8 GPUs, TP2 | validation accuracy 0.907 on the GSM8K test set (1319 questions) |
| Qwen3-30B-A3B-Instruct-2507 | mounted checkout with MoRI-IO PD | PD READ 1P3D, TP2 | validation accuracy 0.913 |
| Qwen3-30B-A3B-Instruct-2507 | image (`main`) | plain, 8 GPUs, TP2, aiter on (`VLLM_ROCM_USE_AITER=1`) | validation accuracy 0.911 |

Not tested: Megatron and QAT training.

One aiter-on run hung before the first training step: a dataloader worker deadlocked in OpenMP after fork. The
image now sets `KMP_INIT_AT_FORK=TRUE` (as verl's `docker/rocm/Dockerfile.rocm` does); the rerun in the table
above was made with it. The hang is intermittent, so one clean run does not prove it is gone.
