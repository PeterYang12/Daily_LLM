"""Minimal repro: with expandable_segments, one live allocation pins the whole segment.

Single process, single GPU, no torch.distributed. Allocates a small tensor, then a large one,
frees the large one, and compares torch's own accounting (``memory_reserved``) against the
driver (``mem_get_info`` / hipMemGetInfo). With expandable segments both allocations share one
contiguous VA range, and the surviving small tensor keeps the whole range's physical pages
mapped -- so torch reports the large block as freed while the driver still counts it as in use.

    python3 expandable_strand.py on    # keep the small tensor -> stranded
    python3 expandable_strand.py on --drop-small
    python3 expandable_strand.py off
"""

import argparse

import torch


def show(tag):
    free_b, total_b = torch.cuda.mem_get_info()
    print(
        f"  {tag:<24} torch_reserved={torch.cuda.memory_reserved() / 2**30:7.2f} GiB   "
        f"driver_used={(total_b - free_b) / 2**30:7.2f} GiB",
        flush=True,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("expandable", choices=["on", "off"])
    p.add_argument("--drop-small", action="store_true", help="free the small tensor too")
    p.add_argument("--gib", type=float, default=60.0)
    args = p.parse_args()

    torch.cuda.set_device(0)
    torch.cuda.init()

    torch._C._accelerator_setAllocatorSettings(f"expandable_segments:{args.expandable == 'on'}")
    print(f"\nexpandable_segments={args.expandable}  drop_small={args.drop_small}  big={args.gib} GiB")

    small = torch.ones(8 * 1024 * 1024, dtype=torch.bfloat16, device="cuda")
    show("small tensor only")

    big = [
        torch.ones(int(2.0 * 2**30 / 2), dtype=torch.bfloat16, device="cuda")
        for _ in range(max(1, int(args.gib / 2.0)))
    ]
    show("after big alloc")

    del big
    if args.drop_small:
        del small
    torch.cuda.empty_cache()
    show("after free+empty_cache")

    segs = torch.cuda.memory_snapshot()
    for s in segs:
        print(
            f"    segment: total={s['total_size'] / 2**30:6.2f} GiB  "
            f"allocated={s['allocated_size'] / 2**30:6.2f} GiB  expandable={s.get('is_expandable')}"
        )


if __name__ == "__main__":
    main()