# Uni-Agent API接口总览（固定版本）

> 基于 `verl-project/uni-agent@10743439dd0a19da44a94cccad069b135d957bf1`，整理日期2026-09-14。  
> 本文同时覆盖 **Gateway HTTP接口、Python/Ray入口、agent/tool/task扩展接口、训练适配与路由接口**。Sandbox的逐项方法、Docker Engine与verdal/E2B接口放在[另一份独立文档](04-sandbox-and-verdal-api-reference.md)。

源码采用实验checkout；其日志模块有已记录的本地fork修复，见[补丁](../reproduce/patches/uni-agent-logging-fork.patch)，不改变本文所列接口签名。机器清单保存实际源文件hash。

## 1. 如何理解“所有API”

Uni-Agent主要是Python框架，不是一套把所有操作都暴露为REST的服务。本版本Gateway源码自己注册了**两个业务HTTP路由**；创建session、启动agent、运行任务等主要是Python或Ray方法。

本文正文列出接入所需的接口、参数、功能与返回值；附录列出该版本`uni_agent/`下所有**公开名称的声明及其公开方法**，包括扩展/实现层，避免只列本次用过的几个函数。下划线私有辅助函数、继承自Pydantic/Ray/vLLM等依赖的全部方法不冒充Uni-Agent自己的API。`_GatewayActor`是例外：它是导出`GatewayActor`的实际实现，本文明确标注。

状态说明：**本次使用**表示实验调用过这条路径，不代表穷尽了所有入参；**源码接口**表示代码存在但本次没有验证该功能。路由与声明的机器可读清单见[api-inventory.json](../evidence/api-inventory.json)。

## 2. 接口分层速查

| 层次 | 典型接口 | 调用方式 | 本次情况 |
|---|---|---|---|
| 模型请求入口 | Gateway的chat/completions、messages | HTTP POST | 三种agent正式评测使用 |
| Session管理 | `create_session/finalize_session/abort_session` | Python异步方法或Ray actor调用 | 直接使用`_GatewayActor`创建/结束session |
| Agent | `build_agent`、`Agent.run` | Python | ReAct、Claude Code、Mini |
| Task | `get_task`、`Task.run`、配置解析 | Python | 官方单题及自写driver使用相关组件 |
| Tool | `Toolbox.call`、模型可见函数schema | Python + 模型tool call协议 | ReAct与sandbox示例 |
| Sandbox | `start/exec/read_file/stop`等 | Python；provider再调用外部服务 | Docker与实验E2B适配器 |
| 训练适配 | `generate_sequences`、RolloutAdapter | Python/Ray/TransferQueue | 本次未运行训练闭环 |
| Agent-aware router | `KVCAwareBalancer.acquire_server`等 | Python/Ray后端接口 | 本次双副本实验未使用该router |
| 日志与观测 | `sample_logging`、metric/trace facade | Python | 使用任务日志，未单独验收rl-insight部署 |

## 3. Gateway HTTP API：业务路由完整列表

设Gateway根地址为`http://<gateway-host>:<port>`。必须先由Python/Ray创建session，以下HTTP调用才能使用该`session_id`。

| 方法与路径 | 功能 | 主要请求字段 | 返回 |
|---|---|---|---|
| `POST /sessions/{session_id}/v1/chat/completions` | 接收OpenAI兼容聊天/工具请求，交给session和模型backend生成 | `messages`、`model`、`tools`、`tool_choice`、`stream`及允许的采样字段 | Chat Completion JSON或SSE，含assistant文本/tool_calls与usage |
| `POST /sessions/{session_id}/v1/messages` | 接收Anthropic兼容请求，供Claude Code等使用 | `messages`、`system`、`model`、`tools`、`tool_choice`、`stream`、`max_tokens`、`stop_sequences`等 | Anthropic风格message JSON或SSE事件 |

来源：[gateway.py](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/gateway.py)。

### OpenAI兼容请求的实际范围

- `messages`必须是非空列表；消息支持role/content、assistant的tool_calls、tool消息的tool_call_id/name等字段。
- `tools`使用OpenAI function schema；配置的tokenizer、chat template和tool parser必须与模型匹配。
- `tool_choice`支持`auto`和`none`；`none`会移除本次注入的tools。指定某个函数、`required`等选择方式不受支持。
- 只支持`n=1`；任何非`null`的`response_format`均被拒绝，不能据此宣称支持JSON schema structured output。
- 默认允许客户端覆盖`temperature/top_p/top_k/max_tokens/stop`。实际可覆盖集合由`GatewayActorConfig.allowed_request_sampling_param_keys`决定。
- 本次正式作业只允许请求覆盖`stop`，其余采样参数由session固定，以免CLI默认值改变比较条件。
- 请求的`model`主要用于响应标识；实际模型backend由Gateway部署时绑定，不会仅凭该字符串自动加载另一套权重。

### Anthropic兼容请求的实际范围

- `system`可为字符串或文本block列表；messages支持文本、tool_use、tool_result及源码支持的图像形式。
- 工具定义采用`name/description/input_schema`，内部转换为统一工具schema。
- `tool_choice`支持字符串或对象形式的`auto/none`，不支持强制指定某个工具或`any`。
- `stop_sequences`在允许覆盖stop时映射为内部`stop`。
- `cache_control`等缓存提示不会变成训练采样参数；存在的兼容字段不代表后端兑现对应缓存能力。
- 多模态还要求processor、模型与backend配套；本次没有做多模态验收。`redacted_thinking`等不支持的内容会被拒绝。

### 返回、流式和错误

JSON响应包含相应协议的文本/工具块、停止原因与token usage。`stream=true`会使用SSE封装；本版本先等待`session.run_generation`得到完整outcome，再组织SSE输出，**不能把它等同于后端逐token实时透传**。

| 情况 | 行为 |
|---|---|
| 正常生成 | 返回JSON或SSE |
| JSON解析失败、adapter拒绝的参数 | 通常HTTP 400及对应协议错误体 |
| session不存在或已被结束移除 | HTTP 404 |
| 未处理的backend/codec异常 | 通常HTTP 500；具体见错误体和任务日志 |

本版本路由代码没有实现API-key身份校验。SDK所需的占位key不构成安全认证；若对外提供服务，访问控制需要另外实现。

### 最小HTTP调用示意

以下只展示请求形状，host、port、session必须替换为实际已创建的实例：

```bash
curl -X POST 'http://<gateway-host>:<port>/sessions/<session-id>/v1/chat/completions' \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-Coder-30B-A3B-Instruct","messages":[{"role":"user","content":"Reply with OK only."}],"max_tokens":64}'
```

Claude Code的`ANTHROPIC_BASE_URL`应为`http://<gateway-host>:<port>/sessions/<session-id>`，由CLI再追加`/v1/messages`；OpenAI兼容客户端的base_url则以`/sessions/<session-id>/v1`结尾。

### 默认文档路由与不存在的控制路由

`FastAPI()`默认还提供`GET /openapi.json`、`/docs`、`/docs/oauth2-redirect`、`/redoc`。这些是自动文档路由；因业务端点直接读取原始Request，自动schema不一定完整描述请求体。

本版本没有定义`POST /sessions`、`GET /sessions`、`POST /agents/run`、`POST /tasks/run`或Gateway自身的`/v1/models`、`/v1/completions`接口。不能把下面的Python方法直接拼成HTTP URL。vLLM自己的这些路由属于另一层服务。

## 4. Gateway Python / Ray管理接口

`uni_agent.gateway.GatewayActor`是Ray包装的actor；通过`actor.method.remote(...)`调用。`uni_agent.gateway.gateway._GatewayActor`是内部实现类，本次实验直接实例化它以运行独立调试路径。

| 方法 | 主要参数 | 功能与返回 |
|---|---|---|
| `start()` | 无 | 启动Gateway HTTP服务；之后才可创建session |
| `create_session(session_id, metadata=None, sampling_params=None)` | 唯一ID、任务metadata、采样参数 | 创建session，返回`SessionHandle(session_id, base_url)`；重复ID报错 |
| `get_session_state(session_id)` | ID | 返回活动session的状态快照，便于调试 |
| `finalize_session(session_id)` | ID | 完成并移除session，返回`list[Trajectory]` |
| `abort_session(session_id)` | ID | 中止并移除session；不存在时可幂等返回 |
| `shutdown()` | 无 | 关闭HTTP服务；调用方仍应妥善完成/中止正在使用的session |

`GatewayManager(llm_client, *, gateway_count, gateway_actor_config=None)`管理一组Ray GatewayActor，依赖已有Ray CPU节点和模型客户端。其API为：

| 方法 | 功能 |
|---|---|
| `create_session(session_id, **kwargs)` | 选择当前活动session最少的actor并登记路由 |
| `finalize_session(session_id)` | 在所属actor完成session、释放计数、返回轨迹 |
| `abort_session(session_id)` | 取消session并释放管理记录 |
| `shutdown()` | 关闭所管理的actor服务并清空管理状态 |

本次未使用GatewayManager集群池；使用的是直接创建的单个`_GatewayActor`。管理方法不是HTTP路由。

### GatewayActorConfig与数据对象

| 配置组 | 字段 | 用途 |
|---|---|---|
| 模型处理 | `tokenizer`, `processor`, `hf_model_type` | 对话编码、模型相关token拼接、多模态处理 |
| 工具解析 | `tool_parser_name`, `rollout_backend`, `enable_tool_parser_cache` | 解析模型生成的工具调用 |
| 模板/多模态 | `apply_chat_template_kwargs`, `mm_processor_kwargs`, `vision_info_extractor`, `vision_info_extractor_kwargs` | 模板与媒体预处理扩展 |
| 请求参数控制 | `allowed_request_sampling_param_keys` | 控制客户端能覆盖哪些采样项 |
| 容量 | `prompt_length`, `response_length` | 两者均配置时限制总轨迹容量 |
| 上下文修改 | `enable_last_assistant_rollback` | 是否允许回滚并复用最新assistant链 |

`SessionHandle`包含`session_id/base_url`。`Trajectory`字段包括`prompt_ids`、`response_ids`、`response_mask`、`response_logprobs`、`finished`、`reward_score`、`reward_metrics`、`num_turns`、`chain_id`、`routed_experts`、`multi_modal_data`、`extra_fields`。具体导出器可能只序列化其中一部分。

要保存logprob，需要在session采样参数中显式启用相应开关；后端返回logprob不代表session一定保存。历史主实验与独立logprob验收的区别见[轨迹文档](../experiments/08-trajectories.md)。

### 高级session/codec接口

| 接口 | 功能 |
|---|---|
| `GatewaySession.run_generation(request, backend)` | 编码上下文、调用backend、更新链与轨迹，返回GenerationOutcome |
| `GatewaySession.finalize()` / `abort()` | 完成或取消session |
| `GatewaySession.snapshot_state()` / `sampling_params` | 状态快照与采样配置 |
| `MessageCodec.build_initial_tokens(...)` | 初始对话token化 |
| `MessageCodec.merge_assistant_tokens(...)` | 拼接模型输出并维护mask/logprob |
| `MessageCodec.merge_context_tokens(...)` | 拼接后续工具/上下文token |
| `MessageCodec.decode_response(...)` | 将模型输出解码成assistant消息与工具调用 |
| `MessageCodec.extract_multi_modal_data(...)` | 提取媒体数据，需相应配置 |
| `MessageCodec.canonicalize_message_for_prefix_comparison(...)` | 规范化消息以进行前缀比较 |

这些是框架内部协作/扩展接口，普通harness接入通常只需HTTP端点与session管理。

## 5. Agent接口

| API | 输入 | 功能 / 返回 |
|---|---|---|
| `build_agent(config)` | 具体`AgentConfig` | 按name懒加载并构造Agent实例 |
| `get_agent_cls(name)` | 注册名 | 返回具体Agent类 |
| `register_agent(name)` | 类装饰器 | 注册自定义agent；位于`agents.registry` |
| `Agent.from_config(config)` | 配置 | 类级构造入口 |
| `await Agent.run(sandbox=..., messages=..., workdir=None)` | 已启动的sandbox、消息、工作目录 | 执行一轮任务，返回`AgentResult` |

`AgentResult`包含`output/transcript/info/finished`。它不负责最终任务reward；评分属于Task层。`finished`允许True/False/None。

### 已注册的agent

| name | 实现 | 运行方式与主要特有配置 | 本次 |
|---|---|---|---|
| `react` | `ReActAgent` | Python循环；`tools/max_steps/action_timeout/timeout_budget` | 使用 |
| `claude_code` | `ClaudeCodeAgent` | 运行真实CLI；`max_turns/run_timeout/enable_web_tools/enable_subagents/disable_slash_commands/verbose/extra_args/extra_env` | 使用 |
| `mini_swe_agent` | `MiniSweAgentAgent` | 运行外部Mini；`step_limit/run_timeout/conda_env/tool_python/run_agent_script` | 使用 |
| `mem_agent` | `MemAgent` | 记忆型流程；`max_steps/max_memorization_length/max_chunks/max_final_response_length` | 源码接口，未实测 |

共同的`AgentConfig`字段为`name/model`。`ModelConfig`字段为`base_url/api_key/model_name/temperature/top_p/top_k/max_total_tokens/max_tokens_per_turn`，并有`sampling_params()`导出已配置采样项。

该版本ReAct对`max_total_tokens`的实际更新规则使用最近一次prompt+completion计数，不能仅凭字段说明把它当成累计生成配额。Claude/Mini有自己的循环及停止语义。

## 6. Task与配置接口

