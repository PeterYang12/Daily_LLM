# Uni-Agent CPU、Sandbox 与黑盒 Agent 实验记录

[本实验总览](README.md) · [全部实验](../README.md)

工作目录：`/path/to/uni-agent-lab`。驱动容器：`ua-lab-cpu`。

## 一键重建 CPU 环境

```bash
CPU_CONTAINER=ua-lab-cpu-replay-new bash /path/to/uni-agent-lab/scripts/cpu-reproduce.sh \
  /path/to/uni-agent-lab-replay-new
```

脚本使用本机同一份固定 ROCm 镜像、源码 HEAD 的两个 tar 归档和 81 项 venv 覆盖层精确依赖锁，在新目录中建立新的 CPU Docker 容器与 `--system-site-packages` venv。它不安装 CUDA torch，不修改宿主 Python，不覆盖已有 checkout / 容器，不带入旧 `.git` 配置或用户凭据。也会从实验目录复制已验证的 Claude 2.1.236 binary，恢复到新容器的独立 home。模型服务和独立 sandbox daemon 是主实验的单独组件，本脚本只负责 CPU driver 环境。

实际已经从归档重建到 `/path/to/uni-agent-lab-replay`、容器 `ua-lab-cpu-replay`，核心 Gateway / Framework / Agent / Sandbox / SWE-bench / Ray / Torch 导入检查通过，并断言 GPU 不可见。日志 `logs/cpu-reproduce-driver.log` 与新 lab 的 `logs/cpu-reproduce-{install,smoke}.log`；这是新环境导入验证，没有把原环境的 685 测试冒充为重跑结果。新的容器名可用 `CPU_CONTAINER=ua-lab-cpu-另一个名字` 指定；目标目录须为尚未存在 checkout 的 `/data2/...` 路径。

以上为第一阶段的真实重建记录。2026-09-12 观察到的宿主中断（原始文件：`results/environment/interruption-20260912.json`）使原容器消失，replay 的文件与日志保留；该 replay 容器不能直接 `docker start` 复用。主 `ua-lab-cpu` 后来已经恢复并验证（原始文件：`results/cpu-restoration-20260912.json`），81 项覆盖层版本一致。重新做空目录重建应使用上面的新名字和新目录。

重建材料：`results/cpu-uni-agent-source.tar.gz`、`results/cpu-verl-source.tar.gz`、`results/cpu-source-versions.json`、`results/cpu-overlay-requirements.lock`、`results/cpu-reproduce-inputs.sha256`。脚本使用不可变镜像 digest、验证 image ID 和输入 SHA256。完整环境 freeze 另外保存，覆盖层 lock 则只包含新装/覆盖的 Python 包，避免从 PyPI 重装镜像内的 ROCm torch/vLLM。模型、sandbox、任务镜像与数据的启动顺序和精确输入见 [复现审计](details/reproduction-audit.md) 及 `results/cpu-reproduction-inputs.json`。

## 环境与隔离

- 复用本机已有镜像 `vllm/vllm-openai-rocm:nightly-9ea8f3ffc354901b740f0b31988900897b7221d7`，image ID `sha256:33b992ce0f367784daf23c535ed63af3703e55ade7f5016a08e9c41a24a7a3b9`。
- CPU 容器：8 CPU、32 GiB memory、2 GiB shm，host network；**没有映射任何 GPU 设备**。
- `/lab` 仅挂载实验目录，未挂载用户 home；`/root` 为实验的独立空目录，真实用户凭据未注入。
- `/tmp`、pip cache、虚拟环境、输出均在 `/data2`。Docker socket 与 Docker CLI 仅用于本实验命名的 sandbox 容器。
- Python 3.12.13；独立 venv `/lab/envs/cpu` 通过 `--system-site-packages` 复用镜像内 ROCm torch 2.12.0 dev、vLLM 0.28.1 dev。它们与上游 CI 的 vLLM 0.23.0 有差异，未尝试用 CUDA wheel 覆盖 ROCm 依赖。
- 新增轻量 Python 依赖包括 ray 2.54.1、hydra-core、omegaconf、tensordict 0.10.0、swe-rex 1.4.0、swebench 4.1.0。安装输出保存在 `logs/cpu-install-*.log`。
- Claude Code 通过官方 native installer 安装进独立容器目录，版本 2.1.236。未读取真实 API key；后续本地端点使用 dummy key。

## 首次发现

1. 官方 CI 命令是 `pytest tests/uni_agent/ -m="cpu and level0"`，CI 还安装 SWE-bench v4.1.0。
2. 一旦安装了可选依赖 swe-rex，`tests/uni_agent/deployment/test_host_runtime.py` 会导入仓库中已不存在的 `uni_agent.deployment.host.deployment`，导致全套测试在 collection 阶段失败；这是旧测试与当前目录结构不一致。首次原始日志：`logs/cpu-collect-initial.log`。后续显式忽略这个文件，不修改公共源码。
3. 绑定的新 `/tmp` 初始权限不是 1777，导致 apt 的 `_apt` 无法创建临时文件，sandbox local demo 的首次 tmux 自动安装失败。已只对实验 `/tmp` 修正权限；原始日志 `logs/sandbox-local-demo.log`，重试另存。
4. 官方 mini-SWE recipe 默认要求 OpenYuanrong 账户和 reverse tunnel；其 agent 本身通过通用 `Sandbox.exec_shell` 运行，能用本地 Docker 做独立验证。该验证与官方 SWE-bench 训练配方要区分。

