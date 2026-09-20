"""Publish the completed profiling study into one numbered experiment directory."""
import argparse,hashlib,json,shutil,statistics
from datetime import datetime,timezone
from pathlib import Path

def read(path):return json.loads(path.read_text())
def fmt(value,digits=1):return 'n/a' if value is None else f'{value:.{digits}f}'

def main(root,docs):
    for marker in ['FOLLOWUP_COMPLETE','NATIVE_PROBE_COMPLETE','VERIFIER_PROBE_COMPLETE']:
        if not (root/marker).exists():raise RuntimeError('Study still running: '+marker)
    summary=read(root/'analysis/summary.json');cases=read(root/'analysis/cases.json')
    assert summary['status']=='complete' and len(cases)==24
    target=docs/'07_swe_sandbox_profiling';target.mkdir(exist_ok=True)
    records=[]
    def copy(source,destination):
        destination=target/destination;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
        records.append({'source':str(source),'destination':str(destination.relative_to(target)),
                        'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()})
    for source in (root/'analysis/figures').glob('*'):
        if source.suffix in {'.svg','.mmd'}:copy(source,'figures/'+source.name)
    for name in ['summary.json','cases.json','cases.csv','tool-calls.csv','tool-stats.csv','scheduling.csv','timeline.json']:
        path=root/'analysis'/name
        if path.exists():copy(path,'results/'+name)
    for provider in ['docker','e2b']:
        copy(root/'microbench'/provider/'summary.json','results/microbench/'+provider+'.json')
    for name in ['raw.json','summary.json','protocol.json','logprob-overhead.json']:
        copy(root/'inference-benchmark'/name,'results/inference/'+name)
    for name in ['native-shell-probe.json','verifier-cpu-probe.json','runtime-provenance.json','runtime-details.json','final-service-provenance.json','templates.json','controls-final-index.json','service-audit.json','completion-audit.json','resource-release.json']:
        path=root/name
        if path.exists():copy(path,'results/'+name)
    copy(root/'manifest.json','configs/manifest.json')
    copy(root/'assets/tmux.tar.gz','assets/tmux.tar.gz')
    copy(root/'assets/tmux-provenance.json','assets/tmux-provenance.json')
    if (root/'uni-agent-runtime.patch').exists():copy(root/'uni-agent-runtime.patch','configs/uni-agent-runtime.patch')
    copy(Path('/home/yuhanya/uni-agent-lab/configs/swe-react-128k.yaml'),'configs/swe-react-128k.yaml')
    for source in Path('/home/yuhanya/uni-agent-lab/scripts/swe_sandbox_profile').glob('*'):
        if source.is_file():copy(source,'code/'+source.name)
    for source in (root/'code-snapshot').glob('*'):
        if source.is_file():copy(source,'code/executed/'+source.name)
    copy(root/'report-drafts/STEPS.md','STEPS.md')
    controls=read(root/'controls-final-index.json')['sources']
    control_rows=[]
    for case in cases:
        provider,iid=case['provider'],case['instance_id']
        base=root/'main-optimized'/provider/iid
        for name in ['result.json','input-config.json','verifier.json','verifier-raw.log','trajectory-audit.json','candidate-delta.patch','candidate-source-only.patch','changed-files.txt']:
            copy(base/name,f'results/cases/{provider}/{iid}/{name}')
        for mode in ['baseline','oracle']:
            source=root/controls[iid]/provider/iid/mode/'result.json'
            row=read(source)
            state_path=source.parent/'agent-environment/initial-state.json'
            control_rows.append({'instance_id':iid,'provider':provider,'mode':mode,'resolved':row.get('resolved'),
                                 'eval_completed':row.get('eval_completed'),'source':str(source),
                                 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                                 'initial_state':read(state_path)})
    (target/'results/controls.json').write_text(json.dumps(control_rows,indent=2)+'\n')
    (target/'results/source-manifest.json').write_text(json.dumps({'run_root':str(root),'copied_files':records},indent=2)+'\n')

    d,e=summary['providers']['docker'],summary['providers']['e2b']
    outcomes={(r['provider'],r['instance_id']):r['resolved'] for r in cases}
    agreement=sum(outcomes['docker',iid]==outcomes['e2b',iid] for iid in {r['instance_id'] for r in cases})
    end_date=datetime.fromtimestamp(float((root/'VERIFIER_PROBE_COMPLETE').read_text()),timezone.utc).date().isoformat()
    micro={p:{x['operation']:x for x in read(root/'microbench'/p/'summary.json')} for p in ['docker','e2b']}
    schedule=summary['scheduling'];native=read(root/'native-shell-probe.json')
    serving={(r['backend'],r['input_length'],r['concurrency']):r for r in read(root/'inference-benchmark/summary.json')}
    counts={'primary_rollouts':24,'accepted_controls':len(control_rows),'fixed_operation_samples':sum(r['n'] for p in micro.values() for r in p.values()),
            'scheduling_attempts':sum(r['jobs'] for r in schedule),'serving_batches':len(read(root/'inference-benchmark/raw.json')),
            'logprob_batches':len(read(root/'inference-benchmark/logprob-overhead.json')['results']),
            'verifier_cpu_runs':len(read(root/'verifier-cpu-probe.json'))}
    assert counts=={'primary_rollouts':24,'accepted_controls':48,'fixed_operation_samples':200,
                    'scheduling_attempts':16,'serving_batches':54,'logprob_batches':24,'verifier_cpu_runs':12},counts
    assert all(r['eval_completed'] and r['resolved']==(r['mode']=='oracle') for r in control_rows)
    (target/'results/experiment-counts.json').write_text(json.dumps(counts,indent=2)+'\n')
    lines=['# 7. ReAct SWE-bench: Docker vs E2B and rollout bottlenecks','',
      f'**Study started 2026-09-14 UTC and completed {end_date} UTC. Fixed Qwen3-Coder-30B-A3B-Instruct weights; rollout and evaluation profiling, with no RL/SFT update.**','',
      'We ran the same 12 SWE-bench tasks on local Docker and the supplied E2B-compatible service, through Uni-Agent Task, ReAct, Gateway, and the official SWE verifier. Each rollout used a fresh execution sandbox and a separate fresh verifier sandbox.','',
      '## Main result','',
      '| Metric | Docker | E2B |','|---|---:|---:|',
      f'| Resolved tasks | **{d["resolved"]}/12** | **{e["resolved"]}/12** |',
      f'| Valid token/mask/logprob trajectories | {d["valid_trajectories"]}/12 | {e["valid_trajectories"]}/12 |',
      f'| Normal agent completion | {d["finished"]}/12 | {e["finished"]}/12 |',
      f'| Mean complete rollout time | {d["mean_wall_s"]:.1f} s | {e["mean_wall_s"]:.1f} s |',
      f'| Median tool share of agent time | {d["median_tool_fraction"]:.1%} | {e["median_tool_fraction"]:.1%} |',
      f'| Mean of per-task serving-GPU activity | {d["gpu_busy_mean_by_task"]:.1f}% | {e["gpu_busy_mean_by_task"]:.1f}% |','',
      f'The providers agreed on task success for {agreement}/12 tasks. Their sampled trajectories still differed in token count and tool use, so the end-to-end time ratio is not a pure platform-speed measurement.','',
      '![Rollout stages](figures/rollout-stages.svg)','',
      '## What the profile shows','',
      f'1. **Small tool operations have a substantial remote cost.** A stateful-shell no-op took a median {micro["docker"]["stateful_shell_true"]["median_s"]*1000:.0f} ms on Docker and {micro["e2b"]["stateful_shell_true"]["median_s"]*1000:.0f} ms on E2B. The tmux implementation expands one command into four exec operations and one file write on E2B.',
      '2. **Concurrency raised rollout throughput.** Four in-flight Flask tasks gave about 3.2 times the scored trajectories per minute of serial execution on each provider. Token-normalized gains were 2.27 times for Docker and 3.03 times for E2B; individual task latency did not improve.',
      '3. **Serving gains depend on context length.** AITER + graphs gave 4.73 times eager throughput for one 2K-input request, but only 1.04 times at 64K input and eight concurrent requests. Both tests included token IDs/logprobs. The separate warmed-prefix logprob check reduced throughput by roughly 1–4%.',
      '4. **Verification has its own cost.** Guest-side wall/user/system timings separate verifier execution from client transport and show where more CPU or faster I/O should be investigated.','',
      '## What was measured','',
      f'{counts["accepted_controls"]} accepted baseline/gold controls; {counts["primary_rollouts"]} primary rollouts; {counts["fixed_operation_samples"]} fixed-operation samples; {counts["scheduling_attempts"]} scheduling attempts; {counts["serving_batches"]} serving batches; {counts["logprob_batches"]} logprob batches; and {counts["verifier_cpu_runs"]} targeted verifier runs. The persistent-shell prototype is separate from primary task scores.','',
      'All task environments were based on the same pinned image digests, configured for 4 CPUs and 8 GiB. Repository initial Git trees matched, baseline code failed, and official patches passed on both providers. Primary pairs ran serially, with alternating provider order, against one AITER + graph 128K service.','',
      '## Execution path','',
      '![Execution architecture](figures/architecture.svg)','',
      '`run.py` starts the Uni-Agent Gateway and calls upstream `framework.task_runner.run_task`. Our `ProfileSWETask` extends the upstream SWE task to add timing and a fresh verifier; upstream ReAct and its tools run inside `ua-lab-cpu`. The Gateway and ReAct are in that coordinator process. The vLLM GPU service is a separate container.','',
      'The task creates and destroys sandbox instances through the selected provider. Local Docker commands go through the dedicated daemon socket; E2B commands use the remote SDK API. ReAct selects actions, while the tool adapter executes them. After grading, the driver calls `finalize_session`, attaches the reward, exports trajectories, and shuts down the Gateway. Sandbox cleanup and Gateway-session finalization are separate lifecycle operations.','',
      '**Fixed settings:** Qwen3-Coder-30B-A3B-Instruct BF16 (30B total / about 3B active parameters), TP1, 131072-token serving limit, 100 ReAct steps, 4096 new tokens per model call, temperature 0.2, top-p 0.9, 1800 s agent deadline, and 600 s evaluation timeout. No per-request random seed was fixed. All benchmark execution ran in containers; the host monitor only read telemetry.','',
      'The base [ReAct YAML](configs/swe-react-128k.yaml) supplies the agent and prompt settings. `run.py` replaces its original sandbox block; [providers.py](code/providers.py) sets the local resource limits, while [prepare_templates.py](code/prepare_templates.py) configures E2B templates. Each case includes its effective `input-config.json`. The old sandbox mounts and 2-CPU setting in the base YAML were not used for this study.','',
      'Runtime: Uni-Agent commit `10743439dd0a19da44a94cccad069b135d957bf1`, E2B SDK 2.49.1, and swebench 4.1.0. [Runtime details](results/runtime-details.json) and the captured [logging patch](configs/uni-agent-runtime.patch) record the existing lab checkout; model image identities are in [service provenance](results/final-service-provenance.json).','',
      '## Per-task outcomes','',
      '| SWE-bench instance | Docker resolved | E2B resolved | Docker seconds | E2B seconds |',
      '|---|---|---|---:|---:|',
      *[f'| {iid} | {"Yes" if outcomes["docker",iid] else "No"} | {"Yes" if outcomes["e2b",iid] else "No"} | '+
        ' | '.join(f'{next(r["wall_seconds"] for r in cases if r["provider"]==p and r["instance_id"]==iid):.1f}' for p in ['docker','e2b'])+' |'
        for iid in sorted({r['instance_id'] for r in cases})],
      '', 'Astropy and Seaborn produced final answers but failed official verification. Pylint reached the 100-step limit on both providers and its final candidate failed verification. Every primary evaluation completed; an unresolved task is retained in the denominator. The sample takes the first qualified issue per repository in the existing 28-task dataset order.','',
      '`Resolved` follows SWE-bench\'s declared FAIL_TO_PASS and PASS_TO_PASS sets. It does not mean every test printed by pytest passed. For Requests 1921, both official-gold and candidate runs contain the same unrelated `test_conflicting_post_params` failure caused by a legacy pytest API; that test is outside both declared sets. The full [Docker verifier log](results/cases/docker/psf__requests-1921/verifier-raw.log) is retained alongside the scored test report.','',
      '## Read next','',
      '- [PROFILE.md](PROFILE.md): measurements, interpretation, and ranked actions.',
      '- [STEPS.md](STEPS.md): replay procedure and resource cleanup.',
      '- [Per-case CSV](results/cases.csv), [raw tool samples](results/tool-calls.csv), [timing trace](results/timeline.json), and [artifact hashes](results/source-manifest.json).',
      '- [code/](code/): replay and analysis tools; `code/executed/` preserves the primary-run snapshot.','',
      'This is a small, previously observed development subset, not a full 500-task leaderboard or a held-out model evaluation. Configured CPU/RAM were matched; physical CPU, storage, kernel, and network placement were not. Results describe this deployment. Optimizer, backward pass, weight synchronization, and checkpoint costs were not measured.','',
      'After measurement, the [sandbox audit](results/service-audit.json) found no running instances owned by this study. The [two study model services and monitors were stopped](results/resource-release.json) to release GPU resources. E2B templates, images, weights, and the CPU coordinator remain available for replay.','',
      f'Complete logs, trajectories, and telemetry: `{root}`.','', '[Back to overview](../README.md)']
    (target/'README.md').write_text('\n'.join(lines)+'\n')

    lines=['# Measured rollout bottlenecks','',
      'A serial rollout consists of environment preparation, repeated model and tool calls, verification, trajectory export, and cleanup. In agentic RL, the training batch also waits for enough scored trajectories; slow tools and long agent tails therefore affect learner supply. The measurements below cover that rollout path. They do not establish the bottleneck of a complete training job.','',
      '## 1. Tool transport and call granularity','',
      'The fixed-operation study removes model sampling from the comparison. All values below are client-observed medians on matched Flask images; they include the current adapters and transport.','',
      '| Operation | Docker | E2B | E2B / Docker |','|---|---:|---:|---:|']
    for name,label in [('exec_true','Execute true'),('read_1024','Read 1 KiB'),('write_1024','Write 1 KiB'),('read_1048576','Read 1 MiB'),('write_1048576','Write 1 MiB'),('stateful_shell_true','Stateful shell: true')]:
        a,b=micro['docker'][name]['median_s'],micro['e2b'][name]['median_s']
        lines.append(f'| {label} | {a*1000:.1f} ms | {b*1000:.1f} ms | {b/a:.2f}× |')
    lines+=['','![Operation latency](figures/operation-latency.svg)','',
      'A stateful-shell no-op generated one E2B file write and four Process/Start operations: send tmux keys, read exit status, read stdout, and read stderr. Local Docker used six CLI invocations because its upload path also creates the parent directory. The trace counts SDK/provider operations; HTTPX instrumentation alone does not capture Connect RPC sends.','',
      '**Action:** batch independent operations, reduce metadata round trips, use persistent channels where supported, and place the coordinator nearer the execution service. This result does not establish that colocated E2B has the same overhead.','']
    if native.get('success'):
        value=native['median_s'];base=micro['e2b']['stateful_shell_true']['median_s']
        lines += [f'**Targeted prototype:** a persistent E2B stdin channel preserved cwd/environment and completed {native["n"]} no-op commands at median **{value*1000:.1f} ms**, versus {base*1000:.1f} ms for tmux-over-exec ({base/value:.2f}× lower median latency). This is a transport prototype, not a full SWE-bench run with a replacement shell adapter. [Raw result](results/native-shell-probe.json)','']
    else:
        lines += ['The persistent-stdin prototype did not complete successfully. No speedup is claimed; see [its result](results/native-shell-probe.json).','']
    lines += ['## 2. Concurrency and idle gaps','',
      '![GPU and tool time](figures/gpu-and-tools.svg)','',
      '| Provider | In-flight tasks | Scored trajectories | Batch seconds | Mean task seconds | Scored trajectories/min | GPU busy |',
      '|---|---:|---:|---:|---:|---:|---:|']
    for row in sorted(schedule,key=lambda r:(r['provider'],r['concurrency'])):
        lines.append(f'| {row["provider"]} | {row["concurrency"]} | {row["scored_trajectories"]}/{row["jobs"]} | {row["wall_s"]:.1f} | {row["mean_task_wall_s"]:.1f} | {row["rollouts_per_minute"]:.2f} | {fmt(row["gpu_busy_mean"])}% |')
    lines+=['','![Scheduling](figures/scheduling.svg)','',
      'Four in-flight tasks improved scored-trajectory throughput by about 3.2 times on both providers. Docker generated about 30% fewer tokens in its concurrent batch, so its token-normalized gain was 2.27 times; E2B gained 3.03 times by that measure. Mean individual task latency increased slightly. Concurrency improved batch throughput by hiding waits; it did not make each issue finish faster.','',
      'Each setting contains four independently sampled copies of the Flask task, not additional primary benchmark cases. A scored trajectory requires valid token records and completed evaluation. Token-normalized throughput is shown because sampled output lengths vary. The finite batch includes preparation, verification, and drain time.','',
      '**Action:** maintain enough independent in-flight rollouts to cover tool waits; use separate verification capacity when it delays new generation. For real RL, asynchronous work also needs policy-version and reward bookkeeping. These tests did not update the policy.','',
      '## 3. Gateway and model execution','',
      '| Mean agent component | Docker | E2B |','|---|---:|---:|']
    for key,label in [('model_api_s','Model API wait'),('gateway_client_s','Gateway + local-client overhead'),('codec_s','Codec subset (overlaps overhead)'),('tools_s','Tool calls'),('tool_setup_s','Tool setup/close')]:
        values=[statistics.mean(r[key] for r in cases if r['provider']==p) for p in ['docker','e2b']]
        lines.append(f'| {label} | {values[0]:.2f} s | {values[1]:.2f} s |')
    matching=[r for r in cases if r['server_metric_boundary_matches']]
    queries=sum(r.get('prefix_cache_queried_tokens') or 0 for r in matching);hits=sum(r.get('prefix_cache_hit_tokens') or 0 for r in matching)
    lines += ['',f'Server request-counter boundaries matched captured model calls for **{len(matching)}/24** primary runs. Across those boundaries, prefix-cache hits were **{hits/queries:.1%}** of queried tokens.' if queries else 'Prefix-cache ratio unavailable for the matched metric windows.',
      'Prefix caching reduces repeated prefill, but does not eliminate decoding work over a long context. API wait includes queueing, inference, transport, and response handling; it is not GPU kernel time.','',
      'Gateway plus local-client overhead averaged about 1 second per primary task; trajectory export averaged under 0.04 seconds. Neither was a leading cost at the measured concurrency. Server prefill/decode counters averaged 8.44/80.91 seconds per Docker task and 6.67/63.46 seconds per E2B task, with about 1 ms total queue time per task in the serial runs. Thus decoding dominated serving time in these rollouts, even with high prefix reuse.','',
      '![Serving throughput](figures/serving-throughput.svg)','',
      '![Serving TTFT](figures/serving-ttft.svg)','',
      'The serving study uses 2K/16K/64K input lengths, concurrency 1/4/8, 256 forced output tokens, and three repeats. Inputs come from a real trajectory with a unique first-block salt; corresponding paired batches use identical IDs on both backends. Logprobs and token IDs are enabled. AITER and graph execution change together, so their effects are not isolated.','',
      '| Input tokens | Concurrency | Eager output tok/s | AITER + graphs output tok/s | Ratio | Eager TTFT | AITER TTFT |',
      '|---:|---:|---:|---:|---:|---:|---:|',
      *[f'| {length} | {concurrency} | {serving["eager",length,concurrency]["output_tokens_per_s"]:.1f} | '+
        f'{serving["aiter_graph",length,concurrency]["output_tokens_per_s"]:.1f} | '+
        f'{serving["aiter_graph",length,concurrency]["output_tokens_per_s"]/serving["eager",length,concurrency]["output_tokens_per_s"]:.2f}x | '+
        f'{serving["eager",length,concurrency]["mean_ttft_s"]:.2f} s | {serving["aiter_graph",length,concurrency]["mean_ttft_s"]:.2f} s |'
        for length in [2048,16384,65536] for concurrency in [1,4,8]],
      '', 'The short-context serving gain does not carry over unchanged to long contexts. At 64K inputs and concurrency 8, both backends spend about 49–50 seconds before the first output token, and their aggregate output rates are close. This deliberately prevents prefix reuse; actual multi-turn rollouts above achieved high cache hits. Concurrency should be tuned with context length and KV capacity, not increased without a workload sweep.','',
      '**Action:** keep prefix reuse, constrain unhelpful context growth, and optimize model serving before adding tensor-parallel GPUs. Backend logs confirm AITER MoE and graph execution; some dynamic GEMM shapes use fallback configurations. A dedicated kernel trace would be needed to attribute individual kernel costs.','',
      '### Logprob overhead','',
      'A separate non-streaming study uses a warmed 16K prefix and token IDs in both modes, toggling logprobs only. This measures capture/response cost, not correctness of a training objective.','',
      '| Backend | Concurrency | No logprobs token/s | With logprobs token/s | With / without | Mean response KiB: off / on |','|---|---:|---:|---:|---:|---:|']
    overhead=read(root/'inference-benchmark/logprob-overhead.json')['results']
    for backend in ['eager','aiter_graph']:
        for concurrency in [1,4]:
            values=[statistics.mean(r['output_tokens_per_s'] for r in overhead if r['backend']==backend and r['concurrency']==concurrency and r['logprobs']==enabled) for enabled in [False,True]]
            sizes=[statistics.mean(r['mean_response_bytes'] for r in overhead if r['backend']==backend and r['concurrency']==concurrency and r['logprobs']==enabled)/1024 for enabled in [False,True]]
            lines.append(f'| {backend} | {concurrency} | {values[0]:.1f} | {values[1]:.1f} | {values[1]/values[0]:.2f}× | {sizes[0]:.1f} / {sizes[1]:.1f} |')
    lines+=['','## 4. Verification, startup, and tails','',
      'Warm create/prepare time, verification, cleanup, and trajectory export are recorded separately. E2B templates were built before primary runs; local images were already cached. Eleven newly prepared templates took 29–77 seconds each to become ready; Flask reused the preflight template. These observed readiness times are not a cold-registry build benchmark. Per-template records are in [templates.json](results/templates.json).','',
      'The targeted verifier study repeats official-gold evaluation twice on Django, Pylint, and Requests. Bash reports command wall time plus user/system CPU time; average cores is (user + system CPU seconds) / wall seconds.','',
      '| Task | Provider | Mean verifier command wall | Mean CPU seconds | Mean active cores | Gold passes |','|---|---|---:|---:|---:|---:|']
    probe=read(root/'verifier-cpu-probe.json')
    for iid in sorted({r['instance_id'] for r in probe}):
        for provider in ['docker','e2b']:
            group=[r for r in probe if r['instance_id']==iid and r['provider']==provider];metrics=[m for r in group for m in r['resources']]
            wall=statistics.mean(m['body_wall_s'] for m in metrics) if metrics else None
            cpu=statistics.mean(m['user_cpu_s']+m['system_cpu_s'] for m in metrics) if metrics else None
            cores=statistics.mean(m['average_cpu_cores'] for m in metrics) if metrics else None
            lines.append(f'| {iid} | {provider} | {fmt(wall)} s | {fmt(cpu)} s | {fmt(cores,2)} | {sum(bool(r["resolved"]) for r in group)}/{len(group)} |')
    lines+=['',
      'CPU time close to command wall time suggests a busy core; low CPU time with a long wall time calls for storage, network, subprocess, or scheduling investigation. It does not identify a unique root cause. This runtime includes package installation and tests, not just test assertions.','',
      'In the first Requests repeat, Docker spent 59.36 seconds in the verifier body with only 1.58 CPU seconds; E2B spent 27.51 seconds with 1.28 CPU seconds. Pytest itself reported 58.32 and 26.34 seconds, so most of this delay was inside the test suite rather than package installation. The suite exercises HTTP requests; per-test and network timings would be needed to attribute the wait further. Adding CPU cores alone is not supported as a remedy by these measurements.','',
      '**Action:** prebuild deterministic environments, keep runtime dependencies ready, and size verifier workers independently of model workers. Keep official tests intact. Investigate slow commands rather than treating every delay as model inference.','',
      'Pylint reached 100 actions on both providers. Under synchronous RL collection, such trajectories can hold up the batch after short tasks finish. Use step/token budgets and inspect failed trajectories; evaluate any early-stopping rule against reward quality rather than optimizing latency alone.','',
      '![Representative timeline](figures/django-timeline.svg)','',
      '## Priorities for an agentic RL integration','',
      '| Priority | Change to evaluate | Evidence from this study | Remaining check |',
      '|---|---|---|---|',
      '| 1 | Keep several independent rollouts in flight | Four-task batches raised throughput and GPU activity | Sweep concurrency on diverse tasks; measure memory and tail latency |',
      '| 2 | Reduce remote shell round trips | E2B tool share and fixed-operation timings | Integrate persistent shell with cancellation, timeout, output limits, and full task checks |',
      '| 3 | Control growing contexts and tune serving | Paired 2K/16K/64K measurements | Preserve rewards when trimming observations; inspect kernels if needed |',
      '| 4 | Separate verifier workers and prebuild dependencies | Guest wall/CPU measurements; distinct verification phase | Profile I/O and dependency setup under concurrency |',
      '| 5 | Add learner instrumentation when training is connected | Valid scored trajectories exist | Measure backward, optimizer, weight synchronization, policy staleness, and checkpoint I/O |','',
      '## Measurement boundaries','',
      '- Python spans are buffered in memory and exported after each case. High-level model/tool phases are used for totals; nested codec and HTTP spans are not added again. Instrumentation overhead was not separately calibrated with an uninstrumented run.',
      '- GPU/cgroup/vLLM sampling runs at 1 Hz. GPU values refer to the active serving device (PCI 0000:66:00.0), not the mean across eight installed GPUs. These are sampled activity counters, not a GPU kernel profile.',
      '- E2B and Docker have matching configured CPU/RAM and repository trees, but different kernel, CPU visibility, storage, and network placement. The remote service implementation was not independently identified.',
      '- The primary score uses one attempt per provider and filters recognized test-path changes before fresh verification. Repeated Flask scheduling trials are not included in its 12-task denominator.',
      '- Trajectory checks cover structure and finite logprobs. Backpropagation, optimizer memory, weight synchronization, checkpoint I/O, and algorithm-specific logprob/mask semantics remain unmeasured.',
      '', '## Evidence','',
      '[Case table](results/cases.csv) · [Tool calls](results/tool-calls.csv) · [Serving raw batches](results/inference/raw.json) · [Logprob study](results/inference/logprob-overhead.json) · [Resource probe](results/verifier-cpu-probe.json)',
      '', 'Open [timeline.json](results/timeline.json) in a Chrome-trace-compatible viewer such as Perfetto to inspect model/tool intervals. Full spans and 1 Hz telemetry remain in the original run directory.']
    (target/'PROFILE.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'published':str(target),'counts':counts,'copied_files':len(records)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path)
    p.add_argument('--docs',type=Path,default=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment'))
    args=p.parse_args();main(args.root,args.docs)
