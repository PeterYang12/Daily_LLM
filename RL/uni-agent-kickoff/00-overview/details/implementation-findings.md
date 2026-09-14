# 这次实际遇到的接口和实现细节

以下记录针对固定的 Uni-Agent `472c875a…`、训练 verl `a9f2985…` 和本实验配置。它们有源码或运行证据，不是对所有版本、硬件组合的结论。正式 128-step 的最终效果另看固定留出集。

| 细节 | 对实验的实际影响 | 本次处理与证据 |
| --- | --- | --- |
| Docker 整文件写入使用 base64 shell 参数 | 修改 xarray 大文件时先触发 Linux E2BIG，模型无法完成编辑 | Docker provider 改成 stdin；1/16 MiB 原始 bytes 回读一致、45 项已有相关测试通过；[记录](../../01-cpu-sandbox-gateway/details/docker-write-file-fix.md) |
| `max_total_tokens` 的公共字段描述与 ReAct 实现不同 | ReAct 的值是最后一次请求 prompt+completion 的停止阈值，不能按字段描述计算整段任务累计消费 | 保留原行为，纠正文档口径；代码见 `uni_agent/agents/react/agent.py` 131–158 行。MemAgent 自己使用累计 completion 计数，因此还需区分 agent |
| 32B 多桶同步在首次尝试中停止前进 | 模型能载入并不代表权重同步可用 | 记录进程栈；恢复后 single-sender 配置通过完整 pilot 和长训的持续同步。两次之间环境也恢复过，没有做恢复后 multi-sender A/B；[执行记录](large-model-experiments.md) |
| HF 导出没有自动变成 BF16 | 一份 32B HF snapshot 实际约 122 GiB | 核验全部 707 个 tensor 的 header 和文件大小；采样另算 BF16 可表示变化；[精度审计](../../02-memagent-long-context/details/large-memagent-effective-weight-deltas.md) |
| 训练 global step 与 Adam step 不同 | 一批题可产生多份 context，进一步分成多个 optimizer minibatch | pilot 的 1 global step 对应 16 sessions、104 真实 contexts + 8 padding、每 rank 7 次 Adam 更新；[逐步讲解](../../03-memagent-32b-rl/details/32b-training-walkthrough.md) |
| warmup 首步 LR=0，但仍执行 optimizer.step | 计数、moment 状态和梯度可以变化，而权重暂时不变 | 保存各 rank 的实际 optimizer step 分布；结合参数差分和任务奖励优势解释 |
| `rollout_corr/*` 指标与实际重加权配置分开 | 出现概率差异/KL 指标不等于启用了额外 off-policy correction | 正式 resolved config 为 `rollout_is=null`、`rollout_rs=null`、`bypass_mode=false`，保留重新计算 old logprob 的 PPO 路径；当前版本滞后单独记录 |
| 未结束轨迹的 mask 取决于配置 | `finished=False` 与“奖励为零”不是同一件事 | 框架/通用 quickstart 默认不清零，Mini 专用训练脚本默认清零；实际值逐 run 保存；[架构说明](architecture-internals.md) |
| 原始 Gateway 轨迹与 TaskResult 分层 | 单独 CLI 推理采集的 reward/finished 字段可以为 null | 用 session ID 关联 TaskResult；12 条大模型黑盒轨迹结构验收通过，未将其称为黑盒 policy update；[案例审计](../../05-swe-code-agents/details/coder30b-case-audit.md) |
| 镜像可能自带文件模式和内容改动 | 原始 `git diff base_commit` 会夸大模型的改动范围 | expanded 中剔除与 model-free baseline 相同的 Sphinx setup.py/tox.ini 块，并单列 mode-only；[扩展案例](../../05-swe-code-agents/details/swe-expanded-case-study.md) |
| boxed 答案中的 LaTeX 空格会影响 LCS | `YG\ Entertainment` 可以得到 0.5，虽然正文和目标名称一致 | 主 reward 不改；另固定一个只替换转义空格的分析规则；[诊断](../methods/tex-space-only-results.md) |
| 原始 HotpotQA 全局 ID 没有保留在 parquet | 生成的 `hotpotqa_dev_row_N` 只适合定位行 | 核验 512/64/64 问题文本、规范化文本及 row hash 无重叠，同时明示原始 ID 验证边界；[分区审计](../methods/memagent-split-independence.md) |

`max_total_tokens` 在本 pin 的 ReAct 路径尤其容易误读：它先用 55K 减去上一次保存的 token 用量限制下一次生成；返回后直接覆盖为本次 prompt+completion。若达到阈值，assistant 消息已保存，但本轮工具尚未执行。累计 token 消费要对每次 usage 单独求和，成本和上下文窗口也应分别记录。这个解释更新没有改变已经冻结或正在运行的任务配置。

读取项目时，可以先沿一条真实任务看清 `Task → Agent → Gateway → rollout → context/mask/reward → trainer`，再追每个字段在具体 agent/backend 的实现。参数名、README 或单条日志都可能省略这里列出的运行语义；本实验把可复现配置、实际产物和解释放在了一起。
