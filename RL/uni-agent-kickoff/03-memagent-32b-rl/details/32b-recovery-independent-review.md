# 32B 恢复与磁盘的独立审计

2026-09-12 的第二次环境重建后，独立审计确认：旧 `memagent_32b_128` 实际完成 46 个日志步，最后保留的模型快照是 step32。该快照有 FP32 HF 权重、scheduler/RNG extra、dataloader 和 TransferQueue，但没有 Adam moments 或原生 model shards。它可以做 step32 推理评估，不能恢复成原来 Adam 状态下的第33步，更不能把后续新跑的步数加到46后称为一次连续128步实验。

本审计只读取现有 checkpoint、源码、配置与文件系统容量；未删除、搬迁、压缩任何旧文件，未构造模型或执行 GPU 计算。机器可读记录见 recovery-independent-audit-v2.json（原始文件：`results/large-memagent/recovery-independent-audit-v2.json`）。新训练启动、保存及恢复实现由训练负责人维护；下面对尚未实测的部分明确保留验收条件。

**HF 配置与 tokenizer 的前次审计已经完成。** 两次独立 CPU 检查分别在 CPU client 和当时 active serving image 的 Python 运行时执行，均为 Transformers 5.16.1、tokenizers 0.23.2、同一 torch/vLLM 版本。两份报告 `required_checks_passed=True`，均未初始化 CUDA/HIP。本次重新计算报告本身、审计脚本，以及 base/step32 五个 config/tokenizer 输入文件的 SHA256，全部仍符合原记录，因此没有重复执行同一项检查。

64 个固定 external 样本共 1,798,330 个 source tokens、388 个 chunks；base 和 step32 的原始 token IDs、解码后的 chunks、chunk 再编码、MemAgent chat prompt IDs、final prompt IDs 全部一致。完整规范化 tokenizer backend、词表151,669项、merges151,387项、26个 added tokens、有效 chat template 与 generation behavior 也一致。这里使用固定 memory 字符串检查 tokenization；没有向模型提供答案或做 checkpoint 能力评测。

raw 文件并非逐字相同：`rope_theta` 迁移到 `rope_parameters`、`torch_dtype` 迁移到 `dtype`、显式列出 full-attention layers；tokenizer JSON 的 ByteLevel 字段及 tokenizer_config 存储形式也变化。在实际 AutoConfig/AutoTokenizer 5.16.1 下，架构和 tokenizer 归一化后相同。模型 config 的 BOS/PAD 位置及 HF attention selector 差异有单独记录：BOS 在相同 generation_config 中保留，PAD 与相同 tokenizer/generation settings 一致；HF `attn_implementation=sdpa` 不控制 native-vLLM attention backend。该审计不能推广到直接消费 raw tokenizer.json 的工具、其他 Transformers 版本或任意 HF 推理路径，也不证明 logits、热更新权重或能力相同。

原始完整证据为 CPU 报告（原始文件：`results/large-memagent/hf-export-semantics-step32-cpu.json`）、serving runtime 报告（原始文件：`results/large-memagent/hf-export-semantics-step32-serving.json`） 与交叉运行时比对（原始文件：`results/large-memagent/hf-export-semantics-step32.json`）。

**优先使用用户自己的另一块文件系统保存新的完整 checkpoint。** 容量快照时 `/data2` 剩约589 GiB，而 `/` 另有约1,315 GiB可用。数字会随其他进程变化，不能替代启动和每次保存前的容量检查。Qwen3-32B 有32,762,123,264参数，原生完整 Adam checkpoint 的最低张量 payload 为：