| API | 功能 / 返回 |
|---|---|
| `get_task(config_or_mapping)` | 根据name创建Task |
| `get_task_cls(name)` | 返回注册Task类，位于`tasks.registry` |
| `register_task(name)` | 注册新任务家族 |
| `TaskConfigResolver.from_file(path)` | 读取YAML任务默认配置 |
| `resolver.resolve(sample_config, runtime_model=None)` | 合并任务默认值、样本设置和运行时模型绑定，返回配置dict |
| `render_prompt_template(metadata, prompt_template)` | 根据metadata渲染纯文本聊天模板，返回`list[dict]`消息列表 |
| `await Task.run()` | 执行任务家族的完整逻辑，返回`TaskResult` |
| `Task.build_sandbox()` / `Task.build_agent()` | 按配置构建对象；构建sandbox对象不等于已启动 |

`TaskConfig`字段：`name/sandbox/agent/prompt/prompt_template/metadata`。`TaskResult`字段：`reward/accuracy/finished/extra_info`；数值结果要求有限。

| 任务name | 家族与常用附加字段 |
|---|---|
| `swe_bench` | SWE-bench；`run_oracle_solution/eval_timeout` |
| `swe_bench_multilingual` | 多语言SWE；`run_oracle_solution/eval_timeout` |
| `swe_rebench` | SWE-reBench；`run_oracle_solution/eval_timeout` |
| `terminal_bench` | 终端任务；`run_oracle_solution` |
| `hotpotqa` | QA任务；`ground_truth` |
| `harbor` | Harbor任务；`harbor_env/timeout_multiplier/override_cpus/override_memory_mb`及Harbor agent设置 |

各家族提供预处理与评分函数，完整名称见附录。SWE本次实际使用 `compute_reward(metadata, sandbox, eval_timeout=600.0)`；它在sandbox运行测试并返回resolved、eval_completed、eval_report等结果dict，不是HTTP评分端点。

正式28题的外层driver调用Task配置与agent构造，再自行保存候选和独立评分；官方示例则调用Task.run。二者不可混称为同一个完整训练入口。

## 7. Tool接口及模型可见函数

| Python API | 功能 |
|---|---|
| `Toolbox.from_specs(specs, sandbox=...)` | 按`{name,...配置}`列表绑定工具 |
| `Toolbox.all(sandbox=...)` | 使用全部已注册工具 |
| `names()` / `schemas()` | 工具名及提供给模型的function schema |
| `start()` / `close()` | 初始化、关闭工具状态 |
| `async with toolbox` / `entered(retry=..., timeout=...)` | 带清理的工具生命周期 |
| `await toolbox.call(name, args=None, *, timeout=None)` | 解析dict/JSON字符串参数、验证并分发调用，返回ToolResult |
| `Tool.schema()` / `Tool.config_schema()` | 模型参数schema / 构造配置schema |
| `await Tool.run(args, timeout=None)` | 自定义工具实现入口 |
| `register_tool(name)` / `get_tool(name, sandbox, **kwargs)` | 注册工具类 / 构造绑定sandbox的工具实例，位于`tools.base` |
| `ToolResult.to_observation(max_length=100000)` | 格式化并按字符长度裁剪观察结果 |

`ToolResult`含`text/status`；`ToolStatus`为`ok/format_error/error/timeout`。`ToolCallFormatError`用于错误调用，`ToolError`用于可反馈给模型的执行错误。它们不是HTTP状态码。

| 注册名 / 模型看到的名字 | 参数 | 功能 |
|---|---|---|
| `stateful_shell` / `shell` | `command` | 运行持久shell命令，保留cwd/env；构造配置含env_vars、command_timeout、width、height |
| `str_replace_editor` | `command/path`及具体编辑参数 | 查看、创建、替换、插入、撤销文件编辑 |
| `submit` | 无参数 `{}` | 表示代码任务提交/结束，不自行判题或上传patch |
| `finish` | `answer` | 表示文本任务完成，返回最终回答 |

Editor命令：`view`可用`view_range`；`create`要求`file_text`；`str_replace`要求`old_str`，可传`new_str`；`insert`要求`insert_line/new_str`；`undo_edit`撤销已记录编辑。路径指sandbox中的绝对路径。

这些是ReAct工具接口。Claude Code和Mini内部的工具由各自harness实现，不都经过Uni-Agent Toolbox，也不应列成Uni-Agent新HTTP路由。

## 8. 训练框架适配接口（本次未运行训练）

| 接口 | 功能 |
|---|---|
| `AgentRunner.__call__(session, raw_prompt, sample_index, **kwargs)` | 单条agent任务的可调用协议，返回TaskResult或None |
| `AgentFramework.from_config(...)` | 构造训练驱动的框架实现 |
| `AgentFramework.generate_sequences(prompts: TensorDict)` | 运行agent session并将轨迹写入TransferQueue |
| `GatewayAgentFramework.from_config(...)` / `generate_sequences(...)` | Gateway、runner、reward及轨迹后处理的集成实现 |
| `build_gateway_manager(*, config, llm_client)` | 根据训练配置构造Gateway池 |
| `build_agent_framework(*, config, gateway_manager, ...)` | 构造配置指定的框架 |
| `AgentFrameworkWorker.generate_sequences(prompts)` | worker侧执行入口 |
| `AgentFrameworkRolloutAdapter.create(*, config, llm_client, ...)` | 将框架接到verl rollout系统 |
| `generate_sequences(prompts)` | 提交TQ批次，不等待全部rollout结果 |
| `generate_sequences_and_wait(prompts)` | 阻塞等待版，供独立运行使用 |
| `framework.task_runner.run_task(...)` | 用框架session执行任务，处理配置/网关绑定 |
| `score_from_runner_result(...)` | 从runner结果提取评分信息 |
| `compute_multi_modal_inputs(processor, input_ids, multi_modal_data, mm_processor_kwargs=None)` | 为单条样本计算多模态输入tensor |
| `compute_position_ids(processor, input_ids, attention_mask, multi_modal_inputs)` | 计算纯文本或多模态位置ID |

这组接口涉及Ray、TensorDict、TransferQueue、verl及可选reward组件，不是HTTP训练API。本次没有调用它们完成loss或optimizer更新。

## 9. Agent-aware router接口

主要入口为`KVCAwareBalancer(servers, config=None, provider_factory=None)`，用于在多个模型服务之间排名和分配请求。本次双副本实验采用静态轮询，没有实际使用本模块。

| 方法 | 功能 |
|---|---|
| `acquire_server(request_id, prompt_ids=None)` | 路由并返回选中的server ID及handle |
| `release_server(server_id, request_id=None)` | 请求完成后释放负载/粘性状态 |
| `get_all_servers()` / `get_status()` | 服务列表和调试状态 |
| `get_total_inflight()` | 当前总inflight数量 |
| `add_servers(servers)` / `remove_servers(server_ids)` | 动态更新服务池 |
| `clear_sticky_cache()` | 清除session粘性绑定 |
| `register_call_back(event, fn)` / `un_register_call_back(event, fn)` | 注册/移除acquire、release、移除server等回调 |
| `require_acquire_fields()` / `require_release_fields()` | 声明路由所需的请求字段 |

`KVCAwareConfig.from_config`解析策略/collector配置；`route(...)`进行副本排名。Strategy、Collector、Parser、Transport和Store公开名称见附录，主要供扩展实现使用。

`KvEventsHttpServer`扩展verl的vLLM服务，增加KV-event端口分配和`get_kv_events_endpoints()`。它调用vLLM的`build_app`，HTTP路由由相应vLLM版本和模型能力提供，不应把继承得到的整个vLLM API都算作Uni-Agent自定义API。

## 10. 日志、观测与CLI

| API | 功能 |
|---|---|
| `sample_logging(log_id, log_path=...)` | 绑定本次执行的日志上下文与文件 |
| `sample_logging.from_context(context)` | 使用已有LogContext |
| `LogContext` / `get_current_log_context()` | 跨执行边界传递、读取日志上下文 |
| `rl_insight.metric_count/metric_gauge/metric_histogram` | 可选指标输出 |
| `rl_insight.trace_span` | 可选完成span输出 |
| `ENABLE_ENV` | rl-insight开关名`VERL_RL_INSIGHT_ENABLE` |

上述观测开关未启用或依赖不可用时可降级，不代表已经部署观测后端。

官方CLI示例包括`examples/inference/parallel_infer_api.py`（外部模型API评测）、`parallel_infer_verl.py`（verl rollout引擎路径）、`parallel_run_oracle.py`（gold控制）、`examples/gateway/debug_launcher.py`（独立Gateway调试）以及各task预处理模块。CLI参数属于脚本入口，不是HTTP端点。SDK/backend是否完整可用仍取决于安装依赖。

## 11. Sandbox接口入口

Uni-Agent公开的sandbox对象包括`SandboxConfig/Sandbox/SandboxBackend/ExecResult/ImageMap/LocalSandbox/build_sandbox`，并可通过registry扩展provider。其完整方法、provider差异、Docker REST、verdal/E2B REST与RPC在[Sandbox API独立文档](04-sandbox-and-verdal-api-reference.md)中列出。

<!-- API_INVENTORY_START -->

## 12. 完整公开名称索引（源码附录）

静态索引共记录 **238个类/函数声明、320个直接声明的方法（含构造器和选定生命周期方法）**。这些数字包括Sandbox；其详细索引在独立Sandbox文档。属性getter计入声明数，不是额外网络接口。

正文解释常用接入面；附录保留精确方法签名及源码首段docstring，包含实现层对象。名称公开不等于已承诺稳定SDK。签名中的self/cls不由调用方显式传入；异步方法需await；property按属性读取。

### 12.1 Gateway、adapter、session与轨迹

#### `uni_agent.gateway.adapters.anthropic`

**`anthropic_error_body`** — 构造对应协议的错误响应体。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/anthropic.py#L54)

```python
anthropic_error_body(status_code: int, message: str) -> dict[str, Any]
```

**`anthropic_build_response`** — 将生成结果封装成对应协议的JSON响应。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/anthropic.py#L115)

```python
anthropic_build_response(outcome: GenerationOutcome, *, model: str) -> dict[str, Any]
```

**`anthropic_stream_response`** — Synthesize an Anthropic Messages SSE stream from a completed outcome. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/anthropic.py#L128)

```python
anthropic_stream_response(outcome: GenerationOutcome, *, model: str) -> StreamingResponse
```

**`anthropic_to_internal`** — Lower an Anthropic Messages request into the internal request shape. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/anthropic.py#L520)

```python
anthropic_to_internal(payload: dict, *, base_sampling_params: dict, allowed_sampling_keys: frozenset[str]) -> InternalGenerationRequest
```

#### `uni_agent.gateway.adapters.openai`

**`openai_error_body`** — 构造对应协议的错误响应体。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/openai.py#L38)

```python
openai_error_body(status_code: int, message: str) -> dict[str, Any]
```

**`openai_build_response`** — 将生成结果封装成对应协议的JSON响应。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/openai.py#L78)

```python
openai_build_response(outcome: GenerationOutcome, *, model: str) -> dict[str, Any]
```

**`openai_stream_response`** — Synthesize an OpenAI chat.completion.chunk SSE stream from a completed outcome. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/openai.py#L99)

```python
openai_stream_response(outcome: GenerationOutcome, *, model: str) -> StreamingResponse
```

**`openai_to_internal`** — Lower an OpenAI chat-completions request into the internal request shape. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/openai.py#L197)

```python
openai_to_internal(payload: dict, *, base_sampling_params: dict, allowed_sampling_keys: frozenset[str]) -> InternalGenerationRequest
```

#### `uni_agent.gateway.adapters.types`

**`MalformedRequestError`** — Raised when adapter request lowering rejects a client payload. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L13)

继承：`ValueError`。

**`OpenAIChatCompletionFunction`** — ``tool_calls[i].function`` object inside an OpenAI chat message. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L19)

继承：`TypedDict`。

直接声明字段：`name`、`arguments`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatCompletionToolCall`** — One entry in an OpenAI assistant message's ``tool_calls`` array. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L26)

继承：`TypedDict`。

直接声明字段：`id`、`type`、`function`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatMessage`** — A single OpenAI chat-completion message. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L34)

继承：`TypedDict`。

直接声明字段：`role`、`content`、`name`、`tool_calls`、`tool_call_id`、`reasoning_content`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatCompletionTool`** — One entry in an OpenAI request ``tools`` array. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L49)

继承：`TypedDict`。

直接声明字段：`type`、`function`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatCompletionRequest`** — Incoming ``POST /v1/chat/completions`` request body shape. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L56)

继承：`TypedDict`。

直接声明字段：`model`、`messages`、`tools`、`tool_choice`、`stream`、`n`、`response_format`、`temperature`、`top_p`、`top_k`、`max_tokens`、`stop`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatCompletionUsage`** — Token usage block inside an OpenAI response. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L73)

继承：`TypedDict`。

直接声明字段：`prompt_tokens`、`completion_tokens`、`total_tokens`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatCompletionChoice`** — One entry in an OpenAI response ``choices`` array. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L81)

继承：`TypedDict`。

直接声明字段：`index`、`message`、`finish_reason`。完整类型/默认值见机器清单；继承字段见基类。

**`OpenAIChatCompletionResponse`** — Outgoing ``POST /v1/chat/completions`` response body shape. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L93)

继承：`TypedDict`。

直接声明字段：`id`、`object`、`created`、`model`、`choices`、`usage`。完整类型/默认值见机器清单；继承字段见基类。

**`AnthropicContentBlock`** — 实现层的数据/辅助声明；字段、类型与用途结合所属模块查阅。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L104)

继承：`TypedDict`。

直接声明字段：`type`、`text`、`source`、`id`、`name`、`input`、`tool_use_id`、`content`。完整类型/默认值见机器清单；继承字段见基类。

