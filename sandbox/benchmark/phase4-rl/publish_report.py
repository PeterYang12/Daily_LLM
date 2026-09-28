#!/usr/bin/env python3
"""Finite CPU-only report publisher. Reads structured evidence; never starts training or contacts W&B."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import time
import uuid

DEFAULT_ROOT = Path('/data2/yuhanya/e2b-rl-pathfinding-20260920')
DEFAULT_BENCHMARK = Path(__file__).resolve().parents[1]
ACTIVE_STATES = {'checking', 'acceptance_running', 'acceptance_validating', 'resume_running',
                 'running', 'stop_pending', 'final_validating'}
STATES = ACTIVE_STATES | {'created','completed','paused','failed','blocked','interrupted'}
TIMERS = {'step_s': 'timing_s/step', 'gen_s': 'timing_s/gen', 'actor_update_s': 'timing_s/update_actor',
          'weight_sync_s': 'timing_s/update_weights', 'checkpoint_s': 'timing_s/save_checkpoint'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def count(value, fallback=None):
    return int(value) if finite(value) and value >= 0 and int(value) == value else fallback


def fmt(value, digits=2):
    return f'{value:,.{digits}f}' if finite(value) else '—'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o644):
    """Explicit fchmod makes public files readable even under the supervisor's umask 077."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode() if isinstance(data, str) else data
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


class Evidence:
    def __init__(self):
        self.cache = {}; self.sources = {}; self.issues = []

    def begin(self):
        self.sources = {}; self.issues = []

    def read(self, path, projection='json', required=False, limit=64*2**20):
        path = Path(path)
        try:
            stat = path.stat(); identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns, projection)
            cached = self.cache.get(str(path))
            if cached and cached[0] == identity:
                self.sources[str(path)] = cached[2]
                return cached[1]
            if stat.st_size > limit:
                self.issues.append({'type': 'input_size_limit', 'path': str(path), 'bytes': stat.st_size})
                return None
            raw = path.read_bytes()
            source = {'path': str(path), 'bytes_read': len(raw), 'sha256': digest(raw),
                      'projection': projection, 'captured_utc': utc()}
            self.sources[str(path)] = source
            if projection == 'json':
                value = json.loads(raw)
            elif projection == 'hash_only':
                value = {'sha256':source['sha256'],'bytes':len(raw)}
            elif projection == 'session':
                row=json.loads(raw)
                value={key:row.get(key) for key in ('phase','global_step','status','instance_id','session_id','supervisor_attempt_id')}
            elif projection in ('metrics', 'evaluation'):
                value = []
                for number, line in enumerate(raw.splitlines(), 1):
                    if not line.strip(): continue
                    try: row = json.loads(line)
                    except ValueError:
                        self.issues.append({'type': 'invalid_jsonl_row', 'path': str(path), 'line': number})
                        continue
                    if projection == 'evaluation':
                        value.append({key: row.get(key) for key in ('step', 'score', 'uid')})
                    else:
                        data = row.get('data', row.get('metrics', {}))
                        step = data.get('training/global_step', row.get('global_step', row.get('step')))
                        if count(step) is None or 'timing_s/step' not in data: continue
                        selected = {'step': int(step)}
                        selected.update({column: data.get(key) if finite(data.get(key)) else None for column,key in TIMERS.items()})
                        selected['reward_mean'] = data.get('critic/rewards/mean') if finite(data.get('critic/rewards/mean')) else None
                        value.append(selected)
            else:
                raise ValueError('Unknown evidence projection')
            self.cache[str(path)] = (identity, value, source)
            return value
        except FileNotFoundError:
            if required: self.issues.append({'type': 'missing_input', 'path': str(path)})
        except (OSError, ValueError, TypeError) as exc:
            self.issues.append({'type': type(exc).__name__, 'path': str(path)})
        return None


def owned_path(root, value, directory):
    if not isinstance(value, str): return None
    path = Path(value).resolve()
    return path if path.is_relative_to((root/directory).resolve()) else None


def supervisor_liveness(status):
    if status.get('state') not in ACTIVE_STATES: return 'terminal_or_not_started'
    try:
        if status.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return 'different_host_boot'
        fields = Path('/proc', str(int(status['supervisor_pid'])), 'stat').read_text().rsplit(')',1)[1].split()
        if fields[19] != str(status['supervisor_start_ticks']) or fields[0] in ('Z','X'):
            return 'supervisor_not_live'
    except (OSError, KeyError, ValueError, IndexError):
        return 'supervisor_liveness_unavailable'
    heartbeat = status.get('heartbeat_unix')
    if finite(heartbeat) and time.time()-heartbeat > 120:
        return 'heartbeat_stale'
    return 'live'


def pretraining_record(root, label, relative, review, evidence):
    """Prefer local results; otherwise use only the hash-checked operator-reviewed copy."""
    local=evidence.read(root/relative)
    if local is not None:return local,root,False
    entry=(review.get('evidence') or {}).get(label) or {}
    path=owned_path(root,entry.get('path'),'results/pretraining-review')
    if entry.get('review_status')!='accepted' or path is None:return {},None,False
    value=evidence.read(path)
    source=evidence.sources.get(str(path),{})
    if value is None or source.get('sha256')!=entry.get('sha256'):
        evidence.issues.append({'type':'pretraining_review_hash_mismatch','label':label,'path':str(path)})
        return {},None,False
    original=entry.get('original_root')
    return value,Path(original).resolve() if isinstance(original,str) else None,True


