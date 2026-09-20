"""Characterize communication correctness without changing the training run."""
import json, os
import torch
import torch.distributed as dist

rank = int(os.environ['LOCAL_RANK'])
torch.cuda.set_device(rank)
dist.init_process_group('nccl')
for dtype in (torch.float32, torch.bfloat16, torch.float64, torch.int32):
    for n in (1, 1024, 1048576):
        for opname, op, expected in [('sum', dist.ReduceOp.SUM, 36), ('max', dist.ReduceOp.MAX, 8)]:
            value = torch.full((n,), rank + 1, dtype=dtype, device='cuda')
            dist.all_reduce(value, op=op)
            torch.cuda.synchronize()
            print(json.dumps({'rank': rank, 'dtype': str(dtype), 'n': n, 'op': opname,
                              'head': value[:4].tolist(), 'passed': bool(torch.all(value == expected))}), flush=True)
value = torch.tensor([rank], device='cuda', dtype=torch.float32)
gathered = [torch.empty_like(value) for _ in range(8)]
dist.all_gather(gathered, value)
print(json.dumps({'rank': rank, 'gathered': [x.item() for x in gathered]}), flush=True)
dist.destroy_process_group()
