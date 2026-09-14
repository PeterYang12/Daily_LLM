# 32B 第32步：分数变化里有什么

固定dev64的原始LCS从0.516629升到0.601278，14题提高、5题下降、45题不变。逐条看这19项后，能确认有5项提升和1项下降的答案事实内容相同，变化来自LaTeX空格或数字逗号。

这六项净贡献+0.03645833的均值变化，占已观察到净增分的43.1%。这是对当前分数差的算术拆分，不是“能力提升中有多少来自某因素”的因果估计。其它13项也不能自动视为知识能力变化。

这是事后定性诊断，没有修改训练奖励、解码参数或checkpoint选择，也没有读取外部dev64–127。完整19项响应及四个示例的原始final-context输入/输出保存在JSON记录（原始文件：`results/large-memagent/dev-step32-qualitative.json`）。

## 四个具体例子

1. **格式收益：YG Entertainment。** 两次自然语言回答都给出同一公司。baseline的boxed答案使用LaTeX转义空格，step32使用text包装，原始LCS从0.5到1。这不是新事实知识的证据。生日、John Waters、Eenasul Fateh和276170/276,170也有同类问题；Nixon任期则因空格拼接反向失分。

2. **最终答案选择改变：David Weissman。** baseline选Christopher Coppola，step32选中了既定gold David Weissman，LCS从0到1。但两份最终memory都保留了竞争性的编剧关联；不能因为最后答对就说整份memory已经准确。

3. **明确的目标答案回退：David Huntsinger。** baseline最后回答gold Larnelle Harris；step32变成Latice Crawford，LCS从1到0。memory也从“未明确提及与Latice合作”变成肯定的合作叙述。这里只核对记录和gold，不把模型在memory里新写出的关系当作经过外部验证的事实。

4. **关系链被保留：The Hard Easy。** baseline最终memory知道该集有Brian Doyle-Murray，但说没有兄弟关系线索并回答Unknown。step32 memory同时保留了客串信息和Brian是Bill Murray哥哥这条关系，最终回答gold Bill Murray。但其中也混入其他剧集和演员关系的说法；最终reward=1不等于所有中间记忆都被验证。

原生MemAgent只按最终boxed答案给reward，并将同一advantage广播到各memory context。上面的案例说明为何需要同时看最终答案、原始memory以及评分格式，不能只读一个LCS均值。

## 全部19项分数变化

| 题目简述 | LCS before → after | 定性范围 |
| --- | ---: | --- |
| YG Entertainment | 0.5000 → 1.0000 | 同一答案的格式/数字呈现 |
| Front Row控制设备 | 0.0000 → 0.0714 | 答案文本或选择变化；未独立验真全部事实 |
| Viglen产品类别 | 0.2000 → 0.2500 | 答案文本或选择变化；未独立验真全部事实 |
| David Huntsinger合作歌手 | 1.0000 → 0.0000 | 答案文本或选择变化；未独立验真全部事实 |
| Delirium写作者范围 | 0.2857 → 0.1818 | 答案文本或选择变化；未独立验真全部事实 |
| Roald Dahl销量 | 0.0000 → 0.1000 | 答案文本或选择变化；未独立验真全部事实 |
| Nixon任期年份 | 0.3333 → 0.0000 | 同一答案的格式/数字呈现 |
| Livesey纪念的战争 | 0.4286 → 1.0000 | 答案文本或选择变化；未独立验真全部事实 |
| Lewiston场馆座席 | 0.0000 → 0.5000 | 答案文本或选择变化；未独立验真全部事实 |
| Ethiopia独立/主权措辞 | 0.0000 → 0.2500 | 答案文本或选择变化；未独立验真全部事实 |
| Boxing Hall of Fame名称 | 0.8333 → 0.8000 | 答案文本或选择变化；未独立验真全部事实 |
| Strasbourg人口数字 | 0.0000 → 0.5000 | 同一答案的格式/数字呈现 |
| Jerry Goldsmith电影制片人 | 0.0000 → 1.0000 | 答案文本或选择变化；未独立验真全部事实 |
| Evolution与The Family Man编剧 | 0.0000 → 1.0000 | 答案文本或选择变化；未独立验真全部事实 |
| Schmeichel获奖称号 | 0.7500 → 0.4286 | 答案文本或选择变化；未独立验真全部事实 |
| Paul Manafort生日 | 0.3333 → 1.0000 | 同一答案的格式/数字呈现 |
| John Waters | 0.5000 → 1.0000 | 同一答案的格式/数字呈现 |
| Eenasul Fateh | 0.5000 → 1.0000 | 同一答案的格式/数字呈现 |
| The Hard Easy与Bill Murray | 0.0000 → 1.0000 | 答案文本或选择变化；未独立验真全部事实 |
