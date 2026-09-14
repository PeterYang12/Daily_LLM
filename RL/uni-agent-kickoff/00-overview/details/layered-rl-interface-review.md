# MemAgent 32B / 8B：训练接口与主流程独立源码复核

复核日期：2026-09-14。范围是本 lab 已完成的两个128步训练，读取 `src/uni-agent`、`src/verl-rl`、两份实际 `resolved_config.yaml` 和 durable 附件；未调用 GPU、网络、服务或读取凭据，也未修改冻结代码。

最容易画错的边界：**Agent → Gateway 是 session-scoped OpenAI-compatible HTTP；Gateway → verl rollout server 是 Python 调用再转 Ray RPC；rollout server → vLLM AsyncLLM 是 Python token 接口。** `vLLMHttpServer` 和 `LLMServerClient` 的类名/说明带有 HTTP/OpenAI，不代表本次训练在 Gateway 后面再次调用公共 `/v1/chat/completions`。

## 1. 本次选中的实现与运行方式

两规模都使用 `PPOTrainerSeparateAsync`，配置为4个 trainer GPU、2个 standalone rollout GPU、rollout TP2、`n=4`、batch4、`parameter_sync_step=1`、1个warmup batch、1个Gateway、最多16个agent sessions。训练时 rollout window 是8192；外部stable评测的16384是另一个服务配方，不能填进本训练图。

入口证据：32B YAML（原始文件：`configs/rl_memagent_32b_128_durable.yaml#L2`）、8B YAML（原始文件：`configs/rl_memagent_8b_128_durable.yaml#L2`）。两份运行时解析配置分别为 `runs/rl/memagent_32b_128_durable/resolved_config.yaml` 与 `runs/rl/memagent_8b_128_durable/resolved_config.yaml`：`trainer_mode` 在1001行，`hybrid_rollout.enable_switch: false` 在1010行，`loss_agg_mode: token-mean` 在97行。自动按负载切换hybrid训练/推理未启用；验证时仍会用明确的hybrid切换钩子。

