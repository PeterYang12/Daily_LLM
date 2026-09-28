#!/usr/bin/env python3
"""Plot first-batch runner completions against finite captured resource prefixes."""
import argparse
import collections
import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics


def captured_rows(path):
    """Read a finite prefix once; hash and parse exactly its complete JSONL lines."""
    with path.open('rb') as stream:
        available = os.fstat(stream.fileno()).st_size
        data = stream.read(available)
    if len(data) != available:
        raise ValueError(f'Input truncated while capturing {path}')
    boundary = data.rfind(b'\n') + 1
    prefix = data[:boundary]
    provenance = {'path': str(path), 'available_bytes_at_capture': available,
        'captured_prefix_bytes': boundary, 'ignored_incomplete_tail_bytes': available - boundary,
        'ends_on_newline': not prefix or prefix.endswith(b'\n'),
        'captured_complete_lines': prefix.count(b'\n'), 'sha256': hashlib.sha256(prefix).hexdigest()}
    return (json.loads(line) for line in io.BytesIO(prefix) if line.strip()), provenance


def finite_percent(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def analyze(root, output):
    result_root = root / 'results'
    directories = sorted(path for path in (result_root / 'trajectories/step_1').iterdir() if path.is_dir())
    if len(directories) != 256:
        raise ValueError('Expected exactly256 first-step trajectory directories')
    sessions, source_hashes = [], {}
    for directory in directories:
        path = result_root / 'sessions' / directory.name / 'result.json'
        data = path.read_bytes(); row = json.loads(data)
        if row['phase'] != 'train' or row['status'] != 'completed' or row['session_id'] != directory.name:
            raise ValueError('A first-batch episode has not completed or identity differs')
        if not math.isfinite(row['started_unix']) or not math.isfinite(row['elapsed_s']) or row['elapsed_s'] <= 0:
            raise ValueError('Invalid episode timing')
        sessions.append(row); source_hashes[str(path.relative_to(root))] = hashlib.sha256(data).hexdigest()
    groups = collections.defaultdict(list)
    for row in sessions:
        if not row.get('sample_uid'): raise ValueError('Missing prompt-group UID')
        groups[row['sample_uid']].append(row)
    if len(groups) != 32 or any({r['rollout_index'] for r in group} != set(range(8)) or len(group) != 8
                               for group in groups.values()):
        raise ValueError('First batch does not contain exactly32 groups of8 rollouts')
    for group in groups.values():
        identifiers = {row.get('instance_id') for row in group}
        if len(identifiers) != 1 or not next(iter(identifiers)) or next(iter(identifiers)) == '?':
            raise ValueError('A prompt group does not contain eight attempts of the same known instance')
    attempts = {row['supervisor_attempt_id'] for row in sessions}
    if len(attempts) != 1: raise ValueError('First batch spans multiple trainer attempts')
    start = min(row['started_unix'] for row in sessions)
    completions = sorted(row['started_unix'] + row['elapsed_s'] for row in sessions)
    end = completions[-1]
    ranks = {str(percent): math.ceil(256 * percent / 100) for percent in (50, 90, 95, 99, 100)}
    milestones = {percent: completions[rank - 1] - start for percent, rank in ranks.items()}
    tail_start = start + milestones['95']; tail_remaining = 256 - ranks['95']
    host, vm, host_plot, vm_plot = [], [], [], []
    host_counts = {'window_sample_rows': 0, 'invalid_gpu_rows': 0, 'gap_flags': 0, 'line_breaks': 0}
    vm_counts = {'window_sample_rows': 0, 'invalid_cpu_rows': 0, 'line_breaks': 0, 'invalid_or_missing_receipt_timestamps': 0}
    host_rows, host_source = captured_rows(result_root / 'resources-host.jsonl')
    previous = None
    for row in host_rows:
        t = row.get('time_ns', 0) / 1e9
        if row.get('record_type') != 'sample' or not start <= t <= end: continue
        host_counts['window_sample_rows'] += 1
        gap = bool(row.get('sample_gap', False)); host_counts['gap_flags'] += gap
        interval = row.get('interval_seconds', 5)
        interval = float(interval) if type(interval) in (int, float) and interval > 0 else 5.0
        cadence_gap = previous is not None and (t < previous or t - previous > 2 * interval)
        gpus = [gpu.get('gfx_busy_percent') for gpu in row.get('gpus', [])]
        valid = len(gpus) == 8 and all(finite_percent(value) for value in gpus)
        if gap or cadence_gap or not valid:
            host_plot.append({'time': t, 'mean_gpu_busy_pct': math.nan}); host_counts['line_breaks'] += 1
        previous = t
        if not valid:
            host_counts['invalid_gpu_rows'] += 1; continue
        point = {'time': t, 'mean_gpu_busy_pct': statistics.mean(gpus),
                 'low_busy_gpus': sum(value < 5 for value in gpus), 'sample_gap': gap}
        host.append(point); host_plot.append(point)
    vm_rows, vm_source = captured_rows(result_root / 'resources-vm.jsonl')
    previous = None
    for row in vm_rows:
        if row.get('record_type') != 'sample': continue
        try:
            stamp = datetime.datetime.fromisoformat(row['received_utc'])
            if stamp.tzinfo is None: raise ValueError('Receipt timestamp lacks timezone')
            t = stamp.timestamp()
        except (KeyError, TypeError, ValueError):
            vm_counts['invalid_or_missing_receipt_timestamps'] += 1; continue
        if not start <= t <= end: continue
        vm_counts['window_sample_rows'] += 1
        guest = row.get('guest', {}); busy = guest.get('cpu', {}).get('busy_pct')
        # The VM capture has a nominal 5s cadence; receipt delays remain explicit.
        gap = bool(row.get('sample_gap', False)) or (previous is not None and (t < previous or t - previous > 10))
        if gap or not finite_percent(busy):
            vm_plot.append({'time': t, 'cpu_busy_pct': math.nan}); vm_counts['line_breaks'] += 1
        previous = t
        if not finite_percent(busy):
            vm_counts['invalid_cpu_rows'] += 1; continue
        point = {'time': t, 'cpu_busy_pct': busy,
                 'firecracker': guest.get('processes', {}).get('live_process_counts', {}).get('firecracker'),
                 'throttled_s': guest.get('cgroup_summary', {}).get('throttled_seconds_continuous_only')}
        vm.append(point); vm_plot.append(point)
    tail_samples = [row for row in host if row['time'] >= tail_start]
    script = Path(__file__)
    plan_path = root / 'configs/training-plan.json'; plan_data = plan_path.read_bytes()
    summary = {
        'run_id': json.loads(plan_data)['run_id'], 'attempt_id': next(iter(attempts)),
        'associated_training_step': 1, 'optimizer_step_completion': 'not_established_by_this_episode_collection_plot',
        'episodes': 256, 'prompt_groups': 32, 'rollouts_per_prompt': 8,
        'distinct_instance_ids': len({row['instance_id'] for row in sessions}),
        'agent_finished_true': sum(row.get('finished') is True for row in sessions),
        'agent_finished_false': sum(row.get('finished') is False for row in sessions),
        'agent_finished_unknown': sum(row.get('finished') is None for row in sessions),
        'first_episode_start_unix': start, 'last_episode_finish_unix': end,
        'collection_wall_seconds': end - start, 'completion_milestone_seconds': milestones,
        'completion_milestone_ranks': ranks, 'percentile_method': 'nearest rank: ceil(N*p/100)',
        'tail_threshold_rank': ranks['95'], 'tail_remaining_episode_count': tail_remaining,
        'tail_remaining_episode_fraction': tail_remaining / 256,
        'after_p95_wall_seconds': end - tail_start,
        'after_p95_wall_fraction': (end - tail_start) / (end - start),
        'gpu_device_count': 8, 'low_busy_gpu_threshold_pct': 5, 'low_busy_gpu_comparison': 'strictly_less_than',
        'host_samples': len(host), 'host_sample_gap_flags': host_counts['gap_flags'], 'host_quality': host_counts,
        'vm_samples': len(vm), 'vm_quality': vm_counts, 'tail_host_samples': len(tail_samples),
        'tail_mean_low_busy_gpu_count': statistics.mean(row['low_busy_gpus'] for row in tail_samples) if tail_samples else None,
        'tail_mean_gpu_busy_pct': statistics.mean(row['mean_gpu_busy_pct'] for row in tail_samples) if tail_samples else None,
        'resource_source_prefixes': {'host': host_source, 'vm': vm_source},
        'resource_timestamp_filter': {'start_unix': start, 'end_unix': end, 'bounds': 'inclusive',
                                      'host_clock': 'sample time_ns', 'vm_clock': 'host received_utc'},
        'script_source_sha256': hashlib.sha256(script.read_bytes()).hexdigest() if script.is_file() else None,
        'training_plan_sha256': hashlib.sha256(plan_data).hexdigest(),
        'trajectory_directory_names_sha256': hashlib.sha256(json.dumps([path.name for path in directories], separators=(',', ':')).encode()).hexdigest(),
        'scope': [
            'Runner clock: first runner entry to last runner return, including client admission and task work.',
            'Excludes earlier Ray dispatch/import and later gateway finalization, trajectory validation and TransferQueue delivery.',
            'Counts completed runner episodes, including agent finished=false; it does not establish256 usable training trajectories.',
            'This is not a completed optimizer step or the full native step timer (policy computation and checkpointing are separate).',
            'GPU busy is sampled device activity, not model FLOP utilization or proof of a compute bottleneck.',
            'Low-busy GPU count uses GFX activity below 5%; means are across valid observed samples, not time-weighted.',
            'Resource lines break at invalid samples, declared gaps, timestamp regressions and cadence gaps (>10s at nominal 5s).',
            'VM CPU is placed at host receipt time; guest collection precedes receipt and process counts can include other clients.',
            'Shading starts at the nearest-rank 95% milestone (completion 244), leaving 12/256 episodes, not exactly 5%.',
            'No causal attribution or RL speedup claim follows from this timeline alone.',
        ],
        'session_source_sha256': source_hashes,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'first-batch.json').write_text(json.dumps(summary, indent=2) + '\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(10, 7), sharex=True, constrained_layout=True)
    minutes = lambda times: [(t - start) / 60 for t in times]
    axes[0].step([0] + minutes(completions), [0] + list(range(1, 257)), where='post', color='#245a81')
    axes[0].axhline(256, color='#999999', linewidth=.7)
    axes[0].set(ylabel='Completed runner episodes', ylim=(0, 270), title='First RL batch: episode collection and resource activity (32 prompts × 8)')
    if host_plot: axes[1].plot(minutes([r['time'] for r in host_plot]), [r['mean_gpu_busy_pct'] for r in host_plot], color='#a55b29')
    axes[1].set(ylabel='Mean GPU busy (%)', ylim=(0, 105))
    if vm_plot: axes[2].plot(minutes([r['time'] for r in vm_plot]), [r['cpu_busy_pct'] for r in vm_plot], color='#38734b')
    axes[2].set(ylabel='VM CPU (%)\n(host receipt time)', xlabel='Minutes since first training runner entered', ylim=(0, 105))
    for axis in axes:
        axis.axvspan((tail_start - start) / 60, (end - start) / 60, color='#e3ba83', alpha=.25)
        axis.grid(alpha=.2)
    axes[0].text(.02, .85, f'Shaded: after 95% milestone, last {tail_remaining}/256 episodes', transform=axes[0].transAxes, fontsize=9)
    fig.savefig(output / 'first-batch-timeline.png', dpi=180)
    plt.close(fig)
    return {key: value for key, value in summary.items() if key != 'session_source_sha256'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root.resolve(), args.output.resolve()), indent=2))
