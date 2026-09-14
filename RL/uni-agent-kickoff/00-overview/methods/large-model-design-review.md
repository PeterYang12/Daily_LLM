# 32B dense / 30B MoE：ROCm 大模型方案只读审阅

日期：2026-09-11 UTC。本文审阅现有源码、固定模型配置与资源预算，没有加载完整模型、占用 GPU 或修改训练源码。配置可行性不等于实际跑通；实际 pilot、吞吐、显存峰值和效果由后续运行记录确认。

建议主线采用 **Qwen3-32B dense + 官方 MemAgent + 4 个 FSDP2 trainer ranks**，推理使用 TP2。当前 6 卡 pilot 的 4 train + 2 rollout 是一个 TP2 副本；全部 8 卡的 4 train + 4 rollout 是两个 TP2 副本。Qwen3-Coder-30B-A3B-Instruct 先做固定 SWE 评估，再单独验收 MoE RL，不应把推理能够加载直接等同于训练/热更新兼容。

## 固定输入与实际代码

| 项目 | 固定值 |
| --- | --- |
| Uni-Agent / verl | `472c875a97f9a2764c81a6ec7581167632bd8bcc` / `a9f2985159536a607211dcac730d3f5d55028950` |
| 实际训练环境 | torch `2.12.0+git6bbd260`、Transformers `5.9.0`、vLLM `0.28.1rc1.dev516+g9ea8f3ffc.rocm723` |
| Qwen3-32B | revision `9216db5781bf21249d130ec9da846c4624c16137`；64 layers、hidden 5120、Q heads 64 / KV heads 8、head_dim 128、untied embeddings |
| Qwen3-Coder-30B-A3B-Instruct | revision `b2cff646eb4bb1d68355c01b18ae02e7cf42d120`；48 layers、hidden 2048、Q heads 32 / KV heads 4、128 experts / top 8、expert intermediate 768、untied embeddings |

保存的模型配置：dense（原始文件：`notes/research/Qwen3-32B-config.json`）、MoE（原始文件：`notes/research/Qwen3-Coder-30B-A3B-Instruct-config.json`）。实际安装版本的纯源码快照：HF Qwen3MoE（原始文件：`notes/research/hf-qwen3-moe-5.9.py`）、vLLM Qwen3MoE（原始文件：`notes/research/vllm-qwen3-moe-0.28.py`）、vLLM RoutedExperts（原始文件：`notes/research/vllm-routed_experts.py`）。这些是审阅材料，不能替代模型权重下载清单或 GPU 验证。

## 显存预算：MoE 激活参数不等于训练状态参数

按配置中的无 bias attention/MLP、两份 untied embedding/head 和 norm/router 参数计算，dense 约 32.762B 参数，Coder MoE 约 30.532B。以下使用 GiB（2³⁰ bytes），是权重/状态数值预算，不是实测显存峰值。

| 内存项 | Qwen3-32B | Coder-30B-A3B |
| --- | ---: | ---: |
| 全模型 BF16 参数 | 61.02 GiB | 56.87 GiB |
| 全模型 FP32 参数 | 122.05 GiB | 113.74 GiB |
| 4-rank FSDP：每 rank 的 FP32 参数 + FP32 梯度 + 两份 Adam moments | 122.05 GiB | 113.74 GiB |
| 同样状态仅用 2-rank FSDP | 244.10 GiB/rank | 227.48 GiB/rank |
| TP2 推理：每 rank 约 BF16 权重 | 30.51 GiB | 28.44 GiB |
| 最大 FP32 embedding/head 单 tensor | 2.898 GiB | 1.159 GiB |
| HF packed gate_up 单层 FP32 tensor | — | 1.500 GiB |