| 内容 | 理论 GiB | 恢复作用 |
| --- | ---: | --- |
| FP32 master model | 122.05 | 恢复训练参数，包括 BF16 会舍去的小变化 |
| FP32 Adam 一阶、二阶 moments | 244.10 | 恢复历史优化器动量 |
| model + moments | 366.15 | 还须加上 scheduler/RNG、data、TQ、metadata |
| 新旧完整 checkpoint 同时存在 | 732.31 | 新 checkpoint 提交成功前保留旧 checkpoint |
| 另行 FP32 HF 导出 | 122.05 | 用于通用推理工具，不是必要的重复原生存储 |
| 另行 BF16 HF 导出 | 61.03 | 推理表示，不能取代 FP32 训练状态 |

本次实际旧 checkpoints 文件逻辑大小：32B pilot122.111 GiB、旧32B step32为122.114 GiB、小模型全部 checkpoint284.534 GiB；小模型包含重复的 native model、HF model和Adam。这些是现存证据，不建议为迁就当前 `/data2` 空间先删除唯一权重。新 run 可放在本人 `/path/to/user` 下独立目录，并以相同绝对路径 bind 进 Docker，保证宿主和容器都能解析指向该目录的 symlink。必须按 checkpoint 的真实目标文件系统检查空间，而非只检查日志 run 目录。新旧两份原生 checkpoint 加一次额外 FP32 HF 导出约854.36 GiB payload，根盘当前容量可以承受；最终仍应动态预留 metadata、其他写入者和保存临时量。

若将来需要降低长期存储，选择有不同证据代价：

| 选择 | 保留的能力与限制 |
| --- | --- |
| 把完整文件复制或无损压缩到另一本人目录 | 校验完整原文件与恢复文件 SHA256 后可保留精确值；压缩率必须实测，且恢复需要解压空间 |
| 为推理另存 BF16 HF | 可复现同 BF16 服务表示，但会丢掉 FP32 master 低位，也没有 moments；不可称训练断点 |
| 退休 pilot 的唯一 HF 或小模型 optimizer | 可保留日志、采样张量和结论证据，但失去完整 checkpoint 重评或原样恢复能力；不能把小型审计 JSON 当作全部权重备份 |
| 自定义 base+delta 存储 | 只有固定 base revision、逐文件完整 hash 和恢复验证齐备时才是无损；引入新的恢复实现，本次没有必要采用 |

旧32B step32仍需外部固定集评估，应继续保留。完整 checkpoint 轮换也应只作用于新 run 中已经提交的旧 checkpoint；不同实验的证据不是轮换对象。

**原生恢复的真实边界。** 固定 verl V1 源码会加载 rank 对应的 model、optimizer、extra，再加载 `data.pt` 和 TQ。目录名决定已完成 `global_steps`；`fit()` 加1后从下一训练步开始，不应人为把新初始化优化器计数改成旧数。StatefulDataLoader 保存自己的 sampler 状态；async 预取已从 dataloader 取走但未训练的题目由 TQ 补齐。已经 finished 的预取组原样保留，pending/running 组会删除已知的旧 partial trajectories 并重新生成，因此不承诺逐 token、逐调度顺序或 bitwise 复现未中断运行。

本实验 batch4、n4、单轮512题；保存第8步时应检查已经消费32个题目、128个session，并核对 dataloader 已取出的题目与 TQ 内尚未消费的题目组成完整边界。不要把为了预取读取的题目计作已训练题目。replay buffer 在 materialize 时已删除当前已消费 prompt keys，但对应轨迹要在稍后的日志写出之后才清除；checkpoint 中的这种无 prompt 轨迹不能再次当作新样本消费。

原生保存顺序是 actor model/optimizer/extra → data → TQ → native latest tracker → callback，之后才执行当步权重同步、验证、rollout JSONL和metrics。`max_actor_ckpt_to_keep=1` 虽在新 actor 保存完成后才删旧 actor，却仍早于新 data/TQ 保存完毕，因此不能单独提供全 run 的可靠轮换。当前拟议实现将 worker retention 设为null，以 driver callback 验证完整文件、fsync、提交独立 atomic pointer后，再删除本run的旧已提交checkpoint。恢复入口应只信任独立提交记录和 manifest，而非原生直接写出的 latest 文本。

