# Active R2 E2B / Uni-Agent pathfinding run

**Stopped by user on September 21, 2026 after six completed native steps; checkpoint 6 is retained.** The 100-step target and later evaluations are incomplete. [Stop verification, completed work and analysis](../REVIEW-20260921.md). The following is the historical plan, not an instruction to resume.

R2 is the active 100-step training run. Its initial attempt started on 2026-09-20 at 18:49:35 UTC; the reviewed checkpoint-1 resume started at 20:24:52 UTC. The checkpoint-1 save and reload proofs matched on all eight ranks. Read [live STATUS](STATUS.md) for current native-step, checkpoint and supervisor counts, and [REPORT](REPORT.md) for measurements. This plan does not assert that the 100-step target has finished.

The earlier R1 attempt is retained in [ATTEMPT-1.md](ATTEMPT-1.md). It ended without a native optimizer update; its rollout and pretraining measurements are separate from R2.

## Active configuration

| Setting | R2 configuration |
|---|---|
| Run ID | `ua-e2b-pathfinding-20260920-r2` |
| Host experiment root | `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2` |
| Container / mount | `yuhanya-e2b-rl-pathfinding-20260920-r2`; experiment mounted at `/experiment` |
| Requested image tag | `verlai/verl:rocm7.14_torch2.12_release_0724` |
| Actual container image | `sha256:1da23791184cb601e0e661dbcae6d689c3949cf041f9c3888f2574a56372ef52` |
| Model | `/models/Qwen3-Coder-30B-A3B-Instruct` in the prepared container |
| Hardware | Eight MI350X; training host CPU allocation 0–191, outer VM allocation 192–383 |
| Training | Uni-Agent and bundled verl; colocated async, GRPO advantages / GSPO loss, R3 router replay |
| Batch | 32 prompts × 8 rollouts; 256 requested trajectories per training batch |
| Actor / rollout | Actor TP1/PP1/CP4/EP8; four TP2 vLLM replicas |
| Agent limits | 131,072 total tokens and 200 turns per episode |
| Admission | Three create permits, sixteen command permits; ticket-based cancellation accounting |
| Sandbox startup budget | 1,800 seconds; per-tool initialization has its own retry budgets |
| Checkpoints | Save every completed native step; retain two checkpoints; model, optimizer and extra state included |
| Resume | Restore checkpoint-1 model, optimizer and scheduler; preserve budgets and the original evaluation at step 0 |
| W&B | Project `e2b-rl-pathfinding`, run ID above |

Training uses **62 qualified tasks** from `dyyyyyyyy/swe-rebench-filtered-1150`. Evaluation uses **16 fixed SWE-bench Verified tasks**, at steps 0, 25, 50, 75 and 100. These are explicit subsets of the separately retained 1,150-row training corpus and 500-row evaluation corpus; subset scores are not full SWE-bench Verified scores. Runtime Parquet excludes gold patches. Protected source and qualification datasets remain private.

## Reviewable code and configuration

The [runtime code link](code/runtime) points to the active R2 `code` directory on this host. Uni-Agent and verl source remain under `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/src/uni-agent`. Treat the link as a view of the active source, not a separate editable checkout.

The active runtime reads `/experiment/configs/train.yaml` and `/experiment/configs/task_config.yaml`. The four configs archived for resume session `20260920T202452Z-d696dc09` are published here:

- [train.yaml](configs/resume-20260920T202452Z-d696dc09/train.yaml)
- [task_config.yaml](configs/resume-20260920T202452Z-d696dc09/task_config.yaml)
- [training-plan.json](configs/resume-20260920T202452Z-d696dc09/training-plan.json)
- [source-provenance.json](configs/resume-20260920T202452Z-d696dc09/source-provenance.json)

[The export manifest](configs/resume-20260920T202452Z-d696dc09/manifest.json) records the exact source paths, attempt ID, hashes, sizes and private credential-scan result. These are byte-identical archived configs, not a regenerated recipe. Hydra writes resume metadata under `/experiment/results/hydra`, outside the source checkout.

## Progress and evidence

Native trainer steps, successful per-rank optimizer calls, checkpoint completion and scheduled evaluation joins define acceptance. Runner results and agent turns have separate counters. The [first-batch timeline](results/first-batch-r2/first-batch-timeline.png) uses runner entry/return timestamps; the native timing table in [REPORT](REPORT.md) includes the rest of the training step. The [startup diagnosis](ATTEMPT-2-DIAGNOSTICS.md) and [cancellation repair](../phase5-optimization/GATE-CANCELLATION.md) preserve failure and recovery evidence.

Raw sessions, trajectories, profiles, resource samples and supervisor logs stay under the R2 root's `results/`; large checkpoint payloads stay under `checkpoints/`. The accepted prefix is `results/operator/checkpoint1-prefix-20260920T184935Z-3c9aeb9d.json`; saved/load rank proofs are in `results/reload-proof/`. The current supervisor record is selected by `results/supervision/latest.json`. Retained checkpoints may be pruned according to the configured limit.

The original run and its reviewed resume are already started. Use the bounded status and checksum commands in [README](README.md) for inspection. The documentation does not launch another trainer, reset budgets, rerun evaluation 0 or restart collectors.
