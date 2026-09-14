# 32B primary GPU1 recovery：独立结果复核

四次固定执行和四份分析已全部通过独立 CPU 审计。两组 base→final128 的题均 LCS 差值均为正，分别报告如下；原生分数、题目、reward、checkpoint 与 repeat 角色均未改变。

| 配对 | Base LCS | Final128 LCS | 差值 | 按 64 题配对的 95% CI | LCS=1 | 增 / 降 / 平 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| first | 0.4396777701 | 0.5948660714 | +0.1551883013 | [+0.0517968750, +0.2609179688] | 20→28 | 18 / 6 / 40 |
| repeat | 0.3901785714 | 0.5693046537 | +0.1791260823 | [+0.0590029762, +0.2974668561] | 20→27 | 22 / 7 / 35 |

这里使用默认 primary 的实际 `ROCM_ATTN`，BI 环境未设置，GPU 为 HIP1 / PCI `0000:06:00.0`。原 GPU0 的 baseline 和超时启动不进入这两组配对；BI=1 / TRITON_ATTN 的 stable 结果也保持独立。

同一模型的两次执行存在可观测变化：

| 模型 | 相同最终 response | 相同 reward | repeat−first 题均 LCS | repeat 增 / 降 |
| --- | ---: | ---: | ---: | --- |
| base | 23/64 | 50/64 | −0.0494991987 | 4 / 10 |
| final128 | 15/64 | 53/64 | −0.0255614177 | 4 / 7 |

这说明本次默认后端的重复执行并非逐回答一致。两个 first/repeat 都保留，不能取高分替代另一组，也不能将重复执行合并成更多独立题目或训练 seed。上面的两个学习 CI 各自使用固定 seed 20260912、20,000 次按题 paired bootstrap；同模型 repeat 诊断没有学习 CI。

沿用原冻结的窄格式分类：

| 配对 | 窄格式 case | 格式净贡献 | 其余文字或选择变化净贡献 | 格式占观测净增 |
| --- | ---: | ---: | ---: | ---: |
| first | 3 | +0.0234375000 | +0.1317508013 | 15.1026% |
| repeat | 2 | +0.0156250000 | +0.1635010823 | 8.7229% |

两组窄格式 case 均为原分类器识别的 TeX 空格变化。其余项仍可能包含日期顺序、别名、引号、答案长度等表达变化，不能当成穷尽的知识增益或因果分解。完整的每题 before/after 回答和分类均保存在新审计中。

审计重验了四份独立 raw 文件的全部 64 题、原生 reward、四份 paired CSV、两个学习 CI、两个 repeat 诊断、实际服务 argv/env/backend/PCI/PID 证据和统一的推理 signature。baseline 两遍在同一实际服务进程中运行，final 两遍也在同一进程中运行。原控制器的 socket 预检查失败记录保持 `failed`；只运行 final 两遍的 continuation 保持自己的完成记录，baseline 没有重跑。

主要记录：

- 完整独立机器审计（原始文件：`results/large-memagent/primary-gpu1-recovery-independent-results.json`）
- 独立复核脚本（原始文件：`scripts/review_primary_gpu1_recovery_results.py`）
- first 原始配对分析（原始文件：`results/large-memagent/comparison-durable-primary-gpu1-recovery-step128/comparison.json`）
- repeat 原始配对分析（原始文件：`results/large-memagent/comparison-durable-primary-gpu1-recovery-step128-repeat/comparison.json`）

这些结果支持该固定长上下文外部集上观察到的正向变化；仍限于一次训练 seed 和所记录的推理配置。没有改名覆盖旧 phase，没有改变分数选择规则，也没有用最终回答的自述补造中间 memory 机制。
