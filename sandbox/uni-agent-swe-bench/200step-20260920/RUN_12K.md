# Container recipe: Qwen3-Coder, SWE-reBench, self-hosted E2B

The recipe is ready for the user to launch. The earlier attempt stopped during initial evaluation; production training completed **0 / 200 steps**. No replacement training or evaluation was started while preparing this recipe.

## Run inside the existing container

From the host, enter the container if needed:

```bash
docker exec -it yuhan-uniagent bash
```

Inside the container:

```bash
cd /work/experiments/rl-swe-e2b-200step-20260920
bash train_qwen3_moe_e2b_12k.sh --check
bash train_qwen3_moe_e2b_12k.sh
```

`--check` resolves the actual shell arguments with Hydra and validates local paths and checkpoint settings. It does not load credentials, contact E2B/W&B, start Ray, initialize a model, or change run state.

The recipe runs in the foreground and prints its new W&B run URL. It starts its own local Ray cluster, renews run-owned sandbox leases, and cleans up when the run exits or is interrupted with Ctrl-C. Do not start Ray separately. The previous automatic recovery/training controllers stay disabled.

To also keep a terminal log, replace the final command above with:

```bash
set -o pipefail
bash train_qwen3_moe_e2b_12k.sh 2>&1 | tee "logs/manual-$(date -u +%Y%m%d-%H%M%S).log"
```

The existing host shortcut now invokes the same recipe:

```bash
bash /home/yuhanya/uni-agent-lab/rl-swe-e2b-200step-20260920/run_12k.sh
```

## Default experiment

| Setting | Value |
|---|---|
| Model | Original Qwen3-Coder-30B-A3B-Instruct |
| Training / evaluation | SWE-reBench 1,150 / SWE-bench Verified 500 |
| Prompt batch × samples | 64 × 8 = 512 |
| PPO minibatch | 16 prompts; 4 optimizer updates per trainer step |
| Training | 200 steps; GRPO / vanilla PPO; LR 1e-6 |
| Hardware | 1 node × 8 MI350X; V1 colocate_async |
| Actor | TP1 / PP1 / CP4 / EP8 / ETP1 |
| Rollout | vLLM TP4; 2 replicas; up to 512 sessions |
| Per LLM call output cap | 12,288 tokens |
| Task budget | 131,072 total tokens; 200 agent turns |
| Evaluation | Full 500 tasks before training, then every 10 steps; n=1 |
| Checkpoints | Every 50 steps; retain latest complete checkpoint |
| Checkpoint contents | Model, HF model, optimizer, extra state |
| Loggers | Console, W&B, local metrics file |
| E2B API / sandbox URL | http://86.38.182.113/e2b |

The initial full evaluation can take substantial time; training-step metrics appear after it finishes and training proceeds. Follow the terminal and `results/sandbox-events.jsonl` for progress during that phase.

The 12K cap applies to **each LLM invocation**, including requests that ask for more output tokens. Smaller requests and limited remaining context keep their smaller limits. The agent's total budget stays 128K. No repetition-based stopping rule is enabled.

## Editable configuration

`train_qwen3_moe_e2b_12k.sh` contains the training defaults and full Hydra overrides, in the same style as upstream `train_qwen3_moe.sh`. It composes `ppo_megatron_trainer` from the prepared source snapshot; it does not use the old resolved `configs/train.yaml` as its base.

Environment variables at the top of the recipe and trailing Hydra `key=value` arguments can change parameters. Trailing arguments take precedence over shell defaults. For example, verify the intended settings without launching:

```bash
TRAIN_PROMPT_BSZ=64 N_RESP_PER_PROMPT=8 UA_MAX_TOKENS_PER_CALL=12288 bash train_qwen3_moe_e2b_12k.sh --check
```

`UA_MAX_TOKENS_PER_CALL` reaches both driver and Ray workers and takes precedence over `configs/generation-limits.json`; this recipe defaults it to 12,288. `MAX_RESPONSE_LENGTH` is a separate trajectory capacity and stays 131,072.

`configs/task_config.yaml` supplies the agent, prompt, tools, 200-turn limit, total token budget, and verifier settings. `configs/worker-env.json` supplies tested ROCm settings. Keep `VLLM_ROCM_USE_AITER_MOE=0` for the installed AITER build. Credentials and E2B URLs are loaded privately from `secrets/runtime.env` (mode 0600); keys are not embedded in the recipe or printed by `--check`.

Checkpoint output is fixed to this experiment's `checkpoints/` directory because the retention callback validates that location. That directory is currently empty, so the first launch starts from the original model with a fresh full baseline. Earlier partial baseline evidence is retained and conflicting aggregate files are archived. Later launches retain `resume_mode=auto` and resume an available checkpoint.

## Relationship to the official script

The earlier launch captured the official script's arguments, applied the requested experiment and hardware overrides, and called `runtime_launch.py` → `verl.trainer.main_ppo.run_ppo(..., TaskRunnerV1)`. The production run stopped during initial evaluation, before training step 1.

This recipe makes those arguments explicit. `manual_run.py` handles config checks and foreground lifecycle; `runtime_launch.py` initializes the E2B admission gate and invokes the same verl V1 trainer. The prepared source snapshot includes the required E2B, ROCm, validation, and 12K generation-limit adaptations.

Validation completed without launching a workload: shell/Python syntax, the actual container `--check`, 994 resolved configuration fields compared against the previous captured config, trailing-override handling, and per-call-cap propagation. The only reference-config difference is explicit inference logging (`disable_log_stats=False`), which the runtime already enabled. The new end-to-end 200-step run remains unexecuted.

## Outputs

- W&B: `yuhanya-amd / uni-agent-swe-rl-rocm`; a new timestamped run URL is printed at startup.
- Metrics: `results/train-metrics.jsonl`.
- Effective training config: `results/training-resolved-config.json` after startup.
- Validation: `results/validation/` and `results/validation-audit/`.
- Sandbox activity: `results/sandbox-events.jsonl`.
- Checkpoints: `checkpoints/global_step_*/`; older checkpoints are pruned after a successful complete save.
- Host runtime: `/home/yuhanya/yuhan-uniAgent/experiments/rl-swe-e2b-200step-20260920`.

All 1,650 task environments retain startup paths: 1,059 cached templates and 591 training environments prepared from verified public OCI layers at sandbox startup. No dataset tasks are removed.
