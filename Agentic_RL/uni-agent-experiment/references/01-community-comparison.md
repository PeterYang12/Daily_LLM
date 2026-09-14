# 社区方案对比：放在正确的层次上看

> 此页来自2026-09-12实验期间保存的官方GitHub/README快照，文档于2026-09-14整理。除mini-swe-agent和用户E2B兼容端点外，表中其他项目只做资料比较，没有在本机跑同等训练或任务benchmark。Star不是能力分数。

## 比较维度

Agent/harness、任务格式、sandbox、模型推理与训练器不是同一层。选择agentic RL方案时，应先明确需要替换哪一层，再比较接口、数据正确性、运行成本和训练验证。

| 层次 | 项目 | 快照中的主要定位 | 本次如何看待 |
|---|---|---|---|
| Agent训练编排 | Uni-Agent | 复用harness、统一任务/sandbox、Gateway轨迹与verl集成 | 本次实际验证其推理组件；未验证完整RL训练 |
| Agent训练编排 | Agent Lightning v1.0 | API Gateway、Rollout Controller、verl；复用真实harness，支持Kubernetes作业 | 与Uni-Agent较接近的方案，适合下一步接口与训练闭环对照 |
| Agent训练编排 | rLLM | 多harness、sandbox、训练backend及benchmark接入 | 比较集成覆盖、轨迹语义与可维护性 |
| 训练系统 | Miles / slime | 大规模RL、SGLang rollout与训练后端集成 | 侧重训练吞吐、同步、稳定性；仍要接任务和sandbox |
| Harness | mini-swe-agent | 简洁的代码修复agent | 本次在Uni-Agent里实际接入并同题比较 |
| Harness | SWE-agent | 代码修复agent及工具/环境设计 | 与Mini不同项目，本次未跑独立SWE-agent |
| Agent产品/工作流 | OpenHands | 当前主README侧重Agent Canvas与开发者控制面 | 更偏工程工作流/产品；不以UI功能数比较RL能力 |
| 评测与任务 | Harbor | 容器化agent benchmark运行与评测 | Uni-Agent提供任务适配；适合统一评测任务格式 |
| Sandbox平台 | E2B | 代码执行与隔离环境 | 本次验证的是用户E2B兼容端点，未核实其底层等同官方部署 |
| Sandbox平台 | OpenSandbox | 统一sandbox API、Docker/Kubernetes运行时与网络控制 | 可作为基础设施候选，未在本机集成实测 |

## 快照规模与许可证

| 项目 | Star快照 | 许可证 |
|---|---:|---|
| Uni-Agent | 599 | Apache-2.0 |
| Agent Lightning | 18,069 | MIT |
| rLLM | 5,823 | Apache-2.0 |
| Miles | 2,810 | Apache-2.0 |
| slime | 8,447 | Apache-2.0 |
| mini-swe-agent | 7,450 | MIT |
| SWE-agent | 20,307 | MIT |
| OpenHands | 87,621 | MIT（所查主仓库） |
| Harbor | 5,158 | Apache-2.0 |
| E2B | 13,761 | Apache-2.0 |
| OpenSandbox | 15,146 | Apache-2.0 |

原始仓库有重定向，例如Harbor与OpenSandbox的规范组织名变化；引用按快照返回的公开项目链接处理。不同发行版、商业服务与依赖的条款应另核对，不能只用主仓库许可证概括所有组件。

## 这次实验对选型的启发

- 若目标是研究agent训练，Uni-Agent、Agent Lightning、rLLM适合比较harness接入、任务封装、token/reward语义与真正训练闭环。
- 若核心目标是ROCm大模型训练系统，Miles/slime的训练实现和已公开AMD资料值得单独验证，不能用本次vLLM推理成功替代其训练验收。
- 若目标是直接交付开发者使用的代码工具，应评估harness/产品的工作流与可操作性，同时保留独立任务测试。
- Sandbox接口可插拔不等于迁移无需工程工作。本次E2B基础任务可用，但SWE镜像模板与远端CLI访问本地Gateway仍需验证。

社区的共同方向是复用真实harness、使用兼容模型API、分离sandbox与训练器、用可验证任务评分，以及扩展异步执行与缓存/资源调度。ROCm是否可用，应分别验证推理、训练kernel、通信和checkpoint。

## 来源

- [Uni-Agent](https://github.com/verl-project/uni-agent)
- [Agent Lightning](https://github.com/microsoft/agent-lightning)
- [rLLM](https://github.com/rllm-org/rllm)
- [Miles](https://github.com/radixark/miles) · [slime](https://github.com/THUDM/slime)
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) · [SWE-agent](https://github.com/SWE-agent/SWE-agent)
- [OpenHands](https://github.com/OpenHands/OpenHands)
- [Harbor](https://github.com/harbor-framework/harbor)
- [E2B](https://github.com/e2b-dev/E2B)
- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)
- [本次保存的API摘要](../evidence/summary/community-snapshot-20260912.json)。

上游Uni-Agent不同文档曾给出不同设置/版本下的30B成绩，本目录没有把这些数字当作本机结果，也不拿它们与28题小样本做直接高低比较。
