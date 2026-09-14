# 30B 级主实验：固定设计与执行记录

本阶段按用户补充的“模型不要太小、需要汇报”要求开展。用户 2026-09-12 进一步要求 30B 级 long steps，正式目标定为 **Qwen3-32B / 128 global steps**。小模型阶段的日志与失败结果继续保留。这里的计划与实际结果分开记录，计划步数不能计作已完成训练。

当前主线为 **`memagent_32b_128_durable`**，04:40:53 UTC从原始base重新开始，使用每8步完整native保存和step8受控resume验收；实时进度（原始文件：`results/large-durable-live-status.json`）、新冻结协议（原始文件：`notes/rl-memagent-32b-128-durable-protocol.json`）、[恢复设计](../../03-memagent-32b-rl/details/rl-32b-durable-recovery-plan.md)与[复现入口](large-model-reproduction.md)已切到该run。旧`memagent_32b_128`的46步日志及step32结果仅作中断历史。

## 模型与环境

| 用途 | 模型 | 固定 revision | 实际参数量 |
| --- | --- | --- | ---: |
| 官方 MemAgent 全参数 RL | Qwen3-32B | `9216db5781bf21249d130ec9da846c4624c16137` | 32,762,123,264 |
| 真实代码修复与黑盒 harness | Qwen3-Coder-30B-A3B-Instruct | `b2cff646eb4bb1d68355c01b18ae02e7cf42d120` | 30,532,122,624；每 token 约 3B active |

两者均以 BF16 原始权重推理，没有量化。`A3B` 是 MoE 的计算激活规模，不能按 3B 估计全参数训练状态。硬件预算、FSDP2、MoE 与权重映射审阅见[方案审阅](../methods/large-model-design-review.md)。

32B 外部评测服务使用 HIP 0、TP1、16384 窗口、eager、非 thinking；Coder30B 使用 HIP 1、TP1、65536 窗口、eager、`qwen3_coder` 工具解析。实际服务为 torch 2.12.0+git6bbd260、vLLM 0.28.1rc1.dev516+g9ea8f3ffc.rocm723、Transformers **5.16.1**；训练 overlay 的 Transformers 是 **5.9.0**，不能将两者混写为一个版本。服务运行时与完整命令（原始文件：`results/environment/large-inference-runtime.json`）、首次真实生成与无参 submit 检查（原始文件：`results/large-service-initial-probes.json`）均已保存。

## MemAgent 训练前后评估

官方原始 HotpotQA MemAgent 数据固定为前 512 条 train、dev 的 0–63 条 monitor、dev 的 64–127 条 external。每条保留完整原文；32B tokenizer 按 5000 token 分块，train 每题 5–6 块，monitor/external 每题 6–7 块，无内容截断。清单含原始 row hash，见manifest（原始文件：`data/rl_memagent_512_64_64/manifest.json`）。

外部评估协议在看到结果前固定：64 题全部运行一次，memory 最多 1024 token，最终输出最多 1024 token，temperature=0、top_p=1，官方 boxed answer LCS reward，使用相同基座 tokenizer。任务配置（原始文件：`configs/rl_memagent_32b_external_task.yaml`）和运行入口（原始文件：`scripts/run_large_memagent.py`）固定输入 checksum，检查服务返回的**实际权重路径**，避免误把 base 重载当成 checkpoint 评估。

另外的 23 个五档长文案例沿用官方 temperature=1.0、top_p=0.7，每个题目/长度组合只采样一次；其随机轨迹和长文图属于独立诊断，没有重复采样方差估计，不并入上述 greedy external64 训练前后比较。

`monitor` 可用于观察训练过程；external 不用于挑 checkpoint 或调整训练参数。最终按预先确定的训练终点做一次 base/final 配对，统计单位是问题，非 memory chunk 或生成 token。LCS=1 表示本 scorer 下答案完全匹配；不冒充另一套 HotpotQA EM。

