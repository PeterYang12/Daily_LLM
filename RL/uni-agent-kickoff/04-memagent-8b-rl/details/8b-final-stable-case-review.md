# 8B final128：稳定协议外部评测与源文案例复核

Qwen3-8B 完成预定 128 个训练 global step 后，在固定 64 道 external 题上，原生题均 LCS 从 **0.4183199179 升至 0.5346726190**，差值 **+0.1163527011**。按题配对、20,000 次 bootstrap 的 95% CI 为 **[+0.0173656205, +0.2194321282]**。原生 `LCS=1` 从 **19/64 升至 28/64**，15 题增分、7 题降分、42 题 reward 不变。

这是 BI=1 / TRITON_ATTN 的预先冻结 stable 协议：HIP1、TP1、BF16、eager、16K model window，greedy、concurrency8，source chunk5000、memory/final 各1024 token。所有四轮使用同一份 external64；每个模型阶段的两遍评测使用同一服务进程。final 固定为 step128，没有用中间 monitor 选 checkpoint。

| 固定配对 | Base LCS | Final128 LCS | 差值 | 按题配对 95% CI |
| --- | ---: | ---: | ---: | --- |
| first | 0.4183199179 | 0.5346726190 | +0.1163527011 | [+0.0173656205, +0.2194321282] |
| repeat | 0.4183199179 | 0.5346726190 | +0.1163527011 | [+0.0173656205, +0.2194321282] |

四次执行均为 64/64 完成、错误 0。Base 两遍的最终回答和 reward 分别 64/64 完全相同，final 两遍也是 64/64 完全相同。满分转移为：保留16道、增加12道、丢失3道、两阶段均未满分33道。Base 与 final 的完整回答仅1题逐字相同；reward 不变不代表文字不变。

重复评测验证了这个记录环境中的可重复性，没有增加独立训练 seed 或题目数量；不能把两遍合并成128题计算置信区间。`LCS=1` 是原生 boxed-answer token 指标满分，不是额外人工语义判定。

## 独立机器复核与恢复记录

final128-stable-independent-audit-20260913.json（原始文件：`results/memagent-8b/final128-stable-independent-audit-20260913.json`） 已重新读取四份原始结果和 provenance，验证全部64个唯一源题及原生 reward，重算两组20,000次配对bootstrap，逐字段校验 paired CSV、模型路径、原方法与 GPU amendment、冻结40项 operations 输入、runtime/实际进程 argv/env/backend 和服务日志前缀。8B provenance 审计在独立子进程中执行，不会修改32B分析器的全局配置。

final128-stable-case-review-independent.json（原始文件：`results/memagent-8b/final128-stable-case-review-independent.json`） 在这些检查通过后，核对本页全部案例的源 context hash、文档编号、原文字符位置、完整 before/after 回答和两遍配对结果。以下是事实说明与原有指标的解释，不引入新的判分规则。

