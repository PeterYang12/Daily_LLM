#!/usr/bin/env python3
"""SDK C64 diagnostic: warm every active connection and record safe RPC errors.

Imports the frozen phase1 runner without editing it. This is a supplemental
diagnostic, not evidence that connection warmup is the cause of an earlier error.
"""
from __future__ import annotations

import asyncio
import base64
from collections import Counter
from enum import Enum
import importlib.util
import json
from pathlib import Path
import re
import signal
import time
from urllib.parse import quote, quote_plus

RUNNER_PATH = Path(__file__).resolve().parents[2] / "phase1-service" / "code" / "runner.py"
_spec = importlib.util.spec_from_file_location("e2b_phase1_warmed_sdk_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

SENSITIVE_FIELD = re.compile(r"api.?key|access.?token|traffic.?token|envd.?token|authorization|password|cookie", re.I)


def validate_arguments(args):
    if (args.adapter != "sdk" or args.scenario != "exec_true" or args.mode != "closed_loop"
            or args.concurrency != 64 or args.residents != 64 or args.duration != 30
            or args.repeat < 1):
        raise ValueError("diagnostic requires --adapter sdk --scenario exec_true --mode closed_loop --concurrency 64 --residents 64 --duration 30 and positive repeat")


def known_credentials(bench):
    """Read only existing in-memory credential fields; never serialize objects."""
    secrets = set()
    def add(value):
        if isinstance(value, str) and value:
            secrets.add(value)
    def mapping(values):
        if hasattr(values, "items"):
            for key, value in values.items():
                if SENSITIVE_FIELD.search(str(key)):
                    add(value)
    mapping(getattr(bench, "cfg", {}))
    for sb in getattr(bench, "handles", {}).values():
        # These are the actual in-memory properties in the pinned E2B SDK.
        for name in ("_envd_access_token", "traffic_access_token", "_traffic_access_token"):
            try:
                add(getattr(sb, name, None))
            except Exception:
                pass
        try:
            mapping(vars(sb))  # includes any name-mangled token string fields
        except TypeError:
            pass
        try:
            mapping(sb._envd_api.headers)
        except (AttributeError, TypeError):
            pass
    return secrets


def redact_message(message, secrets):
    variants = set()
    for secret in secrets:
        variants.update((secret, quote(secret, safe=""), quote_plus(secret),
                         json.dumps(secret)[1:-1], base64.b64encode(secret.encode()).decode()))
    text = str(message)
    # Redact before truncation, otherwise a partial token could survive a cut.
    for secret in sorted(variants, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"\b(?:e2b_|sk-)[A-Za-z0-9_-]{12,}\b", "<redacted-token>", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "<redacted-jwt>", text)
    text = re.sub(r'''(?i)\b(?:authorization|x-api-key|x-access-token|api[_-]?key|(?:envd[_-]?|traffic[_-]?)?access[_-]?token|password|cookie)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^,\n;}]+)''',
                  "<redacted-credential-field>", text)
    text = re.sub(r"(?i)([?&](?:token|key|signature|access_token|api_key)=)[^&#\s]+", r"\1<redacted>", text)
    return text[:2048]


def error_detail(bench, error):
    secrets = known_credentials(bench)
    code = getattr(error, "code", None)
    if isinstance(code, Enum):
        code = code.name
    elif code is not None:
        code = redact_message(str(code), secrets)[:80]
    message = getattr(error, "message", None)
    if not isinstance(message, str):
        message = str(error)
    return {"error_class": type(error).__name__, "rpc_code": code,
            "redacted_message": redact_message(message, secrets)}


class WarmedSDKBench(runner.Bench):
    def __init__(self, args):
        validate_arguments(args)  # reject unsupported use before config/client setup
        super().__init__(args)

    def write(self, kind, **data):
        self.base.update(scenario="exec_true_all_connections_warm",
                         comparison_key="exec_true_all_connections_warm-batch8-v2",
                         warmup_concurrency=8,
                         instrumentation="diagnostic-warmed")
        super().write(kind, **data)

    async def prepare(self):
        await super().prepare()
        if self.stop.is_set():
            raise runner.ForeignWorkload("stopped before all-slot warmup")
        if len(self.handles) != self.args.residents:
            raise runner.InvalidResult("resident count changed before all-slot warmup")
        start = time.monotonic()
        # The inherited warmup has index 0; these 64 IDs are mutually distinct,
        # never overlap the measurement IDs, and stay outside self.rows.
        # Warm with the already-characterized low-concurrency path, then test
        # C64. This avoids replaying the very C64 cold burst being controlled.
        gate = asyncio.Semaphore(8)
        async def warm(slot):
            async with gate:
                return await self.measured(-(slot+2), window="warmup", slot=slot)
        rows = await asyncio.gather(*(warm(slot) for slot in range(self.args.concurrency)))
        valid = all(r["status"] == "success" and r.get("state_check") is True for r in rows)
        self.write("all_connections_warmup", slots=self.args.concurrency,
                   duration_ms=(time.monotonic() - start) * 1000,
                   status_counts=dict(Counter(r["status"] for r in rows)), validated=valid)
        if not valid:
            raise runner.InvalidResult("one or more all-slot warmup operations failed")
        if self.stop.is_set():
            raise runner.ForeignWorkload("stopped during all-slot warmup")
        await self.guard()

    async def action(self, index, slot):
        try:
            return await super().action(index, slot)
        except Exception as error:
            try:
                details = error_detail(self, error)
            except Exception:
                # Preserve parent accounting without exposing an unredacted
                # message if a future SDK object has an unexpected shape.
                details = {"error_class": type(error).__name__, "rpc_code": None,
                           "redacted_message": "<diagnostic unavailable>"}
            self.write("operation_error_detail", operation="exec_true",
                       operation_id=f"{self.run_id}:{index}", worker_id=slot,
                       observed_offset_s=self.offset(), all_slot_warmup=index <= -2,
                       **details)
            raise


async def main():
    args = runner.arguments()
    validate_arguments(args)
    bench = WarmedSDKBench(args)
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, bench.stop.set)
    return await bench.run()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