当前 [engine](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine/fsdp/transformer_impl.py#L253) 将训练参数保持 FP32，并以 BF16 forward、FP32 reduce 做 mixed precision。4-rank 预算采用 16 bytes/parameter 总状态再除以 4；未计激活、all-gather、temporary gradients、logits、通信缓存、allocator reserve。2-rank dense 的状态预算已经接近单卡约 252 GiB，可用余量不足，应从 4 trainer ranks 起步。

MoE 每 token 只选 top 8 专家，会减少参与计算的专家量，但 HF/FSDP2 仍存储并分片全部约 30.5B 参数及对应 optimizer 状态。当前实现不是只为约 3B active parameters 分配显存。它也不是 expert parallel：FSDP2 按 decoder layer 包装、按参数分片，在层 forward 前 all-gather 该层参数；不能期待自动获得 Megatron EP 的 token all-to-all 性能。

reference policy 的 FSDP2 forward-only 路径强制采用 CPUOffloadPolicy，仍有 CPU 状态、搬运和当前层临时 GPU 占用。节点约 3 TiB 主存能提供空间，但 offload 带宽可能成为瓶颈。两种模型 `tie_word_embeddings=false`，现有初始化路径可让非零 rank 使用 meta，rank0 从 CPU 加载，再分片广播；[FSDP2 wrap 选择](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/utils/fsdp_utils.py#L551) 也会分别包装 embedding 与 lm_head，避免它们留在一个大的 root unit。

长上下文不能只按 KV cache 规划。非 fused 训练会生成 full-vocab logits，8K×151936 的 BF16 logits 已约 2.32 GiB，转 FP32 约 4.64 GiB，entropy/logprob/backward 还会额外占用。SWE 评估可使用更长窗口，但首次训练应保持已验证的约 8K 总上下文、microbatch=1、gradient checkpointing；不要直接把 64K 推理窗口照搬进非 fused 全参数训练。

## TP2、ROCm 与通信临时显存

两种模型的 Q/KV head 数均可被 2 整除，适合 TP2。建议保留 `enforce_eager=true`、禁用 torch compile、`use_remove_padding=false`、HF attention `sdpa`，先复用现有可靠路径。这里 SDPA 仍可能选择底层优化 kernel；“非 fused verl forward”不意味着完全不用 GPU kernel。

vLLM 的自定义 all-reduce 开关应放在实际传入 EngineArgs 的路径：

```yaml
actor_rollout_ref:
  rollout:
    tensor_model_parallel_size: 2
    expert_parallel_size: 1
    enforce_eager: true
    engine_kwargs:
      vllm:
        disable_custom_all_reduce: true
    checkpoint_engine:
      backend: nccl
      update_weights_bucket_megabytes: 8192
```

[vllm_async_server](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/vllm_async_server.py#L289) 读取 `engine_kwargs.vllm` 并合并到 args。禁用 custom all-reduce 后仍需 ROCm RCCL 的标准 TP collectives，不能理解为禁用通信。保持单一 `HIP_VISIBLE_DEVICES` mask；不要同时再加 ROCR mask。训练和推理要按实际 HIP/PCI 对应分配。

**8 GiB bucket 不是只占 8 GiB。** [NCCLCheckpointEngine](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/checkpoint_engine/nccl_checkpoint_engine.py#L120) 有 send/recv 两个 buffer，约 16 GiB；接收后交给 vLLM 的 [IPC sender](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/bucketed_weight_transfer.py#L90) 还可能叠加一个 8 GiB buffer，以及重组/full tensor 临时存储。应为相关 GPU 的同步阶段保留至少约 24 GiB 量级的传输空间，实际按进程与时序测峰值。

原 4B 配置的 24 GiB bucket 会使双 buffer 本身达到 48 GiB，大模型不宜直接沿用。当前 dense 最大 FP32 tensor 约 2.898 GiB，MoE packed tensor 1.5 GiB，8 GiB 足以容纳；NCCL 路径还会 [split/merge 大 tensor chunks](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/checkpoint_engine/base.py#L560)。本机这条常规同步路径没有统一预先把所有参数降成 BF16，不能用 BF16 模型大小估计所有传输量。

vLLM 的 `gpu_memory_utilization` 仅是其自己的权重/执行/KV 预算，无法为 verl 额外创建的通信 buffer 预留全部空间。此前 SWE pilot 在 `wake_up kv_cache` OOM，应把生成、睡眠、权重同步和 KV 唤醒四个阶段分别看待。对 dense TP2，BF16 KV 每 token、每 rank 约 128 KiB，单条完整 8K KV 约 1 GiB；MoE 相应约 48 KiB/token，8K 约 0.375 GiB。并发序列、prefix cache 和服务可用余量必须一起控制。先用少量并发、保守 KV 利用率完成一次同步和 wake，再依据峰值增加吞吐。

## MoE 不要求先迁移 Megatron，但有两个独立 kernel 层

Transformers 5.9 的 Qwen3MoeExperts（原始文件：`notes/research/hf-qwen3-moe-5.9.py:214`） 将权重存为：

| HF 参数 | Coder30B 实际形状 |
| --- | --- |
| `model.layers.i.mlp.experts.gate_up_proj` | `[128, 1536, 2048]` |
| `model.layers.i.mlp.experts.down_proj` | `[128, 2048, 768]` |
| `model.layers.i.mlp.gate.weight` | `[128, 2048]` |

源码已有普通 `F.linear`、softmax/topk、按专家取 token 和 `index_add_` 的可微 eager 路径，因此训练侧理论上不必安装 Megatron、Transformer Engine 或专用 fused MoE 才能 forward/backward。FSDP2 的标准 decoder-layer 包装也能处理这些 3D parameters。但 eager 会按活跃专家循环，性能可能明显受限，必须以全模型 pilot 的实际步时评估。

**`use_fused_kernels=false` 不会关闭 HF 的 grouped expert dispatch。** Transformers 5.9 默认 `experts_implementation=grouped_mm`，其可用性判断包含 CUDA-style capability 分支；ROCm 的真实 grouped_mm kernel/反向和分片兼容仍需实测。首次兼容性 pilot 可以明确选择 HF eager experts，之后再将 grouped_mm 作为独立性能改动。

本 pin 的 verl config override 是逐项 `setattr`，因此应写：

```yaml
actor_rollout_ref:
  model:
    use_fused_kernels: false
    use_remove_padding: false
    enable_gradient_checkpointing: true
    override_config:
      attn_implementation: sdpa
      _experts_implementation: eager
```

纯 CPU config 检查已验证：只 `setattr(cfg, 'experts_implementation', 'eager')` 时内部 `_experts_implementation` 仍是 None；设带下划线的 property 才是 eager。见 large-model-config-audit.json（原始文件：`notes/research/large-model-config-audit.json`）。实际启动仍要检查最终 `model.config._experts_implementation`，不能只看 YAML 文本。

推理侧是另一条路径：vLLM 的 Qwen3MoE（原始文件：`notes/research/vllm-qwen3-moe-0.28.py:199`） 使用 FusedMoEFactory；`enforce_eager=true` 关闭 graph capture，不把 MoE 推理改成 HF 的 Python loop。Triton/AITER 等 ROCm MoE 实现仍必须能运行。首轮建议 BF16、TP2、EP=1、无 EPLB、无量化/FP8、无 delta-weight sync，减少尚未验证的组合。

模型配置的 `router_aux_loss_coef=0.001` 也不表示 PPO 会自动加 load-balancing loss：默认 `output_router_logits=false`，HF 在有 labels 的 loss 分支才合入 aux loss，而 verl PPO 消费的是 logits/logprob、自行构造 RL loss。若需要 router regularization，应明确实现并单独验证；本次不能声称已经具备。

## packed experts、untied head 与实时权重更新

[FSDP2 get_per_tensor_param](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine/fsdp/transformer_impl.py#L973) 按 tensor all-gather，然后调用本 pin 已有的 [unfuse_moe_params](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine/fsdp/utils.py#L90)。它将 gate_up 在维度 1 分成 gate/up，逐 expert 生成旧 HF key `experts.E.gate_proj.weight`、`up_proj.weight`、`down_proj.weight`，不必先构造完整模型的大 dict。

实际 vLLM 0.28 的 RoutedExperts.load_weights（原始文件：`notes/research/vllm-routed_experts.py:893`） 同时识别 packed 3D 和 per-expert 2D 输入，并按 expert_id/shard_id 交给 weight loader；Qwen3 attention 还通过 WeightsMapper 合并 q/k/v。也就是说这里已有对应映射，不应仅因 HF5 权重形状变化就宣布“MoE 必须 Megatron”。

热更新按 bucket 调用 `model.load_weights`，不是每批重新载完整 HF checkpoint。untied embedding 与 lm_head 必须分别保留；不要沿用旧 tied 小模型的去重假设。还应关注 loader 返回的 loaded names、映射后的 expert gate/up 顺序、TP 切片和数值 dtype：仅打印“发送了 N 个参数”不等于全部目标参数正确落位。

ROCm 下 dummy 初始化可能将 expert-parallel map buffers 清零；现 pin 在 IPC reload 前已有 [restore_moe_expert_maps](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/utils/vllm/rocm_vllm_moe_expert_map.py#L22)。EP=1 路径通常跳过此恢复，因此先用 TP/ETP 可减少变量；日后启用 EP 时必须核对实际 expert ownership/map，而非仅观察模型能生成字符。

首次 MoE RL 的必要实测点是：完整 checkpoint 的 HF forward/backward 有限；router 和抽样专家参数收到合理梯度；至少一次真实非均匀 reward 更新；FP32 master 与按 BF16 表示的权重差分分别记录；首次及更新后的 vLLM logits/短生成与对应 HF 权重一致到合理容差；多次 sync + KV wake 不 OOM。采样专家时覆盖首/中/末 layer 及多个 expert_id，不能只检查 dense q_proj。

## HF-only 保存：适合评估快照，不等于可恢复训练

当前 [checkpoint manager](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/utils/checkpoint/fsdp_checkpoint_manager.py#L422) 支持 `save_contents: [hf_model]`，会 gather full state 到 CPU、rank0 保存，避免额外保存 sharded optimizer 等大文件。但这不保留 Adam moments、scheduler/RNG 等完整训练状态，不能按精确断点恢复来表述。`rl_path` 负责最终配置和保存验收。

实现虽以 BF16 创建空 `save_model`，传给 `save_pretrained(state_dict=...)` 的是 FSDP 收集到的原 dtype 字典，不会因此自动将 FP32 master 降成 BF16。现有小模型已观察到 HF 文件仍保存 FP32。因此 dense 单次 HF 导出先按约 **122 GiB**、Coder30B 约 **114 GiB** 规划磁盘，并用 safetensors header/实际 bytes 验证；不要按 BF16 的一半大小做保证。CPU gather 与 empty model 也会造成暂时的主存峰值。若另做 BF16 推理导出，应保留清晰文件名/来源，不能覆盖唯一的 master 证据。

## 固定 dev / heldout 与“可汇报效果”

MemAgent 建议在运行前固定 512 train / 64 dev / 64 heldout 的 ID 和顺序，检查 question/source ID 去重与跨分区不重叠。对照至少包括同一 32B base 与同一 32B 训练后 checkpoint；4B→32B 的提升应单独写为模型替换效果，不能混成 RL 收益。保留相同任务、完整 contexts、chunk/memory/token 预算、thinking 开关和验证解码策略。

dev 用于预先声明频率的 checkpoint 比较；按事先确定指标选 checkpoint，heldout 用于固定 base/final 对照，不用 heldout 反复调参或选最好的一次。主 reward 继续记录当前固定 LCS；normalized exact match 的版本必须在看结果之前固定，并明确 `\text` 等包装规则。建议保存原 LCS、PR183 EM 和格式解包诊断各自的逐题列，不混用分母。对多 context 只按最终 session 答案计一题；置信区间/paired bootstrap 的重采样单位是题目，不是 context/token。

SWE 新扩展采用 正式固定选择（原始文件：`results/swe-expanded-v1/selection.json`）：四个新 repo 各 8 个初始候选，固定 seed 的 SHA256 排序，先做未修复 baseline + gold 正负验证。它可作为较原六题更有覆盖面的诊断集，但仍是经镜像/判题条件筛选的小子集。若未来用其中部分进行训练，先固定 train/dev/heldout，或将整组保留作迁移测试；不能在报告里把训练 replay 当 heldout。

SWE 同时报告 resolved、finished、resolved∩finished、工具/解析错误、有效训练 token、耗时和预算。对比 4B/9B/30B base 可以说明更大模型的实际起点；比较 30B base/post 才能说明 RL 增量。优先展示同题前后轨迹与成功/失败的配对计数；一个小 dev 上的最高 checkpoint 或一两个新增正确题不足以宣布稳定训练提升。

这套方案可形成可审阅的结果：大模型的基线能力、工程训练闭环、固定评估上的增量、以及仍失败的具体环节分别有证据。是否出现能力提升由完整实验决定，不应在启动前承诺正向效果。