**实际注入的是 `FullyAsyncLLMServerClient`，它继承 `LLMServerClient`。** [PPOTrainerSeparateAsync.get_llm_client](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_separate_async.py#L180) 返回 `standalone_server_manager.get_client(client_cls=FullyAsyncLLMServerClient)`。图中可简写“verl LLM client”，接口表应保留这个实际类名。

## 2. 接口目录：从数据到生成

| 调用方向 | 真实接口与传输 | 主要输入 → 输出 | 源码锚点 |
| --- | --- | --- | --- |
| Dataset → Task payload | Python `HotpotQAMemAgentDataset.__getitem__` → `build_task_config` | parquet `prompt/context/reward_model/extra_info` → `tools_kwargs['task']`，含`metadata.chunks`和`ground_truth`；原始context被移除，避免重复传输 | [dataset.py:36](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/examples/mem_agent/dataset.py#L36)、[107](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/examples/mem_agent/dataset.py#L107) |
| Trainer → TQ prompt登记 | Python `PPOTrainer._submit_batch_to_rollout` → `tq.kv_batch_put` | `TensorDict` + uid → `train` partition；prompt tag为`is_prompt=True,status=pending,global_steps`；异步模式还保存prompt fields供checkpoint恢复 | [trainer_base.py:1438](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L1438) |
| Trainer → Framework worker | Python `AgentFrameworkRolloutAdapter.generate_sequences` → Ray `framework_worker.generate_sequences.remote(prompts)` | prompt `TensorDict`；训练入口立即返回`None`，结果稍后从TQ消费 | [entry.py:150](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/entry.py#L150) |
| Framework worker → Framework | Python `AgentFrameworkWorker.generate_sequences` → `framework.generate_sequences` | 每题展开`rollout.n`个session，调度各runner；worker初始化时`tq.init()` | [entry.py:87](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/entry.py#L87)、[framework.py:586](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L586) |
| Framework → Gateway生命周期 | Python `GatewayManager.create_session/finalize_session/abort_session` → 对应`GatewayActor.*.remote`，Ray RPC | create返回`SessionHandle(session_id,base_url)`；finalize返回`list[Trajectory]` | [manager.py:75](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/manager.py#L75)、[94](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/manager.py#L94)、[GatewayActor.create_session:228](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/gateway.py#L228) |
| Framework → Agent Runner | 本次配置`dispatch_mode=ray_task`，`_run_agent_runner_ray_task.remote(...)` | `runner_fqn/raw_prompt/SessionHandle/tools_kwargs` → `TaskResult`；Gateway token记录与TQ写入仍由Framework持有 | [framework.py:789](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L789) |
| Runner → Task | Python `run_task` → `TaskConfigResolver.resolve` → `get_task(task).run()` | 将`session.base_url`注入runtime model；以`raw_prompt`覆盖序列化prompt | [task_runner.py:101](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/task_runner.py#L101) |
| MemAgent → 模型客户端 | Python `MemAgent.step` → `OpenAICompatibleChatModel.query` | 当前context的messages、每次max_tokens → text/usage；每个chunk构建新context，只将上一轮memory文字带入下一轮 | [agent.py:204](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/mem_agent/agent.py#L204)、[310](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/mem_agent/agent.py#L310) |
| 模型客户端 → Gateway | aiohttp HTTP POST `session.post(f'{base_url}/chat/completions',json=body)` | OpenAI风格`model/messages/sampling` → `choices[0].message`与usage；本次base_url为`http://<gateway>/sessions/{session_id}/v1` | [model.py:97](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/react/model.py#L97)、[147](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/react/model.py#L147)、[gateway.py:239](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/gateway.py#L239) |
| Gateway HTTP → Session | `POST /sessions/{session_id}/v1/chat/completions` → `_handle_openai_chat_completions` → `openai_to_internal` → Python `session.run_generation(internal,self._backend)` | wire JSON → `InternalGenerationRequest(messages,tools,sampling_params)` → `GenerationOutcome` → OpenAI风格HTTP JSON | [gateway.py:127](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/gateway.py#L127)、[155](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/gateway.py#L155)、[types.py:13](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/types.py#L13) |
| GatewaySession → verl client | Python async `backend.generate(request_id=session_id,prompt_ids=...,sampling_params=...,image_data=...,video_data=...,mm_processor_kwargs=...)` | 实际token context → `TokenOutput`；不是把messages再次发到OpenAI HTTP服务 | [session.py:224](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/session.py#L224)，调用在269行 |
| Client → Load balancer / server | Ray `require_acquire_fields.remote/require_release_fields.remote/acquire_server.remote`；随后Ray `server.generate.remote(...)`；finally `release_server.remote(...)` | sticky `request_id`用于分配；vLLM每轮通常得到新uuid；返回token ids/logprobs/版本等 | [llm_server.py:70](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/llm_server.py#L70)、[99](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/llm_server.py#L99)，server RPC在142行 |
| vLLM server → vLLM engine | `vLLMHttpServer.generate`构建`SamplingParams`、`TokensPrompt(prompt_token_ids=...)`；Python `self.engine.generate(...)`并`async for`取最终输出 | `prompt_ids` → `TokenOutput(token_ids,log_probs,stop_reason,num_preempted,extra_fields)`，`extra_fields.global_steps`记录权重版本 | [vllm_async_server.py:560](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/vllm_async_server.py#L560)、[672](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/vllm_async_server.py#L672)、[756](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/vllm_async_server.py#L756) |

`FullyAsyncLLMServerClient.generate`在上述父类调用外包一层partial-rollout处理：将`prompt_ids + 已生成token`再次提交，合并token/logprobs，扣减同一次响应剩余budget，遇到abort可以续生成，汇总min/max权重版本。源码：[llm_server.py:217](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/llm_server.py#L217)，父类调用在283行。不要把这种生成被中断后的续生成，与磁盘native checkpoint恢复混成同一条接口。

## 3. Gateway保存的token事实，以及TQ写入合同

Gateway的codec负责chat template/token编码与连续token对齐；session按消息prefix选择chain。找不到可续接的chain就建立新prompt。MemAgent每轮重建user context，会物化多个context trajectory，不能画成始终向同一个不断增长的chat history追加。

- [GatewaySession._prepare_generation_inputs:412](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/session.py#L412)：构造真实`context_ids`并控制容量。
- [run_generation:282](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/session.py#L282)：读取backend原始`token_ids/log_probs`；295行`codec.merge_assistant_tokens`保持prompt不可改、对齐mask/logprobs，再记录generation version。
- [Trajectory合同:41](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/types.py#L41)：`prompt_ids`固定；`response_ids`可能同时包含模型生成token和中间加入的context token；`response_mask`中模型token为1、context token为0；`response_logprobs`与response对齐，context位置为0.0；另有`finished/reward_score/reward_metrics/extra_fields`。

[Framework._write_session_trajectories_to_tq:1096](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L1096) 使用 `tq.async_kv_batch_put(keys,fields,tags,partition_id)`。每个context的key为 **`{uid}_{session_index}_{trajectory_index}`**；`uid`是原prompt的GRPO group，session_index是该题第几次rollout，trajectory_index是该session的context段。全部写完后，用`async_kv_put(key=uid,tag={'status':'finished'})`标记prompt完成；零成功session时记failure（662行）。这不是把一段JSON transcript直接交给优化器。

字段构造见 [framework.py:1128](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L1128)：

| TQ字段 | 内容与用途 |
| --- | --- |
| `prompts`,`responses`,`input_ids` | long tensor；`input_ids = concat(prompts,responses)` |
| `attention_mask`,`position_ids` | 此轨迹真实token的mask/position；后续批处理再按需要padding |
| `response_mask`,`loss_mask` | 模型生成token训练mask，Framework写入两者；本次`mask_unfinished_episode=false` |
| `rollout_log_probs` | backend生成时的逐token logprob，float32；与trainer后来重算的`old_log_probs`不同 |
| `rm_scores` | response长度的float32零向量，最后位置写该轨迹携带的session reward |
| `extra_fields.reward_extra_info` | reward metrics/scorer附加信息；另保留Gateway版本等metadata |
| `uid`,`session_id`,`global_steps`,`num_turns` | group/session/调度step/turn元信息；`session_id`字段这里是整数rollout index，并非Gateway UUID |
| `raw_prompt`,`data_source`,`reward_model`,`extra_info`,`tools_kwargs`,`agent_name` | 继承存在的样本字段，供reward/数据追踪使用 |
| tag | `status=success,uid,prompt_len,response_len,seq_len,global_steps,min_global_steps,max_global_steps`；权重跨度和发起调度step分开 |

可选multimodal/routed_experts字段在通用合同中存在，本次Qwen3 dense文本MemAgent不用把它们画成必经模块。

## 4. Reward / GRPO / Trainer 的消费接口

本次reward来源是HotpotQA原生boxed-answer token LCS：[HotpotQATask.run:53](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/tasks/hotpotqa/task.py#L53) 从最终回答计算，返回`TaskResult(reward,accuracy,extra_info)`。任务不是训练一个独立reward model。

配置启用了自定义`score_from_runner_result`，因此Framework走 [framework.py:883](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L883) 的RewardLoopWorker支路。`_score_trajectories`只用session最后一条trajectory构造reward DataProto并调用Ray `worker.compute_score.remote(data)`，再把返回分数广播给同session各context（[1040](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L1040)）。本次callback [task_runner.py:78](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/task_runner.py#L78) 读取`extra_info['runner_reward_info']`，转成`{'score':...,**metrics}`；这层worker主要适配任务结果和VERL reward合同。

Trainer消费链：

1. `ReplayBufferAsync.sample(global_steps,partition_id='train',batch_size=4)`等候可用**prompt groups**；`_materialize_batch`返回`KVBatchMeta(partition_id,keys,tags)`，包含选中各题的所有session/context key。源码：[replay_buffer.py:582](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/replay_buffer.py#L582)、[383](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/replay_buffer.py#L383)。
2. [PPOTrainer._step_once:548](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L548)：sample → balance → 重算`old_log_probs` → reference `ref_log_prob` → advantage → actor update；本GRPO配方无需critic/value网络。
3. [PPOTrainer._compute_advantage:1685](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L1685)通过`tq.kv_batch_get(select_fields=...)`读token scores/masks/logprobs，生成`advantages/returns`，再`tq.kv_batch_put`回写。
4. **GRPO按session最终输出分组，而非把每个context当成独立rollout。** [compute_advantage_for_multi_trajectories:148](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/utils.py#L148)先按`{uid}_{session_id}`挑最后一个trajectory，同一uid的4个session计算相对优势，再把session优势乘response_mask广播回它的所有context。
5. [PPOTrainer._update_actor:1769](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L1769)调用`actor_rollout_wg.update_actor(batch)`；[engine_workers.py:712](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine_workers.py#L712)进入`self.actor.train_mini_batch(data)`。此处分布式FSDP2/优化器才更新模型。TQ不是optimizer，Gateway也不执行backprop。

两规模实际配方是GRPO、无group std归一化、`token-mean`、actor KL loss系数0.01；不可把图标成完整DrGRPO recipe。异步预取意味着“本步提交下一批”和“本步消费已完成批”有重叠，箭头不应暗示严格串行的submit→立刻训练同一批。

## 5. 权重同步：一条独立于磁盘checkpoint的数据流

[PPOTrainerSeparateAsync.on_step_end:292](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_separate_async.py#L292)在348行调用`standalone_checkpoint_manager.update_weights(global_steps)`，本次每个global step同步一次。初始化/恢复后 [on_init_end:191](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_separate_async.py#L191) 先将已装载权重同步到standalone和hybrid；验证 [on_validate_begin:199](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_separate_async.py#L199)可切换hybrid供推理。

`CheckpointEngineManager.update_weights`名字含checkpoint，但这里是**活跃actor→rollout的权重传送**，不通过HF文件导出：

1. [base.py:505](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/checkpoint_engine/base.py#L505)：abort/pause replicas → 释放KV cache → 建传输process group。
2. 同时发出actor与rollout worker group的`update_weights`，`ray.get`等待（535行）。配置backend名为`nccl`、`multi_sender=false`、bucket8192MB；在本ROCm栈通过其分布式通信实现传输，不能画成HTTP传模型文件。
3. Actor侧 [engine_workers.py:727](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine_workers.py#L727)：`actor.engine.get_per_tensor_param()` → `checkpoint_engine.send_weights(per_tensor_param,global_steps)`。NCCL接口定义：[nccl_checkpoint_engine.py:315](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/checkpoint_engine/nccl_checkpoint_engine.py#L315)。单sender不代表其他actor ranks不参与FSDP参数收集；未入sender组的rank仍遍历权重generator参与collectives（327行）。
4. Receiver侧 [CheckpointEngineWorker.update_weights:354](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/checkpoint_engine/base.py#L354)：`receive_weights` → `server_adapter.update_weights(weights,global_steps,wire_format='named_tensors')`。
5. [vllm_rollout.py:210](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/vllm_rollout.py#L210)：Ray `server_handle.collective_rpc.remote('update_weights_from_ipc',...)`配合本地`BucketedWeightSender.async_send_weights`；接收扩展 [utils.py:241](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/rollout/vllm_rollout/utils.py#L241)加载权重。源码允许设备IPC或共享内存fallback，图可标“本地bucket/IPC接口”，不要未经runtime证据把fallback宣称为实际启用。
6. 清KV cache、`server_handle.set_global_steps.remote(global_steps)`（vllm_rollout.py:246），finalize传输引擎、恢复KV cache、resume generation（base.py:545）。生成返回的版本号通过Gateway→Trajectory→TQ tag供staleness分析使用。

## 6. 完整native checkpoint与恢复hook

本次主循环真实顺序为：**actor update → 若到8的倍数则native save/commit → `on_step_end`权重同步 → 若到32的倍数则validation → metrics/rollout dump → 清已消费TQ key → global step加1**。锚点：[PPOTrainer.fit:459](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L459)。Save在`timing_s/step`内，testing在外；不能把validation漏掉或把save重复加到耗时。

| 接口 | 内容 | 锚点 |
| --- | --- | --- |
| `PPOTrainer._save_checkpoint` | `actor_rollout_wg.save_checkpoint`保存model/optimizer/extra；`torch.save(train_dataloader.state_dict(),'data.pt')`；`tq.save_checkpoint(...,metadata={'global_steps':...})`；最后调用callback | [trainer_base.py:920](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L920)，TQ在978行，callback在1000行 |
| `FSDPCheckpointManager.save_checkpoint/load_checkpoint`审计wrapper | 原生save/load完成后读取实际模型/optimizer/scheduler/RNG状态指纹，保存rank证据；不是替代原生存储 | rl_durable_rank_audit.py:71（原始文件：`scripts/rl_durable_rank_audit.py#L71`）；8B对应`rl8_rank_audit.py` |
| `DurableCheckpointCallback` | save前磁盘余量检查；save后校验必需rank文件+TQ payload、fsync，原子发布`checkpoint-commit.json`与`committed_checkpoint.json`；新commit完成后才按lease清理旧committed checkpoint | rl_durable_checkpoint.py:85（原始文件：`scripts/rl_durable_checkpoint.py#L85`）、106（原始文件：`scripts/rl_durable_checkpoint.py#L106`）；8B对应`rl8_durable_checkpoint.py`同名类 |
| `PPOTrainer._load_checkpoint` | 恢复actor model/optimizer/extra、dataloader、`tq.load_checkpoint` | [trainer_base.py:822](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L822)，TQ在878行 |
| `_reissue_inflight_prompts` | 从TQ取pending/running prompt，清它们旧的部分trajectory key，重新置pending并`agent_loop_manager.generate_sequences(batch)` | [trainer_base.py:882](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L882) |

完整恢复并不等于逐token重放in-flight生成。已完成缓存trajectory可以复用，未完成prompt可以重发；durable callback的文件probe是长度加固定头/中/尾检查，不是每个数百GiB native文件的全量hash。最终HF export/外部评测属于训练结束后的另一条工作流，不应画成每一步weight sync必经节点。

## 7. 可直接用于主图的九步流程

1. 读取parquet、分块、形成Task payload；Trainer以uid登记TQ prompt并异步提交Framework。
2. Framework每题启动4个session；通过GatewayManager的Ray生命周期API取得session专用base_url。
3. Ray runner解析HotpotQA Task，MemAgent逐块生成memory，最后生成boxed answer。
4. 每次生成走：Agent HTTP → Gateway codec/session → FullyAsyncLLMServerClient Python → balancer/server Ray RPC → vLLM AsyncLLM。
5. Gateway保存真实token/logprob/mask/version；Task算最终LCS；Framework finalize并经RewardLoopWorker callback取得session reward。
6. Framework把多个context trajectory写入TQ，并把prompt marker置finished。
7. ReplayBuffer以题组取齐数据；Trainer重算old/ref logprob，以session最终reward算GRPO优势并广播到各context；FSDP2 actor训练。
8. 每8步提交完整native checkpoint；每步将actor权重同步到rollout；每32步跑monitor。
9. 写metrics/trace、清已消费TQ keys，继续到128；结束后单独导出HF并做固定external base/final重复评测。

图例建议区分四种箭头：**HTTP JSON、Ray RPC、进程内Python调用、tensor/TQ或权重数据传输**。磁盘checkpoint用存储图形，reward用计算模块；本MemAgent没有必须的shell/tool sandbox动作，通用Task构造sandbox不代表本案例实际使用工具。
