# 第二次宿主中断后的 32B 长训练恢复方案

2026-09-12 04:30 UTC 开始准备。原 `memagent_32b_128` 日志完成到 step46，最后可用权重是 step32 的 HF 导出；只有 extra/scheduler/RNG 和计数观察，没有 Adam moments 和 native model shards。它不能当作 step46、也不能当作可以无损继续优化器的 step32 训练断点。原目录、配置和证据保留。

## 两个可行选项

| 选项 | 新训练起点与步数 | 解释与边界 |
| --- | --- | --- |
| A：fresh base 128（优先） | 固定 revision Qwen3-32B 原始 BF16 权重，新 FP32 master 和 Adam，重新完整 128 global steps | 原 46 步为独立中断试验；新训练有清晰 base→128 对照。512 个训练问题重新从 seed42 的起点消费。约 6 小时计算，另加 checkpoint I/O 与恢复验收。 |
| B：旧 step32 HF warm start + 新 96/128 | 从已训练 FP32 HF 权重初始化，Adam moments 全新，LR warmup 与 globalstep 从 0 开始 | 必须称为两阶段 warm start，不能把新 96 简称为旧 run 的 128。KL reference 默认也会跟随模型路径变化；若要维持原 base reference，还需显式独立 reference 初始化改动。数据游标/异步队列和 scheduler 必须定义重置或跳过边界，不能直接载旧 extra 假装完整 resume。 |

选择 A 可以保留冻结算法而将本次变更限定在可靠保存/恢复；不会依据中途 monitor 增分调整奖励、LR 或选 final checkpoint。最终独立 external64 仍比较固定 base 与新 step128。

## 完整 checkpoint 与提交边界

固定 verl V1 原生接口本身支持 model、optimizer、extra(scheduler/RNG)、StatefulDataLoader、TransferQueue 全状态。原配置主动省略了 model/optimizer，导致此次无法继续 Adam 历史。新配置每 8 个 globalstep 保存前三项，V1 同时保存后两项；不再每次额外复制 HF 权重。最终可从 native shards 合并导出 HF，用于独立评估。

原生保存时序是 actor → data → TQ → latest tracker → driver callback。FSDP manager 的 `max_actor_ckpt_to_keep=1` 会在新 actor 保存完成后删旧 actor，发生在新 data/TQ 提交之前；因此它不是整个训练断点的事务边界。新配置将 worker retention 设为 null，让 `rl_durable_checkpoint.DurableCheckpointCallback` 在全 run 文件齐备、所有文件和目录 fsync 后，写 checkpoint manifest，并原子替换独立 `committed_checkpoint.json`；之后才轮换本 run 的旧 committed checkpoint。

恢复入口只信任独立 committed 指针，并校验 manifest hash、每文件长度和固定 head/middle/tail 64 KiB 探针。该探针不是完整文件 hash，不宣称能检测所有 bitrot。未完成的新目录不自动删除；旧 committed 断点在新断点提交前一直保留。每次保存前的 driver 空间检查不足时停止，保留旧断点。运行前仍需 root 协调持久卷空间。

32B FP32 model 约 122 GiB，Adam 两个 moments 约 244 GiB；一份完整 checkpoint 约 366 GiB。无 HF 重复导出。保留一份最新断点，但新旧同时存在的保存峰值约 732 GiB，按每份 370 GiB 加 128 GiB 余量设置 868 GiB 启动门槛。不能用只有一份大小的剩余容量声称支持安全轮换。

root 已协调独立根盘路径 `/path/to/uni-agent-checkpoints/memagent_32b_128_durable`（启动前根盘约 1315 GiB 可用），新容器 `ua-lab-rl-durable` 以同绝对路径 bind mount；run 下的 `checkpoints` 为绝对 symlink，原数据、日志、源码仍在 data2。未迁移或删除任何旧实验 checkpoint。实际磁盘检查针对 symlink 解析后的 checkpoint 卷。GPU 仍为 HIP2–7 的 4+2 布局。

