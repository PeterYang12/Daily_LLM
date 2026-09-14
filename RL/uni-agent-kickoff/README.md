# Uni-Agent Agentic RL 实验集

这里记录了一台 ROCm 八卡节点上实际跑过的实验：从 Sandbox / Gateway 接通，到 Qwen3-32B、Qwen3-8B 各 128 步 RL，再到代码 Agent、Miles 和 Verdal。**每个实验从自己目录里的 `README.md` 开始读。**

## 从哪里开始

- **第一次了解 Agentic RL**：[背景与架构](00-overview/README.md) → [基础组件实验](01-cpu-sandbox-gateway/README.md) → [8B 训练实验](04-memagent-8b-rl/README.md)。
- **准备汇报**：[整体结果](00-overview/RESULTS.md) → [32B](03-memagent-32b-rl/README.md) 与 [8B](04-memagent-8b-rl/README.md)；图片跟随各实验存放。
- **想先运行一个小例子**：[Verdal Sandbox](08-verdal-sandbox/README.md)，只需兼容服务与 Python / CPU Docker。

## 实验目录

| 目录 | 做了什么 | 主要结果 |
| --- | --- | --- |
| [00-overview](00-overview/README.md) | 共用背景、架构、环境与整体对比 | 六张架构图、接口说明、统一结果与成本口径 |
| [01-cpu-sandbox-gateway](01-cpu-sandbox-gateway/README.md) | CPU 测试、命令/文件 Sandbox、Claude Code 接本地 Qwen | 685 测试通过，真实模型请求与 Gateway 轨迹接通 |
| [02-memagent-long-context](02-memagent-long-context/README.md) | 原始模型逐块读长文、更新记忆、回答问题 | 完成长文推理并分析记忆成功/失败案例 |
| [03-memagent-32b-rl](03-memagent-32b-rl/README.md) | Qwen3-32B，128 步全参数 MemAgent RL | stable external64 平均 LCS 0.42091 → 0.56012 |
| [04-memagent-8b-rl](04-memagent-8b-rl/README.md) | Qwen3-8B，同配方独立训练 128 步 | 平均 LCS 0.41832 → 0.53467，记录迭代约 2 小时 23 分 |
| [05-swe-code-agents](05-swe-code-agents/README.md) | ReAct / Claude Code / Mini-SWE 修复真实仓库 | Coder30B 原六题分别 resolved 4/6、3/6、2/6；扩展 8/29 |
| [06-terminal-bench](06-terminal-bench/README.md) | Coder30B 完成数据处理、语言迁移、虚拟机任务 | 三题 resolved 1/3，finished 2/3 |
| [07-miles](07-miles/README.md) | Miles 的数学 RL、恢复与 Python 工具流程 | 0.6B 真实更新；4B 工具适配后独立 rollout 16/16 |
| [08-verdal-sandbox](08-verdal-sandbox/README.md) | E2B SDK 调用 Verdal 的命令、文件与生命周期 | 完整烟测 11 项通过；保留基础示例代码 |

两组 RL 的分数来自固定 64 题、各一次训练，不能外推到通用能力。代码/Terminal-Bench 是诊断子集；Miles 的 4B 适配结果没有经过 RL 更新。各实验 README 都说明了这些边界。

## 每个实验里怎么看

通常按 **`README.md` → `REPORT.md` → `REPRODUCE.md`** 阅读：先理解任务，再看结果，最后按步骤准备运行。基础实验的复现顺序见 `REPRODUCE.md`，原始命令按其中链接查看 `REPORT.md`；专题分析收进 `details/`。

图片放在所属实验的 `figures/`，可复用代码在 `code/`，兼容修改在 `patches/`；没有这些材料的实验不创建空目录。32B 与 8B 的完整参考 YAML 收在各自复现手册附录中，其他必要参数随步骤说明。两规模共同对比图只保存一份，位于 `00-overview/figures/comparison/`。

## 运行材料的范围

这里保留文档、图片、少量代码和补丁，不包含模型、依赖包、完整实验工程或原始日志。训练手册中的完整脚本、固定数据与历史复现材料需另行准备，不能只 clone 此目录就直接开始训练；Verdal 的短示例可按其 README 单独配置运行。

公共准备见 [00-overview/SETUP.md](00-overview/SETUP.md)，跨实验排障见 [TROUBLESHOOTING.md](00-overview/TROUBLESHOOTING.md)。完整连续阅读版保留为 [离线 HTML 手册](00-overview/reproduction.html)；仓库网页内阅读优先使用各实验 Markdown。
