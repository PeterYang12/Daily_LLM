# Qwen3-32B 长步数 MemAgent RL

本页保留首轮`memagent_32b_128`的历史；该轮随后中断。当前主线已改为从原始base重新开始的[durable 128步训练](rl-32b-durable-recovery-plan.md)，见新实时状态（原始文件：`results/large-durable-live-status.json`）和[当前交接点](../../00-overview/RESULTS.md)。旧monitor结果不能接入新run曲线。

首轮采用Qwen3-32B dense、Uni-Agent官方MemAgent和固定pin的verl V1 separate_async，原目标128个trainer global step。文中状态以`run-outcome.json`及实际日志为准；启动或保存文件本身不代表效果提升。

**2026-09-12 第二次宿主中断后的状态：原 `memagent_32b_128` 完成至日志 step46，最后仅有 step32 HF+extra，缺少 optimizer moments，不能继续原 Adam 状态。** 原 128 步计划未完成，前面的中间结果不能标为最终长跑结果。04:40:53 UTC 已从固定原始 Qwen3-32B 启动独立 `memagent_32b_128_durable`，仍以完整 128 个 globalstep 为目标，每 8 步保存真实 model+optimizer+extra+data+TQ，并在第 8 步主动停止/恢复验收。完整设计、两方案比较及持久盘安排见 [可恢复长跑方案](rl-32b-durable-recovery-plan.md)，冻结协议见 `rl-memagent-32b-128-durable-protocol.json`。新 run 的最终状态以其 `run-outcome.json`、实际计数和 resume audits 为准。

**2026-09-12 04:30 UTC 状态修正：旧 run 已中断。** 宿主在03:43 UTC左右停止本lab容器，已保留完整step1–46指标；最新完整HF导出和四rank Adam计数审计在step32。原`run-outcome.json`的`running`是被中断进程留下的过期状态，已备份并根据独立中断证据（原始文件：`results/environment/interruption-second-20260912.json`）改为`interrupted`。没有观测到训练进程的原生exit code；Docker容器exit137不等同于训练返回码，也不能据此认定OOM。HF不含Adam moments，新的恢复方案不能冒充精确optimizer resume。下文step32结果仍有效，但不是128步终点结果。

## 环境恢复与pilot

2026-09-11的首个32B pilot在首次多桶权重同步停滞，尚未进入训练。rank0在首bucket发送前`torch.cuda.synchronize`等待，relay ranks已在最后同步等待，receiver仍等ZMQ metadata。堆栈保留在`runs/rl/memagent_32b_pilot-diagnostics`；该run标记failed，不能算有效训练。

配置显式改为`checkpoint_engine.engine_kwargs.nccl.multi_sender=false`，只让actor rank0加入checkpoint broadcast，其余actor ranks仍参加FSDP参数all-gather。之前的小模型全部权重装在一个桶中，没有覆盖这个多桶路径。

下一次single-sender入口在2026-09-11 18:06 UTC完成环境设置后遇到外部环境中断；恢复后所有lab容器和镜像不在，训练数据、源码和日志仍在。原因未独立确认。该次甚至没有建立训练run目录，不能据此判断单发送端成功或失败。中断观察保留于`rl-external-interruption-20260912.json`。

2026-09-12恢复同一镜像digest，root对8GPU做了实际tiny-tensor计算验证，训练容器重新创建。新pilot为`memagent_32b_pilot_restart`，4 trainer+2 rollout TP2，HIP2–7。01:12 UTC实际日志确认`1 of 4 actor workers send, world_size 3`，707个参数张量完整通过多桶同步，rank0发送约2.99秒。环境恢复与通信配置变化同时存在，因此这不被描述成严格单变量根因实验。未在恢复后的相同环境重做multi_sender=True/False受控A/B；目前能确认的是所选single-sender配置全链路可用，不能把一个flag当作唯一已证明原因。

## 已冻结的正式协议

