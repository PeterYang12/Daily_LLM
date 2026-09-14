# Qwen3-32B Agentic RL：128步训练阶段总结

[本实验总览](README.md) · [全部实验](../README.md)

> 仓库阅读版：保留方法、步骤、结果和图表。源码链接为固定上游版本参考；未收录的原始实验文件仅以路径引用。

更新日期：2026-09-12 UTC。**Qwen3-32B 的128步全参数MemAgent RL已经完成，完整断点、实际恢复、全训练集覆盖与预登记的稳定辅助评测均已验收。** 在固定external64题上，该稳定协议的平均原生LCS从 **0.42091升至0.56012**，LCS=1题数从 **20增至26**；训练前后各重复一遍，两次base之间、两次final之间的64条最终response分别逐字相同。

上述数字来自单独冻结的`VLLM_BATCH_INVARIANT=1 / TRITON_ATTN`协议。原定GPU0默认后端的final服务超过600秒启动期限，后来才完成初始化；没有执行任何final评测题。实际观察到该卡同时有其他用户长期GPU测试占用及H2D同步等待。原GPU0 baseline和失败记录保留；GPU1上重新配齐的默认后端base/final四轮对照现已完成并独立审计通过，结果在下文单独列出。

用户追加的Qwen3-8B也已完成独立128步与最终评测：相同stable配方下LCS0.41832→0.53467，满分19→28；详见[两规模完整总结](../00-overview/RESULTS.md)。

## 1. 跑的任务与训练配置

模型逐块阅读长文，每次读约5000 token，结合上一轮记忆生成最多1024 token的新记忆；全部材料读完后，再生成最多1024 token的最终答案。同一题采样4次完整执行，以固定上游boxed答案token-LCS作为最终奖励，广播给该次执行的各个context，送入verl做GRPO类策略更新。

| 项目 | 实际配置 / 完成证据 |
| --- | --- |
| 模型 | Qwen/Qwen3-32B，revision `9216db5781bf21249d130ec9da846c4624c16137`，32,762,123,264参数 |
| 执行方式 | Docker、ROCm/gfx950；4个FSDP2 trainer GPU + 2个TP2 rollout GPU |
| 算法 | GRPO，组内优势只做均值中心化、不除以std；token-mean loss；KL loss系数0.01，entropy系数0 |
| 精度与学习率 | FP32 master / BF16 compute，SDPA，gradient checkpointing；LR峰值1e-6，4个globalstep warmup |
| 训练数据 | 固定512个源题；每题4次采样，完整一轮128个globalstep |
| 实际消费 | **512个不同源题各一次、2048个session、13872条真实context、464条synthetic padding** |
| 优化器 | 四个rank各707个Adam状态，最终counter **896**、scheduler128；这些是同一个分布式优化进度，不能将四rank计数再相加 |
| 任务信号 | 179/512个题组的4个reward不全相同；108个globalstep有任务优势信号，其中第1步LR为0；20步为KL-only当前梯度，Adam历史仍可能影响参数更新 |
| 验证划分 | 64题monitor：0/32/64/96/128；另64题external：只评估原始base与预定final128 |

训练期间曾因外部进程终止回滚到完整step32。旧33/34及关联日志带SHA归档，恢复后重新执行，不重复计数。更早另一个中断run的46步也没有并入本轮128。

这里没有完整复现上游DrGRPO recipe：本次关闭了优势的标准差归一化，但仍使用token-mean loss及KL0.01；上游完整DrGRPO recipe还涉及其他损失归一化与KL设置。历史协议里的“DR-GRPO风格”只描述去std这一点，冻结文件保持原样。

两个实际恢复链路都已验证：step8→9受控暂停恢复，以及step32→33在SIGTERM后的恢复。检查包括所有Adam counter、scheduler/RNG和固定model/moment probes，随后核对下一步真正消费的缓存UID。最终又从固定parquet逐题重建source chunks，证明512源题覆盖完整、没有跨恢复重复或遗漏。

证据：最终源覆盖（原始文件：`results/large-memagent/durable-train-source-coverage-final128.json`）、训练阶段汇总（原始文件：`results/large-memagent/durable-step128-training-stage-summary.json`）、最终配置与训练分析（原始文件：`runs/rl/memagent_32b_128_durable/analysis.json`）。

