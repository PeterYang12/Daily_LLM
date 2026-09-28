#!/usr/bin/env python3
"""Finite, foreground /proc sampler; one SSH connection, no service changes.

Uses only the Python standard library. The guest mode is sent over SSH stdin;
it installs nothing in the VM. Output never includes process command lines,
environment variables, sandbox commands, credentials, or application logs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

SCHEMA = 1
DEFAULT_SSH = "/data2/yuhanya/e2b-local-20260916/code/vm-ssh"
HZ = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def utc():
    return datetime.now(timezone.utc).isoformat()


def fields(path):
    return {k: int(v) for k, v in (line.split() for line in Path(path).read_text().splitlines())}


def pressure():
    out = {}
    for name in ("cpu", "memory", "io"):
        try:
            out[name] = {line.split()[0]: {k: float(v) for k, v in
                         (item.split("=") for item in line.split()[1:])}
                         for line in Path("/proc/pressure", name).read_text().splitlines()}
        except OSError:
            out[name] = None
    return out


def process(pid):
    p = Path("/proc", str(pid))
    data = (p / "stat").read_text().rsplit(")", 1)[1].split()
    io = None
    try:
        io = {k: int(v) for k, v in (line.split(":", 1) for line in (p / "io").read_text().splitlines())}
    except (PermissionError, FileNotFoundError):
        pass
    return {"start_ticks": int(data[19]), "ticks": int(data[11]) + int(data[12]),
            "state": data[0], "rss_bytes": int(data[21]) * PAGE, "io": io}


def process_scan():
    """Count executable identities, excluding wrappers and zombies."""
    counts = {"firecracker": 0, "orchestrator": 0, "api": 0}
    fds = {"orchestrator": [], "api": []}
    denied = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            name = Path(os.readlink(p / "exe").removesuffix(" (deleted)")).name
            if name not in counts:
                continue
            state = (p / "stat").read_text().rsplit(")", 1)[1].split()[0]
            if state in ("Z", "X"):
                continue
            counts[name] += 1
            if name in fds:
                fds[name].append(len(list((p / "fd").iterdir())))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            denied += 1
    return {"live_process_counts": counts, "service_fd_counts": fds,
            "permission_denied": denied}


def cgroups():
    rows = {}
    for p in Path("/sys/fs/cgroup/e2b").glob("sbx-*"):
        try:
            cpu = fields(p / "cpu.stat")
            quota, period = (p / "cpu.max").read_text().split()
            rows[p.name] = {"usage_usec": cpu["usage_usec"],
                            "throttled_usec": cpu.get("throttled_usec", 0),
                            "nr_throttled": cpu.get("nr_throttled", 0),
                            "memory_bytes": int((p / "memory.current").read_text()),
                            "populated": fields(p / "cgroup.events")["populated"],
                            "quota_cores": None if quota == "max" else int(quota) / int(period)}
        except (OSError, ValueError, KeyError):
            continue
    return rows


def arp():
    try:
        lines = Path("/proc/net/stat/arp_cache").read_text().splitlines()
        keys = lines[0].split()
        rows = [dict(zip(keys, (int(x, 16) for x in line.split()))) for line in lines[1:]]
        # entries is a shared gauge repeated on CPU rows; do not sum it.
        return {"entries": max((x["entries"] for x in rows), default=0),
                "table_fulls": sum(x.get("table_fulls", 0) for x in rows),
                "forced_gc_runs": sum(x.get("forced_gc_runs", 0) for x in rows),
                "gc_thresh3": int(Path("/proc/sys/net/ipv4/neigh/default/gc_thresh3").read_text())}
    except (OSError, ValueError, KeyError):
        return None


def snapshot(*, guest=False, pids=()):
    began = time.monotonic()
    cpu = {}
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("cpu"):
            a = line.split(); cpu[a[0]] = [int(x) for x in a[1:9]]
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        if k in ("MemTotal", "MemAvailable", "Cached", "SwapTotal", "SwapFree", "Hugetlb"):
            memory[k] = int(v.split()[0]) * 1024
    disks = {}
    for line in Path("/proc/diskstats").read_text().splitlines():
        a = line.split(); name = a[2]
        # Physical host devices only; guest vda plus individual NBD requests.
        if (guest and (name == "vda" or name.startswith("nbd"))) or (not guest and name.startswith("nvme") and "p" not in name):
            disks[name] = [int(x) for x in a[3:]]
    network = {}
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, values = line.split(":"); name = name.strip()
        if name.startswith(("eth", "en")):
            network[name] = [int(x) for x in values.split()]
    selected = {}
    for pid in pids:
        try:
            selected[str(pid)] = process(pid)
        except (OSError, ValueError, IndexError):
            selected[str(pid)] = None
    file_nr = [int(x) for x in Path("/proc/sys/fs/file-nr").read_text().split()]
    result = {"utc": utc(), "monotonic": time.monotonic(), "cpu": cpu, "memory_bytes": memory,
              "disks": disks, "network": network, "pressure": pressure(),
              "load_average": list(os.getloadavg()), "selected_processes": selected,
              "fd_allocated": file_nr[0], "fd_unused": file_nr[1], "fd_limit": file_nr[2]}
    if guest:
        result.update(cgroups=cgroups(), arp=arp(), processes=process_scan())
        result["nbd_devices"] = len(list(Path("/sys/block").glob("nbd*")))
        result["nbd_attached"] = sum(1 for p in Path("/sys/block").glob("nbd*/pid") if p.is_file())
    result["collection_ms"] = (time.monotonic() - began) * 1000
    return result


def delta(new, old):
    return max(0, new - old)


def cpu_metrics(new, old):
    d = [delta(a, b) for a, b in zip(new, old)]
    total = sum(d)
    return {"busy_pct": 100 * (sum(d[:3]) + d[5] + d[6]) / total if total else 0,
            "iowait_pct": 100 * d[4] / total if total else 0,
            "steal_pct": 100 * d[7] / total if total else 0}


def sample(before, after, *, per_sandbox=False):
    dt = after["monotonic"] - before["monotonic"]
    if dt <= 0:
        raise ValueError("sample interval must be positive")
    result = {k: after[k] for k in ("utc", "memory_bytes", "pressure", "load_average", "fd_allocated", "fd_unused", "fd_limit", "collection_ms")}
    result["interval_seconds"] = dt
    result["monotonic_seconds"] = after["monotonic"]
    result["cpu"] = cpu_metrics(after["cpu"]["cpu"], before["cpu"]["cpu"])
    cores = [cpu_metrics(v, before["cpu"][k])["busy_pct"] for k, v in after["cpu"].items()
             if k != "cpu" and k in before["cpu"]]
    result["cpu"].update(logical_cpus=len(cores), busy_core_equivalents=result["cpu"]["busy_pct"] * len(cores) / 100,
                         max_core_busy_pct=max(cores, default=0))
    result["disks"] = {}
    for name, row in after["disks"].items():
        if name not in before["disks"]:
            continue
        d = [delta(a, b) for a, b in zip(row, before["disks"][name])]
        if name.startswith("nbd") and not (d[0] or d[4] or row[8]):
            continue
        ios = d[0] + d[4]
        result["disks"][name] = {"read_bytes_s": d[2] * 512 / dt, "write_bytes_s": d[6] * 512 / dt,
                                  "iops": ios / dt, "await_ms": (d[3] + d[7]) / ios if ios else 0,
                                  "busy_pct": d[9] / dt / 10, "avg_queue": d[10] / dt / 1000,
                                  "in_flight": row[8]}
    result["network"] = {}
    for name, row in after["network"].items():
        if name not in before["network"]:
            continue
        d = [delta(a, b) for a, b in zip(row, before["network"][name])]
        result["network"][name] = {"rx_bytes_s": d[0] / dt, "tx_bytes_s": d[8] / dt,
                                    "rx_errors": d[2], "rx_drops": d[3], "tx_errors": d[10], "tx_drops": d[11]}
    result["selected_processes"] = {}
    for pid, row in after["selected_processes"].items():
        previous = before["selected_processes"].get(pid)
        if row is None or previous is None or row["start_ticks"] != previous["start_ticks"]:
            result["selected_processes"][pid] = {"continuous_identity": False}
            continue
        item = {"continuous_identity": True, "cpu_cores": delta(row["ticks"], previous["ticks"]) / HZ / dt,
                "rss_bytes": row["rss_bytes"], "state": row["state"]}
        if row["io"] is not None and previous["io"] is not None:
            item["read_bytes_s"] = delta(row["io"].get("read_bytes", 0), previous["io"].get("read_bytes", 0)) / dt
            item["write_bytes_s"] = delta(row["io"].get("write_bytes", 0), previous["io"].get("write_bytes", 0)) / dt
        result["selected_processes"][pid] = item
    if "cgroups" in after:
        rows = []
        for name, row in after["cgroups"].items():
            item = {"name": name, **row}; previous = before["cgroups"].get(name)
            item["cpu_cores"] = delta(row["usage_usec"], previous["usage_usec"]) / 1e6 / dt if previous else None
            item["throttled_seconds"] = delta(row["throttled_usec"], previous["throttled_usec"]) / 1e6 if previous else None
            rows.append(item)
        result["cgroup_summary"] = {"count": len(rows), "populated": sum(x["populated"] for x in rows),
            "memory_bytes": sum(x["memory_bytes"] for x in rows),
            "cpu_cores_continuous_only": sum(x["cpu_cores"] or 0 for x in rows),
            "new_cgroups": len(set(after["cgroups"]) - set(before["cgroups"])),
            "exited_cgroups": len(set(before["cgroups"]) - set(after["cgroups"])),
            "throttled_seconds_continuous_only": sum(x["throttled_seconds"] or 0 for x in rows)}
        if per_sandbox:
            result["sandbox_cgroups"] = rows
        for k in ("nbd_devices", "nbd_attached", "processes", "arp"):
            result[k] = after[k]
        if result["arp"] is not None and before["arp"] is not None:
            result["arp"] = {**result["arp"], "table_fulls_delta": delta(after["arp"]["table_fulls"], before["arp"]["table_fulls"])}
    return result


def lag_summary(path):
    """Optional load-generator-produced JSON; do not infer its lag from ours."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
        raw = data["event_loop_lag_ms"]
        allowed = {k: v for k, v in raw.items() if k in ("n", "p50", "p95", "p99", "max")
                   and isinstance(v, (int, float)) and not isinstance(v, bool)}
        return {"event_loop_lag_ms": allowed, "file_mtime_unix": Path(path).stat().st_mtime}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"available": False}


