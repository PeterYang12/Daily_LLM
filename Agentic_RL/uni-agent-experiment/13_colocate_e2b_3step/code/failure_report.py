import collections
import datetime
import difflib
import hashlib
import json
import re
import shutil
import statistics
from pathlib import Path

from runtime_common import ROOT, save


def lines(path):
    result = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return result


def main():
    launch = json.loads((ROOT/'results/launch.json').read_text())
    state = json.loads((ROOT/'state.json').read_text())
    start, end = launch['started_unix'], state['exited_unix']
    metrics = lines(ROOT/'results/train-metrics.jsonl')
    optimizer = {p.stem: lines(p) for p in (ROOT/'results/optimizer').glob('rank-*.jsonl')}
    assert [m['step'] for m in metrics] == [1]
    assert len(optimizer) == 8 and all(len(v) == 1 and v[0]['successful'] for v in optimizer.values())
    episodes = [json.loads(p.read_text()) for p in (ROOT/'results/sessions').glob('*/result.json')]
    episodes = [e for e in episodes if e['started_unix'] >= start]
    audit = json.loads((ROOT/'results/trajectory-audit.json').read_text())
    assert len(episodes) == 256 and not audit['errors'] and not audit['pending']
    by_step = {}
    for step in [1,2]:
        selected = [e for e in episodes if (ROOT/f'results/trajectories/step_{step}'/e['session_id']).exists()]
        by_step[step] = {'rollouts': len(selected), 'resolved': sum(e['reward'] == 1 for e in selected),
                         'finished': sum(bool(e['finished']) for e in selected)}
    telemetry = [r for r in lines(ROOT/'results/telemetry.jsonl') if start <= r['time_unix'] <= end]
    events = [r for r in lines(ROOT/'results/sandbox-events.jsonl')
              if start <= r['time_unix'] <= end and r.get('phase') == 'train']
    max_active = max(r.get('active_training_sandboxes',0) for r in telemetry)
    cleanup = json.loads((ROOT/'results/cleanup.json').read_text())
    assert not cleanup['remaining'] and not cleanup['errors']
    checkpoint_files = [p for p in (ROOT/'checkpoints').rglob('*') if p.is_file()]
    assert not checkpoint_files
    kernel = (ROOT/'results/kernel-fault.log').read_text().splitlines()
    facts = [s for s in kernel if any(x in s.lower() for x in
             ['new correctable hardware errors', 'uncorrectable hardware error', 'gpu reset begin',
              'vram is lost', 'gpu reset(38) succeeded', 'wedged'])]
    (ROOT/'results/kernel-fault-summary.txt').write_text('\n'.join(facts)+'\n')
    train_log = (ROOT/launch['log']).read_text(errors='replace').splitlines()
    first = next(i for i,s in enumerate(train_log) if 'HW Exception by GPU' in s)
    (ROOT/'results/process-fault-excerpt.txt').write_text('\n'.join(train_log[max(0,first-5):first+18])+'\n')
    ranks = [v[0] for v in optimizer.values()]
    timings = {}
    for kind in ['created', 'tools_provisioned', 'exec', 'destroyed']:
        values = sorted(e['seconds'] for e in events if e['event'] == kind and 'seconds' in e)
        timings[kind] = {'count': len(values), 'mean_s': statistics.mean(values) if values else None,
                         'p95_s': values[round((len(values)-1)*.95)] if values else None}
    summary = {'status': 'stopped_gpu_fault', 'target_steps': 3, 'completed_steps': 1,
               'interrupted_step': 2, 'step3_started': False, 'exit_code': state['exit_code'],
               'attempt': launch['attempt'], 'launch_unix': start, 'exit_unix': end,
               'rollout_batches': by_step, 'audited_trajectories': len(audit['rows']), 'trajectory_errors': len(audit['errors']),
               'step1_metrics': metrics[0]['data'], 'optimizer': optimizer,
               'sampled_changed': sum(r['sampled_changed'] for r in ranks),
               'sampled_parameters': sum(r['sampled_parameters'] for r in ranks),
               'max_sampled_delta': max(r['sampled_max_delta'] for r in ranks),
               'max_observed_training_sandboxes': max_active, 'sandbox_timings': timings,
               'sandbox_error_events': [e for e in events if 'error' in e['event']],
               'remaining_sandboxes': cleanup['remaining'], 'checkpoint_files': [],
               'failure': {'uncorrectable_event': 'ERREVENT_ATHUB_INTERRUPT', 'device': '0000:66:00.0',
                           'correctable_xgmi_device': '0000:96:00.0', 'driver_reset': 'automatic, all eight GPUs',
                           'manual_reset_performed': False,
                           'root_cause_limit': 'Driver-reported hardware events; component/firmware/driver root cause not established.'}}
    save(ROOT/'results/failure-summary.json', summary)
    prior_exit = ROOT/'results/main-exit.json'
    if prior_exit.exists():
        shutil.copy2(prior_exit, ROOT/'results/attempt-3-preabort-main-exit.json')
    save(prior_exit, {'status': 'failed', 'exit_code': state['exit_code'], 'started_unix': start,
                     'ended_unix': end, 'error_type': 'ROCm GPU Hang / uncorrectable ATHUB event',
                     'completed_steps': 1, 'evidence': 'kernel-fault.log and train-attempt-3.log'})
    state.update(status='stopped_gpu_fault', completed_steps=1, checkpoint_available=False,
                 automatic_restart=False, failure_summary='results/failure-summary.json')
    save(ROOT/'state.json', state)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figures=ROOT/'figures'; figures.mkdir(exist_ok=True)
    ts=[(r['time_unix']-start)/60 for r in telemetry]
    fig,axs=plt.subplots(2,1,figsize=(10,6),sharex=True)
    axs[0].plot(ts,[r.get('active_training_sandboxes',float('nan')) for r in telemetry],color='tab:blue')
    axs[0].axhline(128,color='gray',linestyle='--',linewidth=1);axs[0].set_ylabel('Live E2B sandboxes');axs[0].set_ylim(bottom=0)
    gpu=[]
    for row in telemetry:
        vals=[float(x['GPU use (%)']) for x in row.get('gpu',{}).values() if isinstance(x,dict) and 'GPU use (%)' in x]
        gpu.append(statistics.mean(vals) if vals else float('nan'))
    axs[1].plot(ts,gpu,color='tab:orange');axs[1].set_ylabel('Mean GPU utilization (%)');axs[1].set_ylim(0,105)
    axs[1].set_xlabel('Minutes from attempt 3 launch')
    update_time=(ranks[0]['time_unix']-start)/60
    for ax in axs:
        ax.axvline(update_time,color='tab:green',linestyle=':',label='Update 1 completed')
        ax.axvline((end-start)/60,color='tab:red',linestyle='--',label='GPU fault / abort');ax.grid(alpha=.25)
    axs[0].legend(loc='upper right',fontsize=8)
    fig.tight_layout();fig.savefig(figures/'runtime.png',dpi=180);fig.savefig(figures/'runtime.svg');plt.close(fig)
    d=metrics[0]['data']
    report=f'''The three-step run was interrupted during step 2 by a driver-reported GPU hardware error. **One of three training steps completed. No trained checkpoint was saved.**

The experiment used Qwen/Qwen3-Coder-30B-A3B-Instruct on eight AMD MI350X GPUs, Uni-Agent ReAct, colocate_async, GSPO with R3 Router Replay, prompt batch 16, rollout n=8, an episode concurrency cap of 128, a 131,072-token Agent budget and up to 200 turns. The user's E2B endpoint was http://10.145.64.67:8088 (user-reported 192 CPUs, 1 TB RAM and quota 512).

| Training step | Completed rollouts | Resolved rollouts | Optimizer update |
|---|---:|---:|---|
| 1 | {by_step[1]['rollouts']} | {by_step[1]['resolved']} | Completed on all eight ranks |
| 2 | {by_step[2]['rollouts']} | {by_step[2]['resolved']} | Interrupted before completion |
| 3 | 0 | — | Not started |

Step 1 had mean reward **{d['critic/rewards/mean']:.6f}**, policy loss **{d['actor/pg_loss']:.6f}** and gradient norm **{d['actor/grad_norm']:.6f}**. All eight ranks reported a successful optimizer update. Parameter sampling detected **{summary['sampled_changed']}/{summary['sampled_parameters']} changed elements**, with maximum sampled absolute change {summary['max_sampled_delta']}. New weights were synchronized and used for the second rollout batch. This proves an actual update occurred; it does not establish a benchmark improvement, and the different batches are not a controlled evaluation comparison.

Step 1 took {d['timing_s/step']/60:.2f} minutes: rollout collection {d['timing_s/gen']/60:.2f} minutes, actor update {d['timing_s/update_actor']/60:.2f} minutes. All **256 completed trajectories** passed checks for token/mask/logprob alignment, finite logprobs and reward agreement.

![Observed runtime and GPU fault](figures/runtime.png)

The separate capacity test verified **128 simultaneously live SWE sandboxes**, 128 successful command checks and zero residual instances. During training the maximum ten-second sampled live count was **{max_active}**. All 256 training sandboxes were cleaned; the post-failure check also found **zero remaining run-owned sandboxes**. Sandbox resource-metric queries returned no usable CPU/memory/disk samples, so no whole-node utilization claim is made. Creation timing in the raw results includes admission waiting; execution timing excludes waiting at the RPC gate.

The failure occurred at approximately **2026-09-20 09:52:53 UTC**. The kernel reported an **uncorrectable ERREVENT_ATHUB_INTERRUPT on PCI 0000:66:00.0**, alongside new correctable xgmi_wafl errors on **0000:96:00.0**. ROCm then reported GPU Hang and aborted the processes. The driver automatically reset all eight GPUs and explicitly reported lost VRAM contents. No manual GPU reset, host reboot or driver change was performed. These logs identify a hardware/driver-level failure; they do not by themselves identify which physical component or firmware/driver defect requires repair.

Checkpoint saving was configured for step 3. Because the process died before that point and the driver lost VRAM, the step-1 weights are **not available as a checkpoint**. A later attempt must restart from the original model, after the machine's GPU/interconnect problem has been addressed. The old 300-step experiment and its checkpoint 100 were not resumed or modified.

Recorded preparation and adaptations:

- All 1,150 official SWE-reBench rows were retained. Fresh E2B image-template builds failed server-side, including a minimal import-only probe. For the bounded smoke run, ready-template tasks were ordered first without model-success filtering; the first 48 training tasks were prepared, of which 32 were actually used across two rollout batches. The planned 48 tasks had no instance-ID overlap with the 500 Verified tasks.
- Async warmup prefetch was disabled, and a 48-prompt guard prevents failure-driven expansion into unprepared tasks. Each rollout uses one independent sandbox for agent execution and final scoring.
- Existing task templates lacked tmux. Verified Debian packages were cached and installed into each fresh sandbox without modifying task source or grading rules.
- The E2B API enforces a one-hour requested lifetime. The runtime therefore uses 3,600 seconds, while retaining the 128K/200-turn Agent budget.
- ROCm settings include TP1/PP1/CP4/EP8 for the actor, TP2 for four rollout replicas, eager inference, chunked prefill, compatible kernels and expandable_segments=False. Resolved configuration and immutable image information are retained.
- Attempt 1 stopped at a wrapper initialization error. Attempt 2 initialized the models but its two-hour sandbox requests were rejected; failed-group refills then reached unmapped tasks. This was corrected and checked with the complete Task configuration before attempt 3.
- Before attempt 3, eight-GPU numerical/collective tests, CP4/EP8 topology tests, Ray GPU mapping, 128-sandbox acceptance and all 48 base/reference task checks passed. Passing a short preflight did not guarantee sustained stability under this workload.

Evidence: [fault summary](results/failure-summary.json), [kernel event summary](results/kernel-fault-summary.txt), [full kernel window](results/kernel-fault.log), [optimizer evidence](results/optimizer), [training metrics](results/train-metrics.jsonl), [trajectory audit](results/trajectory-audit.json), [capacity test](results/capacity-128.json), and [configuration](configs/train.yaml). Raw full logs and trajectories remain in `{ROOT}` inside the container. This report excludes credentials, weights and installed environments.
'''
    (ROOT/'README.md').write_text(report)
    original=Path('/work/uni-agent')
    patch=[]
    for rel in ['uni_agent/sandbox/e2b.py','verl/verl/trainer/ppo/v1/trainer_base.py',
                'verl/verl/workers/engine/megatron/transformer_impl.py']:
        a=(original/rel).read_text().splitlines(True);b=(ROOT/'src/uni-agent'/rel).read_text().splitlines(True)
        patch.extend(difflib.unified_diff(a,b,fromfile='a/'+rel,tofile='b/'+rel))
    (ROOT/'results/local-source-changes.patch').write_text(''.join(patch))
    print(json.dumps({'completed_steps':1,'rollout_batches':by_step,'max_active':max_active,
                      'checkpoint_available':False,'remaining_sandboxes':cleanup['remaining']}))


if __name__=='__main__':
    main()