**`AnthropicMessage`** — 实现层的数据/辅助声明；字段、类型与用途结合所属模块查阅。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L115)

继承：`TypedDict`。

直接声明字段：`role`、`content`。完整类型/默认值见机器清单；继承字段见基类。

**`AnthropicRequest`** — 实现层的数据/辅助声明；字段、类型与用途结合所属模块查阅。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/adapters/types.py#L120)

继承：`TypedDict`。

直接声明字段：`model`、`messages`、`system`、`tools`、`tool_choice`、`stream`、`max_tokens`、`stop_sequences`、`temperature`、`top_p`、`top_k`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.gateway.config`

**`GatewayActorConfig`** — Model and session configuration forwarded into each gateway actor. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/config.py#L16)

直接声明字段：`tokenizer`、`processor`、`tool_parser_name`、`rollout_backend`、`enable_tool_parser_cache`、`hf_model_type`、`apply_chat_template_kwargs`、`mm_processor_kwargs`、`allowed_request_sampling_param_keys`、`vision_info_extractor`、`vision_info_extractor_kwargs`、`prompt_length`、`response_length`、`enable_last_assistant_rollback`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.gateway.gateway`

**`_GatewayActor`** — Ray actor implementation exposed as ``GatewayActor = ray.remote(...)``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/gateway.py#L54)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, config: GatewayActorConfig, backend)` | Create an actor with model codec configuration and backend client. |
| `async start(self) -> None` | Start the FastAPI server backing this gateway actor. |
| `async shutdown(self) -> None` | Stop the FastAPI server backing this gateway actor. |
| `async create_session(self, session_id: str, metadata: dict[str, Any] \| None=None, sampling_params: dict[str, Any] \| None=None) -> SessionHandle` | Create an actor-owned session and return its provider-compatible handle. |
| `async finalize_session(self, session_id: str) -> list[Trajectory]` | Finalize a session, remove it from the actor, and return its trajectories. |
| `async abort_session(self, session_id: str) -> None` | Abort a session and remove it from the actor if it still exists. |
| `async get_session_state(self, session_id: str) -> dict[str, Any]` | Return a snapshot of a live session's state. |

#### `uni_agent.gateway.manager`

**`GatewayManager`** — Owns gateway actors and routes sessions to them. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/manager.py#L18)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, llm_client: LLMServerClient, *, gateway_count: int, gateway_actor_config: GatewayActorConfig \| None=None)` | 构造对象；只列源码显式声明的构造器。 |
| `async create_session(self, session_id: str, **kwargs)` | Create a session on the least-loaded actor, record the route, and return its handle. |
| `async finalize_session(self, session_id: str)` | Finalize a session on its owning actor, release the route, and return its trajectories. |
| `async abort_session(self, session_id: str) -> None` | Abort a routed session on its owning actor and release the route. |
| `async shutdown(self) -> None` | Stop owned gateway actors and clear routing state. |

#### `uni_agent.gateway.session.codec`

**`initialize_generation_prompt`** — Initialize the token suffix inserted by ``add_generation_prompt=True``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/codec.py#L51)

```python
initialize_generation_prompt(processing_class, **apply_chat_template_kwargs) -> list[int]
```

**`MessageCodec`** — Model-scoped request codec used by gateway sessions. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/codec.py#L74)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, tokenizer, *, processor=None, vision_info_extractor=None, vision_info_extractor_kwargs: dict[str, Any] \| None=None, tool_parser_name: str \| None=None, rollout_backend: str \| None=None, enable_tool_parser_cache: bool=True, hf_model_type: str \| None=None, apply_chat_template_kwargs: dict[str, Any] \| None=None, mm_processor_kwargs: dict[str, Any] \| None=None)` | 构造对象；只列源码显式声明的构造器。 |
| `@property mm_processor_kwargs(self) -> dict[str, Any]` | Return processor kwargs shared by CT rendering and inference. |
| `@property generation_prompt(self) -> list[int]` | Return the configured chat template's generation-prompt token suffix. |
| `@property turn_separator(self) -> list[int]` | Return the configured chat template's inter-turn separator tokens. |
| `async extract_multi_modal_data(self, messages: list[dict[str, Any]]) -> tuple[list[Any] \| None, list[Any] \| None]` | Extract image and video inputs when a processor-backed request needs them. |
| `build_initial_tokens(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] \| None=None, image_data: list[Any] \| None=None, video_data: list[Any] \| None=None) -> list[int]` | Build the initial runtime token stream. |
| `merge_assistant_tokens(self, runtime_token_ids: list[int], assistant_token_ids: list[int], response_mask: list[int], response_logprobs: list[float] \| None=None, *, assistant_logprobs: list[float] \| None=None) -> tuple[list[int], list[int], list[float] \| None]` | Merge model-generated tokens and align response metadata. |
| `merge_context_tokens(self, previous_messages: list[dict[str, Any]], updated_messages: list[dict[str, Any]], runtime_token_ids: list[int], response_mask: list[int], response_logprobs: list[float] \| None=None, *, tools: list[dict[str, Any]] \| None=None, image_data: list[Any] \| None=None, video_data: list[Any] \| None=None) -> tuple[list[int], list[int], list[float] \| None]` | Merge appended context and align response metadata. |
| `async decode_response(self, response_ids: list[int], *, tools: list[dict[str, Any]] \| None=None, stop_reason: str \| None=None) -> tuple[dict[str, Any], str]` | Decode model output tokens into an assistant message and finish reason. |
| `canonicalize_message_for_prefix_comparison(self, message: dict[str, Any]) -> dict[str, Any]` | Canonicalize one message before session prefix comparison. |

#### `uni_agent.gateway.session.session`

**`SessionPhase`** — Lifecycle state for a gateway session. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L22)

继承：`str`、`Enum`。

**`TrajectoryBuffer`** — Mutable token buffer for the active trajectory under construction. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L37)

直接声明字段：`prompt_ids`、`response_ids`、`response_mask`、`response_logprobs`、`routed_experts`、`generation_versions`。完整类型/默认值见机器清单；继承字段见基类。

**`LastAssistantStart`** — Stable chain lengths captured immediately before its latest assistant. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L67)

直接声明字段：`response_ids_len`、`message_history_len`、`image_data_len`、`video_data_len`、`tip_hash`。完整类型/默认值见机器清单；继承字段见基类。

**`ChainState`** — One active linear trajectory chain in a gateway session. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L78)

直接声明字段：`chain_id`、`message_history`、`message_tip_hash`、`active_tool_schemas`、`buffer`、`image_data`、`video_data`、`last_assistant_start`、`updated_seq`。完整类型/默认值见机器清单；继承字段见基类。

**`MaterializedChain`** — A closed chain plus the ordering metadata needed at finalize. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L93)

直接声明字段：`trajectory`、`order_seq`。完整类型/默认值见机器清单；继承字段见基类。

**`EncodedData`** — Session-private data prepared before backend generation. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L101)

直接声明字段：`buffer`、`context_ids`、`sampling_params`、`messages`、`tools`、`image_data`、`video_data`、`mm_processor_kwargs`、`capacity_exhausted`、`chain_id`、`incoming_message_prefix_hashes`、`last_assistant_start`、`rollback_applied`、`rollback_dropped_trainable_tokens`。完整类型/默认值见机器清单；继承字段见基类。

**`GenerationOutcome`** — Business result returned by ``GatewaySession.run_generation``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L150)

直接声明字段：`assistant_msg`、`finish_reason`、`prompt_tokens`、`completion_tokens`。完整类型/默认值见机器清单；继承字段见基类。

**`GatewaySession`** — Behavior-bearing state container for one gateway session. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/session.py#L170)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, handle: SessionHandle, codec: MessageCodec, *, prompt_length: int \| None=None, response_length: int \| None=None, sampling_params: dict[str, Any] \| None=None, enable_last_assistant_rollback: bool=True, metadata: dict[str, Any] \| None=None)` | Create an active session bound to a handle and model codec. |
| `@property sampling_params(self) -> dict[str, Any]` | Return a copy of the trusted per-session sampling defaults. |
| `async run_generation(self, request: InternalGenerationRequest, backend) -> GenerationOutcome` | Run one provider-normalized generation request and return its business outcome. |
| `async finalize(self) -> list[Trajectory]` | Close the session and return its materialized token trajectories. |
| `async abort(self) -> None` | Abort the session and prevent further generation. |
| `snapshot_state(self) -> dict[str, Any]` | Return a JSON-serializable snapshot for actor state inspection. |

#### `uni_agent.gateway.session.types`

**`InternalGenerationRequest`** — Lowered request consumed by GatewaySession.run_generation. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/types.py#L13)

继承：`TypedDict`。

直接声明字段：`messages`、`tools`、`sampling_params`。完整类型/默认值见机器清单；继承字段见基类。

**`SessionHandle`** — Address returned to agent runners for a newly created gateway session. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/types.py#L27)

直接声明字段：`session_id`、`base_url`。完整类型/默认值见机器清单；继承字段见基类。

**`Trajectory`** — Token-level training trajectory produced when a gateway session finalizes. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/session/types.py#L41)

直接声明字段：`prompt_ids`、`response_ids`、`response_mask`、`response_logprobs`、`finished`、`reward_score`、`reward_metrics`、`num_turns`、`chain_id`、`routed_experts`、`multi_modal_data`、`extra_fields`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.gateway.utils`

**`normalize_tool_arguments`** — Keep JSON-object arguments as dicts and all other values as strings. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/gateway/utils.py#L9)

```python
normalize_tool_arguments(arguments: Any) -> dict[str, Any] | str
```

### 12.2 Agent、模型客户端与注册

#### `uni_agent.agents.base`

**`ModelConfig`** — The OpenAI-compatible LLM endpoint the agent's policy talks to, plus sampling knobs. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/base.py#L15)

继承：`BaseModel`。

直接声明字段：`base_url`、`api_key`、`model_name`、`temperature`、`top_p`、`top_k`、`max_total_tokens`、`max_tokens_per_turn`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `sampling_params(self) -> dict[str, float \| int]` | Return only sampling knobs explicitly configured by this Agent. |

**`AgentConfig`** — Base config for a registered agent. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/base.py#L62)

继承：`BaseModel`。

直接声明字段：`name`、`model`。完整类型/默认值见机器清单；继承字段见基类。

**`AgentResult`** — Artifacts one Agent produced for an episode. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/base.py#L74)

直接声明字段：`output`、`transcript`、`info`、`finished`。完整类型/默认值见机器清单；继承字段见基类。

**`Agent`** — A solver bound to an `AgentConfig`, runnable over a live sandbox. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/base.py#L91)

继承：`ABC`。

直接声明字段：`name`、`config_model`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, config: AgentConfig \| None=None) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod from_config(cls, config: AgentConfig) -> Agent` | Build an instance from its `AgentConfig` (override to remap fields). |
| `async run(self, *, sandbox: Sandbox, messages: list[dict[str, Any]], workdir: str \| None=None) -> AgentResult` | Solve the task described by ``messages`` inside the live ``sandbox``. |

#### `uni_agent.agents.claude_code.agent`

**`ClaudeCodeConfig`** — Black-box launch params for Claude Code (policy endpoint lives on `AgentConfig.model`). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/claude_code/agent.py#L64)

继承：`AgentConfig`。

直接声明字段：`name`、`max_turns`、`enable_web_tools`、`enable_subagents`、`disable_slash_commands`、`verbose`、`run_timeout`、`extra_args`、`extra_env`。完整类型/默认值见机器清单；继承字段见基类。

**`ClaudeCodeAgent`** — Black-box solver: launch the real Claude Code CLI in the sandbox against ``config.model``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/claude_code/agent.py#L91)

继承：`Agent`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self, *, sandbox: Sandbox, messages: list[dict[str, Any]], workdir: str \| None=None) -> AgentResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.agents.mem_agent.agent`

**`ContextTurnOutput`** — One model turn inside a MemAgent context segment. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mem_agent/agent.py#L44)

继承：`BaseModel`。

直接声明字段：`step_idx`、`response`、`prompt_tokens`、`completion_tokens`。完整类型/默认值见机器清单；继承字段见基类。

**`ContextStepOutput`** — One independently materialized MemAgent context segment. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mem_agent/agent.py#L53)

继承：`BaseModel`。

直接声明字段：`prompt_messages`、`messages`、`steps`、`reward`、`execution_time`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `set_prompt_messages(self, messages: list[dict[str, Any]]) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `set_messages(self, messages: list[dict[str, Any]]) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `set_execution_time(self, execution_time: float) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `add_step(self, step_output: ContextTurnOutput) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `set_reward(self, reward: float) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `get_reward(self) -> float` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`ContextManagerResult`** — Aggregate result of a context-managed MemAgent execution. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mem_agent/agent.py#L81)

继承：`BaseModel`。

直接声明字段：`run_id`、`execution_time`、`trajectory`、`final_state`、`total_steps`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `set_reward(self, reward: float) -> None` | Assign the final task reward to every context segment. |

**`MemAgentConfig`** — Configuration for chunked-context memory updates. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mem_agent/agent.py#L97)

继承：`AgentConfig`。

直接声明字段：`name`、`max_steps`、`max_memorization_length`、`max_chunks`、`max_final_response_length`。完整类型/默认值见机器清单；继承字段见基类。

**`MemAgent`** — Read long input in chunks and carry only a compact memory between contexts. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mem_agent/agent.py#L108)

