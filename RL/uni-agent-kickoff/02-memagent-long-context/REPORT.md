# MemAgent 实验

[本实验总览](README.md) · [全部实验](../README.md)

使用原版 `examples/mem_agent/infer.py`、`HotpotQATask` 和分块记忆 Agent，经本机 vLLM 运行。不是把全部长文塞进模型的一次请求；每块最多5000 token，用最多1024-token记忆传到下一上下文，最后回答。训练实验另见 RL 记录。

## 数据格式实测

数据源 `BytedTsinghua-SIA/hotpotqa`，revision `27275ff4fee67ac0acb6478e405e7ac07efbdc1a`。

- uni-agent recipe 文档要求 `eval_hotpotqa_8k.json` 等文件，但 HF 当前仓库没有这些文件。
- 实际 `eval_50/200/800/3200/6400.json` 使用字符串 context，文件名指文档数，不能直接当token长度。实际长度由本地tokenizer测量，见 `results/memagent/data-inventory.json`。
- `eval_12800.json` 的 context 是12800个整数（上限66633），没有随行的原始文档文本；用Qwen tokenizer解码也不是有效原文。通用 `context_to_text()` 会将这些整数逐行转字符串，runner仍会报无错误完成，但这不是有效的长文QA评测。首个4B试跑已保留并标为无效，**排除分数比较**；后续脚本增加格式前检，改用有真实文本的6400文档档。

## 运行设置

`bash scripts/run_memagent.sh <文档数> <样本数> <run名称> <本地模型目录名> <端口>`。

- Qwen3-4B-Instruct-2507: 18080；Qwen3.5-9B: 18081，均为非thinking文本推理。
- 样本取文件前N项：50与200文档各8条、800文档4条、3200文档2条、6400文档1条。不同长度的样本数不同；这些只是功能验证，不能用于公开长上下文排行榜或严格模型排名。
- 采样沿用官方 task_config：temperature=1、top_p=0.7；4B、9B、32B 五档长文均是每个题目/长度组合一次随机采样，没有重复采样方差估计。LCS score、完全匹配、答案包含率分别保存，不把输出包含关键词当作主指标。它与训练前后 external64 的 temperature=0、top_p=1 greedy 评估分开，不能混为同一组对照。
- 每条JSONL记录保存response、reward、num_chunks、num_contexts、total_steps和耗时；每档summary在 `results/memagent/<model>/`。
- 多个case/训练作业并行运行，耗时只用于本次执行记录，不是隔离环境下的吞吐benchmark。

## 有效结果

| 文档数 | 样本数 | 4B mean LCS / exact | 9B mean LCS / exact |
| --- | ---: | --- | --- |
| 50 | 8 | 0.5734375 / 4 | 0.4916667 / 3 |
| 200 | 8 | 0.6500 / 5 | 0.6500 / 5 |
| 800 | 4 | 0.5000 / 2 | 0.3821429 / 0 |
| 3200 | 2 | 0 / 0 | 0 / 0 |
| 6400 | 1 | 0 / 0 | 0 / 0 |

两模型各23题、共46题均完成，无 API error。最长样本在4B tokenizer下为907,670 token；4B分182块、9B分183块，后者使用自己的tokenizer。题数不同、干扰项顺序不同，不能把这个表当成严格的长度因果实验。

4B最长样本耗时236.6s，9B为2440.3s；它们有不同的输出长度及并行负载，不作为隔离吞吐比较。长文读完后仍可能答错，详见 [逐块记忆案例分析](details/memagent-case-study.md)。

额外诊断复跑保留在 `results/memagent-trace/`，未替换此表的任何原始样本结果。它只记录memory内容，不生成供训练的原始Gateway logprobs；真实训练轨迹另见RL实验。

`run_memagent.sh` 现对已有jsonl/summary/log拒绝覆写；不指定run名时使用时间戳。上游infer本身支持resume，但若直接使用其resume功能，应另行保存运行配置和本次耗时，避免把缓存结果当作新推理结果。