各 attempt 独立保存配置、状态与 TensorBoard。若真实崩溃后已有大于 committed step 的 rollout/validation/agent 日志，resume 会带 SHA256 归档并记录回滚边界，避免静默覆盖；后续未提交 checkpoint 目录原子改名隔离，保留文件长度与固定探针。若崩溃恰发生在 commit 与当步 logging 之间，明确记录已提交但缺日志的步数，不补造轨迹。原始完整日志与各 attempt 的保留 step 上界一起用于最终统计。

## 真实 resume 验收

预定新 run 的 step8 保存完成后，等待该步正常 rollout/metrics 日志与异步 dump 完整落盘，再主动退出。保持总目标和 scheduler 设置从开始就是 128；暂停只用于恢复验收，不把第一段伪装为 8 步独立学习协议。

显式 `--resume` 在同一 run 下新建 attempt YAML/JSON，从 committed step8 载入 native model/optimizer/extra/data/TQ。四个 rank 在实际 load 返回后，比较所有 Adam state 的 step 计数、scheduler/LR、RNG，以及固定 9 个 tensor 的模型值和 Adam 一、二阶 moments 各 1024 个均匀位置探针。任何差异导致恢复失败；随后必须真正完成 step9，并在后续保存中看到 optimizer 计数继续增长。日志保留实际 `RESUME_STATE_VERIFIED`，不是仅检查文件存在。

恢复时关闭额外 `val_before_train`，保留首次 step0 结果；预定 monitor 仍在 32/64/96/128。原生 TQ 恢复会保留完成轨迹、重发 pending/running prompts。因此本方案称为完整优化器/数据恢复，不承诺异步采样轨迹逐 bit 重现。

代码：`scripts/rl_durable_checkpoint.py`、`scripts/rl_durable_rank_audit.py`、`scripts/rl_durable_launch.py`，配置 `configs/rl_memagent_32b_128_durable.yaml`。入口为 `bash scripts/rl_durable_reproduce.sh fresh`，在受控暂停后执行 `bash scripts/rl_durable_reproduce.sh resume`。普通复现使用新 `RL_RUN_NAME`，入口拒绝覆盖旧 run。最终已提交断点可通过 `scripts/rl_export_committed.py` 调用固定 verl 官方 FSDP merger 导出 BF16 HF；该 merger 显式 cast，预计导出约 61 GiB，原生 FP32 和 Adam 完整断点仍保留。

## 启动和验收记录

04:40:53 UTC 新 fresh-base run 正式建立，04:41:00 Ray 启动，04:45:15 actor/reference FSDP 初始化完成。初始化一度停留在大张量 broadcast，随后 native stack 已推进至 CPU copy，最终正常完成；没有将等待误记为故障或训练步。使用原 pinned ROCm torch2.12/HIP7.2镜像及 118 项锁定 overlay，实际 checkpoint 路径与三个 native save/load contents 从 resolved config核验。配置预检通过；6项文件系统故障注入（缺TQ payload、同长度探针区损坏、提交后rotation、rollback日志归档、受控pause/drain等）通过，见 `results/large-memagent/durable-filesystem-selfcheck-v2.json`。这些 CPU 检查不代替实际 GPU optimizer resume 验收。

05:20:09 UTC，第8步全状态 commit 完成，实际 393,217,863,703 bytes（366.2127 GiB）。四 rank 各 707 个已初始化 Adam state，计数均为 56，scheduler 均为 8。原生保存与全文件 fsync 总计 285.806 秒；模型/optimizer 的 `torch.save` 写入约 63 秒，因此只看写文件返回会低估真正持久化成本。05:20:15按计划退出75，rollout与TensorBoard的1–8步都已正常记录。独立11项数据边界检查、12项commit检查通过：StatefulDataLoader已yield36题，其中32题消费、4题完成预取，恢复后的next4 source indexes一致。已消耗128个session、856条真实context和40条padding，32组中12组reward不均匀；step4/8当前梯度仅KL，step1 LR=0。

在这个受控暂停边界，root批准增加导出读锁与精确source guard。旧callback/launcher源码保存在`notes/research/`，旧→新hash、范围和原因保存在`notes/rl-durable-maintenance-amendments.json`；未更改训练配置或原生算法。导出持共享源读锁，checkpoint rotation删除前取得独占锁，避免异步导出被keep1删除源文件；极端情况下保存过程等待导出结束，仍保留新旧两份直到安全删除。跨进程锁测试、原6项文件系统故障注入和算法/LR不变量拒绝测试均通过。05:28启动attempt002，真实GPU恢复和step9继续仍待记录。

