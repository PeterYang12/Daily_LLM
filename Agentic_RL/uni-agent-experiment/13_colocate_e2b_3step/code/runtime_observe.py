import hashlib
import json
import time
from runtime_common import ROOT


def snapshot(engine):
    params = [(name, p) for chunk in engine.module for name, p in chunk.named_parameters()
              if p.requires_grad and p.is_contiguous() and p.numel() > 1024]
    selected = params[::max(1, len(params)//16)][:16]
    return [(name, p.detach().view(-1)[::max(1, p.numel()//1024)][:1024].float().cpu().clone())
            for name, p in selected]


def record(engine, before, grad_norm, successful):
    import torch
    after = snapshot(engine)
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
    row = {'time_unix': time.time(), 'rank': torch.distributed.get_rank(),
           'optimizer_update': engine._ua_observed_updates, 'successful': bool(successful),
           'grad_norm': float(grad_norm) if grad_norm is not None else None,
           'sampled_parameters': total, 'sampled_changed': changed, 'sampled_max_delta': maximum,
           'hash_before': hashes[0].hexdigest(), 'hash_after': hashes[1].hexdigest()}
    path = ROOT/'results/optimizer'/f'rank-{row["rank"]}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        f.write(json.dumps(row) + '\n')
