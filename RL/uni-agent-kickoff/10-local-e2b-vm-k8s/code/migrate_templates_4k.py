#!/usr/bin/env python3
"""Rebuild existing template rootfs images under the deployed 4 KiB page policy.

The API and template manager must already use E2B_SANDBOX_HUGE_PAGES=false.
This script records build completion; verify the resulting FC page size separately.
Registration and start are not idempotent API operations. Their intent is saved
before POST, and an ambiguous POST is never automatically repeated on restart.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit
import uuid

import httpx


DEFAULT_CLIENT_CONFIG = Path("/home/yuhanya/.config/e2b-local-20260916/client.json")
TERMINAL_PHASES = {"ready", "failed", "identity_mismatch"}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Redactor:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def __call__(self, value):
        if isinstance(value, dict):
            return {
                key: "<redacted>" if re.search(
                    r"api.?key|authorization|password|secret|access.?token|refresh.?token",
                    str(key), re.I,
                ) else self(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            value = value.replace(self.api_key, "<redacted>")
            value = re.sub(r"\be2b_[A-Za-z0-9_-]{12,}", "<redacted>", value)
            value = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer <redacted>", value)
            value = re.sub(
                r"(?i)((?:api[_-]?key|x-api-key|authorization|password|secret|"
                r"access[_-]?token)\s*[:=]\s*)[^\s,;\"']+",
                r"\1<redacted>", value,
            )
            return value[:32768]
        return value


def entry_key(item: dict) -> str:
    return f"{item['template_id']}:{item['build_id']}"


def fingerprint(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()


def load_inventory(path: Path) -> list[dict]:
    items = json.loads(path.read_text())
    if not isinstance(items, list):
        raise ValueError("inventory must be a JSON array")
    required = {"template_id", "build_id", "names", "tags", "cpu_count", "memory_mb",
                "min_free_disk_mb", "start_cmd", "ready_cmd"}
    seen_keys, template_tags = set(), {}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or required - item.keys():
            raise ValueError(f"inventory item {index} is missing required fields")
        for field in ("template_id", "build_id"):
            if not isinstance(item[field], str) or not item[field].strip():
                raise ValueError(f"inventory item {index}: invalid {field}")
        uuid.UUID(item["build_id"])
        for field in ("names", "tags"):
            values = item[field]
            if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not value or value != value.strip()
                for value in values
            ):
                raise ValueError(f"inventory item {index}: {field} must be nonempty strings")
        if ":" in item["names"][0]:
            raise ValueError(f"inventory item {index}: first name must not contain a tag")
        for field in ("cpu_count", "memory_mb", "min_free_disk_mb"):
            if type(item[field]) is not int or item[field] < (0 if field == "min_free_disk_mb" else 1):
                raise ValueError(f"inventory item {index}: invalid {field}")
        for field in ("start_cmd", "ready_cmd"):
            if item[field] is not None and not isinstance(item[field], str):
                raise ValueError(f"inventory item {index}: {field} must be a string or null")
        key = entry_key(item)
        if key in seen_keys:
            raise ValueError(f"inventory has duplicate template/build at item {index}")
        seen_keys.add(key)
        tags = template_tags.setdefault(item["template_id"], set())
        if tags.intersection(item["tags"]):
            raise ValueError(f"inventory has overlapping tags for template {item['template_id']}")
        tags.update(item["tags"])
    return items


@contextmanager
def state_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another migration process is using this state file") from error
        yield
    finally:
        os.close(descriptor)


class StateStore:
    def __init__(self, path: Path, api_url: str, redact: Redactor):
        self.path, self.redact = path, redact
        self.data = json.loads(path.read_text()) if path.exists() else {
            "version": 1, "api_url": api_url, "created_at": timestamp(), "entries": {},
        }
        if self.data.get("version") != 1 or not isinstance(self.data.get("entries"), dict):
            raise ValueError("unsupported or invalid migration state")
        if self.data.get("api_url") != api_url:
            raise ValueError("state belongs to a different API URL")

    def get(self, item: dict) -> dict:
        entry = self.data["entries"].get(entry_key(item), {})
        if entry and entry.get("input_sha256") != fingerprint(item):
            raise ValueError(f"inventory changed for recorded entry {entry_key(item)}")
        return entry

    def pending_for_template(self, item: dict) -> list[str]:
        return [key for key, entry in self.data["entries"].items()
                if key != entry_key(item) and entry.get("template_id") == item["template_id"]
                and entry.get("phase") not in {"ready", "failed"}
                and (entry.get("registration_attempted_at") or entry.get("new_build_id"))]

    def update(self, item: dict, **changes) -> dict:
        # All callers run on one asyncio loop; this operation has no await.
        entry = self.data["entries"].setdefault(entry_key(item), {
            "template_id": item["template_id"], "source_build_id": item["build_id"],
            "name": item["names"][0], "tags": item["tags"],
            "input_sha256": fingerprint(item), "created_at": timestamp(),
        })
        entry.update(self.redact(changes))
        entry["updated_at"] = timestamp()
        self.data["updated_at"] = timestamp()
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.redact(self.data), output, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return entry


class RequestFailure(Exception):
    def __init__(self, detail: dict):
        super().__init__(detail.get("error", "API request failed"))
        self.detail = detail


def is_schema_rejection(detail: dict) -> bool:
    # This exact prefix is emitted by the API's OpenAPI middleware before
    # dispatching the build-start handler. Other 400s may follow side effects.
    body = detail.get("body")
    return (detail.get("http_status") == 400 and isinstance(body, dict)
            and body.get("code") == 400 and isinstance(body.get("message"), str)
            and body["message"].startswith(
                "validation error: error in openapi3filter.RequestError: request body has an error:"
            ))


class Migrator:
    def __init__(self, client: httpx.AsyncClient, state: StateStore, timeout: float,
                 poll_interval: float, redact: Redactor):
        self.client, self.state = client, state
        self.timeout, self.poll_interval, self.redact = timeout, poll_interval, redact

    async def request(self, method: str, path: str, deadline: float, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("template migration exceeded its time budget")
        try:
            response = await self.client.request(
                method, path, timeout=min(60.0, remaining), **kwargs,
            )
        except httpx.HTTPError as error:
            raise RequestFailure(self.redact({
                "method": method, "path": path, "error": str(error),
            })) from error
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text[:32768]
        detail = self.redact({"method": method, "path": path,
                              "http_status": response.status_code, "body": body})
        if not response.is_success:
            detail["error"] = f"HTTP {response.status_code}"
            raise RequestFailure(detail)
        return body, detail

    def result(self, item: dict, started: float, phase: str, **changes) -> bool:
        entry = self.state.update(
            item, phase=phase, attempt_elapsed_seconds=round(time.monotonic() - started, 3),
            **changes,
        )
        print(json.dumps(self.redact({
            "template_id": item["template_id"], "source_build_id": item["build_id"],
            "new_build_id": entry.get("new_build_id"), "phase": phase,
            "elapsed_seconds": entry["attempt_elapsed_seconds"],
            "error": entry.get("error") if phase != "ready" else None,
        })), flush=True)
        return phase == "ready"

    async def migrate(self, item: dict) -> bool:
        started, last_status = time.monotonic(), None
        deadline = started + self.timeout
        entry = self.state.get(item)
        if entry.get("phase") == "ready":
            return True
        if entry.get("phase") in TERMINAL_PHASES:
            return False
        if entry.get("registration_attempted_at") and not entry.get("new_build_id"):
            saved = entry.get("registration_response", {}).get("body")
            if isinstance(saved, dict) and saved.get("templateID") == item["template_id"] and saved.get("buildID"):
                entry = self.state.update(item, phase="registered", new_build_id=saved["buildID"])
            else:
                return self.result(item, started, "needs_reconciliation",
                                   error="registration may have completed; recover its build ID before continuing")
        try:
            if not entry.get("new_build_id"):
                # Persist intent before sending this non-idempotent POST.
                self.state.update(item, phase="registering", registration_attempted_at=timestamp())
                body, response = await self.request("POST", "/v3/templates", deadline, json={
                    "name": item["names"][0], "tags": item["tags"],
                    "cpuCount": item["cpu_count"], "memoryMB": item["memory_mb"],
                    "minFreeDiskMb": item["min_free_disk_mb"],
                })
                # Save the response before validation, including an unexpected ID.
                self.state.update(item, registration_response=response)
                if not isinstance(body, dict) or not isinstance(body.get("buildID"), str):
                    raise ValueError("registration returned no buildID; reconciliation required")
                uuid.UUID(body["buildID"])
                if body.get("templateID") != item["template_id"]:
                    return self.result(item, started, "identity_mismatch",
                                       unexpected_template_id=body.get("templateID"),
                                       unexpected_build_id=body["buildID"],
                                       error="registered templateID differs from the inventory; build was not started")
                if uuid.UUID(body["buildID"]) == uuid.UUID(item["build_id"]):
                    raise ValueError("registration returned the source build ID")
                entry = self.state.update(item, phase="registered", new_build_id=body["buildID"])

            build_id = entry["new_build_id"]
            if uuid.UUID(build_id) == uuid.UUID(item["build_id"]):
                raise ValueError("saved new_build_id is the source build ID; reconciliation required")
            suffix = f"{quote(item['template_id'], safe='')}/builds/{quote(build_id, safe='')}"
            status_path = f"/templates/{suffix}/status"
            while time.monotonic() < deadline:
                try:
                    status, response = await self.request(
                        "GET", status_path, deadline, params={"limit": 0},
                    )
                except RequestFailure as error:
                    self.state.update(item, last_poll_error=error.detail)
                    code = error.detail.get("http_status")
                    if code is not None and code < 500 and code not in (408, 429):
                        raise
                    await asyncio.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
                    continue
                if not isinstance(status, dict) or status.get("templateID") != item["template_id"] or status.get("buildID") != build_id:
                    raise ValueError("status response does not match the registered template/build")
                last_status = status.get("status")
                entry = self.state.update(item, last_status=last_status, status_response=response)
                if last_status == "ready":
                    return self.result(item, started, "ready", completed_at=timestamp(), error=None)
                if last_status == "error":
                    return self.result(item, started, "failed", error="template build failed",
                                       failure_response=response)
                if last_status not in ("waiting", "building"):
                    raise ValueError(f"unexpected build status: {last_status!r}")
                if last_status == "waiting" and entry.get("phase") == "start_rejected":
                    return self.result(item, started, "start_rejected",
                                       error="OpenAPI rejected start; correct this build manually before continuing")
                if last_status == "waiting" and not entry.get("start_attempted_at") and not entry.get("start_observed_at"):
                    # A waiting status can be stale. Only a build for which this
                    # script has NEVER attempted start is eligible for POST.
                    self.state.update(item, phase="starting", start_attempted_at=timestamp())
                    try:
                        _, response = await self.request("POST", f"/v2/templates/{suffix}", deadline, json={
                            "fromTemplate": f"{item['names'][0]}:{item['build_id']}",
                            "steps": [], "force": True,
                            # OpenAPI accepts omitted/string commands, but not
                            # JSON null even though the Go model uses pointers.
                            "startCmd": item["start_cmd"] if item["start_cmd"] is not None else "",
                            "readyCmd": item["ready_cmd"] if item["ready_cmd"] is not None else "",
                        })
                        self.state.update(item, phase="started", start_response=response)
                    except RequestFailure as error:
                        if is_schema_rejection(error.detail):
                            return self.result(item, started, "start_rejected",
                                               error="OpenAPI rejected start before the build handler ran",
                                               start_error=error.detail)
                        # The request may have reached the builder. Poll the same
                        # ID; never repeat start, including after process restart.
                        self.state.update(item, phase="start_unknown", start_error=error.detail)
                elif last_status == "building":
                    self.state.update(item, phase="building",
                                      start_observed_at=entry.get("start_observed_at") or timestamp())
                await asyncio.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
            phase = "needs_reconciliation" if last_status == "waiting" else "timed_out"
            return self.result(item, started, phase, error="build did not become ready within the time budget")
        except (RequestFailure, TimeoutError, ValueError) as error:
            entry = self.state.get(item)
            phase = "needs_reconciliation" if not entry.get("new_build_id") else "interrupted"
            detail = error.detail if isinstance(error, RequestFailure) else {"error": str(error)}
            return self.result(item, started, phase, error=str(error), failure_response=detail)


async def run(args, items: list[dict], state: StateStore, config: dict, redact: Redactor) -> int:
    semaphore = asyncio.Semaphore(args.concurrency)
    template_locks = {item["template_id"]: asyncio.Lock() for item in items}
    async with httpx.AsyncClient(
        base_url=config["api_url"].rstrip("/"),
        headers={"X-API-Key": config["api_key"], "User-Agent": "e2b-local-4k-migration/1.0"},
        follow_redirects=False, trust_env=False,
    ) as client:
        migrator = Migrator(client, state, args.timeout, args.poll_interval, redact)

        async def process(item):
            async with template_locks[item["template_id"]]:
                async with semaphore:
                    pending = state.pending_for_template(item)
                    if pending:
                        return migrator.result(item, time.monotonic(), "blocked",
                                               error="another build for this template needs resolution",
                                               blocked_by=pending)
                    return await migrator.migrate(item)

        results = await asyncio.gather(*(process(item) for item in items))
    print(json.dumps({"processed": len(results), "ready": sum(results),
                      "incomplete": len(results) - sum(results)}), flush=True)
    return 0 if all(results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--client-config", type=Path, default=DEFAULT_CLIENT_CONFIG)
    parser.add_argument("--template-id", action="append", help="only migrate this template ID; repeatable")
    parser.add_argument("--limit", type=int, help="maximum number of unfinished entries this run")
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument("--timeout", type=float, default=600, help="seconds per entry in this invocation")
    parser.add_argument("--poll-interval", type=float, default=3)
    parser.add_argument("--dry-run", action="store_true", help="validate and print targets without requests or state writes")
    args = parser.parse_args(argv)
    if (args.limit is not None and args.limit < 1) or args.timeout <= 0 or args.poll_interval <= 0:
        parser.error("limit, timeout, and poll-interval must be positive")
    config = json.loads(args.client_config.read_text())
    if not isinstance(config.get("api_key"), str) or not config["api_key"]:
        raise ValueError("client config must contain a nonempty api_key")
    redact = Redactor(config["api_key"])
    try:
        parsed = urlsplit(config["api_url"])
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("client api_url must be an HTTP(S) URL without credentials or query")
        config["api_url"] = config["api_url"].rstrip("/")
        items = load_inventory(args.inventory)
        if args.template_id:
            requested = set(args.template_id)
            if requested - {item["template_id"] for item in items}:
                raise ValueError("requested template ID was not found in inventory")
            items = [item for item in items if item["template_id"] in requested]

        def select(state):
            selected = [item for item in items if state.get(item).get("phase") != "ready"]
            # Resolve previous POSTs before registering another build of the same
            # template, whose start could cancel a still-running predecessor.
            selected.sort(key=lambda item: not bool(
                state.get(item).get("registration_attempted_at") or state.get(item).get("new_build_id")
            ))
            skipped = len(items) - len(selected)
            print(json.dumps({"inventory_entries": len(items), "already_ready": skipped}), flush=True)
            return selected[:args.limit] if args.limit is not None else selected

        if args.dry_run:
            state = StateStore(args.state, config["api_url"], redact)
            for item in select(state):
                print(json.dumps(redact({"template_id": item["template_id"],
                                         "fromTemplate": f"{item['names'][0]}:{item['build_id']}",
                                         "tags": item["tags"], "force": True,
                                         "phase": state.get(item).get("phase", "new")})))
            return 0
        with state_lock(args.state):
            state = StateStore(args.state, config["api_url"], redact)
            return asyncio.run(run(args, select(state), state, config, redact))
    except Exception as error:
        print(redact(f"migration failed: {error}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted; saved build IDs will be reused on restart", file=sys.stderr)
        sys.exit(130)
