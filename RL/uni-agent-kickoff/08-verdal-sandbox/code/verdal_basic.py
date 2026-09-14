#!/usr/bin/env python3
"""小型 E2B 2.49.1 示例：命令、文件回读、结束本次 sandbox。"""
from __future__ import annotations

import argparse
import getpass
from importlib.metadata import version
import json
import os
import sys
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    settings = {}
    for field, env_name in (("api_url", "E2B_API_URL"),
                            ("sandbox_url", "E2B_SANDBOX_URL"),
                            ("template_id", "E2B_TEMPLATE_ID")):
        settings[field] = os.environ.get(env_name, "").strip()
        if not settings[field]:
            raise SystemExit(f"请先设置 {env_name}；本示例不提供默认服务地址或模板。")
    if version("e2b") != "2.49.1":
        raise SystemExit('本示例依据 e2b==2.49.1；请在独立环境安装该版本。')
    from e2b import Sandbox

    key = os.environ.get("E2B_API_KEY")
    if not key:
        if not sys.stdin.isatty():
            raise SystemExit("非交互运行需通过环境注入 E2B_API_KEY。")
        key = getpass.getpass("E2B API key（不回显）: ").strip()
    if not key:
        raise SystemExit("API key不能为空。")

    sandbox = None
    exit_code = 0
    phase = "create"
    try:
        sandbox = Sandbox.create(
            template=settings["template_id"], timeout=60, request_timeout=20, debug=False,
            api_key=key, api_url=settings["api_url"], sandbox_url=settings["sandbox_url"],
            metadata={"purpose": "uni-agent-doc-example"},
        )
        phase = "command"
        result = sandbox.commands.run("printf 'verdal-example-ok\\n'; uname -s", timeout=10)
        if result.exit_code != 0 or "verdal-example-ok" not in result.stdout:
            raise RuntimeError("命令检查未通过")
        phase = "file_round_trip"
        path = f"/tmp/uni-agent-example-{uuid4().hex}.txt"
        payload = "Hello Verdal / 文件回读\n"
        sandbox.files.write(path, payload)
        if sandbox.files.read(path) != payload:
            raise RuntimeError("文件回读内容不一致")
        print(json.dumps({"status": "passed", "stdout": result.stdout,
                          "file_round_trip": True}, ensure_ascii=False))
    except Exception as error:
        # 不打印请求配置、key或可能包含认证内容的完整异常响应。
        print(json.dumps({"status": "failed", "phase": phase,
                          "error_type": type(error).__name__}), file=sys.stderr)
        exit_code = 1
    finally:
        if sandbox is not None:
            try:
                removed = sandbox.kill()
                print(json.dumps({"cleanup": "deleted" if removed else "already_absent"}))
            except Exception as error:
                print(json.dumps({"cleanup": "failed", "sandbox_id": sandbox.sandbox_id,
                                  "error_type": type(error).__name__}), file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
