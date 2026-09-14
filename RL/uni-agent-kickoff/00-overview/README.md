# 00 · 背景、架构和公共准备

这里集中放跨实验共用的说明。具体任务、步骤和结果进入 [根目录列出的实验](../README.md) 阅读。

## 初次阅读只看三份

1. [background.md](background.md)：这次训练什么，Agent 的多轮执行与 RL 的训练步数有什么区别。
2. [architecture.md](architecture.md)：Task、Agent、Sandbox、Gateway、推理服务和训练器如何分工；包含全部六张架构与流程图。
3. [RESULTS.md](RESULTS.md)：32B / 8B 最终对照、成本、案例、失败恢复和其他实验结论。

喜欢交互查看时，打开 [架构图](diagrams/index.html)，点击模块或接口看输入输出；[训练教学页](training-explorer.html) 用早期真实样本解释 context、reward 和参数更新。教学页的 pilot 记录与最终 128 步实验分开。

## 准备运行时再看

| 内容 | 入口 |
| --- | --- |
| Docker、ROCm、目录、模型、数据、固定版本与启动约定 | [SETUP.md](SETUP.md) |
| 完整状态保存、结果验收、故障定位与迁移边界 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 32B 训练、resume、导出、评测、完整参考 YAML | [03 的复现手册](../03-memagent-32b-rl/REPRODUCE.md) |
| 8B 训练、节点登记、单文件导出恢复、完整参考 YAML | [04 的复现手册](../04-memagent-8b-rl/REPRODUCE.md) |
| SWE / Claude / Mini / 扩展 29 题 | [05 的复现手册](../05-swe-code-agents/REPRODUCE.md) |
| Terminal-Bench 三题 | [06 的复现手册](../06-terminal-bench/REPRODUCE.md) |
| Miles 环境与训练/工具流程 | [07 的复现手册](../07-miles/REPRODUCE.md) |
| Verdal CPU Docker 基础示例 | [08 的复现手册](../08-verdal-sandbox/REPRODUCE.md) |

同一完整 lab 要依次复现 32B 和 8B 时，先完成 8B 模型/CPU 准备与新节点评测登记，再开始训练；原登记器要求 `runs/rl` 尚无历史。详见 SETUP 与 8B 手册，不要删除已有实验来绕过检查。

## 方法与接口细节

- [GRPO 配方说明](methods/rl-algorithm-label-clarification.md)：组内中心化、不除标准差、token-mean loss 与 KL 的准确含义。
- [训练源覆盖](methods/canonical-training-source-coverage.md) 与 [数据分区独立性](methods/memagent-split-independence.md)：训练、monitor、external 如何计数。
- [格式贡献](methods/final-format-contributions.md)、[TeX 空格诊断](methods/tex-space-only-results.md)：为什么分数变化不总等于新增知识。
- [训练接口逐项说明](details/layered-rl-interface-review.md) 与 [其他实验接口](details/layered-other-interface-review.md)：从源码核对调用链。
- [上游项目状态](details/project-status.md)：固定版本的能力、限制与本次适配背景。

## 图表与代码放在哪里

六组公共架构图保存在 `diagrams/`，均提供 PNG / SVG / PDF。32B / 8B 共同对比图在 [figures/comparison](figures/comparison/)；每个实验自己的图在各自目录。

Gateway helper 和 Docker 写文件补丁位于 [01](../01-cpu-sandbox-gateway/README.md)，Miles 兼容补丁位于 [07](../07-miles/README.md)，Verdal 基础示例位于 [08](../08-verdal-sandbox/README.md)。本目录不重复复制这些代码。

详细步骤已按实验拆分；[reproduction.html](reproduction.html) 保留连续阅读顺序，下载后可离线打开。源码链接指向固定上游 commit，未收录的完整实验文件只保留来源说明。