独立审阅已提出的验收条件：保存前按真实 checkpoint 盘检查足够空间；TQ必须确认 `storage_saved=true`、step一致和完整 storage units；四个rank的model与Adam两moments固定数值探针、707个state覆盖率、真实step、LR/scheduler及RNG要在load后匹配save；新run从base开始，老46步记录继续保持独立。探针和文件头/中/尾校验只覆盖抽样位置，不能宣称全部文件逐位相同。

第8步受控停止应在当步正常logger之后，显式等候rollout异步写出，再保存pause标记。真实外部崩溃还必须处理两个额外边界：若checkpoint已提交而该步JSONL尚未写出，应明确恢复证据或标记日志缺口；若commit8后实际跑到14才中断，resume8后会重做9–14，旧分支日志必须归档并记录回滚，不能被同名JSONL静默覆盖，也不能算成一次连续的额外训练步。

启动前补充审阅已完成：新的 `rl_durable_checkpoint.py`、`rl_durable_rank_audit.py`、`rl_durable_launch.py` 已增加真实 checkpoint 目标盘检查、TQ storage/step/unit完整性、resume配置语义比对、大于committed step的日志SHA256归档、后续未完成checkpoint隔离以及缺日志步数记录。实际采用 keep_committed=1、新旧同时保存的约732GiB峰值，根盘至少预留128GiB；这避免让新保存依赖删除旧实验文件。没有发现首次save/load的明确静态阻断，但只有实际完整保存、重新启动、四rank恢复验收及第9步继续更新完成之后，报告才能将其称为已验证可恢复的128步路线。

独立的 CPU async checkpoint边界工具（原始文件：`scripts/audit_async_checkpoint_boundary.py`） 已在旧step32快照上实测，见 11项检查结果（原始文件：`results/large-memagent/step32-async-checkpoint-boundary-audit.json`）。`data.pt`保存的实际游标为132，精确分为128个已消费问题与4个finished预取prompt；TQ这4题的source index与固定seed42 sampler第128–131位置一致。当前刚训练的4题轨迹仍在TQ但prompt已删除，其source index与sampler第124–127位置一致，padding单独排除。将真实data.pt加载到整数索引StatefulDataLoader后，下一4个数据集row位置为308、15、420、266，与从头生成的sampler第132–135位置相同。该检查只构造CPU索引dataloader，所有输入SHA256前后未变，未加载模型或optimizer权重，也没有启动live TQ。新step8快照可复用此工具验收；这个有意限定到首epoch结束前的工具会拒绝final epoch-wrap，避免把prefetch跨epoch误判成漏题。

新实验的独立评估继续固定external64、原始base tokenizer、5000-token chunk、memory/final各1024、greedy/no-thinking，以及TP1 BF16 eager/16384服务。新run从base开始，计划final固定为step128；旧run的base-recovery与旧step32必须作为中断实验独立列出，不得接到新run学习曲线上。最终native shards需导出可加载HF；如果保存在根盘checkpoint volume，当前`start_32b_checkpoint.py`的lab根目录约束会拒绝它。可将最终HF导出到lab内明确目录，或在启动器和推理容器中显式允许/挂载本人checkpoint volume；无论选择哪一条，实际final导出后都须再次核对config/tokenizer等价、输入与服务runtime provenance。旧step32的tokenizer审计不能直接替新导出背书。

**新step8保存及数据边界已独立验收。** 2026-09-12 05:20 UTC训练按协议暂停后，使用专用RL容器隐藏全部GPU执行CPU边界脚本，11项检查全部通过；再用宿主纯标准库读取manifest和固定文件探针，12项检查全部通过。32个checkpoint文件合计393,217,863,703 bytes（366.2127GiB），清单精确覆盖文件集合、每文件长度与固定head/middle/tail探针，独立committed指针中的manifest SHA256也一致。没有解码模型或optimizer文件，没有重写checkpoint输入。

