# 固定环境、模型、数据与容器布局

> 此页记录2026-09-12实验配置及当时交付状态，不是当前机器的实时服务监控。完整原始lab根目录为 `/home/yuhanya/uni-agent-lab`。

## 硬件与系统

| 项目 | 记录 |
|---|---|
| GPU | 8×AMD Instinct MI350X，gfx950，每卡约252GiB可见显存 |
| CPU | 384逻辑CPU |
| 系统RAM | 约3TiB |
| 宿主系统 | Ubuntu22.04，内核5.15系列，完整值见证据 |
| 外层Docker | 29.1.1 |
| 内层sandbox daemon | Docker29.8.0的固定DinD镜像 |
| 模型权重 | 约57GiB，BF16，16个safetensors shard |
| 专用daemon镜像用量 | 一次统计为36个镜像、81.56GB，含任务与工具镜像 |

[GPU计算检查](../evidence/provenance/gpu-preflight.txt) · [lscpu](../evidence/provenance/host-lscpu.txt) · [内核](../evidence/provenance/host-uname.txt) · [sandbox磁盘统计](../evidence/provenance/sandbox-disk-usage.txt)。

## 固定源码与依赖

| 项目 | 固定版本 |
|---|---|
| Uni-Agent | `10743439dd0a19da44a94cccad069b135d957bf1`，加本地日志fork补丁 |
| 随附verl | `a9f2985159536a607211dcac730d3f5d55028950` |
| Python | 3.12.13（本地CPU运行环境） |
| Torch | `2.12.0+git6bbd260` |
| Torch HIP构建 | `7.2.53211` |
| vLLM安装包 | `0.28.1rc1.dev337+g27a94d1ce.rocm723` |
| Transformers | 5.16.1 |
| Ray | 2.54.1 |
| swebench | 4.1.0 |
| Claude Code | 2.1.236 |
| mini-swe-agent / LiteLLM | 2.2.8 / 1.81.7 |
| E2B SDK | 2.49.1 |

版本及hash来自[provenance](../evidence/provenance/runtime.json)。主作业在加入日志fork补丁之前启动，后续官方示例和回归验证使用该补丁；模型、agent提示和评分没有因此重新采样或改写。

固定CPU/GPU基础镜像：

```text
vllm/vllm-openai-rocm@sha256:91e381f072d6a44e1e4c97c82dce06e50e5189905cb3999a11471c5a8fc6a563
image ID: sha256:037450d42652d33826174ac1a6f9f8ef2ec47c51042aa4becee52d44be132862
```

固定DinD镜像：

```text
docker@sha256:5efed980cba3fc126cf54e21a5a6ff8849d05b6e0623d6e7612f48e9cd6cd17e
```

固定janitor基础镜像：

```text
python@sha256:97490e383c4cffb12825431fa24e3d2b70e39fd691a8e33c46bf4c18edca3998
```

镜像tag用于识别，严格复现按digest。保留镜像中的ROCm Torch，以58项CPU覆盖锁补充所需包；不通过无约束安装替换Torch。完整训练环境的依赖约束尚未验收。

## 模型与数据

```text
模型：Qwen/Qwen3-Coder-30B-A3B-Instruct
revision：b2cff646eb4bb1d68355c01b18ae02e7cf42d120

数据：princeton-nlp/SWE-bench_Verified
revision：c104f840cc67f8b6eec6f759ebc8b2693d585d4a
选题seed：20260912
```

[模型文件SHA](../evidence/provenance/model-files.json) · [数据与parquet SHA](../evidence/summary/manifest.json) · [合格28题SHA与名单](../evidence/summary/validated-manifest.json) · [任务镜像清单](../evidence/summary/image-preparation.json)。

## 容器、GPU与地址

| 名称 | 角色 | GPU / 容器内地址 | 宿主端口 |
|---|---|---|---|
| `ua-lab-cpu` | Python作业、Uni-Agent、Gateway、Docker客户端 | 无GPU；172.30.90.2 | Gateway按作业分配临时端口 |
| `ua-lab-sandbox-daemon` | 独立dockerd | 无GPU；172.30.90.4 | 专用Unix socket |
| `ua-lab-model-tp1` | 64K eager基线 | GPU0；172.30.90.3:8000 | 127.0.0.1:18082 |
| `ua-lab-model-128k` | 正式SWE的128K eager服务 | GPU1；172.30.90.5:8000 | 127.0.0.1:18083 |
| `ua-lab-model-tp4` | 四卡TP对照 | GPU4–7；172.30.90.6:8000 | 127.0.0.1:18084 |
| `ua-lab-model-aiter` | 单卡AITER+图执行 | GPU2；172.30.90.7:8000 | 127.0.0.1:18085 |
| `ua-lab-model-aiter-replica` | 第二优化副本 | GPU3；172.30.90.8:8000 | 127.0.0.1:18086 |
| `ua-lab-janitor` | 过期任务回收 | 无GPU，network none | 专用Unix socket |

这些实例按实验阶段启动，不代表八卡一直同时用于一个任务。最初交付保留128K基线与AITER主服务，其他辅助模型停止；当前状态应通过只读status命令查询。

## 主要资源设置

- CPU编排：12CPU、48GiB RAM、4GiB共享内存，不映射GPU。
- 单卡模型：24CPU、192GiB RAM；`HIP_VISIBLE_DEVICES`选择GPU，显存比例0.65。
- TP4模型：48CPU、256GiB RAM，4张GPU。
- 正式任务sandbox：2CPU、8GiB、pids512；限制capabilities和提权。
- daemon：24CPU、128GiB；可信控制面使用privileged。
- janitor：0.25CPU、64MiB、read-only、cap-drop ALL。

模型服务预留的显存包含KV cache和运行时，不能当作权重文件大小。显卡型号、DNS、可用磁盘或源码依赖变化后，应在新节点重新做基础与任务控制。
