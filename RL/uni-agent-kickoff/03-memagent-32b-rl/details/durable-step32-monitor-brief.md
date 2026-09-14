# Qwen3-32B新长训练：第32步监控结果与分数来源

新run `memagent_32b_128_durable` 的固定monitor64题，原始LCS从 **0.49446升至0.58746**，LCS=1从 **19/64增至29/64**。独立读取step0/32原始validation轨迹后，确认15题升分、7题降分、42题不变；两次各64个session、451个context，所有对应的原始文章chunk相同、没有缺题或重复题。

这是新run的第32步中间监控结果，不能代替最终128步或external对照。分数变化中，**9项同一答案的格式变化净贡献+0.0625，占总均值增量+0.09300595的67.2%**。这个比例是逐题分数的算术拆分，不是对“学习到的能力来源”的因果估计。

| 新run固定monitor64 | step0 | step32 |
| --- | ---: | ---: |
| 原始boxed-answer token LCS | 0.4944568452 | 0.5874627976 |
| LCS=1 | 19/64（29.69%） | 29/64（45.31%） |
| session / context | 64 / 451 | 64 / 451 |

其中17题两次均LCS=1，12题新达到LCS=1，2题失去LCS=1。LCS=1是本实验固定的boxed答案空白分词指标，不是额外实施的标准HotpotQA EM。

| 对22项改分的窄规则归类 | 题数 | 升 / 降 | 对64题LCS均值差的贡献 |
| --- | ---: | ---: | ---: |
| TeX转义空格 | 4 | 4 / 0 | +0.03385417 |
| 整数千位逗号 | 3 | 3 / 0 | +0.03906250 |
| 简单text包装显示相同 | 1 | 0 / 1 | −0.00520833 |
| 同一四位年份区间的分隔符 | 1 | 0 / 1 | −0.00520833 |
| 其他答案文本或选择变化 | 13 | 8 / 5 | +0.03050595 |

分类不借助gold修改生成答案。TeX空格沿用此前冻结的v1替换；整数仅移除合法千位分隔逗号，不改数值或单位；text规则只展开无嵌套的简单包装并拒绝其余LaTeX命令；年份规则要求两端四位年份逐字一致。它们只是事后诊断，训练reward、解码参数和checkpoint选择均未改动。其余13项仍包含措辞、答案范围和错误，不能直接命名为知识能力增量。

可用于展示的具体轨迹：

- **格式带来整题涨分：1462 → 1,462。** Euromarché/Carrefour题在step0的自然语言段落已经写出“1,462 hypermarkets”，但boxed中省略逗号，LCS为0。step32仅将boxed写成`1,462`就得到1。Boston人口`35124 → 35,124`同样从0变1；这两项没有显示新事实被学会。
- **找到更准确的目标：Crusaders of Khazan → Arena of Khazan。** step0 memory只保留电脑改编作品Crusaders；step32 memory同时列出1979年的桌游adventure Arena和1990年的电脑改编Crusaders，并选择gold Arena，LCS从2/3升到1。这是比格式例子更具体的答案选择改善。
- **关系链变化：Jim Murray → Bill Murray。** step0 memory把Brian Doyle-Murray与体育作家Jim Murray混为兄弟关系，并选Jim；step32 memory保留了Brian是Bill的哥哥，最终选gold Bill，LCS从0.5升到1。最终正确仍不代表整份memory所有叙述都已逐条验证。
- **解释题意变化：None → Nelson Rockefeller。** 两次memory其实都知道Alfred Balk与Nelson Rockefeller的委员会有关。step0因当时Rockefeller是州长而不是副总统，拒绝给出该名字；step32按题目以其后来的副总统身份指代人名，选中gold。这个0→1例子主要体现题意/答案选择变化，不能说baseline完全不知道该事实。
- **明确回退：Larnelle Harris → Latice Crawford。** step0正确指出Harris出生在7月并与David Huntsinger合作，最终答对。step32 memory增加了“Huntsinger与Latice合作”的叙述，尽管同份memory后部仍列有Harris，最终误选Latice，LCS从1降到0。
- **分数略升但仍未答对：Chief of Protocol题。** step0答United States Ambassador，step32给出更长的一串大使及官职，仍未给出gold Chief of Protocol。原始LCS仅因共用词`of`从0变成1/21≈0.04762，这不构成正确答案的改善。
- **格式也会造成回退：March and April。** 两次自然语言和memory均给出同一月份。step32改用`March \text{ and } April`，原生解包器丢掉包装前的March，LCS从1变2/3。Nixon题两次都是1969–1974，区间呈现方式改变也使分数从1/3变0。

全部64题完整final输入/输出、原始boxed文本、分类、UID及源行位置保存在 独立audit.json（原始文件：`results/large-memagent/durable-step32-monitor-independent/audit.json`）；64题配对CSV（原始文件：`results/large-memagent/durable-step32-monitor-independent/all-pairs.csv`） 和 全部22项改分CSV（原始文件：`results/large-memagent/durable-step32-monitor-independent/score-changes.csv`） 可直接复核。审计从原始trace重新计算final LCS并验证向全部context广播的score/acc，同时按精确FP32舍入检查reward张量字段；再与RL负责人保存的step32 snapshot逐题交叉核对。输入文件前后SHA256保持不变，没有调用模型、GPU或external评估。