def previous_attempts(root, benchmark, evidence):
    data=evidence.read(root/'configs/previous-attempts.json') or {}
    result=[]
    for row in data.get('previous_attempts',[]):
        run_id=row.get('run_id','')
        if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',run_id):continue
        item={'run_id':run_id,'steps':count(row.get('native_steps_completed')),'checkpoint':count(row.get('checkpoint')),
              'at_stall':count(row.get('training_results_at_stall')),'final_results':count(row.get('training_results_final')),
              'startup_cancellations':count(row.get('startup_create_cancellations')),
              'exec_wait_cancellations':count(row.get('cancelled_exec_gate_waits')),
              'cleanup_remaining':count(row.get('cleanup_remaining'))}
        for key in ('public_report','fix_report'):
            path=Path(row[key]).resolve() if isinstance(row.get(key),str) else None
            item[key]=path if path and path.is_relative_to(benchmark.resolve()) and path.is_file() else None
        result.append(item)
    return result


def accepted_baseline(root, run_id, evidence):
    """Retain accepted run history across supervisor sessions using the saved evidence."""
    accepted=[]
    for path in sorted((root/'results/operator').glob('checkpoint1-prefix-*.json')):
        audit=evidence.read(path) or {}
        if audit.get('checkpoint1_prefix_valid') is not True or audit.get('run_id')!=run_id:continue
        owner=audit.get('baseline_attempt_id');proofs=audit.get('saved_rank_proofs',[])
        valid=(isinstance(owner,str) and bool(owner) and len(proofs)==8 and
               {row.get('rank') for row in proofs}==set(range(8)))
        for entry in proofs if valid else []:
            rank=entry['rank'];relative=f'results/reload-proof/save/rank-{rank}.json'
            record=entry.get('file',{});saved_path=root/relative;saved=evidence.read(saved_path) or {}
            captured=evidence.sources.get(str(saved_path),{})
            valid=valid and (record.get('path')==relative and captured.get('sha256')==record.get('sha256') and
                captured.get('bytes_read')==record.get('bytes') and saved.get('phase')=='save' and
                saved.get('global_step')==1 and saved.get('rank')==rank and saved.get('supervisor_attempt_id')==owner)
        if not valid:
            evidence.issues.append({'type':'accepted_baseline_saved_proof_mismatch','path':str(path)})
            continue
        evaluation=next((row for row in audit.get('evaluations',[]) if row.get('global_step')==0),{})
        sessions=[row for row in audit.get('immutable_evidence',[])
                  if isinstance(row.get('path'),str) and row['path'].startswith('results/sessions/') and row['path'].endswith('/result.json')]
        dump_path=root/'results/validation/0.jsonl';evidence.read(dump_path,'evaluation')
        eval_valid=(evaluation.get('task_session_reward_join')=='passed' and evaluation.get('tasks')==16 and
                    count(evaluation.get('resolved')) is not None and 0<=evaluation['resolved']<=16 and
                    evidence.sources.get(str(dump_path),{}).get('sha256')==evaluation.get('sha256') and
                    len(sessions)==16 and len({row['path'] for row in sessions})==16)
        for entry in sessions if eval_valid else []:
            session_path=(root/entry['path']).resolve()
            if not session_path.is_relative_to(root/'results/sessions'):
                eval_valid=False;break
            captured=evidence.read(session_path,'hash_only') or {}
            eval_valid=eval_valid and captured.get('sha256')==entry.get('sha256') and captured.get('bytes')==entry.get('bytes')
        if not eval_valid:
            evidence.issues.append({'type':'accepted_baseline_evaluation_evidence_mismatch','path':str(path)})
        accepted.append({'attempt_id':owner,'audit_path':str(path),'audit_sha256':evidence.sources[str(path)]['sha256'],
                         'evaluation0_verified':eval_valid,'evaluation0_resolved':evaluation.get('resolved') if eval_valid else None})
    if len(accepted)>1:
        evidence.issues.append({'type':'ambiguous_accepted_baseline','count':len(accepted)})
        return None
    return accepted[0] if accepted else None


def first_batch_record(benchmark, run_id, owners, evidence):
    directory=benchmark/'phase4-rl/results/first-batch-r2'
    path=directory/'first-batch.json';row=evidence.read(path)
    if not row or row.get('run_id')!=run_id:return None
    collection=row.get('collection_wall_seconds');tail=row.get('after_p95_wall_seconds')
    fraction=row.get('after_p95_wall_fraction');low_busy=row.get('tail_mean_low_busy_gpu_count')
    valid=(row.get('attempt_id') in owners and row.get('associated_training_step')==1 and
        row.get('episodes')==256 and row.get('prompt_groups')==32 and row.get('rollouts_per_prompt')==8 and
        row.get('tail_threshold_rank')==244 and row.get('tail_remaining_episode_count')==12 and
        finite(collection) and collection>0 and finite(tail) and 0<=tail<=collection and
        finite(fraction) and math.isclose(fraction,tail/collection,rel_tol=1e-6) and
        finite(low_busy) and 0<=low_busy<=8 and count(row.get('tail_host_samples'),0)>0 and
        row.get('low_busy_gpu_threshold_pct')==5 and row.get('gpu_device_count')==8)
    if not valid:
        evidence.issues.append({'type':'first_batch_scope_or_scalar_mismatch','path':str(path)})
        return None
    image=directory/'first-batch-timeline.png'
    if evidence.read(image,'hash_only',limit=8*2**20) is None:image=None
    diagnosis=benchmark/'phase4-rl/ATTEMPT-2-DIAGNOSTICS.md'
    if evidence.read(diagnosis,'hash_only',limit=2**20) is None:diagnosis=None
    return {'run_id':run_id,'attempt_id':row['attempt_id'],'collection_s':collection,
            'after_p95_s':tail,'after_p95_fraction':fraction,'tail_low_busy_gpus':low_busy,
            'tail_samples':row['tail_host_samples'],'data':path,'image':image,'diagnosis':diagnosis}


