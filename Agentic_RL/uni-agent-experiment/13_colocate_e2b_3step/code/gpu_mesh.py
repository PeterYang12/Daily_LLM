import datetime
import json
import os
from pathlib import Path
import torch
import torch.distributed as dist
from megatron.core import parallel_state as ps

root = Path('/work/experiments/colocate-e2b-3step-20260920/results/gpu-mesh')
root.mkdir(parents=True, exist_ok=True)
rank = int(os.environ['LOCAL_RANK'])
torch.cuda.set_device(rank)
dist.init_process_group('nccl', timeout=datetime.timedelta(seconds=120), device_id=torch.device('cuda', rank))
try:
    ps.initialize_model_parallel(tensor_model_parallel_size=1, pipeline_model_parallel_size=1,
        context_parallel_size=4, expert_model_parallel_size=8, expert_tensor_parallel_size=1)
    checks = {}
    for name, group in [('context', ps.get_context_parallel_group()),
                        ('expert', ps.get_expert_model_parallel_group()),
                        ('data', ps.get_data_parallel_group())]:
        ranks = dist.get_process_group_ranks(group)
        tensor = torch.full((4096,), float(rank+1), device='cuda', dtype=torch.float32)
        dist.all_reduce(tensor, group=group)
        assert bool(torch.all(tensor == sum(r+1 for r in ranks)))
        checks[name] = ranks
    result = {'rank': rank, 'success': True, 'groups': checks}
    (root/f'rank-{rank}.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
finally:
    ps.destroy_model_parallel()
    dist.destroy_process_group()