当前宿主的预登记主 external64 为 `results/large-memagent/base-durable`：LCS0.4362723214，LCS=1为19/64。另一次相同配置、相同权重重复为0.3978713396、20/64，实测约0.03840的LCS波动；主baseline不替换，最终128步也将做预登记repeat诊断。因此不能将单次小幅raw-LCS变化自动归因为RL收益。最终128步与主external64、repeat结果仍待完整运行后填写。

05:33:40，四rank实际native load后的完整Adam计数、scheduler、9组模型/一二阶moments固定探针以及RNG fingerprints均与step8保存值一致，TQ恢复4组。05:39第9步完成，梯度0.3112086、PG=-0.0210807、adv范围[-0.41667,0.58333]，LR为1e-6。独立17项恢复审核还确认，第9步恰好消费保存时4组finished预取UID及相应问题，没有重复上一训练批次。第16步保存时Adam计数进一步达到112，scheduler16，确认恢复后继续推进。证据见 `results/large-memagent/durable-step8-resume-step9-independent-audit.json` 和run下 `resume_audits/attempt-002/`。step8的BF16 HF也已独立导出，707tensor/32,762,123,264参数，约61GiB；它仅是导出/回载功能验收，不参与external选择。

恢复后的old-log-prob曾出现间歇性变慢。步骤2–8（包含慢step5，排除保存时间）的计算均值165.68秒，步骤10–15约219.30秒，主要来自oldLP均值36.55→85.59秒；actor约93.03→97.12、ref29.33→31.95秒，总token446.5k→451.8k。worker内部getting→writing时间也增长，因此不是只有driver传输等待。只读现有worker确认FP32 contiguous master shards、BF16 autocast和FSDP mixed precision、SDPA、offload=False与冻结配置一致；这些元数据不等于捕获每个内部算子的dtype。非阻塞locals快照还观察到forward_only=True、no_grad及temperature_is_one=True。

这不是“保存24步修好”的受控结论：step19已恢复oldLP33.11秒，20又升至119.30，22/23恢复约30–32，28又出现71.08，29回到29.93。系统只读采样中，快step19四GPU大体同步繁忙；慢step20部分卡出现较多等待，其他卡仍高busy但功率较低，不能仅凭频率/功率确定CPU、通信或allocator根因。两次native栈位于 `torch.nested.narrow` 的GPU scalar同步边界，可能在等更早提交的GPU工作，不能断言narrow本身是瓶颈。PyTorch TunableOp实际未启用且缓存记录数0，不采用在线TunableOp调优的猜测。详细数据在 `durable-resume-performance-step15.json`、`durable-performance-sysfs-phase-summary-v2.json` 和run的 `diagnostics/`。

06:40对step25的10秒rocprof attach尝试在能力检查阶段即失败：目标进程启动时没有 `ROCP_TOOL_ATTACH=1`，不存在 `rocp-bg-attach` 线程；未进入采样窗口，未获得GPU kernel trace，也未改训练。原始失败日志保留。没有为性能排查重启主线或修改任何数学、power或perf设置；继续完成预定128步，ETA按实际快慢步范围更新。最初基于165秒/步推算的12:33 UTC只是当时估计，不是完成承诺。

## 第三次中断与64步记录

attempt002在07:20:34收到SIGTERM，已记录到34步，最后完整commit32。此次boot_id和driver未改变、容器仍在运行，不能称为新的宿主reboot。10:26:43以独立session的宿主控制进程从32恢复attempt003；旧33/34及相关agent日志带SHA归档，旧TensorBoard后缀按retained边界排除。原始32monitor不重跑。四rank实际load32再次匹配Adam224/scheduler32和保存的state probes，新的33消费恰好是commit32缓存的4组，独立16项审核通过。之后control进程保持PPID1、DEVNULL输入、文件stdout，训练不依赖旧交互exec session。

