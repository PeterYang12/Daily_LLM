# 用首轮32B的一组历史采样看GRPO怎样产生训练信号

这是`memagent_32b_128`的global step **5**、prompt uid `cbf35437-08c7-4ed6-bf3b-ebb742e6b87a`。选择规则在读取分数前固定为：取最早已保存的step≥5，再按uid排序取第一组非均匀reward；不按回答质量或收益大小选择。它只是机制示例，不是效果评测。

该首轮后来在46个完整step日志后因宿主中断而停止。本例保留真实历史值，与当前从原始base重新开始的`memagent_32b_128_durable`分开；新主线见[当前交接点](../../00-overview/RESULTS.md)。

问题：The Rossendale Free Press serves the town how far north of Manchester?

固定ground truth：`["19 mi"]`

| session | 最终boxed答案 | reward | r−组均值 | contexts |
|---|---|---:|---:|---:|
| 0 | 19 | 0.50000000 | +0.25000000 | 7 |
| 1 | 20 | 0.00000000 | -0.25000000 | 7 |
| 2 | 20 | 0.00000000 | -0.25000000 | 7 |
| 3 | 19 | 0.50000000 | +0.25000000 | 7 |

这一组的均值为 **0.25000000**。本协议`norm_adv_by_std_in_grpo=false`，所以有效输出的优势为reward减组均值，不再除以标准差。这里从保存的FP32 reward数学重算，训练张量的末位可能有浮点舍入差异。

本pin先按(uid,session)取最后一个context来算组优势，再将同一个标量乘各context的response mask，广播到该session的memory与最终回答输出；不是把所有context的重复reward再当作独立题目求均值。padding使用独立uid、零reward/零mask，已从本例排除。

正优势提高相应输出在给定输入下的相对概率，负优势降低相对概率；实际更新还受PPO clipping、KL和token聚合影响。这并不能保证对该题或其他题的最终准确率改善，也不能单靠这组示例声称RL获得提升。

完整响应、精确分数和选择来源见小JSON（原始文件：`results/large-memagent/grpo-example.json`），完整输入/输出见原生rollout文件（原始文件：`runs/rl/memagent_32b_128/rollout/5.jsonl`）。算法边界见[训练讲解](32b-training-walkthrough.md)。