def guest_main(args):
    previous = snapshot(guest=True)
    deadline = time.monotonic()
    for index in range(args.samples):
        deadline += args.interval
        time.sleep(max(0, deadline - time.monotonic()))
        schedule_lag = max(0, time.monotonic() - deadline) * 1000
        current = snapshot(guest=True)
        record = sample(previous, current, per_sandbox=args.per_sandbox)
        record.update(sample_index=index, sampler_schedule_lag_ms=schedule_lag)
        print(json.dumps(record, separators=(",", ":")), flush=True)
        previous = current


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path)
    p.add_argument("--vm-ssh", default=DEFAULT_SSH)
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--samples", type=int, default=12)
    p.add_argument("--pid", type=int, action="append", default=[], help="host PID; may be repeated; no descendant inference")
    p.add_argument("--event-lag-file", type=Path)
    p.add_argument("--stop-file", type=Path, help="stop when this file appears; output remains valid")
    p.add_argument("--per-sandbox", action="store_true")
    p.add_argument("--guest", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if not (1 <= args.interval <= 60 and 1 <= args.samples <= 86400):
        p.error("interval must be 1..60 seconds and samples 1..86400")
    if args.guest:
        guest_main(args); return
    if args.output is None:
        p.error("--output is required")
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    remote = f"sudo python3 - --guest --interval {args.interval} --samples {args.samples}"
    if args.per_sandbox:
        remote += " --per-sandbox"
    previous = snapshot(pids=args.pid)
    interrupted = False
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    child = subprocess.Popen([args.vm_ssh, remote], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, bufsize=1)
    count = 0
    status = "complete"
    try:
        child.stdin.write(Path(__file__).read_text()); child.stdin.close()
        with args.output.open("x") as output, selectors.DefaultSelector() as selector:
            output.write(json.dumps({"record_type": "metadata", "schema_version": SCHEMA, "started_utc": utc(),
                "interval_seconds": args.interval, "requested_samples": args.samples, "host_pids": args.pid,
                "per_sandbox": args.per_sandbox, "sampler_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "scope": "read-only finite foreground sampling"}) + "\n")
            output.flush()
            selector.register(child.stdout, selectors.EVENT_READ)
            last = time.monotonic()
            while count < args.samples:
                if interrupted or (args.stop_file and args.stop_file.exists()):
                    status = "stopped"; break
                if not selector.select(timeout=1):
                    if time.monotonic() - last > args.interval + 30:
                        status = "ssh_timeout"; break
                    continue
                line = child.stdout.readline()
                if not line:
                    status = "ssh_ended_early"; break
                guest = json.loads(line)
                current = snapshot(pids=args.pid)
                row = {"record_type": "sample", "received_utc": utc(), "guest": guest,
                       "host": sample(previous, current), "load_generator": lag_summary(args.event_lag_file)}
                output.write(json.dumps(row, separators=(",", ":")) + "\n"); output.flush()
                previous = current; count += 1; last = time.monotonic()
            output.write(json.dumps({"record_type": "completion", "status": status, "samples": count, "ended_utc": utc()}) + "\n")
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait(timeout=5)
    print(json.dumps({"status": status, "samples": count, "output": str(args.output)}))
    if status not in ("complete", "stopped"):
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # The foreground client went away; no unattended remote sampler remains.
        os._exit(0)