继承：`Agent`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, config: MemAgentConfig \| None=None) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async context_session(self) -> AsyncIterator[None]` | Initialize and close the model runtime used by MemAgent. |
| `build_agent_result(self) -> AgentResult` | Convert the completed MemAgent context session into an Agent result. |
| `async update_context(self, messages: list[dict[str, Any]]) -> None` | Finalize the current segment and continue from a newly built context. |
| `async step(self, sampling_params: dict[str, Any] \| None=None) -> ContextTurnOutput` | Run one model call in the active context segment. |
| `get_global_step_idx(self) -> int` | Return the number of model calls across all context segments. |
| `get_current_step_idx(self) -> int` | Return the number of model calls in the current context segment. |
| `get_current_context_step(self) -> ContextStepOutput` | Return the segment currently being constructed. |
| `async run(self, *, sandbox: Sandbox, messages: list[dict[str, Any]], workdir: str \| None=None, raw_data: dict[str, Any] \| None=None) -> AgentResult` | Run the MemAgent policy using explicit context-management calls. |

#### `uni_agent.agents.mini_swe_agent.agent`

**`build_agent_command`** — Build the shell command that runs ``run_agent.py`` inside the sandbox. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mini_swe_agent/agent.py#L32)

```python
build_agent_command(*, config_b64: str, conda_env: str='testbed', tool_python: str, run_agent_script: str) -> str
```

**`parse_agent_result`** — Parse the result JSON from ``run_agent.py``'s stdout. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mini_swe_agent/agent.py#L63)

```python
parse_agent_result(stdout: str) -> dict[str, Any]
```

**`MiniSweAgentConfig`** — Black-box launch params for mini-swe-agent (endpoint lives on `AgentConfig.model`). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mini_swe_agent/agent.py#L85)

继承：`AgentConfig`。

直接声明字段：`name`、`step_limit`、`run_timeout`、`conda_env`、`tool_python`、`run_agent_script`。完整类型/默认值见机器清单；继承字段见基类。

**`MiniSweAgentAgent`** — Black-box solver: launch mini-swe-agent in the sandbox against ``config.model``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/mini_swe_agent/agent.py#L105)

继承：`Agent`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self, *, sandbox: Sandbox, messages: list[dict[str, Any]], workdir: str \| None=None) -> AgentResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.agents.react.agent`

**`ReActConfig`** — White-box launch params: host-side tools + step / timeout budgets. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/react/agent.py#L26)

继承：`AgentConfig`。

直接声明字段：`name`、`tools`、`max_steps`、`action_timeout`、`timeout_budget`。完整类型/默认值见机器清单；继承字段见基类。

**`ReActAgent`** — White-box solver: framework loop + host-side tools over an OpenAI endpoint. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/react/agent.py#L52)

继承：`Agent`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self, *, sandbox: Sandbox, messages: list[dict[str, Any]], workdir: str \| None=None) -> AgentResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |
| `async step(self, cfg: ReActConfig, model: OpenAICompatibleChatModel, toolbox: Toolbox, transcript: list[dict[str, Any]], info: dict[str, Any]) -> str` | Run one turn: query the policy, record its message, run its tool calls. |

#### `uni_agent.agents.react.model`

**`OpenAICompatibleChatModel`** — One-shot chat client against an OpenAI-compatible server (e.g. vLLM / SGLang). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/react/model.py#L29)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, base_url: str, *, api_key: str='EMPTY', model_name: str \| None=None, sampling_params: dict[str, Any] \| None=None, tools_schemas: list[dict] \| None=None, timeout: float \| None=None, max_retries: int=2)` | 构造对象；只列源码显式声明的构造器。 |
| `async aclose(self) -> None` | Close the underlying session (idempotent; safe if it was never opened). |
| `async __aenter__(self) -> OpenAICompatibleChatModel` | 进入异步上下文；生命周期由该类实现。 |
| `async __aexit__(self, *exc_info: object) -> None` | 退出异步上下文并清理。 |
| `async query(self, messages: list[dict[str, Any]], *, sampling_params: dict[str, Any] \| None=None) -> tuple[str, list[dict], dict[str, Any]]` | Run one chat-completion call. |

#### `uni_agent.agents.registry`

**`register_agent`** — Class decorator: register an `Agent` under ``name`` (and stamp ``cls.name``). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/registry.py#L29)

```python
register_agent(name: str) -> Callable[[type[Agent]], type[Agent]]
```

**`get_agent_cls`** — Return a registered agent class by name, importing its module on first use. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/registry.py#L55)

```python
get_agent_cls(name: str) -> type[Agent]
```

**`build_agent`** — Instantiate the agent named by ``config.name`` from its config. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agents/registry.py#L65)

```python
build_agent(config: AgentConfig) -> Agent
```

### 12.3 Task、预处理与reward

#### `uni_agent.tasks.base`

**`TaskConfig`** — Base task config: only the fields every task shares (the model lives on `agent`, not here). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/base.py#L30)

继承：`BaseModel`。

直接声明字段：`name`、`sandbox`、`agent`、`prompt`、`prompt_template`、`metadata`。完整类型/默认值见机器清单；继承字段见基类。

**`TaskResult`** — Outcome of one task episode. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/base.py#L92)

直接声明字段：`reward`、`accuracy`、`finished`、`extra_info`。完整类型/默认值见机器清单；继承字段见基类。

**`Task`** — A task family: turns a `TaskConfig` into the runnable lower layers. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/base.py#L118)

继承：`ABC`。

直接声明字段：`name`、`config_model`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, config: TaskConfig) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async run(self) -> TaskResult` | Run one episode and return its score. |
| `build_sandbox(self) -> Sandbox` | Instantiate the execution sandbox from `TaskConfig.sandbox`. |
| `build_agent(self) -> Agent` | Instantiate the solving agent from `TaskConfig.agent`. |

#### `uni_agent.tasks.config`

**`render_prompt_template`** — Render text-only chat messages from direct Task metadata fields. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/config.py#L29)

```python
render_prompt_template(metadata: object, prompt_template: object) -> list[dict[str, Any]]
```

**`TaskConfigResolver`** — Route and compose Task Config defaults, sample values, and runtime bindings. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/config.py#L103)

直接声明字段：`defaults_by_name`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@classmethod from_file(cls, path: str) -> TaskConfigResolver` | Build a resolver from a YAML mapping or list keyed by Task ``name``. |
| `resolve(self, sample_config: Mapping[str, Any], *, runtime_model: Mapping[str, Any] \| None=None) -> dict[str, Any]` | Resolve one sample using Task Config → Sample Config → runtime model. |

#### `uni_agent.tasks.harbor.preprocess`

**`download_dataset`** — Download and export a Harbor dataset to a persistent local directory. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/preprocess.py#L15)

```python
download_dataset(dataset_ref: str, output_dir: Path | str) -> Path
```

**`build_harbor_dataset`** — Build a dataset that points to Harbor tasks on the shared local filesystem. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/preprocess.py#L33)

```python
build_harbor_dataset(task_root: Path | str, *, dataset_name: str | None=None, max_instances: int | None=None) -> Dataset
```

**`main`** — 模块命令行入口。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/preprocess.py#L85)

```python
main() -> None
```

#### `uni_agent.tasks.harbor.reward`

**`task_result_from_harbor_trial`** — Translate Harbor's persisted TrialResult into Uni-Agent's result shape. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/reward.py#L59)

```python
task_result_from_harbor_trial(payload: dict[str, Any], *, trial_dir: Path, cli_exit_code: int, stdout: str, stderr: str, elapsed: float) -> TaskResult
```

#### `uni_agent.tasks.harbor.task`

**`HarborAgentConfig`** — Harbor agent name and model endpoint. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L46)

继承：`AgentConfig`。

直接声明字段：`name`、`kwargs`、`timeout_sec`。完整类型/默认值见机器清单；继承字段见基类。

**`HarborTaskConfig`** — Configuration for one evaluation-only Harbor CLI trial. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L54)

继承：`TaskConfig`。

直接声明字段：`name`、`sandbox`、`harbor_env`、`agent`、`timeout_multiplier`、`override_cpus`、`override_memory_mb`。完整类型/默认值见机器清单；继承字段见基类。

**`build_harbor_trial_command`** — Build the argv for one collision-safe Harbor trial. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L99)

```python
build_harbor_trial_command(config: HarborTaskConfig, *, trial_name: str, trials_dir: Path) -> list[str]
```

**`build_harbor_process_env`** — Expose the runtime model endpoint under compatible Harbor aliases. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L142)

```python
build_harbor_process_env(config: HarborTaskConfig) -> dict[str, str] | None
```

**`HarborCLIResult`** — 实现层的数据/辅助声明；字段、类型与用途结合所属模块查阅。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L156)

直接声明字段：`exit_code`、`stdout`、`stderr`。完整类型/默认值见机器清单；继承字段见基类。

**`run_harbor_cli`** — Run Harbor directly on the host and capture its terminal output. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L162)

```python
async run_harbor_cli(command: list[str], *, env: dict[str, str] | None=None) -> HarborCLIResult
```

**`HarborTask`** — 该任务家族的运行与评测实现。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/harbor/task.py#L185)

继承：`Task`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self) -> TaskResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tasks.hotpotqa.preprocess`

**`context_to_text`** — Normalize a HotpotQA context, while retaining document titles. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/preprocess.py#L21)

```python
context_to_text(context: Any) -> str
```

**`split_context_into_token_chunks`** — Split a context into decoded, token-bounded chunks. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/preprocess.py#L43)

```python
split_context_into_token_chunks(context: Any, *, tokenizer: Any, chunk_size: int) -> list[str]
```

**`process_example`** — Convert one canonical HotpotQA example to a serialized Task Config. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/preprocess.py#L55)

```python
process_example(example: dict[str, Any], *, tokenizer: Any, chunk_size: int) -> dict[str, Any]
```

**`build_hotpotqa`** — Load and preprocess one HotpotQA split. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/preprocess.py#L84)

```python
build_hotpotqa(*, tokenizer_path: str, split: str, context_chunk_size: int=DEFAULT_CONTEXT_CHUNK_SIZE, max_instances: int | None=None)
```

#### `uni_agent.tasks.hotpotqa.reward`

**`compute_score`** — Score the final boxed answer with the original token-level LCS metric. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/reward.py#L6)

```python
compute_score(solution: str, ground_truths: list[str]) -> float
```

**`remove_boxed`** — 剥离答案的boxed外层标记。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/reward.py#L42)

```python
remove_boxed(value: str) -> str
```

**`last_boxed_only_string`** — 提取最后一个boxed答案片段。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/reward.py#L55)

```python
last_boxed_only_string(value: str) -> str | None
```

#### `uni_agent.tasks.hotpotqa.task`

**`HotpotQATaskConfig`** — 配置数据模型；字段与继承关系见下方及机器清单。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/task.py#L14)

继承：`TaskConfig`。

直接声明字段：`name`、`ground_truth`。完整类型/默认值见机器清单；继承字段见基类。

**`HotpotQATask`** — Score a HotpotQA answer and broadcast its reward to every context chain. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/hotpotqa/task.py#L23)

继承：`Task`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self) -> TaskResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tasks.registry`

**`register_task`** — Class decorator: register a `Task` under ``name`` (and stamp ``cls.name``). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/registry.py#L32)

```python
register_task(name: str) -> Callable[[type[Task]], type[Task]]
```

**`get_task_cls`** — Return a registered task class by name, importing its module on first use. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/registry.py#L45)

```python
get_task_cls(name: str) -> type[Task]
```

**`get_task`** — Build a task from a `TaskConfig` or a flat config mapping. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/registry.py#L55)

```python
get_task(config: TaskConfig | Mapping[str, Any]) -> Task
```

#### `uni_agent.tasks.swe_bench.preprocess`

**`get_image_name`** — Canonical open-source image ref (mirrors swebench's ``instance_image_key``). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench/preprocess.py#L20)

```python
get_image_name(instance_id: str) -> str
```

**`build_swe_bench_verified`** — 构建该模块对应的SWE评测数据集。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench/preprocess.py#L28)

```python
build_swe_bench_verified(max_instances: int | None=None)
```

#### `uni_agent.tasks.swe_bench.reward`

**`compute_reward`** — Score one instance; ``eval_timeout`` (s) comes from the task config. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench/reward.py#L107)

```python
async compute_reward(metadata, sandbox, eval_timeout: float=600.0) -> dict
```

#### `uni_agent.tasks.swe_bench.task`

**`SWEBenchTaskConfig`** — 配置数据模型；字段与继承关系见下方及机器清单。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench/task.py#L16)

继承：`TaskConfig`。

直接声明字段：`name`、`run_oracle_solution`、`eval_timeout`。完整类型/默认值见机器清单；继承字段见基类。

**`SWEBenchTask`** — 该任务家族的运行与评测实现。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench/task.py#L29)

继承：`Task`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self) -> TaskResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tasks.swe_bench_multilingual.preprocess`

**`get_image_name`** — Return the canonical image ref, mirroring swebench's instance image key. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench_multilingual/preprocess.py#L31)

```python
get_image_name(instance_id: str) -> str
```

**`build_swe_bench_multilingual`** — 构建该模块对应的SWE评测数据集。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench_multilingual/preprocess.py#L36)

```python
build_swe_bench_multilingual(max_instances: int | None=None)
```

#### `uni_agent.tasks.swe_bench_multilingual.reward`

**`compute_reward`** — Run the official multilingual evaluation flow inside ``sandbox``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench_multilingual/reward.py#L142)

```python
async compute_reward(metadata: dict, sandbox, eval_timeout: float=1800.0) -> dict
```

#### `uni_agent.tasks.swe_bench_multilingual.task`

**`SWEBenchMultilingualTaskConfig`** — 配置数据模型；字段与继承关系见下方及机器清单。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench_multilingual/task.py#L26)

继承：`TaskConfig`。

直接声明字段：`name`、`run_oracle_solution`、`eval_timeout`。完整类型/默认值见机器清单；继承字段见基类。

**`SWEBenchMultilingualTask`** — 该任务家族的运行与评测实现。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_bench_multilingual/task.py#L40)

继承：`Task`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self) -> TaskResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tasks.swe_rebench.preprocess`

