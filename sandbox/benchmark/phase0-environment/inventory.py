#!/usr/bin/env python3
"""Whitelisted read-only deployment inventory; credentials never serialized."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SSH = "/data2/yuhanya/e2b-local-20260916/code/vm-ssh"
PRIVATE_CONFIG = "/home/yuhanya/.config/e2b-local-20260916/client.json"
ENV_KEYS = {"E2B_SANDBOX_HUGE_PAGES", "E2B_MAX_SANDBOXES_PER_NODE", "HUGEPAGES", "NBDS_MAX", "NBD_POOL_SIZE"}
ANNOTATION_KEYS = {"local.e2b/api-sha256", "local.e2b/orchestrator-sha256", "local.e2b/runtime-version"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def command(args, timeout=30):
    # Never echo command output on failure (kubectl output may contain secrets).
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("read-only inventory command failed")
    return r.stdout.strip()


def disk(path):
    s = os.statvfs(path)
    return {"path": str(path), "size_bytes": s.f_blocks * s.f_frsize,
            "available_bytes": s.f_bavail * s.f_frsize, "free_inodes": s.f_favail}


def machine():
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Hugetlb"):
            memory[k] = int(v.split()[0]) * 1024
    models = {line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
              if line.startswith("model name")}
    return {"kernel": platform.release(), "architecture": platform.machine(), "logical_cpus": os.cpu_count(),
            "cpu_models": sorted(models), "memory_bytes": memory,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "uptime_seconds": float(Path("/proc/uptime").read_text().split()[0]), "root_filesystem": disk("/")}


def guest():
    result = machine()
    nodes = json.loads(command(["k3s", "kubectl", "get", "nodes", "-o", "json"]))
    pods = json.loads(command(["k3s", "kubectl", "-n", "e2b", "get", "pods", "-o", "json"]))
    result["nodes"] = [{"name": n["metadata"]["name"], "capacity": n["status"]["capacity"],
        "allocatable": n["status"]["allocatable"], "kubelet_version": n["status"]["nodeInfo"].get("kubeletVersion"),
        "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"].get("conditions", []))}
        for n in nodes["items"]]
    result["pods"] = []
    for pod in pods["items"]:
        containers = pod["spec"].get("initContainers", []) + pod["spec"].get("containers", [])
        statuses = pod["status"].get("initContainerStatuses", []) + pod["status"].get("containerStatuses", [])
        result["pods"].append({"name": pod["metadata"]["name"], "phase": pod["status"]["phase"],
            "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"].get("conditions", [])),
            "annotations": {k: v for k, v in pod["metadata"].get("annotations", {}).items() if k in ANNOTATION_KEYS},
            "containers": [{"name": c["name"], "image": c["image"], "resources": c.get("resources", {}),
                "selected_environment": {e["name"]: e.get("value") for e in c.get("env", []) if e["name"] in ENV_KEYS}}
                for c in containers],
            "container_status": [{"name": c["name"], "ready": c.get("ready"), "restarts": c.get("restartCount"),
                                  "state": list(c.get("state", {})), "image_id": c.get("imageID")} for c in statuses]})
    result["runtime_binary_sha256"] = {}
    for name in ("api", "orchestrator", "migrator"):
        path = Path("/opt/e2b-local/custom-runtime", name)
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        result["runtime_binary_sha256"][name] = {"sha256": h.hexdigest(), "bytes": path.stat().st_size}
    result["hugepages"] = {p.name: {k: int((p / k).read_text()) for k in ("nr_hugepages", "free_hugepages", "resv_hugepages")}
                           for p in Path("/sys/kernel/mm/hugepages").iterdir()}
    result["nbd_devices"] = int(Path("/sys/module/nbd/parameters/nbds_max").read_text())
    result["neighbor_thresholds"] = {family: {f"gc_thresh{i}": int(Path(f"/proc/sys/net/{family}/neigh/default/gc_thresh{i}").read_text())
                                            for i in (1, 2, 3)} for family in ("ipv4", "ipv6")}
    result["postgres_huge_pages"] = command(["k3s", "kubectl", "-n", "e2b", "exec", "e2b-0", "-c", "postgres", "--",
                                                 "psql", "-U", "postgres", "-d", "postgres", "-XAt", "-c", "SHOW huge_pages;"])
    result["checked_utc"] = utc()
    return result


def api_inventory(config_file):
    config = json.loads(Path(config_file).read_text())
    endpoint = config["api_url"].rstrip("/")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path, auth=False):
        headers = {"X-API-Key": config["api_key"]} if auth else {}
        request = urllib.request.Request(endpoint + path, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=20) as response:
                return response.status, response.headers, response.read(8 << 20)
        except urllib.error.HTTPError as error:
            return error.code, {}, b""
    status, _, _ = get("/health")
    result = {"endpoint": endpoint, "health_http": status, "checked_utc": utc(), "pagination_complete": False}
    unique = {}; token = None; seen_tokens = set(); pages = 0
    while pages < 100:
        params = {"limit": "100"}
        if token:
            params["nextToken"] = token
        code, headers, body = get("/v2/sandboxes?" + urllib.parse.urlencode(params), auth=True)
        result["list_http"] = code
        if code != 200:
            result["pagination_complete"] = False; break
        data = json.loads(body)
        if not isinstance(data, list):
            raise ValueError("unexpected sandbox-list schema")
        for row in data:
            # Keep neither commands nor metadata, cursor, team identifiers, or key.
            ident = row.get("sandboxID") or row.get("sandboxId") or row.get("sandbox_id")
            if not ident:
                raise ValueError("sandbox-list row is missing its identity")
            unique[ident] = {k: row[k] for k in ("state", "cpuCount", "memoryMB", "diskSizeMB") if k in row}
        pages += 1
        token = headers.get("X-Next-Token")
        if not token:
            result["pagination_complete"] = True; break
        if token in seen_tokens:
            result["pagination_complete"] = False; result["cursor_repeated"] = True; break
        seen_tokens.add(token)
    result.update(pages=pages, listed_unique_count=len(unique),
                  states=dict(Counter(str(x.get("state", "unspecified")) for x in unique.values())),
                  scope="credential team; point-in-time paginated view; concurrent changes are possible")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path)
    p.add_argument("--vm-ssh", default=DEFAULT_SSH)
    p.add_argument("--client-config", default=PRIVATE_CONFIG)
    p.add_argument("--guest", action="store_true")
    args = p.parse_args()
    if args.guest:
        print(json.dumps(guest())); return
    if args.output is None:
        p.error("--output is required")
    os.umask(0o077)
    started = utc()
    host = machine()
    host["data2_filesystem"] = disk("/data2")
    raw = command(["systemctl", "show", "e2b-sandbox-vm.service", "-p", "MainPID", "-p", "ActiveState", "-p", "SubState", "-p", "CPUAffinity", "-p", "LimitNOFILE"])
    host["qemu_service"] = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    qemu_pid = host["qemu_service"].get("MainPID")
    if qemu_pid and qemu_pid != "0":
        argv = Path("/proc", qemu_pid, "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        host["qemu_selected_arguments"] = {flag: argv[argv.index(flag) + 1] for flag in ("-m", "-smp") if flag in argv}
    r = subprocess.run([args.vm_ssh, "sudo python3 - --guest"], input=Path(__file__).read_text(),
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
    if r.returncode:
        raise RuntimeError("guest inventory failed; no unfiltered output emitted")
    source = Path("/data2/yuhanya/e2b-local-20260916/build/runtime-4k-20260920")
    commit = command(["git", "-C", str(source), "rev-parse", "HEAD"])
    dirty = subprocess.run(["git", "-C", str(source), "diff", "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    report = {"schema_version": 1, "started_utc": started, "host": host, "vm": json.loads(r.stdout),
              "api": api_inventory(args.client_config), "completed_utc": utc(),
              "inventory_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "runtime_source": {"path": str(source), "head": commit, "tracked_changes_present": dirty == 1,
                                  "note": "source tree may differ from deployed binary; use actual binary hashes"},
              "actions": ["local proc/sysfs reads", "VM proc/sysfs reads", "kubectl get", "read-only SHOW huge_pages", "binary hashes", "HTTP GET health/list"],
              "excludes": ["credentials", "sandbox commands and metadata", "pod full environment", "application logs"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as f:
        json.dump(report, f, indent=2); f.write("\n")
    print(json.dumps({"output": str(args.output), "health_http": report["api"]["health_http"],
                      "listed_count": report["api"]["listed_unique_count"], "pagination_complete": report["api"].get("pagination_complete")}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
