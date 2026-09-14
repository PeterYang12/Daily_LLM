# Uni-Agent 项目现状、版本与本机适配判断

资料核对日期：**2026-09-11 UTC**。本文将官方声明、固定源码已实现的功能和本机待验证的适配分开记录。所有 benchmark 分数均来自官方公开资料，**不是本机复现结果**。本机实验结论由主实验记录汇总。

## 1. 项目定位与成熟度

官方仓库为 [verl-project/uni-agent](https://github.com/verl-project/uni-agent)，Apache-2.0 许可，定位是长程 agent 的推理、评估和强化学习运行框架，与 verl 紧密集成。它不是一个已经训练好的通用 agent 模型，也不是把单个 Agent SDK 装上就能直接训练的封装。

GitHub API 快照显示仓库创建于 2026-01-22，当前仍在频繁迭代。截至本次查询，**唯一公开 release 为 `v0.1.0rc1`，2026-08-26 发布，标记 prerelease**。release notes 明确说 RC 面向集成和可复现性测试，正式版前 API、配置和 recipe 仍可能变化；不能因为公告预计“两周后”正式发布就认定正式版已经存在。

| 本次需要区分的版本 | 值 | 含义 |
| --- | --- | --- |
| 实验固定 Uni-Agent | `472c875a97f9a2764c81a6ec7581167632bd8bcc` | 2026-09-11 的 Continuous Token 集成提交 |
| 该提交 gitlink 指定 verl | `a9f2985159536a607211dcac730d3f5d55028950` | 训练应优先与此配套，单独 clone 最新 verl 不等于使用子模块版本 |
| 初始同级 verl 副本 | `10db40d0da4d59150bb389960b77585f81a89b8d` | 研究时存在的较新副本；主实验另准备匹配 pin 的 `verl-rl` |
| 固定源码 package version | `0.1.0.dev` | 见 `uni_agent/version/version`，不能代替 Git commit |
| 查询时 GitHub main | `10743439dd0a19da44a94cccad069b135d957bf1` | 2026-09-11 10:59 UTC，已领先固定实验版本 |

版本漂移不是形式问题：`1074343` 合并的 [PR #144](https://github.com/verl-project/uni-agent/pull/144) 给 trajectory postprocessor 新增必需的 `task_result` keyword。旧 hook 不接这个参数就可能失效；本文架构按 `472c875` 旧契约描述，未把新 main 的 API 混进来。

官方状态的原始 JSON 已保存到 research/（原始文件：`notes/research`），文件内附 API URL 和 `retrieved_at`，包括 repo、release、roadmap、main 与 open issues 快照。这里的 open issues API 同时包含 PR，不宜把 GitHub 的总数解释为未修复 bug 数。

## 2. 固定版本已有的能力

| 能力 | 固定源码中的证据 | 判断 |
| --- | --- | --- |
| 白盒工具 agent | `uni_agent/agents/react/`、`uni_agent/tools/` | 有完整 ReAct、状态 shell、编辑器和终止工具 |
| 黑盒 Claude Code | `uni_agent/agents/claude_code/agent.py`、quickstart 配置 | CLI 接 Anthropic-compatible 端点，能指向本地 policy |
| 黑盒 Mini-SWE | `uni_agent/agents/mini_swe_agent/`、`examples/mini_swe_agent/` | 已实现，部分概念文档仍写 planned，需以源码为准 |
| MemAgent | `uni_agent/agents/mem_agent/`、`examples/mem_agent/` | 长上下文分块记忆、HotpotQA 训练与评估 recipe |
| 本地 Docker sandbox | `uni_agent/sandbox/docker.py` | 已合并，含拉取/启动 timeout，不能因旧 issue 未关闭就认为不存在 |
| Gateway 训练轨迹 | `gateway/session/`、`framework/framework.py` | 原始 token、mask、logprob、并发 chain、rollback 与 TQ 输出 |
| 多种模型协议 | `gateway/adapters/openai.py`、`anthropic.py` | 固定版支持 Chat Completions 与 Anthropic Messages |
| Colocate / Separate async | quickstart 训练脚本、MemAgent 脚本、verl V1 | 已有集成；硬件兼容性仍需实际验证 |
| Agent-aware router | 当天已合并的 PR #141、`agent_aware_router/` | 可选 KV 复用调度，有 vLLM 接口依赖 |
| 多模态 | Gateway processor / adapter / postprocess 代码 | 是部分基础能力，不能等同所有 GUI/multi-turn VLM recipe 完成 |

主 CI 在 Ubuntu 上对 Python 3.11、3.12 执行 `cpu and level0` 测试，见 [ci.yml](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/.github/workflows/ci.yml)。它不能证明 MI355 上推理、工具执行、长上下文训练或 8 卡分布式链路通过。`pyproject.toml` 接受 Python `>=3.10,<3.13`，应避免把某个 PR 作者在 3.13 的本地测试视为受支持范围。

## 3. AMD MI355：有上游路径，但不是现成的一键 Uni-Agent 配方

在固定 Uni-Agent 的 README、docs、examples、CI 与主体源码中未发现专门的 ROCm/MI355 recipe 或硬件验证说明。GPU 相关能力主要来自 **verl + PyTorch ROCm + vLLM/SGLang + 所用训练后端**。

与本实验配套的 [verl AMD quickstart（固定 a9f2985）](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/docs/amd_tutorial/amd_quick_start.rst) 明确列出：

| 项 | 上游文档声明 |
| --- | --- |
| GPU | MI300X/MI325X `gfx942`；MI350X/MI355X `gfx950` |
| 运行模式 | Fully Async、Colocate |
| 推理引擎 | vLLM、SGLang |
| 训练后端 | FSDP、FSDP2、Megatron |
| 参考镜像 | `amdagi/verl-dev:rocm7.14_torch2.12_release_0724` |
| 参考主机条件 | ROCm 7.14 driver stack，容器访问 `/dev/kfd` 与 `/dev/dri` |

这些是 **verl 文档的支持声明与参考软件组合**，不是必须修改本机驱动的指令，也不是本机现有镜像自动满足全部条件的证明。实际 kernel driver、容器用户态 ROCm、PyTorch、vLLM、attention kernel、模型架构都应一起记录，先在已有组合上做最小验证。

官方 AMD 文档还列出具体限制：`expandable_segments` 与 vLLM custom all-reduce 可能冲突，所以示例关闭 custom all-reduce；SGLang 要求 triton attention；部分 ROCm 修复尚在上游合并过程中，Dockerfile 带有补丁。ROCm Dockerfile 在不同提交还会固定不同的 vLLM / Megatron / TransferQueue 组合，所以只写“用了 ROCm latest”无法复现。

单节点 8 卡适合先完成小模型闭环，再增加规模。通用 dense Qwen 的短 context 通常比 Qwen3.5 hybrid attention、30B MoE、router replay 和 128K context 的完整组合更容易定位兼容问题，这是实现复杂度判断，不是本机速度或可用显存测量。Qwen3.5-4B 与 9B 的官方分数值得参考，但模型较小不等于它的所有 kernel 依赖也最少。

## 4. 官方模型、数据与参考成绩

下面仅选与起步相关的行，来源为固定版 [Inference and Verification](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/docs/source/benchmark/inference.md)：

| 模型与 harness | SWE-Bench Verified 条件 | 官方分数 |
| --- | --- | ---: |
| Qwen3.5-4B / ReAct | 100 turns、64K、Avg@1 | 45.2 |
| Qwen3.5-9B / ReAct | 100 turns、64K、Avg@1 | 53.8 |
| Qwen3.5-9B / ReAct | 200 turns、128K、Avg@1 | 63.8 |
| Qwen3.5-9B / Claude Code | 200 turns、128K、Avg@1 | 51.0 |
| Qwen3-Coder-30B-A3B / ReAct | 100 turns、128K、Avg@4 | 49.2 |

同一模型换 context/turn budget，分数差别很大；Avg@4 也不能当作 Pass@4 或和单次采样直接混比。小样本 smoke 的解题率不应外推成 SWE-Bench Verified 全集分数。

官方 RL 参考结果来自固定版 [README](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/README.md) 与 [RL benchmark 文档](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/docs/source/benchmark/rl-training.md)：

| policy / harness | 训练数据与模式 | 官方 Base → RL |
| --- | --- | ---: |
| Qwen3-30B-A3B / ReAct | R2E-Gym，fully async | 22.2 → 36.8 |
| Qwen3-Coder-30B-A3B / ReAct | R2E-Gym，fully async | 46.2 → 52.0 |
| Qwen3.5-9B / ReAct | SWE-reBench，fully async | 53.8 → 59.2 |
| Qwen3-Coder-30B-A3B / ReAct | SWE-reBench，colocate async | 47.4 → 54.2 |
| Qwen3-Coder-30B-A3B / Claude Code | SWE-reBench，colocate async | 40.2 → 46.2 |

训练数据列不等于该列就是评分集；官方文档说 Base/RL 使用对应任务的 validation metric，相关 SWE recipe 以 SWE-Bench Verified 验证。README 与 benchmark 页行数不完全同步，Claude Code 行在 README 中更完整。另一处 release notes 将 Terminal-Bench 67.4 写作 Terminus-2，而固定版 benchmark 表写 Claude Code；因此本记录不借该行推出 harness 比较结论。

官方报告 partial rollout 在 **8×A100 节点、200 steps** 的一个实验中将耗时从 95.6 h 降到 45.8 h，约 2.1 倍；这是其指定配置结果，不能作为本机 MI355 的预期加速。没有相同模型、task、并发和拓扑对照时也不能归因于单独某一项优化。

SWE quickstart 推荐预处理后的 [swe-rebench-filtered-1150](https://huggingface.co/datasets/dyyyyyyyy/swe-rebench-filtered-1150) 训练集和 SWE-Bench Verified 验证集。预处理保存题目、metadata 与 canonical 镜像名，provider 和 agent 由 runtime recipe 选择；迁移镜像仓库可用 `sandbox.image_map`，不必重写 parquet。

此外 [MemAgent README](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/examples/mem_agent/README.md) 提供 **Qwen3-4B、单节点 8 卡、4 张 FSDP2 train + 4 张 vLLM rollout** 的 recipe，报告 8K–1M 八种长度的 macro score 53.5 → 58.0。这是 HotpotQA 分块记忆任务；它能帮助学习异步 RL，不等价于 SWE 工具 agent 的验证。

## 5. 当前限制与最相关的公开问题

| 问题 | 已核对的状态 | 对本实验的影响 |
| --- | --- | --- |
| [#114 单节点入门](https://github.com/verl-project/uni-agent/issues/114) | open，希望 1–8 卡、小模型、local/Docker、至少两步训练 | SWE 单节点 end-to-end quickstart 仍缺。本任务需要补配置；issue 中“所有训练都多节点”的表述已被 MemAgent 单节点 recipe 部分超越 |
| [#43 本地 SWE 支持](https://github.com/verl-project/uni-agent/issues/43) | open，但 DockerSandbox 已在源码中 | issue 未关闭不能解释成 Docker 不支持；还需验证实际 SWE 镜像/判题链 |
| [#175 MemAgent training spikes](https://github.com/verl-project/uni-agent/issues/175) | open，用户报告 1×8 H100 复现困难 | 官方 recipe 存在不意味着训练曲线一定可复现；不要把未知失败全归因 ROCm |
| [#183 MemAgent reward/PPO 对齐](https://github.com/verl-project/uni-agent/pull/183) | open PR | 潜在修复尚未进入固定版，采用前需比较实际变更 |
| [#185 多 trajectory 的 session loss 权重](https://github.com/verl-project/uni-agent/issues/185) | open，讨论 all 模式行数成为隐式权重的问题 | 多分支训练需要检查 session、reward、advantage 和 loss 计数；`longest` 丢数据，不能当作等价算法修复 |
| [#179 KV offload](https://github.com/verl-project/uni-agent/pull/179) | open PR，依赖扩展 vLLM offload policy | 已合并 router 不等于 KV offload 也已发布 |
| [#168 Responses harness](https://github.com/verl-project/uni-agent/pull/168) | open PR，含 `/v1/responses` adapter | 固定版不能按它假定已支持 Responses 路径 |

从固定源码还可直接确认以下边界：

- `AgentFrameworkRolloutAdapter.create()` 对非空 `teacher_client` 直接报错，当前入口尚不能直接用于 teacher distillation。
- Continuous Token 的增量 context merge 明确拒绝新增 image/video data；初始多模态处理能力不能推成完整任意多轮视觉支持。
- `TaskResult` / RewardLoop 的当前集成以 scalar episode reward 为主，没有通用 step/process reward 张量契约。
- 普通外部 API inference 绕过 Gateway，不会自动产生训练所需原始 token/logprobs；需要训练格式数据时选择 managed 路径或专门 debug capture。
- Gateway session 在内存中维护；普通 debug launcher 到 finalize 才写 artifact。不能把它当作具有持久化 session 控制平面的通用代理。
- `mask_unfinished_episode` 默认 false，启用时清零 loss mask 而不是删除样本。多分支 `longest` 选样按生成长度，不按质量。
- 部分 runtime YAML 与训练脚本仍包含 veFaaS/Modal 凭据字段、CUDA/Megatron 专用配置或多节点默认值；单节点 Docker/ROCm 迁移需要实际缩小并行度、并发、batch 与 context。

## 6. Roadmap 应如何理解

官方 [26Q3 Roadmap #79](https://github.com/verl-project/uni-agent/issues/79) 最近更新时间为 2026-08-31。它列出框架重构、多模态通信优化、长上下文 hybrid sequence parallelism、prefix-tree trajectory storage、多 agent/multi-policy、OpenClaw/AI4S agent 和 GUI Task 等方向。

这份列表是计划索引，没有逐条完成勾选；Anthropic API、Claude Code、assistant rewrite/rollback 等条目在当前源码已经有实现。反过来，多 policy trainer、GUI 和 prefix trie 等仍有 open RFC/PR，不能因为名字出现在 roadmap 就在报告中写“正式支持”。本次应该使用已合并功能建立最小闭环，再按具体需求跟踪这些提案。

## 7. 本机后续路线与验收口径

建议把工作分成逐层可验证的里程碑；下表是判断标准，完成情况由实测记录填写。

| 里程碑 | 足够的验收证据 | 不能由此推断 |
| --- | --- | --- |
| Docker / sandbox | 命令、文件、状态 shell、清理与 timeout 行为正常 | GPU 训练可用 |
| Oracle verifier | 已知正确解与错误解得到预期判题结果 | 模型会解题 |
| 小模型 ReAct | 本地模型真的产生 tool calls，修改隔离项目并完成判题 | benchmark 全集分数 |
| Claude 黑盒 | CLI 访问本地端点，执行真实工具，正确收尾 | Claude 模型参与或产生训练收益 |
| Gateway trajectory | IDs / masks / logprobs 对齐，实际工具观察进入上下文 | 发生 policy update |
| 最小 RL | rollout → scalar reward → 非空训练 mask → 至少两个 global steps，参数/优化器更新和 checkpoint 有证据 | reward 曲线上升、泛化提升 |
| 小规模评估 | 固定验证样本、base vs checkpoint、相同预算 | 论文级复现或统计显著性 |

模型选择可按目的分开：简单 dense 小模型用于验证 token→loss→update 接线；Qwen3.5-4B 或 9B 用于靠近官方 SWE recipe；30B MoE 和 128K context 放到环境、判题及 kernel 组合稳定以后。先用低并发诊断；再按 GPU 利用率、CPU verifier 吞吐、镜像缓存与 rollout 长尾逐项增加规模。

本次主实验已选择 Docker 隔离与固定版本路径，具体 daemon/data-root、使用镜像和模型、实际通过/失败的 case、错误堆栈及修复均由主实验报告记录。架构本文与官方状态快照可作为阅读实验日志的索引。

## 资料索引

- [固定 Uni-Agent README](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/README.md)
- [官方文档站](https://uni-agent.readthedocs.io/en/latest/)（`latest` 会继续变化，复现优先固定源码链接）
- [RC release](https://github.com/verl-project/uni-agent/releases/tag/v0.1.0rc1)
- [固定版 Gateway/Trajectory 说明](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/docs/source/concepts/gateway-and-trajectories.md)
- [固定版 RL quickstart](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/docs/source/quickstart/rl-training.md)
- [配套 verl V1 async](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/docs/advance/v1_async_trainer.md)
- [配套 verl AMD tutorial](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/docs/amd_tutorial/amd_quick_start.rst)
- 本地 API 快照（原始文件：`notes/research`）
