# Uni-Agent × AMD ROCm：实验总结与架构说明

> 实验执行：2026-09-12。整理日期：2026-09-14。  
> 本次完成 **Uni-Agent 组件驱动的 agent 推理、sandbox 执行、独立评测与轨迹采集验证**，没有做 SFT 或 RL 后训练。所有任务使用固定的公开 Qwen3-Coder-30B-A3B-Instruct 权重。

本目录按问题与实验主题组织，重点回答：**搭了什么、怎么运行、实际跑了什么、得到了什么、这些结果说明什么。** 配套图可直接查看 SVG，也保留可编辑的 Mermaid 源码。数据表和选定案例证据随文保存，原始完整实验目录是 `/home/yuhanya/uni-agent-lab`。

**阅读形式**：[多页HTML入口](site/index.html) · [汇报摘要PDF](00-executive-summary.pdf)。Markdown文件与可编辑图源仍是主要内容。

## 先看结论

| 验证目标 | 本次得到的结果 | 详细说明 |
|---|---|---|
| 在 AMD 上运行 30B agent | 8 张 MI350X 基础计算检查通过；30B BF16 单卡模型服务、三种 agent 接入可运行 | [模型与官方示例](experiments/01-model-and-official-examples.md) |
| 比较三种 agent/harness | 同一模型、同一有效 28 题：ReAct **17/28**，Claude Code **11/28**，Mini **12/28** | [正式 SWE 对照](experiments/03-swe-agent-comparison.md) |
| 获得可信的评测分母 | 预选 30 题，先做原始代码/gold 正负控制；28 题环境合格，2 题不补选 | [数据与控制](experiments/02-data-and-controls.md) |
| 本地 sandbox 方案 | 每题 Docker 隔离、文件状态、资源/网络限制、生命周期与独立 TTL 回收均有实测 | [Docker sandbox](experiments/04-docker-sandbox.md) |
| 远端 sandbox 方案 | 用户提供的 E2B 兼容服务可接入；ReAct 修复任务通过 10 项独立测试 | [E2B 实验](experiments/05-e2b-sandbox.md) |
| 提高 ROCm 推理效率 | 单卡 AITER + 图执行：并发 8 约 **1,036 token/s**，同轮 eager 约 **235 token/s** | [TP 与 AITER 对照](experiments/06-serving-tp-and-aiter.md) |
| 验证模型副本扩展 | 并发 32：两个优化副本约 **3,513 token/s**，单副本约 **1,948 token/s** | [副本扩展](experiments/07-replica-scaling.md) |
| 验证轨迹结构 | 正式 84 个 session 的 token/mask 对齐；三种 agent 各有独立 logprob 完整验证 | [轨迹与后训练边界](experiments/08-trajectories.md) |

**统计范围**：正式实验是从 SWE-bench Verified 分层选出的有效 28 题，每种 agent 各作答一次；不是全量 500 题成绩。Claude Code 是真实 CLI，模型仍为本地 Qwen。性能提升来自推理运行配置，不是后训练得到的能力提升。

## 按阅读目的进入

| 阅读目的 | 建议文件 |
|---|---|
| 给老板汇报，先看做成了什么 | [00-executive-summary.md](00-executive-summary.md) |
| 弄清 Uni-Agent、vLLM、agent、harness、sandbox 的关系 | [01-scope-and-terminology.md](01-scope-and-terminology.md) |
| 看容器架构和本地/远端两条链路 | [architecture/01-deployment.md](architecture/01-deployment.md) |
| 看一题从启动到模型、工具、判题、退出的流程 | [architecture/02-agent-task-flow.md](architecture/02-agent-task-flow.md) |
| 看谁常驻、谁退出，以及 Docker/Gateway 接收什么 API | [architecture/03-apis-and-lifecycle.md](architecture/03-apis-and-lifecycle.md) |
| 查Uni-Agent所有接口：HTTP、Python/Ray、agent/task/tool与训练适配 | [Uni-Agent API总览](references/03-uni-agent-api-reference.md) |
| 查sandbox接口：统一Python接口、Docker、verdal/E2B REST与RPC | [Sandbox与verdal/E2B API总览](references/04-sandbox-and-verdal-api-reference.md) |
| 看各项实验完整设置与结果 | [experiments/](experiments/README.md) |
| 看真实成功/失败案例，而不只看分数 | [results/01-case-studies.md](results/01-case-studies.md) |
| 看全部 28 题 × 3 agent 的结果 | [结果矩阵](results/03-per-case-matrix.md)／[CSV](results/per-case-results.csv) |
| 判断适合做什么、下一步如何验证后训练 | [results/02-conclusions-and-next-steps.md](results/02-conclusions-and-next-steps.md) |
| 查看固定版本与复现命令 | [环境版本](reproduce/01-environment-and-versions.md)／[复现步骤](reproduce/02-replay-guide.md) |
| 看社区方案处于哪一层 | [references/01-community-comparison.md](references/01-community-comparison.md) |
| 追溯数据、源码调用和原始记录 | [references/02-evidence-index.md](references/02-evidence-index.md) |

## 本目录保存什么

```text
uni-agent-experiment/
├── README.md / 00-executive-summary.md / 01-scope-and-terminology.md
├── architecture/    部署、执行流程、API 与生命周期
├── experiments/     九项实验主题：目的、方法、设置、结果、结论
├── results/         案例、结论、逐题矩阵与 CSV
├── reproduce/       固定版本、复现指南、脚本与配置快照
├── references/      社区对比、证据索引、Uni-Agent API、Sandbox与verdal/E2B API
├── evidence/        轻量 JSON、逐题结果、选定补丁、原始统计
├── assets/          SVG/PNG 图表、Mermaid 源码
└── tools/           文档数据整理、绘图、渲染与校验工具
```

权重、Docker 层、完整 token 数组和完整任务日志继续保留在原实验目录，不复制进这个文档仓库。真实密钥不进入本目录。脚本快照记录当时的实现，复跑应在独立 lab 布局中使用新输出目录。