## 2. 固定external64：稳定协议的最终结果

两组都采用HIP1、同一固定image/runtime、BF16、TP1、eager、16384窗口、非thinking、temperature0/top_p1、并发8、memory/final各1024，以及相同问题与分块。Base与final分别使用自己的同一服务进程完成first/repeat。

| 预登记配对 | Base平均LCS | Final128平均LCS | 差值 | 95%问题级paired bootstrap区间 | LCS=1 |
| --- | ---: | ---: | ---: | --- | --- |
| First | 0.42090774 | 0.56011905 | **+0.13921131** | **[+0.04010231, +0.24014416]** | 20/64 → 26/64 |
| Repeat | 0.42090774 | 0.56011905 | **+0.13921131** | **[+0.04010231, +0.24014416]** | 20/64 → 26/64 |

每组均64/64完成、错误0。逐题为19题升分、7题降分、38题同分；LCS=1新增9题、丢失3题，净增6题。两遍final的64条response全部相同，全部reward相同；两遍base也具有相同的一致性。重复不合并为128个独立题目。

![稳定协议external64最终对照](figures/final-stable/qwen3_32b_stable_external_final128.png)

该区间以64道问题为重采样单位，20,000次paired percentile bootstrap。它描述这套固定题和一次训练所得到的差异，不包含跨训练seed方差，也不证明任意输入/重启/硬件都具有同样重复性。LCS=1仍按固定boxed答案评分定义，不能等同于独立实现的标准HotpotQA EM。

来源：First完整配对（原始文件：`results/large-memagent/comparison-durable-stable-step128/comparison.json`）、Repeat完整配对（原始文件：`results/large-memagent/comparison-durable-stable-step128-repeat/comparison.json`）、Final同模型重复性（原始文件：`results/large-memagent/stable-final-repeatability/comparison.json`）、[SVG](figures/final-stable/qwen3_32b_stable_external_final128.svg) / [PDF](figures/final-stable/qwen3_32b_stable_external_final128.pdf)。

## 3. 提升中有多少是格式

用在monitor32时已经固定的窄分类规则检查external结果：2道题仅TeX空格变化即可解释得分增加，净贡献 **+0.015625**，约占external净增分的 **11.22%**。其余24道答案文本/选择变化合计 **+0.12358631**；其中17题升分、7题降分。

这个剩余项仍包括答案别名、表述长短、标签覆盖和未被窄规则涵盖的格式差异，不能全部称为“新增知识”或“记忆能力提升”。独立复核已将原始题目、gold、模型最终回答和source context逐项对应，下面是适合汇报的具体例子：

| dev row / 题目目标 | Base → final128 | LCS变化 | 源文核对 |
| --- | --- | --- | --- |
| 69：基地命名对象试飞的飞机 | Verville-Sperry R-1 → **B-17 Flying Fortress** | 0 → 1 | 文档10确认基地，文档191确认人物及B-17，final满足这条关联 |
| 81：指定人物与两位演员共同出演的电影 | Bottom of the World → **The Bye Bye Man** | 0.25 → 1 | final同时满足最小女儿、年份及两位共同演员条件 |
| 94：两部电影的共同苏联片源 | Mechte Navstrechu → **Nebo Zovyot** | 0 → 1 | Base只满足其中一部关联，final满足两部及年份条件 |
| 116：CEO本人作为嘉宾参加的播客 | Maltin on Movies → **Comedy Film Nerds** | 0 → 1 | 源文明确列出嘉宾关系，区别于平台上发布的节目 |
| 123：目标主持人做嘉宾的节目 | Hidden America with Jonah Ray → **Free Radio** | 1 → 0 | final混淆不同演员及节目，是目标答案回退 |
| 124：目标党派终止年份 | 1943 → **1991** | 1 → 0 | final正文仍有正确1943，框选却转答另一个时期 |

也有明显的指标边界：同一日期换词序可升分；Las Vegas扩写为Las Vegas, Nevada从1降到1/3；Pasek & Paul改为成员本名仍可能降分。完整案例和源文位置见[独立案例复核](details/32b-final-stable-case-review.md)，机器证据见逐题审计JSON（原始文件：`results/large-memagent/final-stable-case-review-independent.json`）。

