# Qwen3-32B第96步：监控均值回升，逐题仍有明显进退

新run `memagent_32b_128_durable` 的固定monitor64题，step96原始LCS为 **0.5984375**，高于base的0.4944568452和step64的0.5584535256；LCS=1为 **28/64**。这仍是中间监控结果，继续使用预定的最终step128，不按当前最高分选择checkpoint。

独立审核原始validation0/64/96：三次各64个唯一问题、451个context，题目/gold和所有对应的原始文章chunk一致；final reward重算、整episode广播及FP32 reward张量舍入均通过。分类直接复用step32的同一函数，没有改变reward或增加96步专用修正规则。

| 固定monitor64 | step0 | step32 | step64 | step96 |
| --- | ---: | ---: | ---: | ---: |
| 原始boxed-answer token LCS | 0.4944568452 | 0.5874627976 | 0.5584535256 | 0.5984375000 |
| LCS=1题数 | 19 | 29 | 26 | 28 |

| 配对 | 均值差 | 升 / 降 / 不变 | 两次均LCS=1 | 新达到 / 失去LCS=1 |
| --- | ---: | ---: | ---: | ---: |
| base → 96 | +0.1039806548 | 16 / 5 / 43 | 19 | 9 / 0 |
| 64 → 96 | +0.0399839744 | 12 / 7 / 45 | 22 | 6 / 4 |

base的19道满分题在step96仍全部满分；但相对于step64，仍有4道满分题丢失、6道新达到满分。均值回升不能解释为每道题都改善，也不能将LCS=1称为另外实施的标准HotpotQA EM。

相对base的21项改分中，**7项同一答案的窄格式变化净贡献+0.0703125，占总净增分的67.62%**。剩余14项答案文本或选择变化净贡献+0.0336681548。这只是逐题分数的算术拆分，不是对学习能力来源的因果估计。

| base → 96改分类别 | 题数 | 净均值贡献 |
| --- | ---: | ---: |
| TeX转义空格 | 3 | +0.0260416667 |
| 整数千位逗号 | 3 | +0.0390625000 |
| 简单text包装、显示相同 | 1 | +0.0052083333 |
| 其他答案文本或选择变化 | 14 | +0.0336681548 |

64→96的19项改分中，只有Nixon年份区间被原窄规则识别为同答案格式变化：`1969–1974 → 1969 to 1974`，LCS从0回到2/3，对均值贡献+0.0104166667。其余18项净贡献+0.0295673077；仍不把这组余项自动称为知识能力提高。

几个可以从完整memory与final response直接核对的例子：

- **年份关系恢复：1993 → 1999，0 → 1。** Guns N' Roses题在step64误关联`Last Action Hero`；step96 memory重新保留`End of Days`、前纽约警探角色和歌曲`Oh My God`的1999年宣传线索，最终答1999。这是从一次退步恢复到原本能答对的目标关系，不能全部算成新增知识。
- **歌手答案收窄：0.4 → 1。** David Huntsinger题step64列出Latice Crawford与Larnelle Harris两个名字；step96 memory仅保留Huntsinger与7月6日出生的Larnelle Harris的相关关系，最终答gold Harris。
- **叙述包含正确对象，boxed却选错角色：1 → 0。** Alfred Balk题step96正文明确写“under Nelson Rockefeller”，并说明Rockefeller曾任Gerald Ford政府的副总统；最后boxed却填Gerald Ford。正确人名并未从memory完全消失，错误发生在最终答案角色选择。
- **目标被干扰文档替换：1 → 0。** `2014 S/S`题step64答YG Entertainment；step96 memory转而详细记忆另一个团体MADTOWN的专辑`Mad Town`，final response也直接回答了`Mad Town`的公司J. Tune Camp。原问题与原始chunk都没有改变，变化发生在记忆保留和目标对应上。
- **正确名字还在，多候选使答案变化：1 → 0。** Jerry Goldsmith/电影执行制片人题，step96 memory仍列有Ronald Shusett，但把多名制片人都当作候选并最终选择Gordon Carroll。最终gold匹配丢失，不能描述为完全不知道Shusett这个名字。
- **涨分依然可能未答对：0 → 1/6。** Chief of Protocol题step96改答`member of the California State Assembly`，并在memory中加入另一个电影年份、演员和州议会情节；这些叙述未在本审计中作为事实核验。最终答案仍不同于gold，LCS上涨来自共同词`of`，不构成正确官职的改善。

完整21项base→96改分和19项64→96改分均保留，没有只挑成功例子。见 独立audit.json（原始文件：`results/large-memagent/durable-step96-monitor-independent/audit.json`）、base→96改分CSV（原始文件：`results/large-memagent/durable-step96-monitor-independent/0_to_96-score-changes.csv`） 和 64→96改分CSV（原始文件：`results/large-memagent/durable-step96-monitor-independent/64_to_96-score-changes.csv`）；同目录all-pairs CSV也保留未改分题和完整final response。输入SHA256前后不变，没有运行external评测、调用GPU或更改训练配置。