**`get_image_name`** — Canonical open-source image ref for a swe-rebench instance. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/preprocess.py#L15)

```python
get_image_name(instance_id: str) -> str
```

**`build_swe_rebench`** — 构建该模块对应的SWE评测数据集。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/preprocess.py#L24)

```python
build_swe_rebench(max_instances: int | None=None)
```

#### `uni_agent.tasks.swe_rebench.reward`

**`parse_log_pytest`** — Parse test logs from the PyTest framework into a test-case -> status map. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/reward.py#L70)

```python
parse_log_pytest(log: str) -> dict[str, str]
```

**`parse_log_pytest_v2`** — Parse PyTest logs (later versions), stripping control codes first. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/reward.py#L84)

```python
parse_log_pytest_v2(log: str) -> dict[str, str]
```

**`compute_reward`** — Score one instance; ``eval_timeout`` (s) comes from the task config. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/reward.py#L160)

```python
async compute_reward(metadata, sandbox, eval_timeout: float=600.0) -> dict
```

#### `uni_agent.tasks.swe_rebench.task`

**`SWEREBenchTaskConfig`** — 配置数据模型；字段与继承关系见下方及机器清单。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/task.py#L64)

继承：`TaskConfig`。

直接声明字段：`name`、`run_oracle_solution`、`eval_timeout`。完整类型/默认值见机器清单；继承字段见基类。

**`SWEREBenchTask`** — 该任务家族的运行与评测实现。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/swe_rebench/task.py#L77)

继承：`Task`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self) -> TaskResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tasks.terminal_bench.preprocess`

**`BenchmarkSpec`** — 实现层的数据/辅助声明；字段、类型与用途结合所属模块查阅。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L38)

直接声明字段：`version`、`dataset`、`harbor_ref`、`expected_tasks`。完整类型/默认值见机器清单；继承字段见基类。

**`parse_dockerfile_workdir`** — Return the final literal WORKDIR declared by a Dockerfile. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L86)

```python
parse_dockerfile_workdir(dockerfile: Path) -> str | None
```

**`pack_directory_base64`** — Create a deterministic base64 tarball while preserving executable bits. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L109)

```python
pack_directory_base64(source_dir: Path) -> str
```

**`discover_task_dirs`** — Return task directories exported under a dataset root. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L147)

```python
discover_task_dirs(dataset_dir: Path | str, *, expected_task_count: int | None=None) -> list[Path]
```

**`build_task_row`** — Build one provider-agnostic Uni-Agent sample. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L160)

```python
build_task_row(task_dir: Path, *, benchmark: BenchmarkSpec=BENCHMARKS[DEFAULT_VERSION]) -> dict[str, Any]
```

**`build_terminal_bench`** — Build a Hugging Face Dataset for a configured Terminal-Bench release. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L255)

```python
build_terminal_bench(version: str=DEFAULT_VERSION, *, max_instances: int | None=None)
```

**`main`** — 模块命令行入口。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/preprocess.py#L278)

```python
main() -> None
```

#### `uni_agent.tasks.terminal_bench.reward`

**`parse_json_mapping`** — Parse a JSON object stored in a provider-agnostic parquet field. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/reward.py#L23)

```python
parse_json_mapping(raw: Any, *, field: str) -> dict[str, Any]
```

**`resolve_env_mapping`** — Resolve full-value ``${VAR}`` and ``${VAR:-default}`` references. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/reward.py#L33)

```python
resolve_env_mapping(values: dict[str, Any]) -> dict[str, str]
```

**`install_archive`** — Extract one parquet-embedded task archive into the sandbox. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/reward.py#L53)

```python
async install_archive(sandbox: SandboxBackend, encoded_archive: str, *, target_dir: str) -> None
```

**`parse_reward_files`** — Parse verifier output and select its primary reward. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/reward.py#L90)

```python
parse_reward_files(reward_json: str | None, reward_text: str | None) -> tuple[float, dict[str, float]]
```

**`compute_reward`** — Upload official tests, execute the verifier, and parse its reward. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/reward.py#L123)

```python
async compute_reward(metadata: dict[str, Any], sandbox: SandboxBackend) -> dict[str, Any]
```

#### `uni_agent.tasks.terminal_bench.task`

**`TerminalBenchTaskConfig`** — 配置数据模型；字段与继承关系见下方及机器清单。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/task.py#L21)

继承：`TaskConfig`。

直接声明字段：`name`、`run_oracle_solution`。完整类型/默认值见机器清单；继承字段见基类。

**`build_terminal_bench_sandbox_config`** — Apply provider-specific task environment settings to a SandboxConfig. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/task.py#L29)

```python
build_terminal_bench_sandbox_config(config: SandboxConfig, metadata: dict[str, Any]) -> SandboxConfig
```

**`TerminalBenchTask`** — 该任务家族的运行与评测实现。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tasks/terminal_bench/task.py#L104)

继承：`Task`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self) -> TaskResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

### 12.4 Tool、编辑器与持久shell

#### `uni_agent.tools.base`

**`ToolResult`** — One tool call's normalized result: the ``text`` the model sees plus a ``status`` for the loop's counters. ``status`` is set by whoever knows it -- `Toolbox.call` for bad calls / tool errors, the tool itself for a ``"timeout"`` -- and defaults to ``"ok"``. `to_observation` renders the next-turn content (``str(result)`` gives just the text). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L34)

直接声明字段：`text`、`status`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `to_observation(self, max_length: int=100000) -> str` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`ToolError`** — A user-facing *runtime* failure while executing (bad path, refused command). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L61)

继承：`Exception`。

**`ToolCallFormatError`** — A malformed tool call, caught *before* the tool runs (unknown function, or arguments that don't decode to a JSON object). `Toolbox.call` returns the message to the policy as an observation so it can self-correct; the wording follows the ``"Invalid action: ..."`` convention the policy is trained on. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L70)

继承：`Exception`。

**`build_function_schema`** — Build an OpenAI-compatible function schema from a Pydantic args model. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L121)

```python
build_function_schema(name: str, description: str, model: type[BaseModel]) -> dict
```

**`Tool`** — A host-side tool: a schema plus an async `run` over the sandbox. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L133)

继承：`abc.ABC`。

直接声明字段：`name`、`description`、`args_model`、`config_model`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, sandbox: SandboxBackend, **kwargs: Any)` | 构造对象；只列源码显式声明的构造器。 |
| `schema(self) -> dict` | Return the OpenAI function schema shown to the model. |
| `@classmethod config_schema(cls) -> dict \| None` | JSON schema for this tool's construction kwargs, or ``None`` if it has none. |
| `async run(self, args: dict[str, Any], *, timeout: float \| None=None) -> ToolResult` | Execute the call and return a `ToolResult`. |
| `async start(self) -> None` | Eagerly set up state (open channels); no-op by default. |
| `async close(self) -> None` | Release any state the tool holds (open channels). No-op by default. |

**`register_tool`** — Class decorator: register ``cls`` under registry key ``name``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L201)

```python
register_tool(name: str)
```

**`get_tool`** — Instantiate a registered tool by name, bound to ``sandbox``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L222)

```python
get_tool(name: str, sandbox: SandboxBackend, **kwargs: Any) -> Tool
```

**`Toolbox`** — A set of tool instances bound to one sandbox for a rollout. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/base.py#L233)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, tools: list[Tool])` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod from_specs(cls, specs: list[dict[str, Any]], *, sandbox: SandboxBackend) -> Toolbox` | Build a toolbox from ``{name, ...kwargs}`` config entries bound to ``sandbox``. |
| `@classmethod all(cls, *, sandbox: SandboxBackend) -> Toolbox` | Build a toolbox from every registered tool, each bound to ``sandbox``. |
| `names(self) -> list[str]` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `schemas(self) -> list[dict]` | OpenAI function schemas for every tool (pass straight to the model). |
| `async start(self) -> None` | Eagerly set up every tool once, front-loading first-use cost (no retry). |
| `async __aenter__(self, retry: int=3, timeout: float=60.0) -> Toolbox` | Enter a rollout: start every tool (retrying transient failures) and return the ready toolbox. If a tool can't be started, tools already started are closed before the error propagates; `close` runs again on normal exit. |
| `async __aexit__(self, *exc_info: object) -> bool` | 退出异步上下文并清理。 |
| `async entered(self, **start_kwargs: Any) -> AsyncIterator[Toolbox]` | Parametrized ``async with``: same lifecycle as ``async with toolbox``, but forwards ``retry`` / ``timeout`` to `__aenter__` (the bare ``async with`` can't pass args):: |
| `async call(self, name: str, args: dict[str, Any] \| str \| None=None, *, timeout: float \| None=None) -> ToolResult` | Dispatch one tool call, returning the `ToolResult` for the model. |
| `async close(self) -> None` | Close every tool (release open channels); never raises. |

#### `uni_agent.tools.edit_file`

**`StrReplaceEditorArguments`** — 模型工具调用的参数schema。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/edit_file.py#L47)

继承：`BaseModel`。

直接声明字段：`command`、`path`、`file_text`、`old_str`、`new_str`、`insert_line`、`view_range`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@classmethod parse_json_view_range(cls, value: Any) -> Any` | Accept a JSON-encoded list emitted by an otherwise valid tool call. |

**`EditFileTool`** — 该工具的执行实现；模型可见参数见正文。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/edit_file.py#L100)

继承：`Tool`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, sandbox: SandboxBackend, **kwargs: Any) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async run(self, args: dict[str, Any], *, timeout: float \| None=None) -> ToolResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tools.finish`

**`FinishArguments`** — 模型工具调用的参数schema。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/finish.py#L22)

继承：`BaseModel`。

直接声明字段：`answer`。完整类型/默认值见机器清单；继承字段见基类。

**`FinishTool`** — 该工具的执行实现；模型可见参数见正文。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/finish.py#L27)

继承：`Tool`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self, args: dict[str, Any], *, timeout: float \| None=None) -> ToolResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

#### `uni_agent.tools.shell`

**`CommandResult`** — Outcome of one command run in a shell session. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L33)

直接声明字段：`command_id`、`command`、`exit_code`、`stdout`、`stderr`、`start_time`、`end_time`、`timed_out`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@property duration(self) -> float` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`Shell`** — Minimal session surface used by `ShellTool`. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L51)

继承：`Protocol`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async start(self) -> None` | 启动该对象负责的资源/执行环境。 |
| `async run(self, command: str, *, timeout: float=120.0) -> CommandResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |
| `async close(self) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`SandboxShell`** — `Shell` backed by sandbox ``open_shell()`` (native provider session). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L61)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, handle: Any)` | 构造对象；只列源码显式声明的构造器。 |
| `async start(self) -> None` | 启动该对象负责的资源/执行环境。 |
| `async run(self, command: str, *, timeout: float=120.0) -> CommandResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |
| `async close(self) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`open_shell_session`** — Prefer a native shell; fall back to tmux-over-exec. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L107)

```python
async open_shell_session(backend: SandboxBackend, *, env: dict[str, str] | None=None, width: int=120, height: int=40) -> Shell
```

**`TmuxShell`** — `Shell` backed by a detached tmux session, driven via one-shot exec. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L160)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, backend: SandboxBackend, *, session_id: str \| None=None, width: int=120, height: int=40, shell: str='bash', env: dict[str, str] \| None=None)` | 构造对象；只列源码显式声明的构造器。 |
| `async start(self) -> None` | 启动该对象负责的资源/执行环境。 |
| `async close(self) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async observe(self) -> ToolResult` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async start_command(self, command: str) -> int` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async poll(self, command_id: int) -> int \| None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async run(self, command: str, *, timeout: float=120.0) -> CommandResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |
| `async send_keys(self, keys: str \| list[str]) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async interrupt(self, command_id: int \| None=None) -> int \| None` | Send Ctrl-C, then suspend and kill the current job if it stays alive. |
| `async capture_pane(self, *, entire: bool=False) -> str` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async resize(self, *, width: int, height: int) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`ShellArguments`** — 模型工具调用的参数schema。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L397)

继承：`BaseModel`。

直接声明字段：`command`。完整类型/默认值见机器清单；继承字段见基类。

**`ShellToolConfig`** — Construction kwargs for the shell tool (the ``shell`` entry's kwargs). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L401)

继承：`BaseModel`。

直接声明字段：`env_vars`、`command_timeout`、`width`、`height`。完整类型/默认值见机器清单；继承字段见基类。

**`ShellTool`** — 该工具的执行实现；模型可见参数见正文。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/shell.py#L419)

继承：`Tool`。

直接声明字段：`config`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, sandbox: SandboxBackend, **kwargs: Any) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async start(self) -> None` | 启动该对象负责的资源/执行环境。 |
| `async run(self, args: dict[str, Any], *, timeout: float \| None=None) -> ToolResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |
| `async close(self) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

#### `uni_agent.tools.submit`

**`SubmitArguments`** — 模型工具调用的参数schema。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/submit.py#L23)

继承：`BaseModel`。

**`SubmitTool`** — 该工具的执行实现；模型可见参数见正文。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/tools/submit.py#L30)

继承：`Tool`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async run(self, args: dict[str, Any], *, timeout: float \| None=None) -> ToolResult` | 执行该对象的运行逻辑；具体返回类型见签名。 |

### 12.5 训练框架适配与后处理

#### `uni_agent.framework.base`

**`AgentRunner`** — Callable that executes one agent episode against a Framework-owned Gateway session. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/base.py#L13)

继承：`Protocol`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async __call__(self, *, session: SessionHandle, raw_prompt: object, sample_index: int, **sample_runner_kwargs: object) -> TaskResult \| None` | 实现可调用协议；完整参数见签名。 |