新step8 `data.pt`实际yield36题，其中32题已消费、4题finished预取；当前刚训练的4题以及下一批4题的source index都匹配seed42 sampler相应位置。恢复真实data.pt到CPU整数索引dataloader后，下4个row位置也匹配从头生成的sampler。四rank保存观察各707个Adam state，全部step56、scheduler epoch8，9组模型/一阶moments/二阶moments探针均齐全且有限，CPU/NumPy/Python/CUDA四个RNG域均有记录。step8 rollout JSONL与planned-pause标记已经写出。证据为 step8边界检查（原始文件：`results/large-memagent/durable-step8-async-checkpoint-boundary-audit.json`） 和 step8独立提交检查（原始文件：`results/large-memagent/durable-step8-independent-commit-audit.json`）。这证明保存与数据边界通过；实际进程重新加载后的数值匹配及第9步优化仍由后续恢复记录验收。

**实际恢复及step9继续更新也已独立验收。** `attempt-002`四个rank在05:33:39–40完成原生load，每份保存/加载观察中的state字段完全相同：707个Adam state计数均56、scheduler8、固定9×1024位置的模型与双moments探针，以及四类RNG指纹都匹配。审计hook没有变化，step8 commit hash保持原样。维护修订仅为checkpoint reader lease、import-time指纹和source guard；已保存原始源码，白名单hash链与运行时source audit一致。两次attempt配置只有resume路径/模式、额外初始验证开关、受控暂停及attempt/TensorBoard标识六项运行控制差异。

随后真实step9消费的4个UID恰为step8存下的finished预取组，source indexes为337、485、662、47，每条输入的question文本也与对应训练源行相同；没有重用step8已消费UID。共16个session、每个7个context，112行UID无重复。原生metrics确认step9 actor update完成106.0038秒，PG−0.0210807、gradient norm0.3112086、advantage区间−0.4166667,0.5833333]、LR1e-6，后续权重同步4.2969秒。这串证据支持实际优化器/数据恢复后继续更新，区别于只检查文件可见；它不表示128步已经完成或能力已提升。新Adam scalar计数的继续增长将在后续checkpoint另行观察，探针匹配也不等于全部参数/optimizer逐bit比较。完整17项检查为 [step8恢复至step9独立审计（原始文件：`results/large-memagent/durable-step8-resume-step9-independent-audit.json`）。

**step32后的第二次实际恢复也已验收。** `attempt-003`从完整commit32恢复，四rank707个Adam state计数224、scheduler32以及记录的模型/moments/RNG与保存记录一致。旧attempt-002已执行但未提交的33/34步及相关agent日志带SHA归档，旧attempt保留上界明确限制到32；新canonical33的metrics来自attempt-003。它只消费commit32保存的四个finished预取UID，source indexes63、564、808、707，合计16个session/112个真实context，无padding；PG−0.00985048、gradient norm0.09127776、非零advantage及102.2063秒actor update均有原生记录。见 16项独立恢复检查（原始文件：`results/large-memagent/durable-step32-resume-step33-independent-audit.json`）。旧分支33/34是被回滚的计算，不与新33/34相加计入连续128步。

这次还定位了CPU边界工具的一项适用范围：step32的TQ中仍保留第一次恢复带入的step8已消费轨迹，它们已经没有prompt key。初版工具把所有无prompt轨迹当作“刚消费的当前步”，因此该单项检查失败；原始输出保留，没有改写成pass。补充审计按已保存的rollout8/32 UID严格分开历史和当前组，确认四组历史残留恰为step8、当前四组严格匹配step32 sampler位置，游标132=已消费128+预取4始终正确。实际新33也没有消费这些历史/当前无prompt组，证明它们未造成此处重复训练。完整说明和通过的补充检查为 resumed TQ边界审计（原始文件：`results/large-memagent/durable-step32-resumed-tq-boundary-audit.json`）。未修改或清理上游TQ行为。
