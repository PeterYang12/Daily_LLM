"""Bounded math and RCCL checks after the driver's automatic ECC reset."""
import argparse,json,os,time
from pathlib import Path
import torch
import torch.distributed as dist
p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
rank=int(os.environ['LOCAL_RANK']);torch.cuda.set_device(rank)
world=int(os.environ['WORLD_SIZE']);expected_sum=world*(world+1)//2
dist.init_process_group('nccl', **({'device_id': torch.device('cuda', rank)} if os.environ.get('UA_HEALTH_EAGER_INIT') == '1' else {}))
try:
    x=torch.ones((1024,1024),device='cuda',dtype=torch.bfloat16)
    y=x@x;torch.cuda.synchronize()
    assert bool(torch.all(y==1024))
    value=torch.full((1024*1024,),float(rank+1),device='cuda')
    before=value[:8].cpu().tolist()
    dist.all_reduce(value);torch.cuda.synchronize()
    if not bool(torch.all(value==expected_sum)):
        out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
        result={'rank':rank,'world_size':dist.get_world_size(),'before':before,'after_head':value[:32].cpu().tolist(),'minimum':float(value.min()),'maximum':float(value.max()),'equal_expected_count':int((value==expected_sum).sum()),'elements':value.numel()}
        (out/f'gpu-collective-failure-rank-{rank}.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
        raise AssertionError('All-reduce numerical check failed')
    send=torch.tensor([rank*100+i for i in range(world)],device='cuda',dtype=torch.float32)
    recv=torch.empty_like(send);dist.all_to_all_single(recv,send);torch.cuda.synchronize()
    expected=torch.tensor([i*100+rank for i in range(world)],device='cuda',dtype=torch.float32)
    assert bool(torch.equal(recv,expected))
    checks = 3
    for dtype in (torch.float32, torch.bfloat16):
        for n in (1, 1024, 1048576):
            for iteration in range(3):
                value=torch.full((n,),float(rank+iteration-3),device='cuda',dtype=dtype)
                dist.all_reduce(value)
                assert bool(torch.all(value == world*(iteration-3)+world*(world-1)//2)), (dtype,n,iteration)
                checks += 1
        source=torch.full((world*1024,),float(rank+1),device='cuda',dtype=dtype)
        reduced=torch.empty(1024,device='cuda',dtype=dtype)
        dist.reduce_scatter_tensor(reduced,source)
        assert bool(torch.all(reduced == expected_sum))
        gathered=torch.empty(world*1024,device='cuda',dtype=dtype)
        dist.all_gather_into_tensor(gathered,reduced)
        assert bool(torch.all(gathered == expected_sum))
        checks += 2
    # Four TP2 groups, including their first data collective (lazy NCCL init).
    for ranks in ([i,i+1] for i in range(0,world,2)):
        group=dist.new_group(ranks)
        if rank in ranks:
            value=torch.full((4096,),float(rank+1),device='cuda')
            dist.all_reduce(value,group=group)
            assert bool(torch.all(value == sum(i+1 for i in ranks)))
            checks += 1
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    result={'rank':rank,'device':torch.cuda.get_device_name(rank),'matmul':True,'all_reduce':True,'all_to_all':True,'checks':checks,'allocator_config':os.environ.get('PYTORCH_ALLOC_CONF'),'eager_init':os.environ.get('UA_HEALTH_EAGER_INIT')=='1','time_unix':time.time()}
    (out/f'gpu-health-rank-{rank}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
finally:dist.destroy_process_group()