- 模型：Qwen/Qwen3-32B，revision `9216db5781bf21249d130ec9da846c4624c16137`；FP32 master参数、BF16 forward、FSDP2、SDPA、gradient checkpointing。
- 6卡布局：HIP2–7，4 trainer+2 standalone rollout，一个TP2副本，单发送端RCCL权重传输；hybrid trainer侧rollout用于原生验证/切换。另两卡供独立大模型case使用。KV utilization从pilot的0.40降到0.30，给长跑切换留约25GiB/GPU额外余量。
- 数据：官方train前512题，monitor dev0–63，external dev64–127；问题集合无交集，逐源行及输出文件SHA256保留。所有context保留，每题5–7个5000-token memory块。
- 训练：128 globalsteps，batch4×n4，1 epoch恰覆盖512个prompt/2048个采样session；不将展开context或synthetic padding算成额外问题。
- 算法：原生GRPO、原始boxed答案token-LCS奖励，KL系数0.01，LR1e-6，4个globalstep warmup，`norm_adv_by_std_in_grpo=false`。memory/final各1024token，no-thinking，训练temperature1/top_p0.7。
- 监控：同64题dev在0/32/64/96/128步greedy评估；external只比较固定base与最终128步，不能用它选checkpoint或调参。
- 保存：每32步保存HF与extra，最多2份HF；每份预计约122GiB FP32。原生optimizer moments不保存，因此这些是评估快照，不可称作可精确恢复的Adam训练断点。

独立pilot的forward/backward、checkpoint导出和最后KV wake均通过，入口及自动分析exit0。正式协议已在初始化和首次验证前冻结于`rl-memagent-32b-128-protocol.json`，配置`../configs/rl_memagent_32b_128.yaml`，正式run为`memagent_32b_128`。不会根据中途验证结果修改学习率、奖励或数据。

pilot实际消费16个训练session、104个context和8个padding；4个prompt组中1组reward非均匀，adv范围±0.159722、PG=-0.0035616、KL=0.0001344、clip前grad=0.143626。4个rank各707个Adam state均step7，HF约122.059GiB，未额外保存native model/optimizer shards。抽查251,673,600个参数元素，251,611,035个FP32值改变，转成统一BF16后仍有5,664,852个变化。4题单次post-validation LCS=0.625；pilot没有before-validation，不能给出训练效果差值。

布局选择有实际时间证据：old logprob36.85秒、ref31.44秒、update99.39秒、sync约4.20秒；下一批rollout约72秒，在当前actor结束前约96秒就已就绪。额外TP2副本不改善已观察到的稳态训练瓶颈。正式仍保存每步gen等待、actor时间和staleness，检查后续长输出是否改变这个判断，保持128步目标。pilot首次采样98.09秒、HF保存64.92秒、4题验证35.59秒；预计长跑约6小时加监控验证与保存，实际以日志为准。

异步纠偏的范围以实际resolved config为准：`algorithm.rollout_correction.rollout_is=null`、`rollout_rs=null`、`bypass_mode=false`，`calculate_log_probs=true`。因此当前仍重新计算old log_probs，没有启用额外IS或rejection重加权；`rollout_corr/*`非零只是诊断输出，不能描述成已经实施了完整off-policy correction。staleness是另行记录的实际滞后，本次不修改已冻结配置。

## 实际更新证据

观察模块`../scripts/rl_checkpoint_audit.py`通过上游已有`checkpoint_engine.custom_backend_module`加载。它只在原生checkpoint保存返回后读取optimizer各state的step标量、param group覆盖率和scheduler计数，保存每rank的小JSON；不复制Adam moments，不修改参数、梯度、optimizer或RNG。JSON同时保留hook与原生checkpoint源码SHA256。

同一份小JSON复制到run下`optimizer_step_audits/global_step_N/`，不会随着HF旧checkpoint轮换被删掉。验收时需要确认4个rank均命中、计数一致、state覆盖率合理。实际文件里的import-time源码hash优先于当前磁盘脚本，因为已运行进程不会自动重载修改后的Python模块。

warmup开始时LR为0，Adam计数仍可增长，首globalstep参数可能不变；scheduler日志一般是步末的下一步LR。实际任务学习证据须同时看每prompt组内reward差异、非零advantage/PG、梯度及参数变化。对FP32 master和统一BF16表示分别比较，不能把仅FP32发生微小变化直接当作推理权重改变或能力提高。

