import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from runtime_common import ROOT
from profile_events import emit


def _parameter_samples(engine):
    params = [(name, p) for chunk in engine.module for name, p in chunk.named_parameters()
              if p.requires_grad and p.is_contiguous() and p.numel() > 1024]
    selected = params[::max(1, len(params)//3)][:3]
    # At most 768 sampled values/rank, first native optimizer call only.
    return [(name, p.detach().view(-1)[::max(1, p.numel()//256)][:256].float().cpu().clone())
            for name, p in selected]


def snapshot(engine):
    if getattr(engine, '_ua_observed_updates', 0) != 0:
        return []
    return _parameter_samples(engine)


def _bounded_scalar(value):
    if value is None or isinstance(value,(bool,int,str)):
        return value
    if isinstance(value,float):
        return value if math.isfinite(value) else str(value)
    if hasattr(value,'numel') and value.numel()==1:
        return _bounded_scalar(value.detach().cpu().item())
    if isinstance(value,(list,tuple)) and len(value)<=16 and all(isinstance(x,(bool,int,float,str)) for x in value):
        return [_bounded_scalar(x) for x in value]
    return None


def _optimizer_checkpoint_scalars(optimizer):
    """Bounded read-only scalar view; never invoke compatibility optimizer properties.

    Chained wrappers are traversed through their stored child lists before any
    leaf state is inspected. TE/Apex group steps are read only for nonempty
    groups, because checkpoint serialization may normalize empty-group steps.
    """
    counters = {}
    groups = {}
    unavailable = []
    queue = [('optimizer', optimizer)]
    seen = set()
    sentinel = object()

    def scalar(value, label):
        try:
            return _bounded_scalar(value)
        except Exception as exc:
            unavailable.append(label + '_unavailable_' + type(exc).__name__)
            return None

    while queue and len(seen) < 16:
        label, current = queue.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        try:
            attrs = object.__getattribute__(current, '__dict__')
        except (AttributeError, TypeError):
            unavailable.append(label + '_attributes_unavailable')
            continue
        if not isinstance(attrs, dict):
            unavailable.append(label + '_attributes_not_dict')
            continue

        # Reading ChainedOptimizer.optimizer would assert for dense+expert chains.
        # Only inspect stored attributes; do not invoke descriptors or properties.
        children = None
        child_key = None
        for key in ('chained_optimizers', 'optimizers', 'sub_optimizers'):
            value = attrs.get(key)
            if isinstance(value, (list, tuple)):
                children, child_key = value, key
                break
        if children is not None:
            queue.extend((f'{label}.{child_key}{index}', child) for index, child in enumerate(children[:8]))
            if len(children) > 8:
                unavailable.append(label + '_child_budget_exhausted')
            continue
        inner = attrs.get('optimizer', sentinel)
        if inner is not sentinel and inner is not None and inner is not current:
            queue.append((label + '.inner', inner))
            continue

        for key in ('step_count', 'num_steps', '_step_count'):
            if key in attrs and len(counters) < 32:
                value = scalar(attrs[key], label + '.' + key)
                if value is not None:
                    counters[label + '.' + key] = value
        state = attrs.get('state')
        param_groups = attrs.get('param_groups')
        if not isinstance(param_groups, list):
            unavailable.append(label + '_param_groups_unavailable')
            continue
        for group_index, group in enumerate(param_groups[:16]):
            if not isinstance(group, dict):
                unavailable.append(label + '_param_group_not_dict')
                continue
            group_label = f'{label}.group{group_index}'
            if len(groups) < 32:
                groups[group_label] = {key: scalar(group[key], group_label + '.' + key)
                                      for key in ('lr', 'betas', 'eps', 'weight_decay') if key in group}
            parameters = group.get('params', ())
            if not isinstance(parameters, (list, tuple)):
                unavailable.append(group_label + '_params_unavailable')
                continue
            if not parameters:
                continue
            # TE/Apex stores the iteration counter on the param group.
            if 'step' in group:
                if len(counters) < 32:
                    value = scalar(group['step'], group_label + '.step')
                    if value is not None:
                        counters[group_label + '.step'] = value
                continue
            # Native torch optimizers store per-parameter step counters instead.
            if isinstance(state, dict):
                for parameter_index, parameter in enumerate(parameters[:16]):
                    if len(counters) >= 32:
                        break
                    entry = state.get(parameter, {})
                    if isinstance(entry, dict) and 'step' in entry:
                        counter_label = f'{group_label}.param{parameter_index}.step'
                        value = scalar(entry['step'], counter_label)
                        if value is not None:
                            counters[counter_label] = value
    if queue:
        unavailable.append('optimizer_node_budget_exhausted')
    if not counters:
        unavailable.append('optimizer_step_counters_not_exposed_by_supported_attributes')
    if not groups:
        unavailable.append('optimizer_param_group_scalars_not_exposed')
    return {'step_counters': counters, 'param_groups': groups, 'unavailable': sorted(set(unavailable))}


def checkpoint_probe(engine, phase, checkpoint_dir, global_step=None):
    """Bounded save/load-1 proof; runs only inside native checkpoint operations, never launches GPU work."""
    import torch
    from runtime_common import save
    if phase not in ('save','load'):raise ValueError('Checkpoint proof phase must be save or load')
    match=re.search(r'global_step_(\d+)',str(checkpoint_dir))
    step=int(global_step) if global_step is not None else int(match[1]) if match else None
    if step!=1:return {'status':'not_checkpoint_1','global_step':step}
    began=time.monotonic()
    samples=_parameter_samples(engine)
    if not samples:raise RuntimeError('No model samples available for checkpoint reload proof')
    probes=[{'name':name,'sample_count':value.numel(),
             'sha256_float32':hashlib.sha256(value.numpy().tobytes()).hexdigest()}
            for name,value in samples]
    optimizer=_optimizer_checkpoint_scalars(getattr(engine,'optimizer',None))
    scheduler=getattr(engine,'lr_scheduler',None);scheduler_state={};unavailable=[]
    if scheduler is not None and callable(getattr(scheduler,'state_dict',None)):
        for key,value in scheduler.state_dict().items():
            scalar=_bounded_scalar(value)
            if scalar is not None:scheduler_state[str(key)]=scalar
            else:unavailable.append('scheduler_field_not_scalar:'+str(key))
    else:unavailable.append('scheduler_state_dict_unavailable')
    rank=torch.distributed.get_rank()
    row={'schema_version':1,'phase':phase,'global_step':step,'rank':rank,
         'supervisor_attempt_id':os.environ.get('UA_SUPERVISOR_ATTEMPT_ID'),
         'checkpoint_dir':str(checkpoint_dir),'time_unix':time.time(),'model_probes':probes,
         'optimizer':optimizer,'scheduler_scalars':scheduler_state,'unavailable':unavailable,
         'probe_wall_s':time.monotonic()-began,'scope':'At most 768 model values/rank and bounded optimizer/scheduler scalars; not full-state equality'}
    if phase=='load':
        source=ROOT/'results/reload-proof/save'/f'rank-{rank}.json'
        if not source.is_file():raise RuntimeError('Saved rank proof is missing for checkpoint 1')
        before=json.loads(source.read_text())
        compared=('model_probes','optimizer','scheduler_scalars')
        row['mismatches']=[key for key in compared if before.get(key)!=row[key]]
        row['matched']=not row['mismatches']
        row['saved_evidence_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    save(ROOT/'results/reload-proof'/phase/f'rank-{rank}.json',row)
    if phase=='load' and not row['matched']:
        raise RuntimeError('Checkpoint 1 restored probe mismatch: '+','.join(row['mismatches']))
    return row


def record(engine, before, grad_norm, successful):
    import torch
    after = snapshot(engine) if before else []
    changed = total = 0
    maximum = 0.0
    hashes = [hashlib.sha256(), hashlib.sha256()]
    for (name, a), (other, b) in zip(before, after):
        assert name == other
        changed += int((a != b).sum())
        total += a.numel()
        maximum = max(maximum, float((a-b).abs().max()))
        hashes[0].update(a.numpy().tobytes())
        hashes[1].update(b.numpy().tobytes())
    engine._ua_observed_updates = getattr(engine, '_ua_observed_updates', 0) + 1
    norm = float(grad_norm) if grad_norm is not None else None
    row = {'time_unix': time.time(), 'monotonic_ns': time.monotonic_ns(), 'rank': torch.distributed.get_rank(),
           'global_step':getattr(engine,'_ua_training_global_step',None),
           'supervisor_attempt_id':os.environ.get('UA_SUPERVISOR_ATTEMPT_ID','manual'),
           'optimizer_update': engine._ua_observed_updates, 'successful': bool(successful),
           'grad_norm': norm if norm is None or math.isfinite(norm) else None,
           'gradient_finite': norm is not None and math.isfinite(norm), 'probe_performed': bool(before),
           'sampled_parameters': total, 'sampled_changed': changed if before else None,
           'sampled_max_delta': maximum if before else None,
           'hash_before': hashes[0].hexdigest() if before else None,
           'hash_after': hashes[1].hexdigest() if before else None,
           'scope': 'Native optimizer calls; first-call parameter probe. Counter is not trainer global_step.'}
    path = ROOT/'results/optimizer'/f'rank-{row["rank"]}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        f.write(json.dumps(row, allow_nan=False) + '\n')
    emit('optimizer_update', category='training', phase='train', rank=row['rank'],
         global_step=row['global_step'],
         optimizer_update=row['optimizer_update'], successful=row['successful'],
         grad_norm=row['grad_norm'], probe_performed=row['probe_performed'])
