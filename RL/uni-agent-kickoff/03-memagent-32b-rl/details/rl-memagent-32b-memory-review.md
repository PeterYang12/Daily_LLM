# 32B hybrid KV 恢复的静态内存复核

本记录回应小模型 SWE2 在训练后恢复 hybrid rollout KV cache 时 OOM 对 32B 长跑的影响。只读检查日志、当前 verl 源码和模型配置，未改训练配置、未重启服务。建议由正在运行的独立 pilot 实测后再冻结正式设置。

## 已知失败的位置

`runs/rl/swe_separate_2.log:1254` 已开始保存 `global_step_2`，HF 写盘完成。随后 `on_validate_begin → switch_to_rollout → naive update_weights → rollout.resume(tags=['kv_cache'])` 在 vLLM CuMemAllocator 的 `wake_up` 分配时报错。因此它是 **step2训练/保存后，最终验证切换失败**；只有 step1 有完整 metrics/consumed rollout 文件，不能据缺少 step2 metrics 断言从未训练 step2。

SWE2 使用4B、TP1、32K context、util=.30、24GiB sync bucket；32B当前使用TP2、8K context、util=.40、8GiB bucket。旧日志没有OOM当刻的完整free/mapped/所有进程内存快照，不能直接认定是某一块内存泄漏，也不能按模型参数量线性外推失败。

## .40、.35、.30 代表什么

32B配置：64 layers、8 KV heads、head_dim128、BF16、TP2。每卡每token KV为：

`2(K/V) × 64 × (8/2) × 128 × 2 bytes = 131072 bytes = 128 KiB`

因此16条活动序列，即使每条占满8192token，也只需约16GiB活动KV，不含cache block对齐、prefix缓存和其他实现开销。当前全局session并发16，实际每个replica可能更少。降低vLLM预分配的KV容量不必同时降低16seq或8192 context预算。

按日志设备总量251.98GiB粗算，TP2 BF16模型权重每卡约30.51GiB：

| utilization | vLLM目标预算 | 扣模型后剩余上限 | 相对.40释放预算 |
|---|---:|---:|---:|
| .40 | 100.79GiB | 70.28GiB | 0 |
| .35 | 88.19GiB | 57.68GiB | 12.60GiB |
| .30 | 75.59GiB | 45.08GiB | 25.20GiB |

剩余上限还必须扣activation profile、runtime/non-torch等开销，并不等于实测KV空间。实际cache block/token capacity以vLLM profile为准。如果.30下实际可容纳的token数仍显著超过16×8192，当前并发下吞吐通常不受活动KV容量限制；主要少了prefix缓存容量。不能在实测前保证速度相同。

vLLM的容量是在初始化时决定的。训练前还没有初始化的Adam moments，不会被初次profile计入；训练后再次wake需要与这些新增常驻状态共存。仅通过初始load/generate并不能验证长期切换。

## 避免把不同时刻的峰值相加

4 trainer上的FP32参数约30.51GiB/rank，两个FP32 Adam moments约61.02GiB/rank，梯度在训练峰值再约30.51GiB。原生EngineTrainModeCtx退出时zero_grad，naive hybrid权重更新完成后BucketedWeightSender清理IPC buffer，并在恢复KV前调用aggressive_empty_cache。因此训练梯度峰值、IPC峰值和完整KV不应直接相加后当成“同时占用实测”。

8GiB NCCL bucket的两个常驻buffer合计16GiB仍需要计入。reference使用forward-only offload路径，另有临时all-gather、PyTorch allocator与其他进程开销。当前32B pilot初始FSDP记录actor约30.69GiB allocated，但这发生在Adam初始化之前，也不能据此保证最终wake。

## offload 的取舍

优先建议完成当前pilot的post-update/save/wake；如果需要为正式128step留余量，先校准`.35`或`.30`，保留任务/并发/token预算。它只改变缓存容量，改动范围较小。若仍不能稳定恢复KV，再考虑 `optimizer_offload=true`：

- optimizer offload在global step间可释放约61GiB/rank，代价是每global step每rank约122GiB的CPU↔GPU双向moments传输，4rank合计约488GiB。实际耗时取决于CPU/GPU NUMA和链路，须测量。
- param offload在阶段间释放约30.5GiB/rank，但old-logprob、train和权重导出会多次需要模型。对本工作流，单位释放容量带来的搬运可能比optimizer offload更高。
- 这两个布尔开关是阶段间搬运，Adam计算仍在GPU，不应与FSDP2 `offload_policy: true` 的CPUOffloadPolicy混淆。不要同时改三种机制，避免失去pilot可比性。
- offload不替代BF16有效权重差分和真实nonuniform reward验收；它也不使HF-only checkpoint具有Adam恢复能力。

观察性记录至少应覆盖：初始化后、首次真实Adam更新后、保存后、KV wake后GPU allocated/reserved/device-used，cache capacity、实际同步/验证耗时。只出现“进程还活着”或“初次推理成功”都不足以确认这个转换路径完成。
