# 大模型汇报图

绘图脚本是 `scripts/plot_large_cases.py`，在 `ua-lab-cpu` 的 `/lab/envs/report` 中用 Matplotlib 3.10.8 生成静态 PNG 和 SVG。旧 `plot_lab.py` 及其图片保持独立；没有模型调用。

前两图在图内明确标记 pre-RL checkpoints/inference，表示原始模型的推理记录，不能将其变化解读为本轮 RL 训练收益；第三图同样来自未做本轮 RL 更新的 Coder30B。

当前三张均已完成并实际打开检查：

| 图 | PNG | SVG | 内容 |
|---|---|---|---|
| 六题九行 R/F 热图 | [PNG](../figures/swe-six-nine-rows.png) | [SVG](../figures/swe-six-nine-rows.svg) | 4B、9B、Coder30B × ReAct、Claude、Mini；同六个 ID；右侧显示 resolved、finished、两者均满足 |
| 三模型长文五档 | [PNG](../../02-memagent-long-context/figures/memagent-three-models-five-tiers.png) | [SVG](../../02-memagent-long-context/figures/memagent-three-models-five-tiers.svg) | 4B、9B、32B dense 的 MemAgent 平均答案 LCS；共同文档档位 50/200/800/3200/6400 |
| Expanded 29 例按仓库 | [PNG](../figures/swe-expanded29-by-repo.png) | [SVG](../figures/swe-expanded29-by-repo.svg) | 完整 29 例的四种 R/F 状态；Django 3/8、Sphinx 1/8、SymPy 1/8、xarray 3/5 resolved |

R 表示官方 verifier 判定 resolved，F 表示 Agent 明确 finished。四种颜色分别表示两者均否、仅修复成功、仅正常结束、两者均是。30B ReAct/Claude 的 Flask 单元格有 `*`，表示修改过已有测试文件；source-only 控制另已通过。**4B/9B 没有重新审计这一约束**，所以没有星号不等于确认其遵守了约束。图内保留了“诊断子集，非官方 benchmark 或受控模型排名”的说明。

长文曲线使用共同 docs 档位，避免混用各模型 tokenizer 长度。三模型每档的 `sample_key/source_index/question/answers` 完全一致，并逐项验证原始 JSONL 与 summary 的平均 reward 相同；各档 n=8/8/4/2/1，跨档确实复用了同一组问题前缀，因此不是独立样本。LCS 是 boxed answer 的小写空白分词指标，不是模型 tokenizer 指标。图内注明了 5000-token 分块、1024-token memory cap、并非原生百万 token attention，也不比较墙时吞吐。

图内还明确注明长文 `temperature=1.0、top_p=0.7`，每个题目/长度组合只有一次随机采样，没有重复采样方差估计。这与 external64 固定的 `temperature=0、top_p=1` greedy 评估是两个协议；不能将长文随机样本变化并入训练前后配对收益。

图片随本资料收录；原始数据、绘图脚本和 provenance JSON 保留在完整实验记录中。下方命令用于已有完整实验工作区的重绘。

## expanded 29 例保护

`expanded_figure()` 已实现按仓库的 R/F 堆叠图。它只有在以下条件都满足时才输出正式 PNG/SVG：runner 已完成、聚合文件恰好有 29 个唯一 ID 且与冻结 manifest 完全一致、每题都有真实二值 reward/finished、没有外层未评分错误、汇总与逐题计数一致、各仓库数量为 8/8/8/5。

29 例全部终态后生成正式图：8/29 resolved、28/29 finished、8/29 两者均满足；100-turn expanded 协议与六题 40-turn 图分开报告。

```bash
# 重新生成前两张完整图；只修改派生图和其 provenance。
docker exec ua-lab-cpu /lab/envs/report/bin/python \
  /lab/scripts/plot_large_cases.py --plots swe memagent

# 单独生成第三张；只接受完整 29 例，未完整时仅输出 pending 记录。
docker exec ua-lab-cpu /lab/envs/report/bin/python \
  /lab/scripts/plot_large_cases.py --plots expanded
```