此次 native128 训练成功后，旧导出验收曾因为合法的单个 `model.safetensors` 不带 index 而失败。官方 merger 已成功导出完整权重；恢复只补标准 index，完整 safetensors 文件 SHA 前后相同。原 controller/export 的 `failed` 记录保留，新 export recovery 和 CPU continuation 分开记录。final driver 使用明确的 layout recovery gate（原始文件：`scripts/verify_memagent8b_layout_recovery.py`），保留原 starter 的设备/runtime/实际 backend 检查，调用原冻结 runner。恢复步骤见[8B 评测与单文件恢复章节](../REPRODUCE.md#qwen3-8b-的独立外部评测新节点登记两组配对与单文件恢复)。恢复不会把原失败重写成成功，也没有重跑或替换 baseline。

## 窄格式贡献

| 原有分类 | 题数 | 增 / 降 | 对题均 LCS 的净贡献 |
| --- | ---: | ---: | ---: |
| 相同日期的简单 TeX 包装：row68 | 1 | 1 / 0 | +0.0104166667 |
| 相同姓名的 TeX 空格：row118 | 1 | 1 / 0 | +0.0078125000 |
| 其他答案文字或选择变化 | 20 | 13 / 7 | +0.0981235345 |
| reward 不变 | 42 | 0 / 0 | 0 |

两道窄格式题净贡献 **+0.0182291667**，占观测净增的 **15.6672%**。这是未经修改的 `audit_durable_monitor32.classify` 规则下的算术分解。剩余84.33%不能全部称为知识增长：其中还包括姓名缩写、同一路口的连接词、同一机型省略后缀、年代范围表达、实体选择与实际错误变化。

这份 external 结果与训练 monitor 必须分开。8B monitor 的净增 +0.046875 可以完全由其窄格式贡献解释；**不能把 monitor 的格式比例套到本页 external 上，也不能把本页比例反套到 monitor。**

## 四个源文支持的目标答案改善

表中 `dev row` 使用冻结数据的原始行编号。可读答案取自完整最终回答；机器文件另外保留 raw box 和原生提取字符串，特别是嵌套 TeX 可能改变提取结果。

| dev row | 问题目标 | Base → final128 | 原生 LCS |
| --- | --- | --- | ---: |
| 72 | 《Here We Go Round the Mulberry Bush》原著作者的出生日期 | Clive Donner 的 21 January 1926 → **Hunter Davies 的 7 January 1936** | 0 → 1 |
| 75 | Suicide 的1977专辑歌曲所依据漫画角色的品牌 | Ghost Rider → **Marvel** | 0 → 1 |
| 78 | Caroline Carver 在指定1999年 Hallmark 奇幻电影中的角色 | Fiona → **Princess Jessica** | 0 → 1 |
| 109 | 《“Q” Is for Quarry》作者的侦探小说家父亲 | John Dickson Carr → **C. W. Grafton** | 0 → 1 |

**row72：区分导演与原著作者。** 文档17写的是电影导演 Clive Stanley Donner，出生于21 January 1926。文档109写明电影改编自 Hunter Davies 的同名小说，文档177给出 Davies 的出生日期7 January 1936。Base 选到了导演，final 选到题目要求的作者。这里不仅有日期/TeX表达差异，目标人物本身也改正了。

**row75：回答品牌，而不是相关角色。** 文档17明确 Ghost Rider 是 Suicide 首张专辑中的歌曲，并基于 Marvel Comics 角色；文档81明确这张首专于1977年发行。Base 框选相关歌曲/角色名 Ghost Rider，final 框选问题要求的 Marvel 品牌。

**row78：演员与特定角色配对。** 文档96说明《The Magical Legend of the Leprechauns》是1999年 Hallmark Entertainment 电视奇幻电影，演员包含 Caroline Carver。文档109明确她在该片中的角色是 Princess Jessica。Base 的 Fiona 没有本题源文支持，final 改成正确角色。

**row109：作品、作者、父亲的关系。** 文档7写明《“Q” Is for Quarry》属于 Sue Grafton 的系列；文档96明确她是侦探小说家 C. W. Grafton 的女儿。文档17虽提到另一位侦探小说家 John Dickson Carr，却与题目父女关系无关。Final 的目标人名符合原文关系。

这些题每个 context 含200份文档，本次各分6个source chunk后再final。它们支持“这几题的最终目标答案更符合给定材料”，没有单独证明模型获得了新事实，也没有定位中间哪一次 memory 更新变好了。

## 两个真实回退

| dev row | Base → final128 | 原生 LCS | 源文支持的判断 |
| --- | --- | ---: | --- |
| 85 | 700 block of Higuera Street, **San Luis Obispo**, California → **Santa Cruz**, California | 4/9 → 1/4 | Base 含正确目标地点，final 换到另一家机构的地址。 |
| 113 | **drawings** → **people** | 1 → 0 | Albertina 的约65,000项对象是素描，final 的人口单位错误。 |

**row85：把另一个含 gum 线索的地址当成景点地址。** 文档81和109明确 Bubblegum Alley 在 San Luis Obispo；文档10的 Santa Cruz 地址属于 Digital Media Factory，位于以前的 Wrigley Gum 厂房。Base 因多写具体街道而未拿到 LCS 满分，但地点事实正确。Final 说明中同时出现两地，最后却框选 Santa Cruz。这是目标地点回退，不能仅称格式差异。

**row113：相同数量对应了错误对象。** 文档109将 Hanna Varis 作品关联到 Albertina；文档17明确馆藏约65,000 drawings。Final 框选 people。源文中确实有其他文档使用约65,000 people，例如 Karaboro 语言使用者和市场日游客；这些只是可能的干扰证据，不能据此断言某份文档在某次 memory 压缩中覆盖了正确事实。

## 指标与表达的边界

| dev row | 变化 | 原生 LCS | 解读 |
| --- | --- | ---: | --- |
| 68 | 同一 Zaheer Khan 出生日期的嵌套 TeX/空格写法改变 | 1/3 → 1 | 最终事实都为7 October 1978；原生 boxed 提取对包装敏感。 |
| 69 | B-17 Flying Fortress **bomber** → B-17 Flying Fortress | 1 → 0.75 | 同一飞机，省略类别后缀却降分；原窄分类归入 other。 |
| 65 | North Avenue **and** Techwood Drive → North Avenue **at** Techwood Drive，两者均带 Atlanta, Georgia | 3/7 → 4/7 | 同一路口，连接词与 gold 更贴合；不是新的地理事实。 |
| 89 | 正文写对 Jillian Belk 但框选 Jillian Bell → 框选 Dr. Lauren Boswell | 0.5 → 0 | Base 已有正文/框选不一致，final 又选到另一部剧的人物；reward只看最终boxed answer。 |

原生 reward 只取最终回答末尾300字符中的最后一个 boxed answer，小写后按空白token计算LCS，再除以候选和gold中较长的token数。它不会把正文中的正确句子当成正确框选，也不是事实核查器。不能把全部其他类增分解释为事实能力增长，或把全部降分都解释为事实错误。

## 与32B比较时能说到哪里

在同一 stable external 配方上，32B 是0.4209077381→0.5601190476，8B 是0.4183199179→0.5346726190；两者各自 base→final 的按题配对区间下界都大于0。8B final 的原生满分题数为28，32B为26，但32B的平均部分分数更高。这两个指标排序不同，应同时报告，不能只挑有利的一个。

这里只完成每个规模一次训练；各自提升的CI不是“32B比8B提升更多”的检验，不能据区间或点差宣称两种规模存在显著能力差异。成本的独立日志复核见model-scale-final-training-costs-independent.json（原始文件：`results/model-scale-final-training-costs-independent.json`）：同样111个普通步骤，median 为32B 163.29秒、8B 52.94秒；记录的128迭代总时间为7.77小时与2.38小时。时间是本次运行的观测成本，包含生成长度和并行负载差异，不是独立控制的硬件速度测试。

## memory 机制的证据范围

每轮 formal external 保存最终 `response`、reward、chunk/context/step数量和运行 provenance。四轮各有388个source chunk、452个含final的context。独立检查没有发现中间memory字段，`infer.log`也没有完整prompt/memory更新序列；新增layout附件仅记录恢复验收关联。

因此，本次可以复核最终回答、材料、原生指标和重复性，不能定位“第几块压缩丢掉了drawings”“哪个memory版本保留了作者”。最终回答里“from the provided memory”属于模型自述，不是已保存的中间状态。训练或monitor轨迹不能替代这些external题本身缺失的memory记录。

复核命令均为CPU Docker执行。已存在的结果不覆盖，复跑请使用新输出名：

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/audit_8b_final_results.py \
  --first /lab/results/memagent-8b/comparison-stable-step128/comparison.json \
  --repeat /lab/results/memagent-8b/comparison-stable-step128-repeat/comparison.json \
  --output /lab/results/memagent-8b/final128-stable-independent-audit-20260913.json

docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/review_8b_external_cases.py \
  --audit /lab/results/memagent-8b/final128-stable-independent-audit-20260913.json \
  --output /lab/results/memagent-8b/final128-stable-case-review-independent.json
```