## 已完成结果

| Case | 结果 | 证据 |
|---|---|---|
| 官方 CPU level0（显式排除过期 deployment 测试） | **685 passed, 12 deselected**, 94.74 秒 | `logs/cpu-level0.log`、`results/cpu-level0.xml` |
| 官方 local sandbox demo，在 CPU Docker 容器内部运行 | **通过**；sum=7、编辑后 product=8，cwd 跨调用保持，文件读写/上传/下载断言全部通过 | `logs/sandbox-local-demo-retry.log` |
| 官方 Docker sandbox demo，driver 与执行 sandbox 为不同容器 | **通过**；同上，执行 sandbox 无 host 文件系统挂载、无 GPU | `logs/sandbox-docker-demo-retry.log` |
| 真实 Claude Code 2.1.236 → Gateway fake backend | **通过**；CLI 输出 OK、exit=0、1 条 trajectory | `results/blackbox-gateway/cpu-claude-fake/` |
| 真实 Claude Code → Gateway → Qwen3-4B-Instruct-2507，照 README 仅配置 response_length | **失败**；CLI 默认 max_tokens=32000 未被限制，16k vLLM 返回 400，0 条 trajectory | `results/blackbox-gateway/cpu-claude-real/` |
| 同上，同时配置 prompt_length=12000、response_length=64 | **通过**；CLI 输出 OK、exit=0、1 条 trajectory，prompt=1102 tokens、response=2 tokens | `results/blackbox-gateway/cpu-claude-real-budgeted/` |

当前源码版本：uni-agent `472c875a97f9a2764c81a6ec7581167632bd8bcc`（package `0.1.0.dev`），verl `10db40d0da4d59150bb389960b77585f81a89b8d`（package `0.10.0.dev0`）。完整 Python 包版本：`results/cpu-environment-freeze.txt`。

## 两个运行时坑

**Gateway budget 文档不符合当前实现。** `GatewaySession` 只有 `prompt_length` 与 `response_length` 同时非空时，才建立 `prompt_length + response_length` 的总上下文预算。仅传 `--response-length 64` 不会形成逐轮 64-token 限制。Claude Code 2.1.236 此时仍请求 32000 tokens，而本机 vLLM max_model_len=16384；实际失败已记录。安全复现方式是同时配置两者，使总和不超过推理服务的 max_model_len。对于自建 driver，还可配置 `allowed_request_sampling_param_keys` 不接受客户端的 `max_tokens`，用可信 session sampling_params 固定逐轮上限（toy harness 使用 1024）。

**独立 Docker daemon 的 cgroup v2 nesting。** 初版 daemon 直接运行 dockerd，创建子容器时失败 `cannot enter cgroupv2 /sys/fs/cgroup/docker ... invalid state`。主 agent 将 daemon 经 Docker 官方 `/usr/local/bin/dind` wrapper 启动后修复；没有修改主机 cgroup。独立 daemon socket 为 host `/path/to/uni-agent-lab/run/docker.sock` / CPU driver `/lab/run/docker.sock`，镜像和容器层存储均在 `/data2`。其 bridge 被禁用，因此子容器使用 `--network host`。

Gateway 正常 shutdown 时，最新 uvicorn/starlette 打印 `asyncio.exceptions.CancelledError` lifespan 日志；已成功写出输出，CLI returncode=0。这是退出日志噪声，不等于 case 失败。

## 复现命令

以下命令均从 host 执行，要求主记录中的 `ua-lab-cpu`、独立 sandbox daemon、Qwen 推理服务已启动。带固定名字的测试、gateway 和 toy 命令用于说明原实验配置；再次执行应为输出和 session 选择新名字。黑盒 SWE / toy driver 会拒绝覆盖已有结果。

```bash
# 官方测试
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  export RAY_ENABLE_UV_RUN_RUNTIME_ENV=0
  python -m pytest tests/uni_agent -m "cpu and level0" \
    --ignore=tests/uni_agent/deployment/test_host_runtime.py \
    --junitxml="/lab/results/cpu-level0-replay-$(date -u +%Y%m%dT%H%M%SZ).xml" -q
'

# 官方 sandbox demo，wrapper 仅设置本地现有镜像、容器名和资源/network 参数
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  export DOCKER_HOST=unix:///lab/run/docker.sock
  DEBUG_MODE=1 SANDBOX_IMAGE=python:3.12-slim \
    python /lab/scripts/sandbox-docker-demo.py
'

# 真实 Claude Code、本地 Qwen、原生 Anthropic Messages→Gateway→token completion
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:/root/.local/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  CLAUDE_CODE_MAX_RETRIES=0 python examples/gateway/debug_launcher.py \
    --backend openai-completions \
    --backend-base-url http://127.0.0.1:18080/v1 \
    --backend-model Qwen3-4B-Instruct-2507 \
    --tokenizer /lab/models/Qwen3-4B-Instruct-2507 \
    --session-id "cpu-claude-real-budgeted-replay-$(date -u +%Y%m%dT%H%M%SZ)" \
    --output-dir /lab/results/blackbox-gateway \
    --run-claude --claude-prompt "Reply with OK only." \
    --claude-timeout 120 --prompt-length 12000 --response-length 64
'
```