**`AgentFramework`** — Abstract base for trainer-driven agent frameworks. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/base.py#L26)

继承：`ABC`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@classmethod from_config(cls, *, config, **kwargs) -> AgentFramework` | 按配置构造该类。 |
| `async generate_sequences(self, prompts: TensorDict) -> None` | Run agent sessions and write finalized trajectories to TransferQueue. |

#### `uni_agent.framework.entry`

**`build_gateway_manager`** — Spawn the gateway actor pool (driver-side, driver-owned) and return its manager. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/entry.py#L32)

```python
build_gateway_manager(*, config, llm_client) -> GatewayManager
```

**`build_agent_framework`** — Wire the configured framework subclass over an injected gateway manager. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/entry.py#L67)

```python
build_agent_framework(*, config, gateway_manager, reward_loop_worker_handles=None) -> AgentFramework
```

**`AgentFrameworkWorker`** — Ray actor host: initializes TQ in this process and owns one AgentFramework. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/entry.py#L88)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, *, config, gateway_manager, reward_loop_worker_handles=None) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async generate_sequences(self, prompts) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`AgentFrameworkRolloutAdapter`** — Trainer-facing adapter satisfying the `agent_loop_manager_class` contract. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/entry.py#L108)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod create(cls, *, config, llm_client, teacher_client=None, reward_loop_worker_handles=None, **_) -> AgentFrameworkRolloutAdapter` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `generate_sequences(self, prompts) -> None` | Submit a TQ batch generation task without waiting for rollout results. |
| `generate_sequences_and_wait(self, prompts) -> None` | Blocking variant of `generate_sequences` for standalone (non-trainer) runs. |

#### `uni_agent.framework.framework`

**`GatewayAgentFramework`** — Reference AgentFramework implementation for Gateway-backed agent loops. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/framework.py#L293)

继承：`AgentFramework`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, gateway_manager, *, runner_registry: dict[str, _RunnerConfig], reward_loop_worker_handles=None, custom_reward_function_configured: bool=False, processor=None, rollout_config=None, log_dir: str \| None=None, mask_unfinished_episode: bool=False, trajectory_postprocessor: TrajectoryPostprocessor \| None=None, trajectory_postprocessor_kwargs: dict[str, object] \| None=None)` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod from_config(cls, *, config, gateway_manager, processor=None, reward_loop_worker_handles=None) -> GatewayAgentFramework` | 按配置构造该类。 |
| `async generate_sequences(self, prompts: TensorDict) -> None` | Run rollout-manager generation and write outputs into TransferQueue. |

#### `uni_agent.framework.multi_modal_postprocess`

**`compute_multi_modal_inputs`** — Return processor-produced multimodal tensors for a single sample. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/multi_modal_postprocess.py#L31)

```python
compute_multi_modal_inputs(processor, input_ids: torch.Tensor, multi_modal_data: dict[str, Any] | None, mm_processor_kwargs: dict[str, Any] | None=None) -> dict[str, torch.Tensor]
```

**`compute_position_ids`** — Return text-only or multimodal-aware position ids for a single sample. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/multi_modal_postprocess.py#L67)

```python
compute_position_ids(processor, input_ids: torch.Tensor, attention_mask: torch.Tensor, multi_modal_inputs: dict[str, torch.Tensor]) -> torch.Tensor
```

#### `uni_agent.framework.task_runner`

**`score_from_runner_result`** — Adapt the managed Runner result for a VERL RewardLoopWorker scorer. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/task_runner.py#L78)

```python
score_from_runner_result(*, data_source: str, solution_str: str, ground_truth: object, extra_info: dict[str, Any], **_reward_manager_kwargs: Any) -> dict[str, int | float | bool]
```

**`run_task`** — Resolve the sample's task, run it against ``session``, and return its result. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/framework/task_runner.py#L101)

```python
async run_task(*, session: SessionHandle, tools_kwargs: dict[str, Any] | None=None, raw_prompt: Any=None, sample_index: int | None=None, task_config_path: str | None=None, api_key: str='EMPTY', model_name: str | None=None, **_: Any) -> TaskResult
```

### 12.6 路由、collector、store和策略

#### `uni_agent.agent_aware_router.balancer`

**`KVCAwareBalancer`** — Pure-framework router shell. See module docstring. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/balancer.py#L47)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, servers: dict[str, Any], config: Optional[dict]=None, provider_factory: Callable[..., Any]=None) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `register_call_back(self, event: str, fn: Callable) -> None` | Append ``fn`` to the listeners for ``event``. |
| `un_register_call_back(self, event: str, fn: Callable) -> None` | Remove ``fn`` from ``event``'s callback list (idempotent). |
| `get_all_servers(self) -> list[str]` | List all active server ids. |
| `get_status(self) -> dict` | Construction + routing snapshot for debugging. |
| `release_server(self, server_id: str, request_id: str \| None=None) -> None` | Release a server after a request completes; fires ``on_release``. |
| `acquire_server(self, request_id: str, prompt_ids: list[int] \| None=None) -> tuple[str, Any]` | Delegate to ``route()`` for a best-first ranking, return ``(top, handle)``. |
| `require_acquire_fields(self) -> list[str]` | ``generate()`` kwargs this router consumes at acquire time. |
| `require_release_fields(self) -> list[str]` | Identity fields this router consumes at release time. |
| `clear_sticky_cache(self) -> dict` | Drop every sticky binding so returning sessions re-route (verl #7115). |
| `get_total_inflight(self) -> int` | Total in-flight requests across the pool (verl #7115 drain polling). |
| `add_servers(self, servers: dict[str, Any]) -> None` | Bulk-add servers to the pool (provider is keyed by init-time addresses, untouched here). |
| `remove_servers(self, server_ids: list[str]) -> None` | Bulk-remove servers; fires ``on_servers_removed`` to invalidate sticky bindings. |

#### `uni_agent.agent_aware_router.collectors.collector`

**`Collector`** — Unified collector — composes Transport + Parser. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/collector.py#L87)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, transport: Transport, parser: Parser) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `start(self) -> None` | Start the collector — launch event-loop thread and subscribe. |
| `stop(self) -> None` | Stop the collector — cancel tasks, drain cleanup, stop event-loop thread. |

**`get_collector`** — Create a Collector by name — one place does both composition and config binding. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/collector.py#L415)

```python
get_collector(name: str, collectors_config: CollectorConfig, server_addresses: dict[str, str] | None=None, kv_event_endpoints: dict[str, list[str]] | None=None, balancer_handler=None) -> Collector
```

#### `uni_agent.agent_aware_router.collectors.parse.base`

**`KVCacheUpdate`** — Mutable accumulator for KVCacheStore updates, built by the parser. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/base.py#L34)

直接声明字段：`node_id`、`add_blocks`、`remove_blocks`、`clear_all`、`block_size`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `add(self, layer: Layer, block_hashes: list[str]) -> None` | Fold stored blocks into ``add_blocks`` under ``layer``. |
| `remove(self, layer: Layer, block_hashes: list[str]) -> None` | Fold removed blocks into ``remove_blocks`` under ``layer``. |
| `clear(self) -> None` | Mark the replica for a full block clear. |
| `set_block_size(self, size: int) -> None` | Set the learned block size (first BlockStored wins; set by the parser). |

**`MetricsUpdate`** — Structured update command for PerReplicaStore. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/base.py#L73)

直接声明字段：`node_id`、`metrics`、`is_delta`、`request_id`。完整类型/默认值见机器清单；继承字段见基类。

**`StickyUpdate`** — Structured update command for the per-request store (sticky binding). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/base.py#L95)

直接声明字段：`action`、`request_id`、`replica_id`、`replica_ids`。完整类型/默认值见机器清单；继承字段见基类。

**`Parser`** — Abstract base for data parsers. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/base.py#L118)

继承：`ABC`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `parse(self, raw_data: bytes \| str \| Any, node_id: str) -> KVCacheUpdate \| MetricsUpdate \| StickyUpdate \| None` | Parse raw data and return a structured update. |

#### `uni_agent.agent_aware_router.collectors.parse.basic.inflight`

**`InflightParser`** — Parse ``StatisticEvent`` → inflight + dispatch/complete ``MetricsUpdate`` deltas. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/basic/inflight.py#L70)

继承：`Parser`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `parse(self, raw_data: bytes \| str \| Any, node_id: str) -> MetricsUpdate \| None` | Dispatch on acquire/release; ignore other events / non-event payloads. |

#### `uni_agent.agent_aware_router.collectors.parse.basic.sticky`

**`StickyParser`** — Parse ``StatisticEvent`` → ``StickyUpdate`` for sticky bindings. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/basic/sticky.py#L38)

继承：`Parser`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `parse(self, raw_data: bytes \| str \| Any, node_id: str) -> StickyUpdate \| None` | Dispatch on the event type; ignore non-event payloads. |

#### `uni_agent.agent_aware_router.collectors.parse.vllm.kv`

**`VLLMKVParser`** — vLLM KV-cache parser — msgpack payload → KVCacheUpdate. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/vllm/kv.py#L34)

继承：`Parser`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `parse(self, raw_data: bytes \| str, node_id: str) -> KVCacheUpdate \| None` | Parse msgpack payload and return structured update command. |

#### `uni_agent.agent_aware_router.collectors.parse.vllm.kv_event`

**`KVCacheEvent`** — Standardized KV cache event — normalized from backend-specific ZMQ payloads. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/vllm/kv_event.py#L27)

直接声明字段：`event_type`、`node_id`、`block_hashes`、`parent_block_hash`、`token_ids`、`block_size`、`medium`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@classmethod from_raw(cls, raw_data: Any, default_node_id: str \| None=None) -> list[KVCacheEvent]` | Parse msgpack-decoded raw data into a list of KVCacheEvent instances. |
| `@property is_store(self) -> bool` | True if this is a block-stored event. |
| `@property is_remove(self) -> bool` | True if this is a block-removed event. |
| `@property is_clear(self) -> bool` | True if this is an all-blocks-cleared event. |

#### `uni_agent.agent_aware_router.collectors.parse.vllm.metrics`

**`VLLMMetricsParser`** — vLLM Prometheus metrics parser — parses HTTP response text. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/parse/vllm/metrics.py#L32)

继承：`Parser`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `parse(self, raw_data: bytes \| str, node_id: str) -> MetricsUpdate \| None` | Parse Prometheus text and return structured metrics. |

#### `uni_agent.agent_aware_router.collectors.provider`

**`CollectorManager`** — Lifecycle manager for data collectors. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/provider.py#L28)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, collectors_config: CollectorConfig, collection_names: list[str], server_addresses: dict[str, str] \| None=None, kv_event_endpoints: dict[str, list[str]] \| None=None, balancer_handler=None) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `start(self) -> None` | Start all collectors. |
| `stop(self) -> None` | Stop all collectors. |

#### `uni_agent.agent_aware_router.collectors.transport.base`

**`Transport`** — Abstract base for data transport layers. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/transport/base.py#L27)

继承：`ABC`。

直接声明字段：`is_async`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `async subscribe(self, handler: Callable[[bytes \| str, str], None]) -> None` | Start data acquisition and deliver each item to handler. |
| `stop(self) -> None` | Signal stop and close protocol-level resources (sockets/clients). |

#### `uni_agent.agent_aware_router.collectors.transport.callback`

**`StatisticEvent`** — Packed Balancer callback — the payload the callback transport delivers. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/transport/callback.py#L36)

直接声明字段：`event`、`request_id`、`replica_id`、`server_ids`、`prompt_len`。完整类型/默认值见机器清单；继承字段见基类。

**`CallbackTransport`** — Pure-forwarder Transport backed by Balancer callbacks. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/transport/callback.py#L68)

继承：`Transport`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, balancer_handler: Any) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async subscribe(self, handler: Callable[[bytes \| str, str], None]) -> None` | Register three callbacks on the Balancer; no loop, returns at once. |
| `stop(self) -> None` | Unregister every callback registered by ``subscribe`` (idempotent). |

#### `uni_agent.agent_aware_router.collectors.transport.http`

**`HTTPTransport`** — HTTP polling transport — fetches Prometheus metrics from endpoints. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/transport/http.py#L34)

继承：`Transport`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, endpoints: dict[str, str], interval: float=5.0, http_timeout: float=10.0) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async subscribe(self, handler: Callable[[bytes \| str, str], None]) -> None` | Start the HTTP polling loop — delivers response text to handler. |
| `stop(self) -> None` | No protocol-level resources to close here. |

#### `uni_agent.agent_aware_router.collectors.transport.zmq`

**`ZMQTransport`** — ZMQ transport — replay + sub dual socket per endpoint. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/collectors/transport/zmq.py#L47)

继承：`Transport`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, endpoints: dict[str, list[str]], base_retry_delay: float=1.0, max_retry_delay: float=30.0, max_retry_attempts: int=5, retry_backoff_factor: float=2.0) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `async subscribe(self, handler: Callable[[bytes \| str, str], None]) -> None` | Spawn per-endpoint subscription tasks, deliver payloads to handler. |
| `stop(self) -> None` | Signal stop, cancel tasks, close ZMQ sockets. No loop dependency. |

#### `uni_agent.agent_aware_router.config.base`

**`ConfigError`** — Raised when config validation fails. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/config/base.py#L29)

继承：`ValueError`。

**`StrategyConfig`** — Base config for routing strategies. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/config/base.py#L91)

#### `uni_agent.agent_aware_router.config.collector`

**`CollectorConfig`** — Config for the collectors module. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/config/collector.py#L30)

直接声明字段：`http_interval`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.agent_aware_router.config.router`

**`KVCAwareConfig`** — Top-level config for KVCAwareBalancer, parsed from OmegaConf DictConfig. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/config/router.py#L44)

直接声明字段：`strategy`、`collector`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@classmethod from_config(cls, cfg: DictConfig \| dict) -> KVCAwareConfig` | Two-step parsing of VeRL-transmitted config. |
| `apply_override(self, override: dict[str, Any] \| None) -> None` | Apply a flat runtime override dict onto the declared config fields. |
| `validate(self) -> None` | Validate the full config. Raises ConfigError with all violations. |

#### `uni_agent.agent_aware_router.config.strategy`

**`KVCAwareStrategyConfig`** — Config for KVCache-Aware routing strategy. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/config/strategy.py#L29)

继承：`StrategyConfig`。

直接声明字段：`load_threshold`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.agent_aware_router.debug`

**`is_debug_enabled`** — True when the debug master switch is on (``UNI_AGENT_ROUTER_DEBUG``). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/debug.py#L46)

```python
is_debug_enabled(environ: Mapping[str, str] | None=None) -> bool
```

**`get_debug_var`** — Query one ``UNI_AGENT_ROUTER_<NAME>`` variable's raw string value. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/debug.py#L52)

```python
get_debug_var(name: str, environ: Mapping[str, str] | None=None) -> str | None
```

#### `uni_agent.agent_aware_router.insight.emitter`

**`WriteKind`** — Discriminator for `WriteEvent` — which store write path fired. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/insight/emitter.py#L48)

**`WriteEvent`** — What the collector hands to `Emitter.on_write` after a store write. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/insight/emitter.py#L72)

直接声明字段：`kind`、`node`、`deltas`、`new_values`、`load`、`turn_sum`、`inflight_count`。完整类型/默认值见机器清单；继承字段见基类。

**`Emitter`** — Singleton bridge from router state to rl-insight. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/insight/emitter.py#L102)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `on_score(self, replica: str, components: dict[str, float]) -> None` | Emit A-class score components (load/s_cache/avail/need/remaining). |
| `on_route(self, latency_s: float) -> None` | Emit one ``score()`` policy-scoring latency sample (global, no replica). |
| `on_write(self, event: WriteEvent) -> None` | Emit the B-class primitives for one store write, dispatched by kind. |

#### `uni_agent.agent_aware_router.logging`

**`get_router_logger`** — Return a loguru bound logger for an agent_aware_router component. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/logging.py#L66)

```python
get_router_logger(name: str) -> loguru.Logger
```

#### `uni_agent.agent_aware_router.server.http_server`

**`KvEventsHttpServer`** — vLLMHttpServer plus kv-events port allocation and server getters. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/server/http_server.py#L53)

继承：`vLLMHttpServer`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, *args, **kwargs)` | 构造对象；只列源码显式声明的构造器。 |
| `get_kv_events_endpoints(self)` | Get kv-events ZMQ endpoint addresses. |
| `async run_server(self, args: argparse.Namespace)` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `async run_headless(self, args: argparse.Namespace)` | Run headless server in a separate thread. |

#### `uni_agent.agent_aware_router.server.net_utils`

**`is_valid_ipv6_address`** — 检查是否为合法IPv6地址。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/server/net_utils.py#L21)

```python
is_valid_ipv6_address(address: str) -> bool
```

**`get_free_port_range`** — Find ``count`` consecutive free ports, optionally holding them open. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/server/net_utils.py#L47)

```python
get_free_port_range(address: str, count: int, with_alive_sock: bool=False) -> tuple[int, list[socket.socket] | None]
```

#### `uni_agent.agent_aware_router.server.replica`

**`KvEventsReplica`** — vLLMReplica whose per-node actors are KvEventsHttpServer instances. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/server/replica.py#L23)

继承：`vLLMReplica`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, *args, **kwargs)` | 构造对象；只列源码显式声明的构造器。 |