| 项目 | 输出 | 当前意义 |
| --- | --- | --- |
| 首次 32B base / external64 | 原始 JSONL（原始文件：`results/large-memagent/base/results.jsonl`）、执行来源（原始文件：`results/large-memagent/base/provenance.json`） | 环境中断，仅 30/64，不计完整指标 |
| 旧driver 32B base / external64 | 执行来源（原始文件：`results/large-memagent/base-recovery/provenance.json`） | 64/64，LCS0.4288938492、21/64；driver6.16.13，仅作历史 |
| 当前driver 32B base / external64 | 执行来源（原始文件：`results/large-memagent/base-durable/provenance.json`） | 64/64，error0，LCS **0.4362723214**、LCS=1 **19/64**；driver7.1.0.31500000，新final配对起点 |
| 六卡工程 pilot | 配置（原始文件：`configs/rl_memagent_32b_pilot.yaml`）、日志（原始文件：`runs/rl/memagent_32b_pilot.log`） | 校准 4 trainer + 2 rollout / TP2，不能当作正式效果实验 |
| 旧正式训练，中断历史 | 旧128-step配置（原始文件：`configs/rl_memagent_32b_128.yaml`）；run名`memagent_32b_128` | 01:25启动，03:43宿主中断；46/128步有完整日志，step32 HF保留，无最终external |
| 新正式训练 / final external | durable配置（原始文件：`configs/rl_memagent_32b_128_durable.yaml`）；run名`memagent_32b_128_durable` | 04:40 fresh-base启动，monitor基线 **0.4944568452**；终点预定step128 / `final-durable-step128`，尚未完成 |

新旧两轮使用相同 **4 trainer + 2 rollout（一个TP2副本，HIP2–7）**、128 global steps、batch4 × rollout n=4，128步完整执行才覆盖512 train一次。DR-GRPO关闭组内标准差归一化，KL0.01、峰LR1e-6、4步warmup，保留当前reward的`\\text{}`解包语义，不混入上游PR183的采样/奖励变化。旧run每32步导出约122GiB FP32 HF，不含Adam moments，因此无法精确resume。

新durable run每8步保存model、optimizer、extra(scheduler/RNG)、data、TQ完整原生状态。根盘专用卷保留最新一份完整断点，新旧共存直到独立committed指针提交后才轮换；step8计划退出75并实际验证恢复后继续。最终step128再合并导出BF16 HF，计划lab内路径`runs/rl/memagent_32b_128_durable/exports/global_step_128/huggingface`，独立评测名`final-durable-step128`。文件存在、计数增加或计划暂停都不能替代真实恢复与最终能力验证。

## Coder30B 代码修复

原六题与小模型使用相同 40-turn 配置、64K 服务、2048 单轮输出、temperature 0.2 / top_p 0.9、900 秒任务外层超时，便于同题诊断。入口（原始文件：`scripts/run_large_swe.py`）、恢复后完整六题运行（原始文件：`results/large-swe/coder30b-six-recovery/provenance.json`）；首次中断记录（原始文件：`results/large-swe/coder30b-six/provenance.json`）另行保留。

扩展集先在四个新仓库内按固定 seed 的 hash 排序选择各 8 题，再做 baseline/gold 正负控制，模型结果没有参与筛选。32 题均 baseline reward=0、gold reward=1，其中 3 个 xarray 镜像的 baseline 存在额外 PASS_TO_PASS 失败，按预定严格规则排除。正式 **29 题：Django 8、Sphinx 8、SymPy 8、xarray 5**。这仍是有环境筛选的诊断子集，不能称 SWE-bench 官方总分。

扩展预算在模型调用前固定：同一 prompt 与采样，100 turns、1800 秒外层超时、单轮 2048 token、`max_total_tokens=55000`；并发 4。当前 ReAct 实现把最后一次请求的 prompt+completion token 数保存为 `total_tokens`，并在达到 55K 时结束，因此这里是当前请求用量的停止阈值，未累计所有 API 请求的消费。新加入的工具输出还可能使一次请求越过该软阈值；vLLM 另有 65536 的硬窗口。配置（原始文件：`configs/swe-react-coder30b-expanded.yaml`）、固定案例与镜像（原始文件：`results/swe-expanded-v1/final-manifest.json`）、[排除依据](../../05-swe-code-agents/details/swe-expanded.md)、恢复后完整运行（原始文件：`results/large-swe/coder30b-expanded-recovery/provenance.json`）。因预算和题集不同，扩展组不与原六题拼成同条件排行榜。