12:32:46完整commit64保存393,278,089,980 bytes，四rank各707 Adam states均448，scheduler64。monitor完成并继续65。64阶段冻结文件为run下 `analysis_snapshots/step_64.json`、`step_64_validation.json`：累计256训练prompt组、1024sessions、6932条真实context、236条padding，87/256组reward非均匀。52个globalstep有task reward/adv信号；4/8/16/19/24/29/30/35/37/43/59/62共12步当前梯度只有KL，另记首步LR=0边界。

| 固定monitor64 | 平均官方token-LCS | LCS=1题数 |
| --- | ---: | ---: |
| step0 | 0.4944568452 | 19/64 |
| step32 | 0.5874627976 | 29/64 |
| step64 | 0.5584535256 | 26/64 |

step64相对base逐题14提高、9下降、41不变。独立审核确认0/32/64输入chunks逐题一致。base→64原始净增0.06399668，其中7项窄格式变化净贡献0.05989583，占93.59%；其余16项变化净贡献0.00410085。32→64回落0.02900927，格式项净+0.0052083，其余15项净-0.0342176。不能把这些剩余贡献直接命名为能力增量，也不能把原始LCS提升解释成稳定问答提升。详细64配对及23/16项变化见 `results/large-memagent/durable-step64-monitor-independent/` 和 `notes/durable-step64-monitor-brief.md`。继续预定final128，不选择中间step32。

root的detached最终controller现在独占64/96/128 HF导出、CPU有效权重审计及最终analysis.json生产与评估；本训练监控只写阶段snapshot，不与它竞争writer。

## 96步记录

14:22:08完整commit96，393,276,696,713 bytes，四rank各707 states均Adam672/scheduler96。随后固定monitor64完成并继续训练。累计384训练prompt组、1536sessions、10,388真实contexts、364padding；137/384组reward非均匀，81个globalstep有task信号。15个zero-advantage/KL-only当前梯度步骤为4/8/16/19/24/29/30/35/37/43/59/62/69/70/93，仍需单独记首步LR=0。

96 monitor仍是64题/451contexts：平均官方LCS0.5984375，LCS=1为28/64；相对base16提高、5下降、43不变，base原先19个满分题全部保留。相对64，LCS满分题丢4增6。独立审核确认源chunks/奖励重算与阶段snapshot一致。

base→96原始净差0.10398065，其中7项窄格式变化净贡献0.0703125（67.62%），其余14项变化净贡献0.03366815；64→96原始净差0.03998397，格式中的年份区间呈现贡献0.0104167，其余18项净贡献0.0295673。这些是事后解释，不将剩余值直接命名为能力增量，也不替换官方reward或最终128选择。原始配对在 `analysis_snapshots/step_96_validation.json`，实际训练分析在 `step_96.json`，独立审阅材料由audit_recovery_v2记录。自动controller已保留96 BF16导出及CPU审计。


## Native128 training complete — 2026-09-12 16:14 UTC

The canonical durable run completed normally at 2026-09-12T16:13:58.520213+00:00. Whole native128 committed at 2026-09-12T16:09:04.776869+00:00 with 393278449602 bytes. All four ranks have 707 initialized Adam states with actual counter896 and scheduler128. The counter is one distributed update count, not 896×4.

Canonical consumed rollout files1..128 contain512 prompt groups,2048 sessions,13872 real contexts and464 synthetic padding rows.179/512 groups have nonuniform rewards;108 globalsteps have task-reward GRPO signal and20 have zero-advantage/KL-only current gradients. Globalstep1 uses LR0. Independent source coverage matched every source row exactly once.

Monitor at step128:64 questions,451 contexts,LCS0.5938400689223058,full-LCS26/64; initial0.49445684523809524,19/64. Paired14 improved,4 worse,46 unchanged. Step96→128 is -0.0045974311. Independent narrow-format audit attributes +0.0677083333 of the observed +0.0993832237 delta to7 format-change items (68.13%); remaining differences are not a proven capability gain. External base/final/repeat evaluation is owned by the root final controller and remains separate.

Artifacts: `analysis_snapshots/step_128.json`, `analysis_snapshots/step_128_validation.json`, complete `validation_curve.csv`, and `results/large-memagent/durable-step128-training-stage-summary.json`. No manual write to final `analysis.json` and no duplicate HF export. All training-agent CPU stage jobs finished.
