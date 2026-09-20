import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

from runtime_common import ROOT, save


def json_lines(path):
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def quantile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[round((len(values)-1)*fraction)]


def main():
    launch = json.loads((ROOT/'results/launch.json').read_text())
    ended = json.loads((ROOT/'results/main-exit.json').read_text())
    metrics = json_lines(ROOT/'results/train-metrics.jsonl')
    assert ended.get('exit_code') == 0, ended
    assert [r['step'] for r in metrics] == [1, 2, 3], [r.get('step') for r in metrics]
    start = launch['started_unix']
    telemetry = [r for r in json_lines(ROOT/'results/telemetry.jsonl')
                 if start <= r['time_unix'] <= ended['ended_unix']]
    events = [r for r in json_lines(ROOT/'results/sandbox-events.jsonl')
              if r['time_unix'] >= start and r.get('phase') == 'train']
    episodes = []
    for path in (ROOT/'results/sessions').glob('*/result.json'):
        row = json.loads(path.read_text())
        if row.get('started_unix', 0) >= start:
            episodes.append(row)
    optimizer = {}
    for path in sorted((ROOT/'results/optimizer').glob('rank-*.jsonl')):
        rows = [r for r in json_lines(path) if r['time_unix'] >= start]
        optimizer[path.stem] = rows
    assert len(optimizer) == 8
    for rank, rows in optimizer.items():
        assert [r['optimizer_update'] for r in rows] == [1, 2, 3], (rank, rows)
        assert all(r['successful'] and r['grad_norm'] is not None and math.isfinite(r['grad_norm']) for r in rows)
    checkpoint = ROOT/'checkpoints/global_step_3'
    checkpoint_files = list(checkpoint.rglob('*'))
    assert any(p.is_file() and p.stat().st_size > 1024*1024 for p in checkpoint_files)
    cleanup = json.loads((ROOT/'results/cleanup.json').read_text())
    assert not cleanup['remaining'] and not cleanup['errors'], cleanup
    verification = json.loads((ROOT/'results/checkpoint-verification.json').read_text())
    assert verification['success'] and verification['fresh_vllm_load'], verification
    audit = json.loads((ROOT/'results/trajectory-audit.json').read_text())
    assert not audit['errors'] and not audit['pending'], audit
    maximum_active = max((r.get('active_training_sandboxes', 0) for r in telemetry), default=0)
    assert maximum_active <= 128, maximum_active
    timings = {}
    for kind in ['created', 'tools_provisioned', 'exec', 'destroyed']:
        times = [r['seconds'] for r in events if r['event'] == kind and 'seconds' in r]
        timings[kind] = {'count': len(times), 'mean_s': statistics.mean(times) if times else None,
                         'p50_s': quantile(times, .5), 'p95_s': quantile(times, .95),
                         'max_s': max(times) if times else None}
    steps = []
    for row in metrics:
        d = row['data']
        steps.append({'step': row['step'], 'reward_mean': d.get('critic/rewards/mean', d.get('critic/score/mean')),
                      'policy_loss': d.get('actor/pg_loss'), 'gradient_norm': d.get('actor/grad_norm'),
                      'step_s': d.get('timing_s/step'), 'rollout_s': d.get('timing_s/gen'),
                      'update_actor_s': d.get('timing_s/update_actor'),
                      'weight_sync_s': d.get('timing_s/update_weights')})
    summary = {'status': 'completed', 'steps': steps, 'attempt': launch['attempt'],
               'elapsed_s': ended['ended_unix']-start, 'maximum_observed_training_sandboxes': maximum_active,
               'completed_episode_results': len(episodes),
               'resolved_episode_results': sum(r.get('reward', 0) == 1 for r in episodes),
               'optimizer': optimizer, 'sandbox_timings': timings,
               'sandbox_error_events': [r for r in events if 'error' in r['event']],
               'checkpoint': str(checkpoint), 'checkpoint_files': len([p for p in checkpoint_files if p.is_file()]),
               'checkpoint_bytes': sum(p.stat().st_size for p in checkpoint_files if p.is_file()),
               'cleanup_remaining': cleanup['remaining'],
               'checkpoint_verification': verification, 'trajectory_audit_count': len(audit['rows'])}
    save(ROOT/'results/final-summary.json', summary)
    with (ROOT/'results/steps.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(steps[0])); writer.writeheader(); writer.writerows(steps)
    manifest = {}
    for folder in ['code', 'configs']:
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and '__pycache__' not in str(path):
                manifest[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(ROOT/'results/run-file-hashes.json', manifest)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figures = ROOT/'figures'; figures.mkdir(exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ts = [(r['time_unix']-start)/60 for r in telemetry]
    axes[0].plot(ts, [r.get('active_training_sandboxes', float('nan')) for r in telemetry])
    axes[0].axhline(128, color='gray', linestyle='--', linewidth=1)
    axes[0].set_ylabel('Live E2B sandboxes'); axes[0].set_ylim(bottom=0)
    gpu_average = []
    for row in telemetry:
        values = [float(v['GPU use (%)']) for v in row.get('gpu', {}).values()
                  if isinstance(v, dict) and 'GPU use (%)' in v]
        gpu_average.append(statistics.mean(values) if values else float('nan'))
    axes[1].plot(ts, gpu_average, color='tab:orange')
    axes[1].set_ylabel('Mean GPU utilization (%)'); axes[1].set_xlabel('Minutes from successful launch')
    axes[1].set_ylim(0, 105)
    for row in optimizer.get('rank-0', []):
        at = (row['time_unix']-start)/60
        for ax in axes:
            ax.axvline(at, color='tab:green', alpha=.5, linestyle=':')
        axes[0].annotate('Update '+str(row['optimizer_update']), (at, 128),
                         xytext=(3, -12), textcoords='offset points', fontsize=8)
    for ax in axes: ax.grid(alpha=.25)
    fig.tight_layout(); fig.savefig(figures/'runtime.png', dpi=180); fig.savefig(figures/'runtime.svg'); plt.close(fig)
    table = '\n'.join(f"| {s['step']} | {s['reward_mean']} | {s['gradient_norm']} | {s['step_s']} |" for s in steps)
    report = f'''Three-step Uni-Agent ReAct RL smoke run completed.

The successful attempt completed three trainer steps using Qwen/Qwen3-Coder-30B-A3B-Instruct, colocate_async, eight AMD MI350X GPUs, prompt batch 16 and eight rollouts per prompt. The Agent budget was 131,072 tokens and 200 turns. R3 Router Replay and GSPO were enabled. This run validates execution and parameter updates; it does not establish a benchmark improvement.

| Step | Mean training reward | Gradient norm | Step seconds |
|---|---:|---:|---:|
{table}

The maximum sampled live training-sandbox count was {maximum_active}. A separate acceptance test verified 128 simultaneously live environments, 128 successful command checks and zero leftover instances. The final training cleanup also found zero remaining run-owned sandboxes. Sandbox CPU/memory/disk metric queries returned no usable samples; the chart therefore shows live-instance counts and measured GPU utilization, not E2B node resource usage.

![Sandbox concurrency and GPU utilization](figures/runtime.png)

Data and implementation details:

- All 1,150 official SWE-reBench rows were retained. For this bounded run, previously qualified ready-template tasks were ordered first, shuffle was disabled, and the first 48 tasks were used. The 500 Verified rows were checked for overlap with those tasks and were not used for training or evaluation in this smoke run.
- Fresh E2B image imports failed during server-side environment initialization. Existing templates were reused; the build failures and the import-only probe are retained under results/. This server issue remains unresolved.
- Async warmup prefetch was disabled. A 48-prompt submission guard stops unexpected failure-driven refills rather than silently expanding the task pool.
- The E2B API permits a maximum one-hour requested lifetime, so instances used a 3,600-second lease. The user-reported E2B node has 192 CPUs, 1 TB RAM and quota 512.
- Required tmux packages were cached from Debian and installed into each fresh task sandbox. Task code and tests were not altered by that provisioning.
- ROCm adaptations include actor TP1/PP1/CP4/EP8, rollout TP2 (four replicas), eager inference, chunked prefill batching and compatible kernels. Full resolved settings and hashes are included.
- Attempt 1 stopped at wrapper initialization. Attempt 2 initialized models but its sandbox creates were rejected for requesting a two-hour lease; no optimizer step completed. Later unmapped-task errors were secondary to failed-group refill. The successful attempt is number {launch['attempt']}.

Checkpoint: `{checkpoint}`. The checkpoint contains {summary['checkpoint_files']} files totaling {summary['checkpoint_bytes']} bytes. Its exported HF weights were compared with the base model and loaded in a fresh vLLM process; the generation check returned nonempty output and finite logprobs. Optimizer evidence for all eight ranks is in results/optimizer/; metrics are in results/train-metrics.jsonl; structured results are in results/final-summary.json. Inspect sample parameter changes together with gradient norms because weight decay alone can change parameters even when policy advantages are zero. Sandbox creation timing includes admission-queue waiting; RPC execution timing starts after its separate admission gate.

Reproduction entry: `bash code/launch.sh`. Inspect `configs/train.yaml`, `configs/task_config.yaml`, and the scoped E2B credential path before running elsewhere. Credentials are deliberately excluded from this report and the file-hash manifest.
'''
    (ROOT/'README.md').write_text(report)
    state = json.loads((ROOT/'state.json').read_text())
    state.update(status='completed', completed_steps=3, final_checkpoint=str(checkpoint))
    save(ROOT/'state.json', state)
    print(json.dumps({k: summary[k] for k in ['status', 'attempt', 'elapsed_s',
          'maximum_observed_training_sandboxes', 'completed_episode_results', 'checkpoint']}))


if __name__ == '__main__':
    main()
