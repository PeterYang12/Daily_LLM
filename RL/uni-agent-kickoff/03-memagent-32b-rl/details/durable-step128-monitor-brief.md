# 32B 最终 128 步：独立监控审计

真实 `memagent_32b_128_durable` 在 2026-09-12 16:13:58 UTC 正常退出，128 步完整训练已完成。固定 64 道监控题的原生 LCS 从 0.4944568452 升至 0.5938400689，净增 0.0993832237；原生 `LCS=1` 从 19/64 升至 26/64。这里的分数来自训练期间的固定监控集，最终 external 评测仍是另一组独立结果。

| 监控点 | 题均原生 LCS | LCS=1 题数 |
| --- | ---: | ---: |
| base / step 0 | 0.4944568452 | 19/64 |
| step 32 | 0.5874627976 | 29/64 |
| step 64 | 0.5584535256 | 26/64 |
| step 96 | 0.5984375000 | 28/64 |
| final / step 128 | 0.5938400689 | 26/64 |

独立脚本直接重读 step 0、96、128 的 validation JSONL，每次均为 64 个完整 session、451 条 context。三轮的源文章 chunk hash 一致，每条 question / gold / context index 均完整，最终原生 reward 及广播到记忆 context 的 float32 reward 逐项吻合。所有 final response 和题均 reward 与 RL 分析快照相符，检查期间输入 hash 未改变。

base→128 共 14 题分数上升、4 题下降、46 题不变；base 的 19 道满分题全部保留，另外新增 7 道。7 个窄格式变化 case 贡献了 +0.0677083333，占观测净增的 68.13%；其余答案文字或选择变化合计贡献 +0.0316748904。该比例是分数的算术归类，不能解释成能力提升的因果比例。

| 变化类别 | case 数 | 对 64 题均值的净贡献 |
| --- | ---: | ---: |
| 相同整数增加千位逗号 | 3 | +0.0390625000 |
| 相同答案移除 TeX 空格转义 | 3 | +0.0234375000 |
| 相同可见文字改变简单 TeX 包装 | 1 | +0.0052083333 |
| 其他答案文字或选择变化 | 11（7 升、4 降） | +0.0316748904 |

可以用于展示的具体例子包括：Apple Remote 对应程序的控制方式由 `a keyboard or mouse` 收敛到 `keyboard function keys`，LCS 0.25→1；Jerry Goldsmith 电影的 executive producer 从 `not explicitly mentioned` 改为 `Ronald Shusett`，0→1。它们展示这两道题的答案定位改善。与此并存的失败包括海岸地名从含正确 `Yellowcraig` 的长答案偏到 `Fenton Barns`，0.25→0，以及出生日期从 `April 1, 1949` 的 TeX 写法变成 `January 3, 1976`，1/3→0。无法据此将所有剩余增分都视为知识增长。

指标对格式和答案长度敏感。例如 `1462`→`1,462` 得到 0→1，但整数本身没有改变；训练也可能输出更多正确细节而失分，step 96→128 的 `2000`→`March 14, 2000` 从 1 降至 1/3。因此原生 `LCS=1` 不等同于独立 HotpotQA EM，分数也不能单独证明 agent 的通用推理能力。

step 96→128 的题均值变化为 −0.0045974311，5 题升、9 题降、50 题不变，满分题由 28 降至 26。最终报告遵循训练前设定的 step 128，不按中间监控分数回选 checkpoint。

- 最终监控独立审计及逐题证据（原始文件：`results/large-memagent/durable-step128-monitor-independent/audit.json`）
- base→128 全部 64 题（原始文件：`results/large-memagent/durable-step128-monitor-independent/0_to_128-all-pairs.csv`）
- base→128 变化题（原始文件：`results/large-memagent/durable-step128-monitor-independent/0_to_128-score-changes.csv`）
- 96→128 全部 64 题（原始文件：`results/large-memagent/durable-step128-monitor-independent/96_to_128-all-pairs.csv`）
- [完整 512 题训练源覆盖说明](../../00-overview/methods/canonical-training-source-coverage.md)

复核命令已执行成功；输出路径存在时会拒绝覆盖：

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/rl/bin/python /lab/scripts/audit_durable_monitor.py \
  --after-step 128 --previous-step 96
```