正式external输出保存最终response和context计数，没有逐轮memory内容。因此上述例子支持“最终目标答案更符合材料”或“目标答案回退”，不能定位到某一次记忆压缩，也不能用回答中的自述填补中间轨迹。

这与monitor上的格式比例不同：最终monitor净增0.09938322，其中7项窄格式变化贡献0.06770833，约68.13%。两套问题和评测路径分别统计，不能把monitor的比例直接套到external。

来源：External First格式拆分（原始文件：`results/large-memagent/final-stable-format-first.json`）、Repeat格式拆分（原始文件：`results/large-memagent/final-stable-format-repeat.json`）、最终monitor独立审计（原始文件：`results/large-memagent/durable-step128-monitor-independent/audit.json`）。所有拆分保留原生reward和原配对CI；这是算术分类，不是能力来源的因果估计。

## 4. 长训练过程

| Monitor globalstep | 平均LCS | LCS=1题数 |
| --- | ---: | ---: |
| 0 | 0.49445685 | 19/64 |
| 32 | 0.58746280 | 29/64 |
| 64 | 0.55845353 | 26/64 |
| 96 | 0.59843750 | 28/64 |
| **128** | **0.59384007** | **26/64** |

![128步monitor曲线](figures/final-training/rl_memagent_32b_128_durable_validation.png)

分数不是单调上升，满分题数也没有持续增加。本轮始终使用预定step128做终点评估，没有用step32或96替代。最终monitor相对base有14题升分、4题降分、46题同分，原19个满分题全部保留，新增7个。

从fresh启动到native结束的墙钟约 **11小时33分钟**，包括受控暂停、初始化以及SIGTERM后的等待/恢复。Canonical 128步记录的迭代耗时约 **7小时46分钟**，其中native `timing_s/step`合计26,985秒，另有记录在步上的validation约974秒；这两个口径都不等于纯GPU计算时间。排除step1及保存/验证步骤后的111步，中位耗时约 **163.3秒**、平均约 **177.7秒**。与8B比较时会使用相同计数口径，避免把中断时间算作模型规模开销。

每8步保存完整native状态，单份最终断点约366.3GiB；新旧两份交替提交，fsync完成及独立pointer提交后才轮换。推理用的最终BF16 HF约61GiB，另存于lab数据盘。固定9个完整tensor、251,673,600个元素的CPU审计发现 **29.31%** 的BF16值相对base变化，全部有限。这证明该抽样范围存在可表示的参数更新，不是所有32B参数的变化比例，也不是能力指标。

来源：完整实时终态（原始文件：`results/large-durable-live-status.json`）、[训练诊断图](figures/final-training/rl_memagent_32b_128_durable_training.png)、最终权重精度审计（原始文件：`results/large-memagent/durable-step128-export-weight-deltas.json`）、HF配置/tokenizer等价检查（原始文件：`results/large-memagent/durable-step128-native-hf-export-semantics-cpu.json`）。

## 5. 另外完成的30B级案例

这些案例使用训练前的Qwen3-Coder-30B-A3B-Instruct，是工具链/代码修复实验，与上面的32B MemAgent RL收益分开。

| 固定诊断集 / harness | 修复通过resolved | 正常结束finished | 两者交集 |
| --- | ---: | ---: | ---: |
| 原六题 / ReAct | 4/6 | 5/6 | 4/6 |
| 原六题 / Claude Code | 3/6 | 5/6 | 3/6 |
| 原六题 / Mini-SWE | 2/6 | 1/6 | 1/6 |
| 独立扩展29题 / ReAct | 8/29 | 28/29 | 8/29 |
| Terminal-Bench三题 / ReAct | 1/3 | 2/3 | 1/3 |

六题baseline0/6、gold6/6；扩展套件先做32题环境控制，按已定环境规则排除3题后形成29题。六题和扩展集预算不同，不能拼成同条件排行榜。这些是本机诊断子集，不是官方完整benchmark分数。

