#!/usr/bin/env python3
"""Verify migrated templates through their original IDs after HugeTLB is removed.

Creates at most --concurrency short-lived sandboxes, checks configured resources,
reads back one small /tmp file, and verifies Firecracker RAM from the VM host.
Every sandbox returned by the SDK is killed in finally. No model is invoked.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time
import uuid

from e2b import AsyncSandbox, AsyncTemplate


ROOT = Path("/data2/yuhanya/e2b-local-20260916")
CLIENT_CONFIG = Path("/home/yuhanya/.config/e2b-local-20260916/client.json")
HUGE_FIELDS = ("AnonHugePages", "ShmemPmdMapped", "FilePmdMapped", "Private_Hugetlb", "Shared_Hugetlb")


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value, key):
    if isinstance(value, dict):
        return {name: redact(item, key) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        value = value.replace(key, "<redacted>")
        return re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer <redacted>", value)[:8192]
    return value


def save_report(path, report, key):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(redact(report, key), output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_targets(inventory_path, state_path):
    inventory = json.loads(inventory_path.read_text())
    state = json.loads(state_path.read_text())
    if not isinstance(inventory, list) or not inventory or not isinstance(state.get("entries"), dict):
        raise ValueError("inventory or migration state has an invalid format")
    targets, seen = [], set()
    for item in inventory:
        template_id = item["template_id"]
        if template_id in seen:
            raise ValueError("inventory must contain one entry per template")
        seen.add(template_id)
        migration = state["entries"].get(f"{template_id}:{item['build_id']}", {})
        if migration.get("phase") != "ready" or not migration.get("new_build_id"):
            raise ValueError(f"template {template_id} has not completed migration")
        if uuid.UUID(migration["new_build_id"]) == uuid.UUID(item["build_id"]):
            raise ValueError(f"template {template_id} still names its source build")
        for field in ("cpu_count", "memory_mb"):
            if type(item.get(field)) is not int or item[field] <= 0:
                raise ValueError(f"template {template_id} has invalid {field}")
        tags = item.get("tags")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag for tag in tags):
            raise ValueError(f"template {template_id} has no valid tag")
        tag = "default" if "default" in tags else tags[0]
        targets.append({
            "template_id": template_id,
            "target": template_id if tag == "default" else f"{template_id}:{tag}",
            "tag": tag, "expected_build_id": migration["new_build_id"],
            "cpu_count": item["cpu_count"], "memory_mb": item["memory_mb"],
        })
    return targets


async def read_page_evidence(sandbox_id, script):
    if not re.fullmatch(r"[a-z0-9]+", sandbox_id):
        raise ValueError("SDK returned an invalid sandbox ID")
    process = await asyncio.create_subprocess_exec(
        str(ROOT / "code/vm-ssh"), "sudo", "python3", "-", "--sandbox-id", sandbox_id,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(script), timeout=30)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    try:
        data = json.loads(stdout)
    except ValueError as error:
        raise RuntimeError(f"page verifier returned no JSON (exit {process.returncode}): {stderr.decode(errors='replace')[:2048]}") from error
    machine = data.get("machine_config", {})
    mappings, smaps = data.get("guest_mappings", []), data.get("guest_smaps", [])
    summary = {
        "pid": data.get("pid"), "exit_code": process.returncode,
        "four_kib_verified": data.get("four_kib_verified") is True,
        "huge_pages": machine.get("huge_pages"),
        "machine_vcpu_count": machine.get("vcpu_count"),
        "machine_memory_mb": machine.get("mem_size_mib"),
        "page_sizes": sorted({mapping["page_size"] for mapping in mappings}),
        "kernel_page_sizes": sorted({vma.get("KernelPageSize") or "missing" for vma in smaps}),
        "mmu_page_sizes": sorted({vma.get("MMUPageSize") or "missing" for vma in smaps}),
        "hugepage_kib": sum(int((vma.get(field) or "0 kB").split()[0]) for vma in smaps for field in HUGE_FIELDS),
        "problems": data.get("problems", []),
    }
    return summary


async def check_tag_binding(target, config):
    tags = await AsyncTemplate.get_tags(target["template_id"], api_key=config["api_key"],
                                        api_url=config["api_url"], request_timeout=30)
    matches = [tag.build_id for tag in tags if tag.tag == target["tag"]]
    if len(matches) != 1 or uuid.UUID(matches[0]) != uuid.UUID(target["expected_build_id"]):
        raise RuntimeError("template tag does not point to the completed migration build")
    return matches[0]


async def verify_one(target, config, page_script, report, output, stop_new):
    started, sandbox = time.monotonic(), None
    result = {**target, "status": "running", "started_at": now(), "cleanup_ok": False}
    report["results"].append(result)
    save_report(output, report, config["api_key"])
    try:
        result["tag_build_before"] = await check_tag_binding(target, config)
        sandbox = await AsyncSandbox.create(
            target["target"], timeout=180, request_timeout=60,
            metadata={"purpose": "verify-migrated-4k-template", "run_id": report["run_id"]},
            **{name: config[name] for name in ("api_key", "api_url", "sandbox_url")},
        )
        result["sandbox_id"] = sandbox.sandbox_id
        save_report(output, report, config["api_key"])
        info = await sandbox.get_info(request_timeout=30)
        result["resources"] = {"cpu_count": info.cpu_count, "memory_mb": info.memory_mb}
        if info.template_id != target["template_id"]:
            raise RuntimeError("created sandbox belongs to a different template")
        if info.cpu_count != target["cpu_count"] or info.memory_mb != target["memory_mb"]:
            raise RuntimeError("sandbox CPU/memory differs from the original inventory")

        marker = "e2b-4k-check-" + uuid.uuid4().hex
        path = "/tmp/" + marker
        await sandbox.files.write(path, marker, request_timeout=20)
        command = await sandbox.commands.run("cat " + shlex.quote(path), timeout=20)
        if command.exit_code != 0 or command.stdout != marker:
            raise RuntimeError("temporary file command/readback failed")
        if await sandbox.files.read(path, request_timeout=20) != marker:
            raise RuntimeError("temporary file SDK readback failed")
        await sandbox.files.remove(path, request_timeout=20)
        result["file_roundtrip_ok"] = True

        pages = await read_page_evidence(sandbox.sandbox_id, page_script)
        result["pages"] = pages
        if (pages["exit_code"] != 0 or not pages["four_kib_verified"]
                or pages["page_sizes"] != [4096] or pages["huge_pages"] not in (None, "None")
                or pages["kernel_page_sizes"] != ["4 kB"] or pages["mmu_page_sizes"] != ["4 kB"]
                or pages["hugepage_kib"] != 0):
            raise RuntimeError("Firecracker guest memory did not pass the 4 KiB verification")
        if pages["machine_vcpu_count"] != target["cpu_count"] or pages["machine_memory_mb"] != target["memory_mb"]:
            raise RuntimeError("Firecracker CPU/memory differs from the original inventory")
        result["tag_build_after"] = await check_tag_binding(target, config)
        result["tag_binding_verified"] = True
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        if sandbox is not None:
            for attempt in range(2):
                try:
                    deleted = await sandbox.kill(request_timeout=30)
                    result.update(cleanup_ok=True, cleanup_deleted=bool(deleted))
                    break
                except Exception as error:
                    result["cleanup_error"] = str(error)
                    if attempt == 0:
                        await asyncio.sleep(1)
            if not result["cleanup_ok"]:
                result["status"] = "failed"
                stop_new.set()
                report["cleanup_failed_sandbox_ids"].append(sandbox.sandbox_id)
        else:
            result["cleanup"] = "no sandbox ID returned by create"
        result.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
        save_report(output, report, config["api_key"])
        print(json.dumps(redact({name: result.get(name) for name in
                                 ("template_id", "sandbox_id", "status", "cleanup_ok", "elapsed_seconds", "error")},
                                config["api_key"])), flush=True)
    return result["status"] == "passed" and result["cleanup_ok"]


async def run(targets, config, output, concurrency):
    report = {"run_id": uuid.uuid4().hex, "status": "running", "started_at": now(),
              "template_count": len(targets), "concurrency": concurrency, "results": [],
              "cleanup_failed_sandbox_ids": []}
    page_script = (ROOT / "code/verify_guest_4k.py").read_bytes()
    semaphore = asyncio.Semaphore(concurrency)
    stop_new = asyncio.Event()

    async def limited(target):
        async with semaphore:
            if stop_new.is_set():
                report["results"].append({**target, "status": "skipped",
                                          "reason": "stopped after sandbox cleanup failure"})
                save_report(output, report, config["api_key"])
                return False
            return await verify_one(target, config, page_script, report, output, stop_new)

    try:
        results = await asyncio.gather(*(limited(target) for target in targets))
        report.update(status="passed" if all(results) else "failed",
                      passed=sum(results),
                      failed=sum(item["status"] == "failed" for item in report["results"]),
                      skipped=sum(item["status"] == "skipped" for item in report["results"]))
    finally:
        if report["status"] == "running":
            report["status"] = "interrupted"
        report["finished_at"] = now()
        save_report(output, report, config["api_key"])
    print(json.dumps({name: report[name] for name in ("status", "template_count", "passed", "failed", "skipped")}))
    return 0 if report["status"] == "passed" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--client-config", type=Path, default=CLIENT_CONFIG)
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("concurrency must be positive")
    if args.output.resolve() in {args.inventory.resolve(), args.state.resolve(), args.client_config.resolve()}:
        parser.error("output must not overwrite an input file")
    config = json.loads(args.client_config.read_text())
    key = config.get("api_key", "")
    if not isinstance(key, str) or not key:
        raise ValueError("client config has no API key")
    try:
        for name in ("api_url", "sandbox_url"):
            if not isinstance(config.get(name), str) or not config[name]:
                raise ValueError(f"client config has no {name}")
        targets = load_targets(args.inventory, args.state)
        return asyncio.run(run(targets, config, args.output, args.concurrency))
    except Exception as error:
        print(redact(f"verification failed: {error}", key), file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("verification interrupted; sandbox cleanup was attempted", file=sys.stderr)
        sys.exit(130)