正式训练也出现了这个区别的真实例子：第19、22、24步的advantage最小/最大值均为0，PG loss为0，但grad分别约0.01309、0.01260、0.03056，来自非零KL项。这描述的是当步梯度；Adam历史动量仍可能继续影响参数更新。`rl_analyze.py`会单列zero-advantage与KL-only-current-gradient步骤，并统计去掉padding后的非均匀prompt组比例。前26步的独立快照有104组、其中42组非均匀，23步出现非均匀reward和非零adv信号；其中首步仍受LR=0的warmup边界影响。该快照不替代128步最终统计。

## 第32步记录

2026-09-12 03:04 UTC，第32步保存和固定64题验证完成，随后第33步成功继续训练。此时是128步计划的中间点，没有提前结束或选择checkpoint替代最终128步。

| 固定monitor dev64 | 原始token-LCS均值 | LCS=1题数 |
| --- | ---: | ---: |
| step0 | 0.5166294643 | 21/64 |
| step32 | 0.6012784091 | 28/64 |

逐题14题提高、5题下降、45题不变，LCS均值增加0.0846489448。两次均64个session/451个context，全部重新按final context复算且广播一致。这是中途监控集的正向结果；外部dev64–127尚未做最终对照，不能据此宣布稳定泛化提升。

进一步逐条审阅发现，至少5项提升和1项下降只是同一答案的LaTeX空格或数字逗号呈现差异；这六项净贡献+0.03645833 LCS，占已观察到总净增分的43.1%。因此原始LCS的全部增量不能当作知识能力提高。也能看到最终答案选择的真实变化与回退，以及最终答对但memory仍含可疑关联的情况。完整19项和四个具体例子见[事后定性审阅](rl-32b-dev32-qualitative.md)，没有据此改奖励、训练配置或选择最终checkpoint。

32步实际消费128个prompt、512个session、3444条真实context，以及140条padding；128组中50组reward非均匀（39.0625%）。第19、22、24、29步为zero-advantage/zero-PG而非零KL梯度；其余28步有非均匀reward和非零adv信号，仍须记住首步LR=0。原生token/logprob数组全部对齐、数值有限。边界固定的分析快照为`../runs/rl/memagent_32b_128/analysis_snapshots/step_32.json`，不会混入后来完成的第33步。

4个训练rank各707个state的Adam计数均为224，scheduler last_epoch32，未初始化参数state数0；hook的import-time hash与冻结协议一致，4份计数记录也已复制到checkpoint轮换范围之外。HF约122.059GiB，只有HF与小型extra/audit文件；原生optimizer moments与model shards没有重复保存。CPU权重精度审计由独立脚本完成，见`../results/large-memagent/step32-effective-weight-deltas.json`：固定9tensor中BF16表示仍有34,038,531个元素变化（13.5249%），这些是表示变化证据，不是效果分数。

排除首批等待与第32步保存/验证后，step2–31每步平均161.04秒（152.24–174.92），等待采样平均0.02369秒，actor update平均94.81秒，权重同步平均3.90秒，staleness为1。第一段完整长跑支持保留6卡布局的吞吐判断。所有512/64/64源行也已核对为各自互不重复的问题，分区间问题集合无交集。

## 复现入口

在模型和数据均已下载、固定镜像已存在的环境：

```bash
RL_RUN_NAME=memagent_32b_pilot_restart_new bash scripts/rl_reproduce.sh memagent_32b_pilot_restart
RL_RUN_NAME=memagent_32b_128_new bash scripts/rl_reproduce.sh memagent_32b_128
```

入口拒绝覆盖旧run和并发trainer；`RL_GPU_IDS`可显式指定已协调的物理HIP GPU列表。当前长跑需要6卡，不能与占用这些卡的推理服务同时启动。日志、原生trajectories/NPZ token masks、rollout和validation JSONL、TensorBoard及分析文件都保留在新run目录。