ReAct与Claude的Flask案例修改了现有测试，违反提示约束，已保留标记；剥离测试改动后，各自源码补丁仍通过同一verifier的1个修复测试和59个回归测试。Claude路径使用真实Claude Code CLI与本地Qwen；这些推理轨迹没有执行黑盒策略参数更新。

32B原始模型还完成23个问题×长度的分块记忆case，最长材料接近90万source token。它们只涉及8个底层问题、使用随机采样，表现随长度下降；完成长材料处理不等于具备原生百万token注意力窗口或稳定答对。

细节：[30B案例审计](../05-swe-code-agents/details/coder30b-case-audit.md)、[扩展29题](../05-swe-code-agents/details/swe-expanded-case-study.md)、[Terminal-Bench](../06-terminal-bench/REPORT.md)、[长文失败/记忆案例](../02-memagent-long-context/details/32b-memory-case-study.md)。

<a id="6-当前结论与尚在继续的部分"></a>

## 6. 最终结论与边界

本次已经证明这台ROCm节点可以运行真实32B全参数Agentic RL：任务执行、经验采集、分布式更新、完整保存、恢复及独立回载评测形成了可检查的闭环。在固定external64和稳定辅助协议下，观察到可重复的原生评分提升；同时存在7道退步题及评分口径限制，不能据此宣称通用Agent能力或所有长文记忆任务都提升。

默认后端的GPU1匹配恢复实验、追加的8B128步及两规模完整比较均已完成。本页稳定协议结果原样保留，不用默认后端的较高分数替换。

## 7. 默认ROCM_ATTN后端：GPU1完整匹配补充对照

由于GPU0被其他作业占用，另在HIP1/PCI0000:06:00.0上固定新的恢复profile，重新执行两遍原始base，再执行两遍同一个final128。BI和attention override未设置，实际后端为ROCM_ATTN；其余TP1/BF16/eager16K、greedy、并发8与memory/final预算保持默认配方。同一模型的两遍使用同一个服务进程。重建baseline是由资源可用性触发，没有按得分选择。

| 预定配对 | Base平均LCS | Final128平均LCS | 差值 | 95%问题级paired bootstrap | LCS=1 |
| --- | ---: | ---: | ---: | --- | --- |
| First | 0.43967777 | 0.59486607 | +0.15518830 | [+0.05179688, +0.26091797] | 20/64 → 28/64 |
| Repeat | 0.39017857 | 0.56930465 | +0.17912608 | [+0.05900298, +0.29746686] | 20/64 → 27/64 |

两对均完整64题、无错误。First为18题升分、6题降分、40题同分；repeat为22题升分、7题降分、35题同分。窄格式变化分别贡献+.0234375（3题、净增的15.10%）与+.015625（2题、8.72%），其余仍不能全归为知识收益。

默认后端在本次条件下有明显重复差异：base两遍只有23/64条response相同、50/64个reward相同，均值相差−0.04950；final两遍只有15/64条response相同、53/64个reward相同，均值相差−0.02556。温度0不保证此环境下完整Agent执行逐字重复。两对都保留，各自作配对，不取高分、不合并题数，也不与TRITON_ATTN结果混配。

切换base到final时，第一版恢复controller的端口bind探针报瞬态占用并停止；此时两base已完成、final零题。原failed状态和完整基线保留，独立记录的continuation只执行final两遍与四份分析。之后现场无LISTEN/连接，现象与TIME_WAIT相符，但没有当时的TIME_WAIT快照，不能确证原因。

来源：First（原始文件：`results/large-memagent/comparison-durable-primary-gpu1-recovery-step128/comparison.json`）、Repeat（原始文件：`results/large-memagent/comparison-durable-primary-gpu1-recovery-step128-repeat/comparison.json`）、四轮与格式独立审计（原始文件：`results/large-memagent/primary-gpu1-recovery-independent-results.json`）。独立审计重算四份raw64、四份CSV、两组CI与两份重复性报告，并核对实际PID/后端/参数及未重跑baseline。

架构与下一台机器复现：[离线架构探索](../00-overview/training-explorer.html)、[详细复现手册](../00-overview/SETUP.md)、[HTML阅读版](../00-overview/reproduction.html)、已交付v1复现包（原始文件：`reproduction/uni-agent-lab-kit-20260912.tar.gz`）。
