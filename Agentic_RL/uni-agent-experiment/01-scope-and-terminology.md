# 范围与术语：这次到底跑了什么

## 1. 实验的准确名称

**基于 Uni-Agent 组件的代码 agent 推理、sandbox 与评测实验。**

模型完成多轮推理，agent 读取和修改真实仓库、执行测试，系统保留轨迹与结果。模型使用固定公开权重，全程没有梯度反传、optimizer step、SFT 或 RL 参数更新。后训练收益没有被测量。

本目录中的“推理”包括整个多轮 agent 任务，不仅是一条聊天请求；其中 GPU 模型服务的性能微基准是另外一类实验。

## 2. 名称、职责与本次实现

| 名称 | 是什么 | 本次使用方式 |
|---|---|---|
| `verl-project/uni-agent` | GitHub 源码仓库／Python 框架 | 检出源码，在 CPU 容器内安装；不是本次使用的镜像名或容器名 |
| `vllm/vllm-openai-rocm` | Docker 基础镜像 | 提供兼容的 ROCm/PyTorch/vLLM 环境；可启动多个不同用途的容器 |
| `ua-lab-cpu` | 一个实际容器 | 运行评测脚本、Uni-Agent 组件、Gateway 和 ReAct 循环 |
| vLLM | 模型推理服务 | 在独立 GPU 容器中加载 Qwen，处理模型请求 |
| Qwen3-Coder-30B-A3B-Instruct | 模型权重 | 约30B总参数／3B激活的MoE，BF16；不是30B稠密模型 |
| Agent / harness | 围绕模型运转的执行程序 | 管理上下文、调用模型、执行工具、处理反馈、结束任务 |
| Sandbox | 代码执行环境 | 每题的仓库、进程和文件状态；本地使用独立Docker容器 |
| Task / verifier | 任务定义与评分 | 题目、初始仓库、测试补丁、测试结果与reward |
| Uni-Agent Gateway | 模型协议代理与session记录组件 | 接收agent请求，调用vLLM，记录token与工具反馈 |
| verl | 后训练基础框架 | 本次加载部分token/服务器基础组件，没有运行trainer |

**镜像和容器不同**：本次 CPU 编排容器与 GPU 模型容器采用同一种基础镜像，但它们是不同进程空间、不同挂载和不同设备权限的容器。在容器内 `pip install` Uni-Agent 后，`docker ps`仍显示原来的基础镜像名。

## 3. 三种agent的来源与位置

| 配置 | 真正的agent实现 | Uni-Agent提供什么 | 主循环位置 |
|---|---|---|---|
| `react` | Uni-Agent自带ReAct实现 | 循环、工具分发、预算与结果对象 | CPU编排容器 |
| `claude_code` | Anthropic的真实Claude Code CLI | 配置、启动、模型端点绑定、结果适配 | 每题sandbox |
| `mini_swe_agent` | 第三方mini-swe-agent | 配置、启动和结果适配 | 每题sandbox |

Claude Code程序和Claude模型是两个概念。本次真实CLI调用的是本地Qwen，未调用Anthropic模型。mini-swe-agent也是第三方项目，并非Uni-Agent自己实现的完整agent；它与名称相近的SWE-agent是不同项目。

Agent类型在作业配置中预先确定。正式实验分别运行三种配置，每种各28题；没有让一个任务在三种agent之间自动切换。

## 4. Uni-Agent的具体调用范围

本次确实调用了以下上游组件：

| 上游组件 | 用途 |
|---|---|
| `TaskConfigResolver`、`get_task` | 合并任务配置、渲染prompt、构建相应agent |
| `ReActAgent` | ReAct模型交互与工具循环 |
| `ClaudeCodeAgent`、`MiniSweAgentAgent` | 在已有sandbox中启动外部CLI |
| `Toolbox`、shell/editor/submit | ReAct的工具接口 |
| `DockerSandbox` | 创建/删除容器、执行命令、文件操作的基础实现 |
| `_GatewayActor`与session/codec | 模型请求转换与轨迹采集 |
| SWE-bench `compute_reward` | 用测试结果判断问题是否解决 |

**外层工作由实验driver完成**：并发限制、保存候选补丁、逐题证据、新容器回放和汇总是自写逻辑。正式28题并未直接调用完整训练侧 `AgentFrameworkRolloutAdapter` 或 `parallel_infer_verl.py`；也没有使用TransferQueue训练调度、参数同步或优化器。

官方 `examples/inference/parallel_infer_api.py` 对ReAct与Claude Code分别做了单题示例；正式主比较使用 `run_swe.py --gateway`调用上述组件。具体可查看[当时执行的脚本快照](reproduce/scripts/executed/run_swe.py)中的 `task.build_agent().run(...)` 和 `_GatewayActor(...)`。

## 5. 是API服务，还是脚本

本次以**一次性评测作业**启动。脚本运行期间创建Gateway HTTP服务，所有任务完成后关闭Gateway并退出。CPU容器可以继续存活，但不代表里面一直有一个Uni-Agent平台API在运行。

计划／下一步动作通常也来自模型。harness组织上下文，向模型提问；模型返回工具调用或文字；harness执行动作并把反馈放入下一轮上下文。没有另部署一个先完成全部plan的独立规划服务。

## 6. `verdal`、E2B与verl

本次没有安装或直接调用一个已明确识别为`verdal`的框架；实际连接的是用户给出的E2B兼容端点，客户端为E2B SDK。其后端品牌与虚拟化实现没有得到独立核实。

若“verdal”实际指的是“verl”，本次只复用了随Uni-Agent检出的部分基础组件，并未运行verl后训练。

接下来建议看[容器部署图](architecture/01-deployment.md)和[一题的执行流程](architecture/02-agent-task-flow.md)。
