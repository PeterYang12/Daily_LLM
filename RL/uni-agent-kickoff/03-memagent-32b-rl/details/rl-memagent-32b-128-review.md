# 32B / 128 global steps 配置与观察 hook 复核

本记录为 2026-09-12 的**静态只读复核**，对象是 `configs/rl_memagent_32b_128.yaml` 与 `scripts/rl_checkpoint_audit.py`。配置尚待 pilot 的实际同步、显存、optimizer 更新及保存验收后冻结；源码分析不能替代这些运行证据。未改训练算法、训练源码或 GPU 服务。

## 训练预算和评测隔离

当前配置 `512 train / train_batch_size=4 / total_epochs=1 / total_training_steps=128` 能达到 128 个 global step。v1 trainer 的循环同时要求 `current_epoch < total_epochs` 和 `global_steps <= total_training_steps`，而 `steps_per_epoch = len(train_dataset) // train_batch_size`。因此若之后把 batch 改为 8，需要至少 2 epochs；只改 `total_training_steps` 不能保证长跑完成。

每题 n=4，总计划消耗 512 个 prompt group、2,048 个真实 session。数据 manifest 中 train 为 116 题含 5 个 context chunk、396 题含 6 个 chunk；若每题恰消费一次且没有漏收，合计为 11,824 次记忆更新、2,048 次最终回答，即 **13,872 条真实 context trajectory**。这些是计划值；最终应从被 trainer 消费的 rollout 按 uid 去重计数，剔除 synthetic padding，不能以排队任务数代替实际消费数。

训练只引用 train.parquet 和 val.parquet；val 为原 dev 0–63 行，在 global step 0/32/64/96/128 监测。external.json 的原 dev 64–127 行不在 trainer 配置中，外部 base/final 使用独立运行入口。最终 checkpoint 事先固定 step 128，不能依据 external 得分选择 step 64 或临时改变协议。

train memory/final 各 1024 token，chunk 5000；训练服务 context 8192，外部服务 16384。两种服务上实际每次请求均采用分块记忆，而非一次喂完整长文本；每次 prompt+generation 仍须检查未被截断。外部 baseline 与 final 服务预算完全相同，dev 和 external 的值应分开画图和解释。

## LR、global step 与实际 Adam counter

`norm_adv_by_std_in_grpo=false`、LR 1e-6、4 个 scheduler step warmup、KL loss coefficient 0.01 均已显式记录。此时 `parameter_sync_step=1`，scheduler 每 global step 前进一次；其内部训练数据已经展开成多条 context trajectory，一次 global step 可以有多次 Adam optimizer 更新。

`get_constant_schedule_with_warmup` 返回 `LambdaLR`，初始化比例为 0。因此首个 global step 内，即使 Adam state 的 step 增加、moments 更新，LR=0 时也不能据此宣称模型参数改变。`engine_workers.train_mini_batch` 仅在最后一个内部 mini-batch 调 scheduler；日志中的 LR 是当轮完成后、下一轮将使用的值。报告要分别列 global steps、Adam counter、nonuniform reward group、有效 PG/梯度和实际 BF16 权重变化；KL-only 梯度也不能冒充任务 reward 带来的有效更新。

## checkpoint hook 的执行位置

`checkpoint_engine.custom_backend_module: rl_checkpoint_audit` 通过 [engine_workers.py](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine_workers.py#L678) 在每个含 actor role 的训练 worker 中导入，`PYTHONPATH` 的训练入口包含 `/lab/scripts`。这不是只在 root driver 或 rollout server 中注册：trainer rank 同样执行该导入。

hook 替换 `FSDPCheckpointManager.save_checkpoint` 的 class method，即使 manager 实例已创建，稍后的 method lookup 仍会命中 wrapper。调用链为 actor worker 的 `save_checkpoint` → actor engine → FSDP checkpoint manager。native manager 保存时持有的 optimizer/scheduler 就是训练 engine 的对象；禁用 optimizer 文件保存不会把这些对象设为 None。

wrapper 先调用 native save **一次**，成功返回后再读取 `optimizer.state` 中的 scalar step，并写每 rank 小 JSON。`__rl_optimizer_audit__` 标志防止重复包装。读取 `.item()` 可引起同步等待，但不写梯度、参数、optimizer state 或 RNG；不复制 Adam moments。native `global_step` 参数与读取出的 Adam counter 是两组独立字段，没有互相冒充。

实际 pilot 应留下 rank 0/1/2/3 四份 `optimizer-step-audit-rank-N.json`：各自 `world_size=4`、`native_checkpoint_save_returned=true`、optimizer class 正确、state_entries_with_step>0、counter 合理且跨 rank 一致。不存在这些文件时，不能仅凭 YAML 中写了 module 就宣称 hook 已执行。若某些参数始终无梯度，optimizer state 可能尚未初始化；新增的 param_group_parameter_entries 和 param_group_parameters_without_initialized_state 可核对初始化覆盖率，“已有 state 的 counters 相同”本身不等于“全部模型参数均被有效更新”。

小 JSON 同时复制到 `run/optimizer_step_audits/global_step_N/`，不会随大型 actor checkpoint rotation 删除。对于实际文件路径 `run/checkpoints/global_step_N/actor/optimizer-step-audit-rank-R.json`，`path.parents[2]` 为 checkpoints、`parents[3]` 为 run，路径推导正确。副本在独立rank文件中写入，没有跨rank写同一文件。

运行中的 Python 进程不会因为磁盘 hook 文件改了而自动热更新。若 pilot 已 import/安装旧 wrapper，首个 checkpoint 之前修改文件仍可能继续执行旧版；以 audit 内 install 时捕获的 hook_source_sha256 判定实际版本。正式新进程会加载冻结的新文件，不能用当前磁盘 hash 代替已运行版本的证据。

## 保存和资源边界

当前经 root 更新的计划在 step **32/64/96/128** 保存 `hf_model + extra`，`max_actor_ckpt_to_keep=2`。原生 checkpoint manager 的 rotation 在下一次保存前清掉更旧的 actor checkpoint，使大型 model snapshot 最多保留两份；正常完成后预计留下 step 96 与 128，早期32/64的optimizer计数通过独立小JSON保留。模型 master 为 FP32，HF export 仍可能约 122 GiB/份，两份约 244 GiB，加元数据与写入峰值预留空间。最终用 safetensors header/实际 bytes 验证 dtype。

这不是精确可恢复 checkpoint：HF export 和 extra 中没有原生 sharded model/Adam moments。额外小 audit JSON 只保存计数证据，不补足训练恢复状态。发生中断只能据实际保存内容决定继续方式，不能宣称完整 optimizer resume。

4 trainer + 4 standalone rollout、TP2 的配置在本机 8 卡范围内；FP32参数/梯度/Adam moments 在4个trainer上约122 GiB/rank，仍需加reference/activations/临时all-gather。8 GiB sync bucket 的两个 NCCL buffer 占16 GiB，rollout IPC还有额外buffer；untied 32B无需旧小模型的单个超大bucket workaround。当前先保持正在验证的单sender路径；只有实际内存/同步证据要求时再调整并记录新配置。pilot能启动不等于首个真实训练更新后KV wake、保存、再次同步都通过。

静态复核未发现阻止当前128-step预算或泄漏external到trainer的配置问题。进入正式长跑前仍需 pilot 验证：至少一个真实非均匀reward更新、热同步与KV恢复、四rank计数、可加载HF导出。正式结束后以完整64题外部配对、有效BF16权重差分和逐step日志给出结论。
