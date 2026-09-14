# Miles 独立 CPU 冷重建记录（2026-09-12）

本次从没有 `envs/miles` 的隔离目录重新建立 Miles overlay，37 个锁定版本全部一致，核心与补丁模块的 CPU 导入通过。全程未映射 GPU，未启动 Ray、SGLang 服务、模型或训练。此结果补充了环境复现证据，不重新验证此前的 GPU 训练结果。

任务从 13:49:41 UTC 开始，14:11 完成环境与导入校验，14:13 完成记录归档，约 24 分钟。隔离目录为 `/path/to/uni-agent-reproduction-check-v3-20260912`；实际验证记录和脚本已复制到主实验新增的 results/miles-cold-rebuild-v3-20260912（原始文件：`results/miles-cold-rebuild-v3-20260912`） 中。主实验环境、历史锁文件、源码与冻结评测输入均未修改。

| 检查 | 实际结果 |
| --- | --- |
| 固定镜像 | `lmsysorg/sglang-rocm@sha256:b82c13f1ab16690ba00974d1c38885e9934665d13a3ec1d2a6a615ae3ec072cd` |
| 镜像 config ID | `sha256:334898a4b0cfbecbf18d788a0687011cfbb2d817e8e9b5ea694717308cd1de67`，与历史镜像一致 |
| Python | `3.10.12` |
| Torch 运行时版本 | `2.9.1+rocm7.2.0.git7e1940d4` |
| Torch distribution metadata | `2.9.1+rocm7.2.0.lw.git7e1940d4`；metadata 本身包含 `.lw`，安装前后没有变化 |
| HIP | `7.2.26015-fc0010cf6a` |
| Torch 来源 | `/opt/venv/lib/python3.10/site-packages/torch/__init__.py`，路径、版本文件 SHA 与 metadata 均保持不变 |
| Overlay pins | 37/37 与 `configs/miles_overlay_reproduction.lock` 完全一致，均安装在新 `/lab/envs/miles/lib/python3.10/site-packages` |
| SGLang 来源 | `/lab/src/sglang-miles/python/sglang/__init__.py`；实际 import 使用指定源码 |
| Miles 来源 | `/lab/src/miles/miles/__init__.py`，editable `miles==0.1.0` |
| CPU 边界 | Docker Devices 为空、DeviceRequests 为空、非 privileged；容器无 `/dev/kfd` 或 `/dev/dri`；Torch 检查为 0 GPU、不可用、未初始化 |
| 不变性 | 两个实验目录的历史锁、patch、相关源码文件、Git HEAD/diff 保持一致；主实验 14 个冻结输入与协议及事前快照一致 |

导入成功的模块包括 `sglang.srt.constants`、`miles.utils.arguments`、`miles.backends.fsdp_utils`，以及两处补丁所在的 `hf_sglang_triton_patch` 和 `loss_hub.math_utils`。两份补丁分别做了 `git apply --reverse --check`，均通过。没有调用补丁中的 GPU 数值计算函数。

实际使用的构建顺序保存在 build_overlay.py（原始文件：`results/miles-cold-rebuild-v3-20260912/build_overlay.py`）：固定镜像启动独立 CPU 容器，8 CPU、32 GiB 内存、8 GiB shm，只挂载隔离实验目录；建立 `--system-site-packages` venv；显式加入基础镜像 site-packages 和 SGLang-Miles 源码优先级 `.pth`；使用 `--no-deps -r` 安装 37 个 exact pins；在容器自己的 Git 配置中声明 `/lab/src/miles` 为 safe.directory；最后 `--no-deps -e` 安装 Miles。

两项实际失败和修正都保留了原始记录：

- 第一轮额外添加了 `cap-drop ALL`，使容器 root 不能向宿主用户拥有的结果目录写文件。该轮尚未创建 venv 或安装包。保留失败日志和已停止的容器，第二轮使用 Docker 默认 capabilities，仍无 GPU device、无 privileged 权限。没有更改宿主目录权限。
- Miles editable 构建首次遇到 Git `dubious ownership`。仅在新容器内添加精确的 `safe.directory=/lab/src/miles` 后重试成功，没有改宿主 Git 配置。修正与重试位于 known-corrections.json（原始文件：`results/miles-cold-rebuild-v3-20260912/known-corrections.json`） 和 resume_after_editable_failure.py（原始文件：`results/miles-cold-rebuild-v3-20260912/resume_after_editable_failure.py`）。

`pip check` 没有全通过。基础镜像已存在 TileLang 缺 `torch-c-dlpack-ext`，以及其 `apache-tvm-ffi` 版本声明不匹配。新 overlay 还显示 Miles 声明的 `mcp`、`memray`、`nvidia-resiliency-ext`、`onnxscript`、`qwen-vl-utils`、`ring-flash-attn`、`torchft-nightly` 未安装，以及系统 `pygobject` 缺 `pycairo`。这些声明问题保存在 base-pip-check.log（原始文件：`results/miles-cold-rebuild-v3-20260912/base-pip-check.log`） 和 overlay-pip-check.log（原始文件：`results/miles-cold-rebuild-v3-20260912/overlay-pip-check.log`）。本次保持历史固定 overlay 配方，没有为了让 `pip check` 变绿而增添软件包，因此不能据此声称整个 Miles 所有功能的依赖都满足。

Pip 提示宿主用户拥有的缓存目录未被 root 使用，因此下载缓存被禁用；安装成功，未复用旧 Miles overlay。新 overlay 占用约 681 MiB。镜像压缩层合计约 21.9 GiB，展开大小约 60.94 GiB；结束时 root 空闲 879.26 GiB、`/data2` 空闲 399.89 GiB，保留了后续 native checkpoint 及两次 HF 导出的空间。成功容器 `ua-lab-miles-cold-v3` 在校验末仅运行 `sleep infinity`，随后已停止以释放资源；第一轮容器 `ua-lab-miles-cold-v3-permission-attempt1` 也已停止。停止操作只涉及本次新增的两个 CPU 容器，见生命周期记录（原始文件：`results/miles-cold-rebuild-v3-20260912/container-lifecycle.json`）。

完整校验以 audit.json（原始文件：`results/miles-cold-rebuild-v3-20260912/audit.json`） 为准，简表见 summary.json（原始文件：`results/miles-cold-rebuild-v3-20260912/summary.json`），逐包版本与安装路径见 overlay-probe.json（原始文件：`results/miles-cold-rebuild-v3-20260912/overlay-probe.json`），归档文件 SHA 见 artifacts-sha256.json（原始文件：`results/miles-cold-rebuild-v3-20260912/artifacts-sha256.json`）。历史 lock 中“cold replay 尚未独立重跑”的注释为保留原始 hash 而没有改写；本记录提供这次独立重跑的新证据及适用范围。
