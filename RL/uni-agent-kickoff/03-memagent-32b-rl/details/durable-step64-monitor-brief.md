# Qwen3-32B第64步：监控分数仍高于base，但相对32步回落

新run `memagent_32b_128_durable` 的固定monitor64题，在step64原始LCS为 **0.55845**，高于base的0.49446，低于step32的0.58746。LCS=1对应 **19 → 29 → 26题**。这是同一组监控题的中间结果，继续按预定协议训练至128；没有选择step32替代最终checkpoint，也没有调整reward或采样。

独立从原始validation0/32/64重建三组结果：每次64个唯一问题、451个context；题目、gold和各原始文章chunk一致，final LCS已重新计算，全部context的score/acc及FP32 reward广播吻合。新审计直接导入 step32使用的原分类函数（原始文件：`scripts/audit_durable_monitor32.py`），没有针对64步结果扩展字符串修正规则。

| 固定monitor64 | step0 | step32 | step64 |
| --- | ---: | ---: | ---: |
| 原始boxed-answer token LCS | 0.4944568452 | 0.5874627976 | 0.5584535256 |
| LCS=1 | 19/64 | 29/64 | 26/64 |
| session / context | 64 / 451 | 64 / 451 | 64 / 451 |

| 配对范围 | 均值变化 | 升 / 降 / 不变 | 两次均LCS=1 | 新达到 / 失去LCS=1 |
| --- | ---: | ---: | ---: | ---: |
| base → step64 | +0.0639966804 | 14 / 9 / 41 | 15 | 11 / 4 |
| step32 → step64 | −0.0290092720 | 7 / 9 / 48 | 24 | 2 / 5 |

相对base的23项改分中，7项同答案的窄格式变化净贡献 **+0.0598958333**：4项TeX空格升分、2项整数千位逗号升分，以及1项相同年份区间表示的失分。它们占这次净均值增量的 **93.59%**；剩余16项答案文本或选择变化净贡献+0.0041008471。该比例只是当前配对分数的算术拆分，不能表述为“93.59%的学到能力来自格式”，也不能将其余项全部视为事实知识增量。

相对step32，唯一被同一窄格式规则识别的改分是月份题：`March \text{ and } April`恢复为普通`March and April`，LCS从2/3回到1，给均值增加0.0052083333。其余15项净贡献−0.0342176053，合起来才是−0.0290092720的回落。句子长短、答案范围和题意解释没有被擅自归入格式修正。

具体例子说明，这条曲线同时包含改善、回退与评分限制：

- **Bill Murray关系链丢失：1 → 0。** step32最终memory保留了Brian Doyle-Murray是Bill的哥哥。step64 memory偏向另一集`Evicted!`和Erik/Danny Estrada，称没有`The Hard Easy`客串演员的兄弟信息，最后输出`Not enough information`。这里能从完整memory看到任务相关关系被另一条叙述替代。
- **年份与电影混淆：1999 → 1993，1 → 0。** step64将题目重新关联到`Last Action Hero`，把歌曲`Oh My God`、纽约警探描述与洛杉矶的Jack Slater混在同一memory，还将此前`End of Days`关联称为错误。最终改答1993，与固定gold1999不同；这不是数字标点变化。
- **正确信息还在，但答案字段变了：0.5 → 0。** Strasbourg题在step64 memory中同时保留“2014年276,170 inhabitants”和居民称谓Strasbourgeois，最终却回答居民称谓。问题本身英文表达较差，这例体现数量/称谓的题意选择变化，不能简单说模型忘记了人口数字。
- **追加答案和包装共同影响：1 → 0.5。** 编剧题step32只答David Weissman；step64额外列出Nick Vallelonga，并用`\text{ and }`包装连接词。新memory还声称两人都有Evolution编剧credit。gold名字仍在最终回答里，但多答案及原生解包行为改变分数。按原窄规则，它仍属于“其他答案文本变化”，没有新增专用修正来挽回分数。
- **题目前提被质疑：1 → 0。** Canberra题step64明确指出该飞机服役时间在二战之后，因此答None，而固定gold是English Electric Canberra。本审计保留官方0分，不进行外部事实重判；仅从失分不能断言这是知识能力退化，回答的拒绝理由也需要人审。
- **仍有明确的目标选择改善：Apple Remote题0 → 1。** step64最后选择了gold `keyboard function keys`，memory指向Front Row文档中的相关句子；step32答的是universal remote。step64也保留了其他未经逐项核实的控制方法，不能因最终答对就宣布整份memory正确。
- **行业判断纠正但仍是部分匹配：Viglen题0.25 → 0.4。** step32误答insurance-related products；step64改为IT infrastructure and technology products，接近gold IT products and services。它支持答案方向变化，但不等于标准化准确率从0变1。

所有64题的两组配对、每次完整final输入/输出、raw boxed文本、格式诊断及UID都在 独立audit.json（原始文件：`results/large-memagent/durable-step64-monitor-independent/audit.json`）。可直接核对 base→64的23项改分（原始文件：`results/large-memagent/durable-step64-monitor-independent/0_to_64-score-changes.csv`） 与 32→64的16项改分（原始文件：`results/large-memagent/durable-step64-monitor-independent/32_to_64-score-changes.csv`），相应all-pairs CSV保留未改分题。源文件前后SHA256不变；没有读取external评测结果、调用GPU或修改训练配置。

这些中间观察说明：监控LCS并非单调，格式收益与答案选择变化需要分开。最终结论仍由预定step128和同机器、同协议的独立base/final评测给出，不能从一个中间最高点推出稳定泛化提升。