#### `uni_agent.agent_aware_router.store.data_store`

**`DataStore`** — Unified data access layer — single entry point for all store operations. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/store/data_store.py#L29)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `get_metric(self, node_id: str, key: str) -> Any` | Query a single metric by canonical key. |
| `get_metrics(self, node_id: str) -> dict[str, Any]` | Get a node's full metrics snapshot. |
| `get_metric_node_ids(self) -> list[str]` | Return all node IDs that have metrics in the store. |
| `refresh_metrics(self, new_data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]` | Batch refresh metrics from collectors. |
| `get_block_size(self) -> int \| None` | Get learned block size. |
| `set_block_size(self, size: int) -> None` | Set block size (learned from first BlockStored event). |
| `add_kv_blocks(self, node_id: str, block_hashes: list[str], layer: Layer=Layer.GPU) -> None` | Add KV cache blocks to a node. |
| `remove_kv_blocks(self, node_id: str, block_hashes: list[str], layer: Layer=Layer.GPU) -> None` | Remove KV cache blocks from a node. |
| `clear_kv_node(self, node_id: str) -> None` | Clear all KV cache blocks for a node. |
| `get_kv_block_count(self) -> int` | Return the number of unique block hashes currently cached. |
| `kv_node_has_blocks(self, node_id: str) -> bool` | Return True if node_id appears in at least one cached block. |
| `has_kv_block(self, block_hash: str) -> bool` | Return True if block_hash is present in the cache index. |
| `get_layer_prefix_hit_rate(self, node_id: str, hash_strs: list[str], layer: Layer=Layer.GPU) -> float` | Query prefix-cache hit rate for a node at a given layer. |
| `kv_cache_load(self, node_id: str) -> float` | KV-cache load = ``retained_blocks / num_gpu_blocks`` (∈ [0,1]). |
| `per_replica_block_counts(self) -> dict[str, int]` | Return ``{replica_id: number of distinct prefix blocks it retains}``. |
| `incr_metric(self, node_id: str, key: str, delta: int \| float=1) -> int \| float` | Apply a signed delta to one metric for one node (inflight ±1). |
| `incr_metrics(self, node_id: str, deltas: dict[str, int \| float]) -> dict[str, int \| float]` | Apply multiple signed deltas to one node under a single lock. |
| `get_sticky_binding(self, request_id: str) -> str \| None` | Return the bound replica_id for ``request_id`` (None if cold/evicted). |
| `put_sticky_binding(self, request_id: str, replica_id: str) -> None` | Bind / refresh ``request_id → replica_id`` (driven by ``on_acquire``). |
| `invalidate_sticky_binding(self, request_id: str) -> None` | Drop one request_id's sticky binding. |
| `invalidate_sticky_replica(self, replica_id: str) -> None` | Drop every sticky binding pointing at a removed replica. |
| `clear_sticky_bindings(self) -> int` | Drop every sticky binding regardless of target replica. |
| `sticky_status(self) -> dict` | Return a debugging snapshot of the sticky bindings. |
| `incr_per_request(self, request_id: str, key: str, delta: int \| float=1)` | Apply a signed delta to one per-request value; return the new value. |
| `get_per_request(self, request_id: str, key: str, default: Any=None)` | Return one per-request value (``default`` if unset/evicted). |
| `set_per_request(self, request_id: str, key: str, value: Any) -> None` | Set one per-request value (creates the row if new). |
| `del_per_request(self, request_id: str, key: str) -> None` | Drop one per-request value (no-op if absent). |

#### `uni_agent.agent_aware_router.store.kv_cache_store`

**`KVCacheStore`** — Mutable data carrier for KV cache mapping tables. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/store/kv_cache_store.py#L25)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod singleton(cls) -> KVCacheStore` | Return the shared singleton instance. |
| `clear_replica(self, replica_id: str) -> None` | Clear all blocks for a replica from the reverse index. |
| `add_blocks(self, replica_id: str, block_hashes: Iterable[str], layer: Layer=Layer.GPU) -> None` | Add blocks to a replica at a layer, updating the reverse index. |
| `remove_blocks(self, replica_id: str, block_hashes: Iterable[str], layer: Layer=Layer.GPU) -> None` | Remove blocks from a replica at a layer, updating the reverse index. |
| `per_replica_block_counts(self) -> dict[str, int]` | Return ``{replica_id: number of distinct GPU prefix blocks it retains}``. |
| `get_layer_prefix_hit_rate(self, node_id: str, hash_strs: list[str], layer: Layer=Layer.GPU) -> float` | Prefix-cache hit rate for a node at a layer, ∈ [0.0, 1.0]. |

#### `uni_agent.agent_aware_router.store.per_replica_store`

**`PerReplicaStore`** — Per-replica metric store: ``{node_id: {canonical_key: value}}``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/store/per_replica_store.py#L25)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod singleton(cls) -> PerReplicaStore` | Return the shared singleton instance. |
| `get(self, node_id: str, key: str \| None=None) -> Any \| dict[str, Any]` | Read metrics. |
| `incr(self, node_id: str, key: str, delta: int \| float=1) -> int \| float` | Apply a numeric delta to one key for one node (inflight ±1). |
| `incr_many(self, node_id: str, deltas: dict[str, int \| float]) -> dict[str, int \| float]` | Apply multiple signed deltas to one node under a single lock. |
| `refresh(self, new_data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]` | Batch refresh from collectors. |
| `all_ids(self) -> list[str]` | Return all node IDs currently in the store. |

#### `uni_agent.agent_aware_router.store.per_request_store`

**`PerRequestStore`** — Singleton per-request state store — ``request_id → {key: value}``, LRU-bounded. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/store/per_request_store.py#L38)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, max_size: int=DEFAULT_PER_REQUEST_MAX_SIZE) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod singleton(cls) -> PerRequestStore` | Return the shared singleton (tests construct a fresh instance / reset _instance). |
| `@property max_size(self) -> int` | Configured per-request table capacity. |
| `get(self, request_id: str, key: str, default: Any=None) -> Any` | Return the per-request value for ``key`` (``default`` if unset/evicted). |
| `set(self, request_id: str, key: str, value: Any) -> None` | Set ``request_id``'s ``key`` to ``value`` (creates the row if new). |
| `incr(self, request_id: str, key: str, delta: int \| float=1) -> int \| float` | Add ``delta`` to ``request_id``'s numeric ``key``; return the new value. |
| `delete(self, request_id: str, key: str) -> None` | Drop ``key`` from ``request_id``'s row (no-op if absent). |
| `delete_where(self, key: str, value: Any) -> int` | Drop ``key`` from every request whose value for it equals ``value``. |
| `delete_key(self, key: str) -> int` | Drop ``key`` from every request that has it, regardless of value. |
| `count(self, key: str) -> int` | Number of requests that currently have ``key`` set. |
| `reset(self) -> None` | Clear all per-request state (test helper; not used on the hot path). |

#### `uni_agent.agent_aware_router.strategies.base`

**`ReplicaInfo`** — Descriptor of a routable replica. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/strategies/base.py#L23)

直接声明字段：`replica_id`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.agent_aware_router.strategies.kvc_aware`

**`StrategyError`** — Strategy construction or scoring error. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/strategies/kvc_aware.py#L56)

继承：`Exception`。

**`KVCacheAwareStrategy`** — Runtime strategy constructed from a ``KVCAwareStrategyConfig``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/strategies/kvc_aware.py#L60)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, *, alpha: float, load_threshold: float, layer_weights: dict[Layer, float], memory_overload_filter: bool=True, do_shortcut: bool=True, slow_cut: SlowCut \| str=SlowCut.CAPACITY_TOKEN_AWARE, load_weights: tuple[float, float, float, float]=DEFAULT_LOAD_WEIGHTS, overload_mode: OverloadMode \| str=OverloadMode.KV_CACHE_USAGE_PERC) -> None` | 构造对象；只列源码显式声明的构造器。 |
| `set_capacity(self, max_num_seqs: int, max_num_batched_tokens: int) -> None` | Inject ``--max-num-seqs`` from the server handle's rollout config. |
| `@classmethod from_config(cls, cfg: KVCAwareStrategyConfig) -> KVCacheAwareStrategy` | Construct from config. ``max_num_seqs`` is injected by the Balancer via ``set_capacity`` after fetching from the server handle. |
| `is_overloaded(self, store: DataStore, replica: ReplicaInfo) -> bool` | Return True if ``replica`` is overloaded (``load > load_threshold``). |
| `score(self, prompt_ids: list[int] \| None, store: DataStore, replicas: list[ReplicaInfo], request_id: str \| None=None) -> list[float]` | Score each replica. Larger is better. |

#### `uni_agent.agent_aware_router.strategies.registry`

**`StrategyRegistry`** — Class-level registry mapping strategy config type → strategy class. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/strategies/registry.py#L28)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `@classmethod register(cls, config_cls: type, strategy_cls: type) -> None` | Register a runtime strategy class for a config dataclass type. |
| `@classmethod get(cls, config_cls: type) -> type` | Look up the runtime strategy class registered for ``config_cls``. |

#### `uni_agent.agent_aware_router.strategies.routing`

**`RoutingStrategy`** — Routing scoring strategy. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/strategies/routing.py#L33)

继承：`Protocol`。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `score(self, prompt_ids: list[int] \| None, store: Any, replicas: list[Any], request_id: str \| None=None) -> list[float]` | Score each replica. Larger is better; negatives are allowed. |

**`route`** — Return replica ids ranked best-first by ``strategy``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/strategies/routing.py#L63)

```python
route(strategy: Any, prompt_ids: list[int] | None, store: Any, replicas: list[Any], request_id: str | None=None) -> list[str]
```

#### `uni_agent.agent_aware_router.types.emit_spec`