def wandb_link(root, run_id, status, evidence):
    """Only publish a configured-run URL after native init evidence; never read credential files."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', run_id): return {'state': 'pending'}
    cache = root/'results/report-publisher/wandb-initialized.json'
    prior = evidence.read(cache)
    pattern = re.compile(r'https://wandb\.ai/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/runs/'+re.escape(run_id)+r'(?![A-Za-z0-9_.-])')
    if prior and prior.get('run_id') == run_id and pattern.fullmatch(prior.get('url','')):
        return {'state': 'initialized', 'url': prior['url'], 'evidence': prior.get('evidence')}
    structured = evidence.read(root/'results/wandb-initialized.json')
    if structured and structured.get('initialized') is True and structured.get('run_id') == run_id:
        entity, project = structured.get('entity',''), structured.get('project','')
        if all(re.fullmatch(r'[A-Za-z0-9_.-]+', v) for v in (entity,project)):
            return {'state':'initialized','url':f'https://wandb.ai/{entity}/{project}/runs/{run_id}',
                    'evidence':'structured initialization record'}
    # A native local W&B run artifact must exist as well as its explicit View-run URL.
    if not any((root/'results/wandb').glob('**/run-'+run_id+'.wandb')):
        return {'state':'pending'}
    attempts = status.get('attempts') or []
    for attempt in reversed(attempts):
        directory = owned_path(root, attempt.get('directory'), 'results/supervision')
        if directory is None: continue
        for path in sorted(directory.glob('trainer-*.log'))[:8]:
            try:
                with path.open('rb') as stream: raw = stream.read(4*2**20)
            except OSError: continue
            text = re.sub(r'\x1b\[[0-9;]*m', '', raw.decode(errors='replace'))
            for line in text.splitlines():
                if 'wandb:' not in line or 'View run at' not in line: continue
                match = pattern.search(line)
                if not match: continue
                item = {'run_id':run_id,'url':match[0],'initialized_seen_utc':utc(),
                        'evidence':'native W&B View-run URL plus local run artifact',
                        'source_log':str(path),'source_prefix_bytes':len(raw),'source_prefix_sha256':digest(raw)}
                atomic(cache, json.dumps(item,indent=2)+'\n', mode=0o600)
                evidence.sources[str(path)] = {'path':str(path),'bytes_read':len(raw),'sha256':digest(raw),
                    'projection':'W&B initialization URL only; no log text published','captured_utc':utc()}
                return {'state':'initialized','url':item['url'],'evidence':item['evidence']}
    return {'state':'pending'}


def snapshot(root, benchmark, evidence):
    evidence.begin()
    plan = evidence.read(root/'configs/training-plan.json') or {}
    requested = count(plan.get('training_steps'),100)
    run_id = str(plan.get('run_id','ua-e2b-pathfinding-20260920'))
    pointer = evidence.read(root/'results/supervision/latest.json') or {}
    status_path = owned_path(root,pointer.get('status_file'),'results/supervision')
    status = evidence.read(status_path,required=True) if status_path else {}
    status = status or {}
    baseline=accepted_baseline(root,run_id,evidence)
    known_attempts={a.get('attempt_id') for a in status.get('attempts',[]) if a.get('attempt_id')}
    if baseline:known_attempts.add(baseline['attempt_id'])
    metrics = evidence.read(root/'results/train-metrics.jsonl','metrics')
    metrics_present = metrics is not None
    metrics = metrics or []
    by_step = {}; duplicates = []
    for row in metrics:
        if row['step'] in by_step: duplicates.append(row['step'])
        by_step[row['step']] = row
    completed = 0
    while completed+1 in by_step: completed += 1
    if not metrics_present:
        value = status.get('progress',{}).get('completed_native_global_step')
        completed = int(value) if finite(value) else None
    checkpoint = evidence.read(root/'results/latest-checkpoint.json') or status.get('latest_checkpoint') or {}
    checkpoint_step = checkpoint.get('global_step')
    if not finite(checkpoint_step): checkpoint_step = None
    state = status.get('state') if status.get('state') in STATES else 'not_started_or_status_unavailable'
    liveness = supervisor_liveness(status)
    accepted = (state=='completed' and completed==requested and checkpoint_step==requested and
                status.get('final_acceptance',{}).get('native_training_steps')==requested and not duplicates)
    review=evidence.read(root/'configs/supervisor-prerequisites.json') or {}
    p3,p3_origin,p3_reused=pretraining_record(root,'phase3','results/replay-older8b-asap/summary.json',review,evidence)
    p3_executed=None; p3_records=0
    for path in ((p3_origin or root)/'results/replay-older8b-asap').glob('replay-*.json'):
        record=evidence.read(path)
        if record:p3_executed=(p3_executed or 0)+len(record.get('actions',[]));p3_records+=1
    if p3_records and p3_records!=count(p3.get('episodes')):
        evidence.issues.append({'type':'incomplete_replay_detail_set','found':p3_records,'expected':p3.get('episodes')})
        p3_executed=None
    if p3_executed is None and p3:
        path=benchmark/'phase3-replay/results/episodes.csv'
        if path.is_file():
            raw=path.read_bytes();rows=list(csv.DictReader(io.StringIO(raw.decode())))
            try:
                statuses={key:sum(row.get('status')==key for row in rows) for key in {row.get('status') for row in rows}}
                planned=sum(int(row['planned_actions']) for row in rows);executed=sum(int(row['executed_actions']) for row in rows)
                if len(rows)==p3.get('episodes') and statuses==p3.get('statuses') and planned==p3.get('actions') and 0<=executed<=planned:
                    p3_executed=executed
                    evidence.sources[str(path)]={'path':str(path),'bytes_read':len(raw),'sha256':digest(raw),
                        'projection':'published replay action counts, cross-checked against reviewed summary','captured_utc':utc()}
            except (KeyError,ValueError,TypeError):pass
    p3_cleanup = evidence.read((p3_origin or root)/'results/replay-older8b-asap/cleanup.json') or {}
    p5,p5_origin,p5_reused=pretraining_record(root,'phase5','results/phase5-gates/summary.json',review,evidence)
    cohorts = p5.get('cohorts',[]); gate_stats = {}
    for variant in ('create_and_setup','create_only'):
        rows = [r for r in cohorts if r.get('variant')==variant]
        values = [r['all_ready_and_checked_s'] for r in rows if finite(r.get('all_ready_and_checked_s'))]
        gate_stats[variant] = {'cohorts':len(rows),'planned':sum(r.get('count',0) for r in rows),
            'successful':sum(r.get('successes',0) for r in rows),
            'median_s':statistics.median(values) if values else None}
    component_path=benchmark/'phase5-optimization/results/startup-attempts.csv'
    components={}
    if component_path.exists():
        raw=component_path.read_bytes(); evidence.sources[str(component_path)]={'path':str(component_path),
            'bytes_read':len(raw),'sha256':digest(raw),'projection':'scalar startup component columns','captured_utc':utc()}
        rows=list(csv.DictReader(io.StringIO(raw.decode())));cohort_ids={r.get('cohort') for r in cohorts}
        for variant in gate_stats:
            matching=[r for r in rows if r.get('variant')==variant and r.get('cohort') in cohort_ids and r.get('ok') in ('True','true','1')]
            components[variant]={}
            for key in ('sdk_create_s','tools_setup_s','create_gate_wait_s'):
                values=[]
                for row in matching:
                    try: value=float(row[key])
                    except (ValueError,KeyError):continue
                    if math.isfinite(value):values.append(value)
                components[variant][key]=statistics.median(values) if values else None
    capacity,capacity_origin,capacity_reused=pretraining_record(root,'capacity256','results/capacity-256.json',review,evidence)
    diagnosis=evidence.read((capacity_origin or root)/'results/server-diagnostics/capacity256/summary.json') or {}
    if diagnosis and diagnosis.get('cohort')!=capacity.get('cohort'):
        evidence.issues.append({'type':'capacity_diagnosis_cohort_mismatch'});diagnosis={}
    qualification=evidence.read(root/'results/dataset-qualification-summary.json') or {}
    cleanup=evidence.read(root/'results/pretraining-cleanup.json') or {}
    # Native validation dumps can contain private generated text; project only step/score/UID.
    evaluations=[];validated={r.get('global_step'):r for r in status.get('evaluations') or []}
    for step in (0,25,50,75,100):
        rows=evidence.read(root/f'results/validation/{step}.jsonl','evaluation')
        if rows is None:continue
        scores=[r.get('score') for r in rows]
        expected=int(plan.get('validation_rows',16))
        complete=(len(rows)==expected and all(r.get('step')==step for r in rows) and
                  all(isinstance(r.get('uid'),str) and r['uid'] for r in rows) and len({r.get('uid') for r in rows})==expected and
                  all(finite(score) and score in (0,1) for score in scores))
        prefix_verified=bool(step==0 and complete and baseline and baseline['evaluation0_verified'] and
                             baseline['evaluation0_resolved']==int(sum(scores)))
        evaluations.append({'step':step,'rows':len(rows),'expected':expected,
            'resolved':int(sum(scores)) if complete else None,
            'state':'supervisor_verified' if step in validated and complete else 'accepted_prefix_verified' if prefix_verified else
                    'native_dump_complete' if complete else 'partial_or_invalid'})
    baseline_dump=next((row for row in evaluations if row['step']==0 and row['state']!='partial_or_invalid'),None)
    baseline_progress={'completed_episodes':baseline_dump['rows'] if baseline_dump else 0,
                       'unique_tasks':baseline_dump['rows'] if baseline_dump else 0,
                       'expected':count(plan.get('validation_rows'),16),
                       'source':'native_validation_dump' if baseline_dump else 'current_run_session_records'}
    if baseline_dump is None:
        ids=set()
        for path in (root/'results/sessions').glob('*/result.json'):
            row=evidence.read(path,'session') or {}
            if row.get('phase')!='validation' or row.get('global_step')!=0 or row.get('status')!='completed':continue
            if known_attempts and row.get('supervisor_attempt_id') not in known_attempts:continue
            baseline_progress['completed_episodes']+=1
            if isinstance(row.get('instance_id'),str):ids.add(row['instance_id'])
        baseline_progress['unique_tasks']=len(ids)
    analysis_pointer=evidence.read(root/'results/supervision/latest-analysis.json') or status.get('latest_analysis') or {}
    analysis_dir=owned_path(root,analysis_pointer.get('output'),'results')
    analysis=evidence.read(analysis_dir/'summary.json') if analysis_dir else None
    analysis_is_train=bool(analysis and analysis.get('phase_filter')=='train')
    figure_sources=[]
    if analysis_is_train:
        for name in ('training-step-times.png','operation-latency.png'):
            path=analysis_dir/name
            if path.is_file():figure_sources.append(path)
    return {'snapshot_id':uuid.uuid4().hex,'generated_utc':utc(),'requested':requested,'run_id':run_id,
        'completed':completed,'checkpoint_step':checkpoint_step,'state':state,'liveness':liveness,
        'heartbeat_utc':status.get('heartbeat_utc'),'accepted':accepted,'duplicates':duplicates,
        'observed_steps':sorted(by_step),'steps':[by_step[s] for s in sorted(by_step)],
        'train_rows':count(plan.get('train_rows'),62),'validation_rows':count(plan.get('validation_rows'),16),
        'prompt_batch':plan.get('prompt_batch'),'rollouts_per_prompt':plan.get('rollouts_per_prompt'),
        'creation_permits':plan.get('creation_permits'),'exec_permits':plan.get('exec_permits'),
        'separate_create_gate':str(plan.get('separate_create_gate','unknown')),
        'p3':p3,'p3_executed_actions':p3_executed,'p3_cleanup':p3_cleanup,'gate_stats':gate_stats,'gate_components':components,
        'reused_pretraining_labels':[label for label,reused in [('phase3',p3_reused),('phase5',p5_reused),('capacity256',capacity_reused)] if reused],
        'previous_attempts':previous_attempts(root,benchmark,evidence),'baseline_progress':baseline_progress,
        'first_batch':first_batch_record(benchmark,run_id,known_attempts,evidence),'accepted_baseline':baseline,
        'capacity':capacity,'diagnosis':diagnosis,'qualification':qualification,'cleanup':cleanup,
        'evaluations':evaluations,'analysis':analysis if analysis_is_train else None,
        'analysis_phase_valid':analysis_is_train,'figure_sources':figure_sources,
        'wandb':wandb_link(root,run_id,status,evidence),
        'remaining_seconds_estimate':status.get('progress',{}).get('remaining_seconds_estimate'),
        'supervisor_error_recorded':bool(status.get('error'))}


def link(path, report):
    return os.path.relpath(path, report.parent)


def render(model, report, benchmark, detailed):
    m=model; completed='unavailable' if m['completed'] is None else str(m['completed'])
    checkpoint='none recorded' if m['checkpoint_step'] is None else str(int(m['checkpoint_step']))
    state=m['state']
    if state=='completed' and not m['accepted']:state='completion_unverified'
    if m['liveness'] not in ('live','terminal_or_not_started'):state+='; '+m['liveness']
    lines=[f"Requested **{m['requested']} training steps**; **{completed}/{m['requested']} recorded**, checkpoint **{checkpoint}**, state **{state}**.",'',
        f"Updated {m['generated_utc']}. Supervisor heartbeat: {m['heartbeat_utc'] or 'unavailable'}. Snapshot `{m['snapshot_id']}`.",'']
    lines.append(f"Active run: `{m['run_id']}`.")
    if m['accepted']:
        lines.append('The supervisor accepted the requested native steps, final checkpoint, reload proof and scheduled evaluations.')
    else:
        lines.append('The requested training run has not yet passed final acceptance. Starting processes, agent turns and completed rollouts do not count as completed training steps.')
    progress=m['baseline_progress']
    if progress['completed_episodes']==progress['unique_tasks'] and progress['completed_episodes']<=progress['expected']:
        lines += ['',f"Current-run baseline evaluation: **{progress['completed_episodes']}/{progress['expected']} episodes completed**. This is episode progress, not optimizer-step progress."]
    else:
        lines += ['',f"Current-run baseline evaluation: **{progress['unique_tasks']}/{progress['expected']} distinct tasks**, with {progress['completed_episodes']} completed episode records; task-count validation is pending."]
    lines += ['',f"The training subset contains **{m['train_rows']} SWE-reBench tasks**; evaluation uses **{m['validation_rows']} SWE-bench Verified tasks**, at steps 0, 25, 50, 75 and 100. This is not a full 500-task Verified score."]
    q=m['qualification']
    if q:
        lines.append(f"Environment/reward controls resolved {count(q.get('baseline_resolved'),'—')}/{count(q.get('baseline_count'),'—')} baseline cases and {count(q.get('oracle_resolved'),'—')}/{count(q.get('oracle_count'),'—')} oracle cases. These **{count(q.get('oracle_count'),'—')} control cases** validate the prepared environments and scoring path; they are not the trained model's results.")
    wb=m['wandb'];lines += ['',f"[W&B run]({wb['url']}) — native initialization observed; remote delivery of every metric is not independently checked." if wb['state']=='initialized' else 'W&B: initialization evidence is pending; no live run link is asserted.']
    if finite(m['remaining_seconds_estimate']) and m['remaining_seconds_estimate']>0:
        lines += ['',f"The supervisor's current remaining-time estimate is **{m['remaining_seconds_estimate']/3600:.1f} hours**, based on completed-step timers. It does not guarantee overnight completion."]
    if m['duplicates']:
        lines += ['', 'Native metric rows contain duplicate step IDs; progress remains subject to supervisor validation.']
    if m['supervisor_error_recorded']:
        lines += ['', 'The supervisor recorded an error. Its private diagnostic text is intentionally not copied into this public report.']
    first=m['first_batch']
    if first:
        text=(f"**R2 first batch.** Collecting the first **256 rollouts** took **{first['collection_s']/60:.1f} min**. "
            f"The last 12 rollouts added **{first['after_p95_s']/60:.1f} min ({100*first['after_p95_fraction']:.1f}% of the collection span)**; "
            f"during that interval an average of **{first['tail_low_busy_gpus']:.1f} of 8 GPUs** had **less than 5% device activity**.")
        if first['diagnosis']:
            text+=f" Separately, the [startup diagnosis]({link(first['diagnosis'],report)}) found ten shell initialization attempts that spent **52–59 s of a 60 s budget waiting for command permits**; all ten recovered on retry."
        text+=f" This runner clock excludes policy updates and checkpointing. [Measurement details]({link(first['data'],report)})."
        lines += ['',text]
        if first['image']:
            lines += ['',f"![R2 first-batch runner completion and sampled resource activity]({link(first['image'],report)})"]
    if m['previous_attempts']:
        lines += ['', '**Previous attempt retained separately**', '',
                  '| Run | Native updates | Checkpoint | Training results |','|---|---:|---|---|']
        for old in m['previous_attempts']:
            checkpoint_old='none' if old['checkpoint'] is None else str(old['checkpoint'])
            results=f"{old['at_stall']} at stall; {old['final_results']} final" if old['at_stall'] is not None and old['final_results'] is not None else 'see retained evidence'
            lines.append(f"| `{old['run_id']}` | {old['steps'] if old['steps'] is not None else '—'} | {checkpoint_old} | {results} |")
        for old in m['previous_attempts']:
            if old['fix_report']:
                lines += ['',f"R1's execution-admission cancellation leak was reproduced and repaired with ticket ownership; [fix and validation evidence]({link(old['fix_report'],report)}). The **{old['startup_cancellations']} startup/create cancellations** and **{old['exec_wait_cancellations']} cancelled exec-gate waits** are different counters, not unique episode counts. R1 results are excluded from the active run's step and evaluation totals."]
                if old['public_report']:lines += ['',f"[Retained R1 report]({link(old['public_report'],report)})."]
    a=m['gate_stats']['create_and_setup'];b=m['gate_stats']['create_only']
    reduction=100*(1-b['median_s']/a['median_s']) if a['median_s'] and b['median_s'] else None
    lines += ['', '**Three measured findings**', '']
    if m['reused_pretraining_labels']:
        lines += ['The following pretraining measurements are retained, hash-checked R1 evidence. They are not measurements of the ongoing R2 training run.','']
    if reduction is not None:
        ca=m['gate_components'].get('create_and_setup',{});cb=m['gate_components'].get('create_only',{})
        policy='the training baseline retains the original gate policy' if m['separate_create_gate']=='0' else 'the current training gate flag is recorded separately'
        lines.append(f"1. **Client admission affects startup.** Across {a['cohorts']} baseline and {b['cohorts']} create-only cohorts, releasing the create permit after SDK create reduced median 32-instance cohort completion from **{a['median_s']:.2f} s to {b['median_s']:.2f} s ({reduction:.1f}% lower)**. SDK-create P50 increased from {fmt(ca.get('sdk_create_s'),3)} to {fmt(cb.get('sdk_create_s'),3)} s, and tools-setup P50 from {fmt(ca.get('tools_setup_s'),3)} to {fmt(cb.get('tools_setup_s'),3)} s. The whole cohort improved while individual components slowed. This is a startup result, not an RL speedup; {policy}.")
    else:lines.append('1. **Startup comparison:** completed comparison evidence is not available yet.')
    c=m['capacity'];d=m['diagnosis']
    if c:
        errors=c.get('errors',[]);error_s=errors[0].get('elapsed_s') if errors else None
        text=f"2. **The coexistence check stopped before 256.** It observed **{c.get('observed_simultaneous_running','—')} running instances**, with {c.get('commands_passed','—')} command checks passed. One preparation call failed after {fmt(error_s)} s; {c.get('not_attempted','—')} planned creates were not dispatched. Final remaining instances: {len(c.get('remaining_ids',[]))}. A single failed attempt does not establish a capacity limit of 177."
        if d:
            text+=' Server evidence points to a **generic envd initialization/readiness timeout after Firecracker start**. The message “syncing took too long” does not prove a clock-synchronization fault; client/server linkage is temporal/template/duration evidence, not a shared request-ID join.'
        lines.append(text)
    else:lines.append('2. **Coexistence check:** completed diagnostic evidence is not available yet.')
    p3=m['p3'];statuses=p3.get('statuses',{})
    if p3:
        executed=m['p3_executed_actions'] if m['p3_executed_actions'] is not None else 'an unavailable count'
        lines.append(f"3. **Real replay has a measured fidelity boundary.** Of {count(p3.get('episodes'),'—')} older 8B episodes, **{count(statuses.get('replayed'),0)} matched through native verification** and **{count(statuses.get('observation_divergence'),0)} stopped at observation divergence**. The run executed {executed} of {count(p3.get('actions'),'—')} recorded actions and retained the unexecuted suffixes. This is a distinct corpus from the newer 30B 32-task/256-episode timing study.")
    else:lines.append('3. **Real replay:** completed fidelity evidence is not available yet.')
    if detailed:
        for relative,title in [('phase5-optimization/results/startup-gate-comparison.png','Native-provider create-gate comparison'),
                               ('phase3-replay/results/replay-coverage.png','Real replay coverage')]:
            if (benchmark/relative).is_file():lines += ['',f"![{title}]({link(benchmark/relative,report)})"]
    lines += ['', '**Native training timing**', '']
    if m['steps']:
        rows=m['steps'][-10:]
        lines += ['| Step | Step wall, s | Rollout collection, s | Actor update, s | Weight sync, s | Checkpoint, s | Reward mean |',
                  '|---:|---:|---:|---:|---:|---:|---:|']
        for row in rows:
            lines.append('| '+str(row['step'])+' | '+' | '.join(fmt(row.get(key),4 if key=='reward_mean' else 2) for key in
                ('step_s','gen_s','actor_update_s','weight_sync_s','checkpoint_s','reward_mean'))+' |')
        lines += ['', 'These are native top-level timers for completed training steps only. Rollout collection includes environment/model/tool/verifier waiting. The listed components are separate intervals within step wall time; they are not an exhaustive partition. Evaluation timing is outside this table. No nested or parallel profile spans are summed into wall time.', '',
                  f"[All recorded native step rows]({link(benchmark/'phase4-rl/results/published-native-steps.csv',report)})."]
    else:
        lines.append('No complete native training-step timer has been recorded yet. Model initialization, baseline evaluation or rollout collection may be active; no missing duration is rendered as zero.')
    if m['evaluations']:
        lines += ['', '**Evaluation on the fixed 16-task subset**', '', '| Training step | Resolved / expected | Evidence |','|---:|---:|---|']
        for row in m['evaluations']:
            score='pending' if row['resolved'] is None else str(row['resolved'])
            label={'supervisor_verified':'Supervisor task/session/reward join passed',
                   'accepted_prefix_verified':'Checkpoint-1 prefix task/session/reward join passed; evidence unchanged',
                   'native_dump_complete':'Native dump complete; supervisor join pending','partial_or_invalid':'Partial or invalid dump'}[row['state']]
            lines.append(f"| {row['step']} | {score} / {row['expected']} | {label} |")
    else:lines += ['', 'No completed evaluation-round score is available yet.']
    analysis=m['analysis']
    if analysis:
        counts=analysis.get('training_session_status_counts',{})
        lines += ['',f"Latest train-filtered profile analysis: session states `{json.dumps(counts,sort_keys=True)}`; quality issues **{analysis.get('quality_issue_count','unavailable')}**. Gateway phases are joined only through exact session identities. P3/P5 events and candidate gate policies are excluded."]
    else:
        lines += ['', 'A usable train-filtered profile summary is pending; no pretraining SDK percentiles are substituted for it.']
    for path in m.get('published_rl_figures',[]):
        lines += ['',f"![Train-only native/profile timing]({link(path,report)})"]
    lines += ['', f"[Replay evidence]({link(benchmark/'phase3-replay/RESULTS.md',report)}) · [Gate comparison]({link(benchmark/'phase5-optimization/RESULTS.md',report)}) · [Capacity diagnostic]({link(benchmark/'phase5-optimization/CAPACITY.md',report)}) · [Offline scheduling what-if]({link(benchmark/'phase3-replay/SIMULATION.md',report)}) · [Profiling boundaries]({link(benchmark/'phase4-rl/PROFILING.md',report)}) · [Snapshot validation and source hashes]({link(benchmark/'phase4-rl/report-validation.json',report)})."]
    return '\n'.join(lines)+'\n'


def publish(root=DEFAULT_ROOT, benchmark=DEFAULT_BENCHMARK, evidence=None):
    root=Path(root).resolve();benchmark=Path(benchmark).resolve();evidence=evidence or Evidence()
    # These are public reporting directories only; private ROOT/results retains its existing modes.
    for directory in (benchmark,benchmark/'phase4-rl',benchmark/'phase4-rl/results'):
        directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o755)
    m=snapshot(root,benchmark,evidence)
    outputs={};m['published_rl_figures']=[]
    for source in m['figure_sources']:
        data=source.read_bytes();destination=benchmark/'phase4-rl/results'/('published-'+source.name)
        atomic(destination,data);outputs[str(destination)]={'sha256':digest(data),'bytes':len(data)}
        evidence.sources[str(source)]={'path':str(source),'bytes_read':len(data),'sha256':digest(data),'projection':'train-filtered PNG','captured_utc':utc()}
        m['published_rl_figures'].append(destination)
    fields=['step',*TIMERS,'reward_mean'];stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=fields)
    writer.writeheader();writer.writerows(m['steps']);data=stream.getvalue()
    table=benchmark/'phase4-rl/results/published-native-steps.csv';atomic(table,data)
    outputs[str(table)]={'sha256':digest(data.encode()),'bytes':len(data.encode())}
    for report,detailed in ((benchmark/'phase4-rl/REPORT.md',True),(benchmark/'OVERNIGHT-20260920.md',False)):
        text=render(m,report,benchmark,detailed);atomic(report,text)
        outputs[str(report)]={'sha256':digest(text.encode()),'bytes':len(text.encode())}
    source=Path(__file__).resolve();raw=source.read_bytes()
    manifest={'schema_version':1,'snapshot_id':m['snapshot_id'],'generated_utc':m['generated_utc'],
        'requested_steps':m['requested'],'recorded_steps':m['completed'],'checkpoint_step':m['checkpoint_step'],
        'state':m['state'],'supervisor_liveness':m['liveness'],'final_acceptance':m['accepted'],
        'active_run_id':m['run_id'],'baseline_evaluation_progress':m['baseline_progress'],
        'first_batch_attempt_id':m['first_batch']['attempt_id'] if m['first_batch'] else None,
        'accepted_baseline':m['accepted_baseline'],
        'reused_pretraining_labels':m['reused_pretraining_labels'],
        'previous_run_ids':[row['run_id'] for row in m['previous_attempts']],
        'publisher':{'path':str(source),'sha256':digest(raw)},'inputs':list(evidence.sources.values()),
        'outputs':outputs,'quality_issues':evidence.issues,
        'validation':{'explicit_public_mode_0644':all(Path(path).stat().st_mode&0o777==0o644 for path in outputs),
            'public_report_directories_traversable':all((benchmark/relative).stat().st_mode&0o005==0o005 for relative in ('.','phase4-rl','phase4-rl/results')),
            'output_hashes_match':all(digest(Path(path).read_bytes())==record['sha256'] for path,record in outputs.items()),
            'training_metrics_only_native_step_rows':all(finite(row['step_s']) for row in m['steps']),
            'profile_phase_train_or_unavailable':m['analysis'] is None or m['analysis'].get('phase_filter')=='train',
            'no_raw_task_text_or_credentials_exported':True},
        'limits':['Snapshot inputs are individually captured and hashed, not an atomic transaction across all producers.',
                  'Running state and recorded steps do not imply final training acceptance.',
                  'Only whitelisted scalars, known report links and train-filtered figures enter public output.']}
    atomic(benchmark/'phase4-rl/report-validation.json',json.dumps(manifest,indent=2)+'\n')
    atomic(root/'results/report-publisher/validation-latest.json',json.dumps(manifest,indent=2)+'\n',mode=0o600)
    return {'snapshot_id':m['snapshot_id'],'recorded_steps':m['completed'],'requested_steps':m['requested'],
            'checkpoint_step':m['checkpoint_step'],'state':m['state'],'wandb':m['wandb']['state'],
            'validation':manifest['validation'],'quality_issue_count':len(evidence.issues)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=DEFAULT_ROOT);p.add_argument('--benchmark',type=Path,default=DEFAULT_BENCHMARK)
    p.add_argument('--watch',action='store_true');p.add_argument('--interval',type=float,default=60)
    p.add_argument('--max-hours',type=float,default=96);p.add_argument('--stop-file',type=Path)
    a=p.parse_args()
    if not 10<=a.interval<=3600 or not 0<a.max_hours<=96:p.error('interval must be10..3600 seconds; max-hours must be positive and <=96')
    a.root=a.root.resolve();directory=a.root/'results/report-publisher';directory.mkdir(parents=True,exist_ok=True)
    stop_file=a.stop_file or a.root/'run/STOP_REPORT_PUBLISHER';stopped=False
    def stop(signum,frame):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with (directory/'publisher.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('A publisher already owns this run')
        deadline=time.monotonic()+a.max_hours*3600;evidence=Evidence();count=0;errors=0
        while not stopped and not stop_file.exists() and time.monotonic()<deadline:
            began=time.monotonic()
            try:
                result=publish(a.root,a.benchmark,evidence);count+=1;print(json.dumps(result),flush=True)
            except Exception as exc:
                errors+=1
                record={'time_utc':utc(),'error_type':type(exc).__name__,'snapshot_count':count}
                atomic(directory/'publisher-error.json',json.dumps(record)+'\n',mode=0o600)
                print(json.dumps(record),flush=True)
                if not a.watch:raise
            if not a.watch:break
            next_tick=min(deadline,began+a.interval)
            while not stopped and not stop_file.exists() and time.monotonic()<next_tick:
                time.sleep(min(.5,max(0,next_tick-time.monotonic())))
        atomic(directory/'publisher-exit.json',json.dumps({'ended_utc':utc(),'snapshots':count,'errors':errors,
            'status':'stopped' if stopped or stop_file.exists() else 'finite_window_complete' if a.watch else 'snapshot_complete'})+'\n',mode=0o600)


if __name__=='__main__':main()
