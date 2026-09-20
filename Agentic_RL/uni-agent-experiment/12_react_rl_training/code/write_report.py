"""Publish the completed experiment from audited evidence, without credentials."""
import argparse,hashlib,json,shutil,statistics,subprocess,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment/12_react_rl_training')

def load(name):return json.loads((ROOT/name).read_text())
def copy(source,destination):
    target=DEST/destination;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/source,target)
def fmt(value):return f'{value:,.2f}'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,row))+' |' for row in rows])

def main():
    subprocess.run(['docker','exec','ua-rl-gpu','/opt/venv/bin/python','/lab/rl-swe-e2b-20260916/code/verify_wandb.py'],check=True,timeout=300)
    s=load('results/training-summary.json');b=load('results/sandbox-analysis.json');audit=load('results/trajectory-audit.json')
    assert s['last_step']==300 and s['step_sequence_complete'] and s['batch_shape_valid']
    assert s['exit']['exit_code']==0 and not audit['errors']
    clean=load('results/main-final-cleanup.json');assert clean['success']
    saved=load('results/saved-model-verification.json');assert saved['success']
    val=s['validation'];base=val[0];last=val[-1];gain=100*(last['accuracy']-base['accuracy'])
    ordinary=[r for r in s['training_updates'] if r['step']%100]
    ops=b['operations'];natives=load('results/native-exec-acceptance/summary.json')['latency_summary']
    life=b['lifetime_s']['sum'];paused=b['allocated_during_generation_pause_s']
    timing_rows=[]
    attempt_rows=[]
    for attempt in sorted({r.get('system/attempt',1) for r in ordinary}):
        selected=[r for r in ordinary if r.get('system/attempt',1)==attempt]
        attempt_rows.append([attempt,len(selected),fmt(statistics.mean(r['timing_s/step'] for r in selected)),fmt(statistics.mean(r['timing_s/gen'] for r in selected)),fmt(statistics.mean(r['timing_s/update_actor'] for r in selected))])
    for key,label in [('step','Whole step'),('gen','Rollout wait'),('old_log_prob','Old policy logprobs'),('adv','Advantage calculation'),('update_actor','Actor update'),('update_weights','Weight synchronization')]:
        values=[r.get('timing_s/'+key,0) for r in ordinary]
        timing_rows.append([label,fmt(statistics.mean(values)),fmt(statistics.median(values)),fmt(sorted(values)[int(.95*(len(values)-1))])])
    operation_rows=[]
    utilization_rows=[[key,fmt(value['sample_seconds']/60),fmt(value['mean_gpu_utilization_pct']),fmt(value['mean_active_sandboxes'])]
                      for key,value in b['telemetry_by_trainer_phase'].items()]
    for key in ['sandbox_create_queue','sandbox_create','sandbox_destroy','file_read','file_write','sandbox_exec:bash','sandbox_exec:tmux:send-keys','sandbox_exec:tmux:capture-pane','tool_call','verifier_exec','model_roundtrip']:
        if key in ops:
            r=ops[key];operation_rows.append([key,r['n'],fmt(r['mean']*1000),fmt(r['p95']*1000)])
    eval_table=table(['Step','Resolved','Rate','Wilson 95% interval'],[[v['step'],f"{v['resolved']}/{v['n']}",f"{v['accuracy']:.1%}",f"{v['wilson_95'][0]:.1%}–{v['wilson_95'][1]:.1%}"] for v in val])
    launches=[load(str(p.relative_to(ROOT))) for p in sorted((ROOT/'results/attempts').glob('attempt-*/main-launch.json'))]
    urls=list(dict.fromkeys([x['wandb_url'] for x in launches if 'wandb_url' in x]))
    current=load('results/main-launch.json')
    if current['wandb_url'] not in urls:urls.append(current['wandb_url'])
    elapsed=s['exit']['time_unix']-min(x['launched_unix'] for x in launches)
    first_exit=load('results/attempts/attempt-1/main-exit.json')['time_unix']
    first_recovery=load('results/attempts/attempt-2/main-launch.json')['launched_unix']
    recovery_gap=first_recovery-first_exit
    wandb=' · '.join(f'[W&B attempt {i+1}]({url})' for i,url in enumerate(urls))
    restore=[]
    for p in (ROOT/'results/main/restore-checks').glob('*.jsonl'):
        restore.extend(json.loads(line) for line in p.read_text().splitlines())
    assert restore and all(r['sampled_model_restored'] and r['scheduler_restored'] for r in restore)
    (DEST/'README.md').write_text(f'''# 12. Full ReAct RL with a remote E2B service

**Completed: 300 retained optimizer steps with Qwen3-Coder-30B-A3B-Instruct.** Each update consumed four tasks with four sampled solutions per task: **4,800 training trajectories**. The final fixed 16-task evaluation resolved **{last['resolved']}/16**, compared with **{base['resolved']}/16** before training ({gain:+.2f} percentage points). This small qualified subset supports a systems experiment; it is not a full SWE-bench score or a reliable estimate of generalization gains.

{wandb}

[Systematic analysis](ANALYSIS.md) · [Reproduction and operations](STEPS.md) · [Audited results](results/training-summary.json)

![Architecture](figures/architecture.svg)

| Item | Actual configuration |
|---|---|
| Model / hardware | Qwen3-Coder-30B-A3B-Instruct, full parameters; 8 AMD MI350X |
| Container | `verlai/verl:rocm7.14_torch2.12_release_0724` |
| Framework | Uni-Agent ReAct + bundled verl; GRPO advantages, GSPO loss |
| Frozen data | 62 qualified SWE-reBench training tasks; 16 SWE-bench Verified evaluation tasks |
| Batch / sampling | 4 prompts × rollout n=4 = 16 trajectories per update |
| Parallelism | Megatron EP8 / TP1; four vLLM TP2 replicas |
| Sandbox | User E2B API `10.145.64.67:8088`; 4 CPU / 8 GiB each |
| Concurrency | 16 active task sessions; at most 3 simultaneous creation requests |
| Update / checkpoint | 300 steps; save at 100/200/300, retain only latest |
| Final checkpoint | `checkpoints/global_step_300`; model, optimizer, scheduler, RNG, dataloader |

{eval_table}

![Training results](figures/training-curves.svg)

The optimizer evidence includes {s['nonzero_gradient_updates']} updates with nonzero gradients and {s['sampled_parameter_change_updates']} updates with sampled parameter changes. Uninformative GRPO groups have equal rewards and zero advantages; nonzero optimizer momentum can still change parameters. This run includes backward passes and policy updates, with the updated policy synchronized to rollout engines after every step.

E2B creation averaged {ops['sandbox_create']['mean']*1000:.1f} ms. The main systems cost was multi-turn rollout waiting and alternating GPU rollout/training. A same-sandbox command control reduced mean wrapper round-trip time from {natives['sdk']['mean_ms']:.1f} ms to {natives['native']['mean_ms']:.1f} ms using direct envd argv execution. This is a command benchmark, not an end-to-end RL speedup.

The first attempt stopped after step 103 due to a GPU ECC fault. Recovery retained step 100 and recomputed later updates. A reproducible ROCm allocator/communication initialization issue was removed by disabling expandable segments, followed by all-rank numerical checks. Separate W&B attempts preserve the rollback history. The analysis documents the evidence and limitations.

Raw sessions, token arrays, profiles, logs and checkpoints remain in `/home/yuhanya/uni-agent-lab/rl-swe-e2b-20260916`. This bundle contains reproducible code, frozen data, figures and compact evidence; it excludes credentials and model payloads.
''')
    analysis=f'''# Full RL results and sandbox bottleneck analysis

## What was trained

This experiment ran the complete policy-learning loop: sample software-repair tasks, run ReAct against a policy exposed through Uni-Agent Gateway, execute tools in E2B, score the candidate patch with a fresh verifier, materialize token trajectories, calculate GRPO advantages, apply GSPO policy gradients, and synchronize new weights to vLLM. There is no critic or reference-policy KL term. All model parameters are trainable; this is not a LoRA run.

Uni-Agent supplies the task/agent interfaces, Gateway sessions, tool integration and trajectory conversion. The bundled verl supplies Ray scheduling, policy loss, optimization, Megatron training and vLLM rollout. The local `ua_rl` package adapts the E2B deployment, isolates verification and records timing evidence. ReAct runs on CPU in the training container. E2B manages remote task environments; its service does not host the policy or optimizer.

The frozen corpus has 62 training tasks from the Uni-Agent-recommended SWE-reBench derivative (`dyyyyyyyy/swe-rebench-filtered-1150`) and 16 evaluation tasks from SWE-bench Verified. Baseline and gold-patch qualification tests establish that the environment exposes the failure and accepts the official fix. Qualification uses no policy success filter. Gold patches are used for qualification, not supplied to the agent or optimizer. Training/evaluation instance IDs do not overlap. The corpus is small and repeated across 300 steps; public pretrained weights can have pre-existing benchmark exposure.

## Configuration and learning results

The prompt batch is 4, rollout n is 4, and each update consumes 16 trajectories. Adam uses LR 1e-6, ten warmup steps, constant decay style, zero weight decay and gradient clipping at 1. GSPO clipping is 0.0004; token importance weights are capped at 2. Megatron uses TP1/PP1/CP1/EP8/ETP1, BF16 and full recomputation, with parameter/optimizer CPU offload. Four vLLM TP2 replicas share the same eight GPUs; rollout and training alternate. The memory fraction is 0.5; context capacity is 40,960 tokens, per-turn generation is capped at 4,096, and ReAct at 60 turns. AITER is enabled except for the unsupported MoE API; custom vLLM all-reduce is disabled. See the resolved YAML and launcher for complete settings.

{eval_table}

The change from step 0 to 300 is {gain:+.2f} percentage points, or {last['resolved']-base['resolved']:+d} tasks. Evaluation is greedy on the same 16 cases. Wilson intervals show marginal uncertainty; they do not account for paired outcomes, repeated checkpoint inspection, dataset selection or contamination. No claim of statistically established general improvement follows from this experiment.

There are {s['nonzero_gradient_updates']}/300 nonzero-gradient updates, {s['sampled_parameter_change_updates']}/300 updates with sampled parameter changes, and {s['mixed_reward_groups']}/{s['total_prompt_groups']} prompt groups containing both successful and unsuccessful samples. These are different measures. Equal-reward groups provide no GRPO contrast. The chosen n=4 trades diversity against environment and inference cost. Increasing n or task diversity is a future controlled experiment, not a measured improvement here.

![Learning curves](figures/training-curves.svg)

## Wall time and throughput

The following statistics exclude checkpoint/evaluation steps (100, 200, 300). Timers are trainer wall-clock phases. `gen` is time waiting for a usable rollout batch, including inference, tools, verification and asynchronous scheduling. It must not be labelled as E2B latency.

{table(['Phase','Mean seconds','Median seconds','P95 seconds'],timing_rows)}

![Trainer phases](figures/trainer-phase-times.svg)

The continuation changes the allocator setting, so phase means are also reported separately by attempt. Different sampled tasks and trajectory lengths prevent treating this as a controlled allocator performance comparison.

{table(['Attempt','Ordinary updates','Mean step s','Mean rollout wait s','Mean actor update s'],attempt_rows)}

Total elapsed time from the original main launch to final trainer exit was {elapsed/3600:.2f} hours, including interruption and recovery. The gap between the first crash and the first recovery launch was {recovery_gap/3600:.2f} hours; this is not training or E2B execution time. Retained step timers sum to {sum(r['timing_s/step'] for r in s['training_updates'])/3600:.2f} hours. Periodic evaluation is timed separately and is not always inside the step timer, so these quantities should not be treated as identical wall-time scopes.

Checkpoint save mean: {s['timings']['save_checkpoint']['mean']:.1f} seconds; periodic evaluation mean: {s['timings']['testing']['mean']:.1f} seconds. Checkpoints are synchronous. The first checkpoint occupied 398.17 GiB with model, optimizer and extra state; the previous directory is pruned only after the new save completes. Peak disk capacity must accommodate both temporarily. Saved-model verification: {saved['success']}.

The `perf/throughput` metric includes context tokens and should not be interpreted as generated tokens per second. Per-task time sums below are sums across concurrent tasks, not wall time and not additive to trainer wall time.

## E2B lifecycle and workload

For every episode the task controller creates an agent sandbox, initializes the repository and shell environment, runs the agent, extracts the candidate source patch, and destroys the sandbox. It then creates a fresh verifier sandbox from the task template, applies the candidate patch and official test patch, runs the official task-family reward, and destroys that sandbox. Recognized test-path edits are filtered from the candidate patch. This separation protects the verifier's initial filesystem; it does not eliminate every possible form of reward exploitation by executable source patches.

Each environment has 4 CPU and 8 GiB. The measured peak was {b['peak_from_api_inventory']} simultaneous sandboxes from API inventory and {b['peak_from_lifetimes']} from completed lifecycle spans. The separate capacity test also verified sixteen real SWE environments. There are {b['completed_sandbox_lifetimes']:,} completed lifetimes in the profiled attempts. This count includes evaluation, speculative/in-flight work and discarded work after rollback, so it is not exactly twice the 4,800 consumed training trajectories.

{table(['Operation','Samples','Mean ms','P95 ms'],operation_rows)}

Creation timings cover the request returning a sandbox handle. Environment preparation is measured separately. Verification time includes real test execution, so it is not a pure RPC measurement. Tool calls can contain multiple commands or file operations; their inclusive times cannot be added to nested RPC times. The native adapter still performs one login-environment initialization per sandbox and preserves ReAct's tmux shell state.

## What sandbox costs matter

1. **Command wrappers can dominate short tools.** The controlled same-sandbox comparison measured {natives['sdk']['mean_ms']:.1f} ms via the SDK login-shell wrapper and {natives['native']['mean_ms']:.1f} ms through direct envd argv RPC. Thirteen acceptance checks covered output, argv, files, persistent shell, environment, cancellation and a real verifier. The reduction primarily removes repeated shell startup; it is not evidence that the network alone costs the difference. The main run uses native execution.
2. **Environment occupancy extends across policy updates.** Completed environments accounted for {life/3600:.2f} sandbox-hours; {paused/3600:.2f} hours ({100*paused/life:.1f}%) overlapped intervals where colocated rollout was paused for policy work. Summed model waits overlapped these intervals by {b['model_wait_during_generation_pause_s']/3600:.2f} request-hours. These measures show retained state/capacity, not proven idle CPU. Separating rollout from training or supporting suspend/resume can reduce retained capacity, but requires a new comparison.
3. **Tail tasks determine batch availability.** Long repairs, tests, repeated tool loops and interrupted episodes retain slots. Increasing the sandbox limit alone cannot remove GPU generation or policy-update pauses. The next tuning sweep should vary session concurrency, creation concurrency and rollout n independently while keeping tasks and policy fixed.
4. **Fresh verification spends capacity to improve reward integrity.** Two environments per completed task isolate candidate execution from reward evaluation. Template caching, prewarmed verifier pools and resource classes matched to test cost are plausible optimizations. Reusing mutable agent filesystems would change the verification design.
5. **Transport and placement are separate choices.** This run exercises the user's E2B service. It does not measure a matched Docker-versus-E2B RL comparison. Earlier rollout-only comparisons remain useful context but cannot supply the missing full-RL causal comparison.

![Command comparison](figures/sandbox-command-latency.svg)

## Measurement boundaries

Client spans measure lifecycle requests, command/file operations, model round trips, tool calls and verifier body time. Host telemetry samples GPU utilization and API inventory. W&B `sandbox/window_*` aggregates sessions that finished since the preceding logging call; asynchronous completions do not align exactly with consumed batches. Token trajectories contain prompt/response IDs, model-token masks, behavior logprobs, rewards and finish state. Unfinished episodes are masked from optimization; raw generation work still consumes time.

The following time-weighted telemetry uses samples within active attempt windows, excludes long sampling gaps, and assigns each sample by the trainer's host-clock phase spans. It averages utilization across eight GPUs; coarse sampling and boundary effects remain. Initialization, interrupted work without a completed span, and unclassified intervals are shown separately.

{table(['Trainer phase','Sampled minutes','Mean GPU use %','Mean active sandboxes'],utilization_rows)}

The remote resource-metrics API returned HTTP 200 with empty sample arrays ({b['resource_metrics_nonempty']}/{b['resource_metrics_files']} files were nonempty); `/metrics` was unavailable. Therefore this report makes no measured claims about E2B-host CPU saturation, physical RAM pressure or scheduler internals. Four CPUs/eight GiB are reservations, not measured utilization. Network RTT, wrapper overhead and remote program execution must not be conflated.

All {audit['exported_trajectories']:,} exported main-run trajectories passed the recorded array/finite-logprob checks. Exported counts include speculative and rolled-back work; consumed batch dumps independently establish 4,800 trajectories in 300 retained updates. Some cancelled sessions have no final exported trajectory and are listed explicitly in the audit.

## Interruption, recovery and reproducibility

The first attempt completed step 103 before an ECC hardware fault at 2026-09-16 19:27 UTC. Kernel logs reported retired memory pages and automatic GPU resets. The completed checkpoint at 100 was retained; steps 101–103 were rolled back. W&B continuation IDs avoid overwriting original history or silently dropping replayed lower step numbers. The model, optimizer, scheduler, RNG and dataloader were restored. All recorded continuation rank checks matched sampled model values and scheduler state ({len(restore)} checks).

Recovery also reproduced an incorrect first lazy RCCL reduction in the ROCm 7.14 image with expandable allocator segments enabled. Eager communication initialization or disabling expandable segments removed the tested failure; ROCm 7.2 passed the control. The continuation retains the original image with `PYTORCH_ALLOC_CONF=expandable_segments:False`. Eight-rank BF16/FP32 reductions, reduce-scatter, gather, all-to-all and TP2 subgroup tests passed before resuming. This diagnosis does not prove every numerical operation in the earlier attempt was correct; the reported learning result should be confirmed with a clean repeat for stronger conclusions. No host driver upgrade or reboot was performed.

The checkpoint did not include TransferQueue in-flight state. Recovery is therefore not an exact replay of pending episodes. The host supervisor preserves attempts, retries only after numerical preflight, monitors progress and limits recovery attempts. The final model is checked separately from the in-memory policy. Only `global_step_300` is retained; original weights remain separate.

## Recommended next experiment

Use this result as evidence that the full integration works and as a measured baseline for scheduling. First add server-side CPU/RAM/queue metrics. Then run a controlled concurrency sweep with the same task set and fixed policy; report completed verified trajectories per second, P95 episode latency, GPU busy time and sandbox-hours. Compare colocated scheduling with separate rollout GPUs only when resources permit. For learning efficacy, expand the qualified training corpus and use a larger held-out evaluation, repeated seeds and paired outcomes. Keep full verification and record environmental failures separately from incorrect code.

## Evidence map

| Evidence | Location |
|---|---|
| Completed updates, batches, evaluation and timings | `results/training-summary.json` |
| Session operations, occupancy and pauses | `results/sandbox-analysis.json` |
| Tokens, masks, rewards and export audit | `results/trajectory-audit.json` |
| Independent saved-model verification | `results/saved-model-verification.json` |
| Checkpoint completion records | `results/checkpoint-100.json`, `checkpoint-200.json`, `checkpoint-300.json` |
| Restore checks and incident | `results/recovery/` |
| Frozen task IDs and hashes | `configs/data/frozen-manifest.json` |
| Exact source revisions and patches | `results/versions.json`, `results/*changes.patch`, `results/verl-observability.patch` |
| Full raw profiles, attempts and checkpoint payload | Runtime directory linked in the README |

{wandb}
'''
    (DEST/'ANALYSIS.md').write_text(analysis)
    for p in (ROOT/'code').rglob('*'):
        if p.is_file() and p.suffix in ('.py','.sh') and '__pycache__' not in p.parts:copy(p.relative_to(ROOT),p.relative_to(ROOT))
    for p in (ROOT/'figures').iterdir():
        if p.suffix in ('.svg','.png','.mmd'):copy(p.relative_to(ROOT),p.relative_to(ROOT))
    names=['training-summary.json','sandbox-analysis.json','trajectory-audit.json','main-final-cleanup.json','saved-model-verification.json','checkpoint-100.json','checkpoint-200.json','checkpoint-300.json','main-launch.json','recovery-events.jsonl','versions.json','wandb-verification.json','uni-agent-changes.patch','verl-observability.patch']
    for name in names:copy('results/'+name,'results/'+name)
    copy('results/attempts/attempt-1/incident.json','results/recovery/attempt-1-incident.json')
    for p in (ROOT/'results/main/restore-checks').glob('*.jsonl'):copy(p.relative_to(ROOT),'results/recovery/'+p.name)
    for p in (ROOT/'results/attempts/attempt-2/accepted').glob('*.json'):copy(p.relative_to(ROOT),'results/recovery/'+p.name)
    status={'status':'complete','target_steps':300,'completed_steps':300,'last_observed_step':300,'published_unix':time.time(),'wandb_runs':urls}
    steps=DEST/'STEPS.md'
    text=steps.read_text().replace('Training is currently active; use the status commands while it runs.','The original run completed. These commands inspect its artifacts; a replay must use a separate runtime/output directory.')
    text=text.replace('step 100 is retained for recovery','only step 300 is retained after completion')
    text=text.replace('The final report will include the complete replay recipe. The existing preparation and training code already records these stages:',
                      'The prepared-lab replay consists of these stages. Frozen parquet files, existing E2B template IDs, pinned source revisions, source patches and the Python environment inventory are included; image building and model downloads are external dependencies:')
    steps.write_text(text)
    (DEST/'results/status.json').write_text(json.dumps(status,indent=2)+'\n')
    main_launch=json.loads((DEST/'results/main-launch.json').read_text());main_launch.update(save_freq=100,checkpoints_to_keep=1)
    (DEST/'results/main-launch.json').write_text(json.dumps(main_launch,indent=2)+'\n')
    (DEST/'results/source-manifest.json').write_text(json.dumps({'runtime':str(ROOT),'status':'complete','copied_files':[
        {'destination':str(p.relative_to(DEST)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(DEST.rglob('*')) if p.is_file() and p!=DEST/'results/source-manifest.json' and '__pycache__' not in p.parts]},indent=2)+'\n')
    print(json.dumps(status))

if __name__=='__main__':main()