**`EmitKey`** — Canonical emit-side metric key names. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/types/emit_spec.py#L39)

直接声明字段：`KV_CACHE_USAGE_PERC`、`NUM_REQUESTS_RUNNING`、`NUM_REQUESTS_WAITING`、`KV_CACHE_LOAD`、`INFLIGHT_TOKENS`、`PROMPT_TOKENS`、`PROMPT_TOKENS_CACHED`、`EXTERNAL_PREFIX_CACHE_HITS`、`ESTIMATED_FLOPS_PER_GPU`、`DISPATCHED_COUNT`、`COMPLETED_COUNT`、`PROMPT_LEN_SUM`、`INFLIGHT_AVG_TURN`、`KV_EVICTIONS`、`LOAD`、`S_CACHE`、`AVAIL_RATIO`、`NEED_RATIO`、`REMAINING_RATIO`、`ROUTE_LATENCY_SECONDS`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.agent_aware_router.types.layer`

**`Layer`** — Canonical cache-layer names (backend-agnostic). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/types/layer.py#L28)

继承：`str`、`Enum`。

#### `uni_agent.agent_aware_router.types.metric_spec`

**`MetricKey`** — Canonical metric key names — backend-agnostic, strategy-layer unified. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/types/metric_spec.py#L32)

直接声明字段：`KV_CACHE_USAGE_PERC`、`NUM_REQUESTS_RUNNING`、`NUM_REQUESTS_WAITING`、`NUM_GPU_BLOCKS`、`PREFIX_CACHE_QUERIES`、`PREFIX_CACHE_HITS`、`TTFT_SECONDS_SUM`、`TTFT_COUNT`、`QUEUE_TIME_SECONDS_SUM`、`QUEUE_TIME_COUNT`、`TPOT_SECONDS_SUM`、`TPOT_COUNT`、`PROMPT_TOKENS`、`PROMPT_TOKENS_CACHED`、`GENERATION_TOKENS`、`EXTERNAL_PREFIX_CACHE_HITS`、`ESTIMATED_FLOPS_PER_GPU`、`INFLIGHT_COUNT`、`INFLIGHT_TOKENS`、`INFLIGHT_TURN_SUM`、`DISPATCHED_COUNT`、`COMPLETED_COUNT`、`PROMPT_LEN_SUM`。完整类型/默认值见机器清单；继承字段见基类。

#### `uni_agent.agent_aware_router.types.overload_mode`

**`OverloadMode`** — How ``is_overloaded`` decides a replica is overloaded. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/types/overload_mode.py#L31)

继承：`str`、`Enum`。

#### `uni_agent.agent_aware_router.types.slow_cut`

**`SlowCut`** — Fallback scoring mode (used after the sticky short-circuit misses). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/types/slow_cut.py#L28)

继承：`str`、`Enum`。

#### `uni_agent.agent_aware_router.utils.hash`

**`compute_hash`** — Compute xxhash for a single block given parent hash and token bytes. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/utils/hash.py#L26)

```python
compute_hash(parent_hash: int, block_bytes: bytes, seed: int=0) -> int
```

**`get_prefix_hashes_incremental`** — Continue a chained-prefix-hash computation from a checkpoint. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/utils/hash.py#L53)

```python
get_prefix_hashes_incremental(prompt_ids: list[int], block_size: int, parent_hash: int, n_done: int, seed: int=0) -> tuple[list[int], int]
```

#### `uni_agent.agent_aware_router.utils.knob`

**`coerce_knob_value`** — Coerce ``raw`` to ``default``'s type; raise ``ConfigError`` on mismatch. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/utils/knob.py#L34)

```python
coerce_knob_value(name: str, raw: Any, default: Any) -> Any
```

#### `uni_agent.agent_aware_router.utils.prefix_cache`

**`resolve_prefix_hashes`** — Return ``str(h)`` for each full-block chained prefix hash of ``prompt_ids``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/agent_aware_router/utils/prefix_cache.py#L26)

```python
resolve_prefix_hashes(prompt_ids: list[int], request_id: str | None, store: Any) -> list[str]
```

### 12.7 日志上下文

#### `uni_agent.logging.context`

**`LogContext`** — Explicit routing information for one logical execution log. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/logging/context.py#L35)

直接声明字段：`log_id`、`log_path`。完整类型/默认值见机器清单；继承字段见基类。

**`get_current_log_context`** — Return the logging context bound to the current execution. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/logging/context.py#L48)

```python
get_current_log_context() -> LogContext | None
```

#### `uni_agent.logging.session`

**`sample_logging`** — Bind a log ID to records in this block; usable as ``with`` or ``async with``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/logging/session.py#L34)

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `__init__(self, log_id: str, log_path: Path \| str \| None=None)` | 构造对象；只列源码显式声明的构造器。 |
| `@classmethod from_context(cls, context: LogContext) -> sample_logging` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `__enter__(self) -> sample_logging` | 进入同步上下文。 |
| `__exit__(self, *exc_info) -> bool` | 退出同步上下文并清理。 |
| `async __aenter__(self) -> sample_logging` | 进入异步上下文；生命周期由该类实现。 |
| `async __aexit__(self, *exc_info) -> bool` | 退出异步上下文并清理。 |

### 12.8 可选观测接口

#### `uni_agent.rl_insight.__init__`

**`metric_count`** — Record a counter increment (no-op when emit is off). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/__init__.py#L81)

```python
metric_count(name: str, amount: float=1.0, documentation: str='', **labels: Any) -> None
```

**`metric_gauge`** — Record a gauge value (no-op when emit is off). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/__init__.py#L89)

```python
metric_gauge(name: str, value: float, documentation: str='', **labels: Any) -> None
```

**`metric_histogram`** — Record one histogram sample (no-op when emit is off). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/__init__.py#L97)

```python
metric_histogram(name: str, value: float, documentation: str='', **labels: Any) -> None
```

**`trace_span`** — Report one completed span through verl's RLInsightLogger (version-gated, no-op when off). [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/__init__.py#L112)

```python
trace_span(name: str, *, start_time_ns: int, end_time_ns: int, attributes: dict[str, Any] | None=None) -> None
```

#### `uni_agent.rl_insight.adapter`

**`init_rollout_trace_config`** — Initialize rollout trace identity from the trainer config. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/adapter.py#L116)

```python
init_rollout_trace_config(config: Any) -> None
```

**`TaskSpanState`** — Mutable task result collected by `task_span`. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/adapter.py#L126)

直接声明字段：`start_ns`、`task_name`、`image_ref`、`prompt_hash`、`status`、`error`、`reward`、`accuracy`、`finished`、`reward_posted`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `record_result(self, result: Any, *, reward_posted: bool) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`task_span`** — Bind task identity and report one completed task span. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/adapter.py#L162)

```python
task_span(tools_kwargs: dict[str, Any] | None, *, task_name: str, prompt: Any)
```

**`GenerationSpan`** — Mutable gateway-generation span state. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/adapter.py#L200)

直接声明字段：`identity`、`start_ns`、`status`、`error`、`finish_reason`、`prompt_tokens`、`completion_tokens`、`chain_id`、`turn`、`type`、`tools`、`content`。完整类型/默认值见机器清单；继承字段见基类。

| 方法签名 / 属性getter | 功能（优先保留源码docstring） |
|---|---|
| `capacity_exhausted(self, *, prompt_tokens: int, chain_id: int \| None) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `success(self, *, prompt_tokens: int, completion_tokens: int, chain_id: int \| None, turn: int, assistant_msg: dict[str, Any], finish_reason: str \| None) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `failure(self, exc: BaseException) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |
| `report(self) -> None` | 该类声明的方法；参数和返回类型如下，具体分支见源码。 |

**`start_generation_span`** — Start one gateway-generation span. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/adapter.py#L285)

```python
start_generation_span(identity: dict[str, Any]) -> GenerationSpan
```

**`agent_loop_session`** — Create an Agent Loop session, falling back when installed verl is old. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/rl_insight/adapter.py#L290)

```python
agent_loop_session(*, experiment_name: Any | None=None, sample: Any, session: Any, traj: Any=0, uid: Any=None, global_steps: Any=None, session_id: Any=None)
```

### 12.9 通用工具

#### `uni_agent.utils`

**`simple_timer`** — Accumulate the elapsed wall time of the ``with`` block into ``timing_raw[name]``. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/utils.py#L10)

```python
simple_timer(name: str, timing_raw: dict[str, float])
```

**`get_event_loop`** — 获取或创建事件循环。 [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/utils.py#L26)

```python
get_event_loop()
```

**`auto_await`** — Auto await a coroutine function. [源码](https://github.com/verl-project/uni-agent/blob/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent/utils.py#L36)

```python
auto_await(func)
```

### 12.10 包导出、别名与常量

`__all__`记录公开导入入口。这里也覆盖非函数声明的导出，例如GatewayActor的Ray别名、ToolStatus类型别名、emitter单例、指标规格常量与版本号。它们不增加HTTP路由数。

| 模块 | 显式导出名 |
|---|---|
| `uni_agent` | `__version__` |
| `uni_agent.agent_aware_router` | `KVCAwareBalancer` |
| `uni_agent.agent_aware_router.collectors` | `CollectorManager` |
| `uni_agent.agent_aware_router.collectors.parse` | `Parser`, `KVCacheUpdate`, `MetricsUpdate`, `StickyUpdate` |
| `uni_agent.agent_aware_router.collectors.parse.basic` | `InflightParser`, `StickyParser` |
| `uni_agent.agent_aware_router.collectors.parse.vllm` | `VLLMKVParser`, `VLLMMetricsParser` |
| `uni_agent.agent_aware_router.collectors.transport` | `Transport`, `CallbackTransport`, `HTTPTransport`, `ZMQTransport` |
| `uni_agent.agent_aware_router.config` | `CollectorConfig`, `ConfigError`, `KVCAwareConfig`, `KVCAwareStrategyConfig`, `StrategyConfig` |
| `uni_agent.agent_aware_router.insight` | `Emitter`, `WriteEvent`, `WriteKind`, `emitter` |
| `uni_agent.agent_aware_router.insight.emitter` | `Emitter`, `WriteEvent`, `WriteKind`, `emitter` |
| `uni_agent.agent_aware_router.server` | `KvEventsHttpServer`, `KvEventsReplica` |
| `uni_agent.agent_aware_router.store` | `DataStore` |
| `uni_agent.agent_aware_router.strategies` | `ReplicaInfo`, `StrategyRegistry`, `route` |
| `uni_agent.agent_aware_router.types` | `EmitKey`, `EMIT_SPECS`, `Layer`, `MetricKey`, `METRIC_SPECS`, `OverloadMode`, `SlowCut` |
| `uni_agent.agent_aware_router.utils` | `compute_hash`, `get_prefix_hashes_incremental`, `coerce_knob_value` |
| `uni_agent.agents` | `Agent`, `AgentConfig`, `ModelConfig`, `AgentResult`, `build_agent`, `get_agent_cls` |
| `uni_agent.agents.claude_code` | `ClaudeCodeAgent`, `ClaudeCodeConfig` |
| `uni_agent.agents.mem_agent` | `ContextManagerResult`, `MemAgent`, `MemAgentConfig` |
| `uni_agent.agents.mini_swe_agent` | `MiniSweAgentAgent`, `MiniSweAgentConfig` |
| `uni_agent.agents.react` | `ReActAgent`, `ReActConfig` |
| `uni_agent.framework` | `AgentFramework`, `GatewayAgentFramework`, `AgentRunner` |
| `uni_agent.gateway` | `GatewayActor`, `GatewayManager` |
| `uni_agent.gateway.adapters` | `anthropic_build_response`, `anthropic_error_body`, `anthropic_stream_response`, `anthropic_to_internal`, `MalformedRequestError`, `openai_build_response`, `openai_error_body`, `openai_stream_response`, `openai_to_internal` |
| `uni_agent.gateway.session` | `InternalGenerationRequest`, `GatewaySession`, `MessageCodec`, `SessionHandle`, `Trajectory`, `TrajectoryBuffer` |
| `uni_agent.logging` | `sample_logging`, `LogContext`, `get_current_log_context` |
| `uni_agent.rl_insight` | `ENABLE_ENV`, `metric_count`, `metric_gauge`, `metric_histogram`, `trace_span` |
| `uni_agent.sandbox` | `ExecResult`, `ImageMap`, `Sandbox`, `SandboxBackend`, `SandboxConfig`, `LocalSandbox`, `build_sandbox` |
| `uni_agent.tasks` | `Task`, `TaskConfig`, `TaskConfigResolver`, `TaskResult`, `get_task` |
| `uni_agent.tasks.hotpotqa` | `HotpotQATask`, `HotpotQATaskConfig`, `compute_score` |
| `uni_agent.tools` | `Tool`, `ToolError`, `ToolCallFormatError`, `ToolResult`, `ToolStatus`, `Toolbox` |

## 13. 版本、来源与使用边界

- [固定版本源码](https://github.com/verl-project/uni-agent/tree/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent)。每个附录声明带源码行号。
- [api-inventory.json](../evidence/api-inventory.json)保存签名、字段、导出、路由、源文件SHA256及E2B契约索引。
- [extract_api_inventory.py](../tools/extract_api_inventory.py)只解析源码AST与生成客户端；[render_api_appendices.py](../tools/render_api_appendices.py)生成附录，不会导入/启动训练、模型或sandbox。
- `examples/`脚本参数、私有函数、第三方继承API与未来版本新增API不在“Uni-Agent自有公开声明”计数中。配置属性详见正文或JSON字段记录。
- 本次新增的是源码核对文档；实际使用范围仍以[实验索引](../experiments/README.md)为准，没有据此新增训练或远端服务兼容结论。
