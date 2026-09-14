# E01：模型服务与官方example

## 目的

验证AMD环境能执行目标30B模型，并确认Uni-Agent真实agent入口可以调用模型、操作仓库和完成评分。模型API连通性与完整agent任务分别验证。

## 模型和运行环境

- 模型：`Qwen/Qwen3-Coder-30B-A3B-Instruct`，约30B总参数、3B激活的MoE，BF16。
- 固定模型revision：`b2cff646eb4bb1d68355c01b18ae02e7cf42d120`。
- 机器：8×MI350X，gfx950，每卡约252GiB可见显存。
- 基础镜像：`vllm/vllm-openai-rocm`，按digest固定；完整版本见[环境清单](../reproduce/01-environment-and-versions.md)。
- 权重约57GiB，因此本次单卡即可容纳。没有为了用满八卡而把所有任务都配置成TP8。

## 做了什么

### GPU基础计算

用Docker映射`/dev/kfd`与`/dev/dri`，Torch检测到8个设备；每个设备执行16×16矩阵乘，结果符合预期。ROCm版Torch仍使用`torch.cuda.*`这套API名称，`torch.version.hip`提供AMD构建信息。

记录：[gpu-preflight.txt](../evidence/provenance/gpu-preflight.txt)。这证明设备基础可用，不能代替多卡训练验证。

### 四种模型API

| 检查 | 请求/响应内容 | 结果 | 支撑的后续功能 |
|---|---|---|---|
| Chat Completions | 让模型只回复OK | HTTP 200，返回文本 | 常规ReAct/模型客户端 |
| 结构化tool call | 提供`add`工具schema，请求19与23相加 | HTTP 200，返回结构化工具调用 | Agent决定后续工具动作；此项本身不执行工具 |
| Anthropic Messages | 用兼容messages协议请求OK | HTTP 200 | Claude Code直接API接入 |
| token-ID Completions | 发送token ID，要求返回token ID及logprob | HTTP 200，返回原始token数据 | Gateway轨迹构建 |

eager服务与单卡AITER服务都完成了这四项检查。对应原始记录位于原实验目录 `results/model-smoke/`、`results/model-smoke-aiter/`。

### 官方Uni-Agent推理入口

实际执行了上游 `examples/inference/parallel_infer_api.py`。它对已启动的模型API发请求，使用Uni-Agent任务/agent实现运行SWE题目。

| 配置 | 题目 | 结果 | 记录的作业wall time |
|---|---|---:|---:|
| ReAct + eager vLLM | `pallets__flask-5014` | 1/1通过 | 198.9秒 |
| Claude Code + eager vLLM | 同题 | 1/1通过 | 117.7秒 |
| ReAct + AITER服务，日志并发适配后的验证 | 同题 | 1/1通过 | 72.5秒 |

这里的Claude是实际CLI。题目要求Flask Blueprint名称不能为空，agent读取仓库、修改代码，官方测试认可修复。不同运行的轨迹并不相同，不能用上述单题wall time直接计算纯推理加速比。

另用记录更完整的driver，在AITER服务上执行了一次ReAct + Gateway + 新容器判题，1/1通过，记录wall time约50.8秒。它作为优化服务的功能验收，不加入正式28题分数。

## 得到了什么

1. 30B模型能够在本机ROCm Docker环境中实际推理。
2. ReAct和真实Claude Code能通过官方入口执行真实仓库任务。
3. OpenAI/Anthropic兼容接口与原始token输出可用，为两类harness与Gateway连接提供基础。
4. AITER配置不仅有微基准输出，也经过接口和实际任务检查；尚未对完整28题或500题重新做质量回归。

## 证据

- [官方ReAct结果](../evidence/summary/official-api-react.json)
- [官方Claude Code结果](../evidence/summary/official-api-claude.json)
- [优化服务官方入口结果](../evidence/summary/official-api-react-aiter-fixed.json)
- [优化服务Gateway任务结果](../evidence/summary/aiter-react-swe-smoke.json)
- 模型服务脚本：[64K eager](../reproduce/scripts/serve_model.sh)、[128K eager](../reproduce/scripts/serve_model_128k.sh)、[AITER](../reproduce/scripts/serve_model_aiter.sh)。