每题保留模型补丁、忽略文件模式变化的 content patch、原始 verifier stdout/stderr、逐测试结果、agent 返回与配置，分别统计 `resolved`、`finished`、两者交集。通过测试的未结束轨迹不能直接当作正常提交。

## 已遇到的工程问题

首次 32B pilot 成功加载 actor/ref 与 TP2 vLLM 后，初始多桶权重同步停止前进。进程栈显示 sender/relay 在不同同步阶段互等；使用上游提供的 `checkpoint_engine.engine_kwargs.nccl.multi_sender=false` 隔离多 sender 环路。原始运行和栈保留。2026-09-12 01:12 的 restart pilot 已实际完成首次同步：1/4 actor 发送，world size 3，完整权重发送约 2.99 秒、接收端重组 707 tensors。仍需完成反向传播、更新后同步、HF 保存与最终验证才算完整 pilot 验收。

该问题在旧 4B 单桶配置下没有暴露，说明放大模型会覆盖新的训练工程路径。两次 32B 尝试之间还发生了宿主环境恢复，且恢复前的 single-sender 入口没有真正启动模型。因此目前证明的是所选配置可以完成多桶同步与训练，未在恢复后的相同环境重做 multi-sender 受控 A/B，不能把一个开关作为唯一已证明原因。完整结论将以正式运行产物更新本文。

2026-09-12 00:56 检查发现原 lab 容器与宿主 Docker 镜像均已不存在，宿主 uptime 约 6.5 小时，`/opt/rocm` 不再存在，但 data2 中的模型、源码、venv、sandbox image layers 和已落盘记录保留。不能将空窗期描述为训练持续运行；中断原因未确定。中断证据（原始文件：`results/environment/interruption-20260912.json`）记录首次 base 30/64、SWE six 3/6、expanded 无完整结果。恢复原镜像后 8 个 gfx950 GPU 均通过实际 tensor 计算；新的 amdgpu module version 为 6.16.13，设备名返回空字符串，因此不会将旧 MI350X 字符串冒充新环境实测名称。

恢复后重新完整跑 base64，并通过运行时记录函数（原始文件：`scripts/lab_provenance.py`）记录 image、包版本、kernel、driver；配对分析器（原始文件：`scripts/analyze_large_memagent.py`）要求完整无错 64×2、相同题目/预算和一致 runtime，才计算配对差值及 bootstrap 95% CI。SWE 大文件回写另触发 Linux argv 长度限制，已改用 Docker stdin 传输并通过 16 MiB 实际校验，见[修复记录](../../01-cpu-sandbox-gateway/details/docker-write-file-fix.md)。未改模型 prompt 或 reward。

正式资源分配改为 6 卡有实测依据：pilot 下一批 16 session 在 01:15:34 全部就绪，当前 batch 的 actor 更新到 01:17:10 才结束，预取提前约 96 秒。生成约 72 秒，old/ref logprob + actor update + sync 约 172 秒，当前瓶颈在训练端。剩余 HIP 0/1 并行做大模型独立案例；正式过程记录等待时间，若长输出使生成成为瓶颈也不缩减 128 步或更换评估题目。冻结协议（原始文件：`notes/rl-memagent-32b-128-protocol.json`）记录了选择依据、配置和源码 hash。

2026-09-12 03:43 UTC 左右发生第二次宿主中断。此次固定vLLM与dind镜像、lab容器仍在，容器进程停止；host uptime重新计时，amdgpu module version由6.16.13变为7.1.0.31500000。正式训练已落盘46个完整step日志，最新HF为step32，未保存step33–46对应完整权重或Adam moments。原因未确定；Docker状态为exit137且OOMKilled=false，不将它自动解释成显存OOM。第二次中断记录（原始文件：`results/environment/interruption-second-20260912.json`）保留时间、容器状态、旧live snapshot与文件hash。

04:30 UTC时CPU驱动容器及独立sandbox已按原digest恢复，45个sandbox镜像仍可用；没有重装Python依赖，也没有由恢复脚本启动GPU服务。独立daemon重启会重建socket并重置group，`lab_services.py start sandbox`现已在ready后恢复本用户group和660权限。新的GPU健康检查、训练恢复及相同runtime的external对照必须另行完成。旧run状态已明确改为interrupted，不能把已停止的watch日志继续当作实时进度。
