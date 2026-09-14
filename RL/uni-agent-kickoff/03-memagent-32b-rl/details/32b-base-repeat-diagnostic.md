# Qwen3-32B 同一 base 的重复评估诊断

主baseline继续使用 `base-durable`，原始LCS为0.4362723214，LCS=1为19/64。`base-durable-repeat`仅作为一次重复诊断，LCS为0.3978713396，LCS=1为20/64。两次均完成固定external64题，逐题奖励已按原生函数复算；运行时、输入文件SHA256、任务参数、模型root、窗口及并发8完全匹配。重跑结果没有替换主baseline，也不会据此选择训练checkpoint。

两次执行间的描述性均值差为−0.0384009818。这不是RL收益，也不是足以估计稳定噪声方差的样本；没有为这两次同base执行计算训练效果CI或显著性。匹配的配置与runtime记录本身也不能定位数值内核、调度或逐块记忆轨迹导致差异的根因。

| 逐题观察 | 数量 |
| --- | ---: |
| 完整final response逐字相同 | 28/64 |
| 原生规则解包后的答案相同 | 45/64 |
| reward相同 | 53/64 |
| repeat reward提高 / 降低 | 4 / 7 |
| 两次都LCS=1 | 17 |
| 仅主baseline LCS=1 / 仅repeat LCS=1 | 2 / 3 |
| 两次都不是LCS=1 | 42 |

分类只比较两次模型生成的字符串，不依照ground truth重写答案。先判断完整response相同，再判断原生提取答案相同；随后允许原生`split()`的空白处理，以及此前已经固定的 [TeX空格v1](../../00-overview/methods/tex-space-only-definition-v1.md) 单次反斜杠+ASCII空格替换。其他区别统一保留为“其他boxed答案文本差异”，不自动判定为语义或能力变化。

| 互斥分类 | 题数 | 改分题数 | 对repeat−main均值差的贡献 |
| --- | ---: | ---: | ---: |
| 完整response相同 | 28 | 0 | 0 |
| response不同、原生解包答案相同 | 17 | 0 | 0 |
| 仅固定TeX空格规则即可相同 | 3 | 2 | +0.015625 |
| 其他boxed答案文本差异 | 16 | 9 | −0.0540259818 |

三个TeX空格案例是`Owsley\ Stanley`、`Nebo\ Zovyot`和`Ian\ Watkins`的普通空格/TeX空格变化；前两题LCS从0.5变1，第三题reward不变。其余16题包含不同年份、道路编号、专辑名，也包含日期呈现、缩写和答案长短变化，不能全部解释成内容正确率变化。比如dev64的boxed年份从2003变2001，dev77道路从A41变A5117，dev87专辑从The Marshall Mathers LP 2变Recovery；dev121则是`third season`变`3`，这类文本变化的语义是否等价不由本诊断判定。dev72含嵌套`\text{}`和日期位置变化，原生解包规则会丢失部分字符，保留原始response供检查，没有增加针对该题的修正规则。

所有64题的完整response、原生解包答案、固定格式诊断结果、reward及分类都保留在 comparison.json（原始文件：`results/large-memagent/base-durable-repeat-diagnostic/comparison.json`） 与 all-pairs.csv（原始文件：`results/large-memagent/base-durable-repeat-diagnostic/all-pairs.csv`）。另外提供 36个response不同的逐题记录（原始文件：`results/large-memagent/base-durable-repeat-diagnostic/response-differences.csv`） 和 11个reward变化的逐题记录（原始文件：`results/large-memagent/base-durable-repeat-diagnostic/reward-differences.csv`）。输入文件前后SHA256一致，脚本只执行CPU分析，没有调用模型。复现入口为 audit_memagent_repeat.py（原始文件：`scripts/audit_memagent_repeat.py`）。
