"""Probe why a freed block does not go back to the driver under expandable_segments.

Companion to ``expandable_strand.py``. The rule this script demonstrates:

    Physical pages return to the driver when a *segment* is destroyed, and a segment is
    destroyed only once it has zero live blocks.

``expandable_segments`` matters only because it funnels every allocation of a given pool into
one growable segment. So a single surviving block keeps that whole segment -- and every page
under it -- checked out, while ``memory_reserved()`` only counts the surviving block and
therefore looks nearly empty. Which pool the survivor lands in decides which segment it holds,
and torch keeps separate pools either side of a 1 MiB threshold:

  * ``large-first`` / ``large-last`` -- a 16 MiB survivor shares the blocks' large-pool
    segment, so nothing is returned. Both variants behave the same, i.e. whether the survivor
    sits before or after the blocks inside the segment makes no difference.
  * ``small-pool`` -- a 512 KiB survivor gets its own small-pool segment, the large-pool
    segment empties out, and the blocks are returned.
  * ``none`` -- control.
  * ``--expandable off`` -- every block is its own segment, so the survivor holds only itself
    and any mode returns cleanly.

Each run ends by dropping the survivor, which should return the pages immediately: that is the
same segment going from one live block to zero.

Numbers are printed as deltas against a baseline taken at startup, because other processes
sharing the GPU make the absolute driver figure meaningless.

    python3 strand_probe.py large-first
    python3 strand_probe.py large-last
    python3 strand_probe.py small-pool
    python3 strand_probe.py none
    python3 strand_probe.py large-first --expandable off
    python3 strand_probe.py large-first --cycles 3   # shows the pages are reused, not leaked
"""

import argparse

import torch

# Survivor sizes either side of torch's 1 MiB large/small pool threshold.
POOL_SURVIVOR_BYTES = {"large": 16 * 2**20, "small": 512 * 2**10}

# mode -> (which pool the survivor lands in, whether it precedes or follows the big blocks)
MODES = {
    "none": (None, None),
    "large-first": ("large", "before"),
    "large-last": ("large", "after"),
    "small-pool": ("small", "before"),
}

BLOCK_BYTES = 2 * 2**30


def driver_used_gib():
    free_b, total_b = torch.cuda.mem_get_info()
    return (total_b - free_b) / 2**30


def bf16_of_size(nbytes):
    return torch.ones(nbytes // 2, dtype=torch.bfloat16, device="cuda")


def segment_summary():
    by_pool = {}
    for s in torch.cuda.memory_snapshot():
        count, total = by_pool.get(s["segment_type"], (0, 0))
        by_pool[s["segment_type"]] = (count + 1, total + s["total_size"])
    if not by_pool:
        return "none"
    return "  ".join(f"{pool}={n}seg/{t / 2**30:.2f}GiB" for pool, (n, t) in sorted(by_pool.items()))


def show(tag, baseline):
    print(
        f"  {tag:<26} driver_delta={driver_used_gib() - baseline:+7.2f} GiB  "
        f"torch_reserved={torch.cuda.memory_reserved() / 2**30:6.2f} GiB  "
        f"segments: {segment_summary()}"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=list(MODES))
    p.add_argument("--expandable", choices=["on", "off"], default="on")
    p.add_argument("--gib", type=float, default=20.0, help="total size of the blocks to free")
    p.add_argument("--cycles", type=int, default=1, help=">1 shows whether pages are reused or leaked")
    p.add_argument("--device", type=int, default=0)
    args = p.parse_args()

    torch.cuda.set_device(args.device)
    torch.cuda.init()
    torch._C._accelerator_setAllocatorSettings(f"expandable_segments:{args.expandable == 'on'}")

    survivor_pool, survivor_order = MODES[args.mode]
    print(
        f"\nmode={args.mode}  expandable={args.expandable}  "
        f"survivor={survivor_pool or 'none'}  blocks={args.gib} GiB"
    )

    # Touch the device first so the CUDA context is up before the baseline is taken, otherwise
    # its overhead shows up as a constant offset in every delta below.
    del_me = bf16_of_size(2**20)
    del del_me
    torch.cuda.empty_cache()

    baseline = driver_used_gib()
    free_gib = torch.cuda.mem_get_info()[0] / 2**30
    if free_gib < args.gib + 2.0:
        raise SystemExit(
            f"only {free_gib:.1f} GiB free on device {args.device} (other processes may be using it); "
            f"rerun with --gib below {max(0.0, free_gib - 2.0):.0f}"
        )
    show("baseline", baseline)

    # Held in a list so the survivor stays referenced until the very end.
    survivors = []
    if survivor_order == "before":
        survivors.append(bf16_of_size(POOL_SURVIVOR_BYTES[survivor_pool]))
        show("survivor allocated", baseline)

    for cycle in range(args.cycles):
        big = [bf16_of_size(BLOCK_BYTES) for _ in range(max(1, int(args.gib * 2**30 // BLOCK_BYTES)))]
        show(f"cycle {cycle}: blocks alloc", baseline)

        if survivor_order == "after" and cycle == 0:
            survivors.append(bf16_of_size(POOL_SURVIVOR_BYTES[survivor_pool]))
            show("survivor allocated", baseline)

        del big
        torch.cuda.empty_cache()
        show(f"cycle {cycle}: blocks freed", baseline)

    withheld = driver_used_gib() - baseline - torch.cuda.memory_reserved() / 2**30
    print(f"  -> {withheld:+.2f} GiB withheld from the driver but not counted by torch")

    if survivors:
        del survivors
        torch.cuda.empty_cache()
        show("survivor dropped", baseline)


if __name__ == "__main__":
    main()
