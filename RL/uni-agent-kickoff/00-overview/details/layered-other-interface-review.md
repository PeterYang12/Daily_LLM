# 分层架构补充：本次代码Agent、外部MemAgent与Miles的实际接口

2026-09-14只读核对。以下区分本次已执行路径与框架可选能力，没有启动GPU、服务、sandbox或联网请求。固定Uni-Agent为`472c875a97f9a2764c81a6ec7581167632bd8bcc`，Miles为`50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9`；实际工作树含已记录的ROCm/保存恢复补丁。

## 实际调用链总表

| 实验 | Agent→模型路径 | 执行环境路径 | 学习/产物边界 |
| --- | --- | --- | --- |
| Coder30B ReAct，六题与扩展29题 | `ReActAgent`→`OpenAICompatibleChatModel`→直接HTTP `18082/v1/chat/completions`→原始Coder30B vLLM | Python `Toolbox`→DockerSandbox→专用Docker daemon→每题testbed容器 | 推理与测试评分；无Gateway token训练轨迹、无optimizer更新 |
| Coder30B Claude Code六题 | 容器内真实Claude CLI→session `/v1/messages`→本地`_GatewayActor`→`OpenAICompletionsBackend`→`18082/v1/completions` | Claude自身Bash/Read/Edit/Write在题目容器内执行；Uni-Agent管理容器与最终verifier | 有真实Gateway token IDs/mask/logprobs；本次没有训练更新 |
| Coder30B Mini-SWE六题 | 容器内Mini `LitellmModel`→session `/v1/chat/completions`→同一类Gateway→`18082/v1/completions` | Mini自己的`LocalEnvironment`运行在已经创建的Docker题目容器中 | 有token轨迹，仍为推理；Mini内部LocalEnvironment不是宿主环境 |
| 32B/8B独立external评测及原始32B长文case | `examples/mem_agent/infer.py`→HotpotQA Task→MemAgent→直接`/v1/chat/completions`→固定base或HF128 vLLM | Task配置LocalSandbox，但MemAgent主要处理Python内存中的文本chunks，没有执行shell工具 | 独立回载推理/评分；没有Gateway/TQ/optimizer更新 |
| Miles GSM8K与0.6B工具RL | Miles rollout generator→SGLang router `18766/generate`→SGLang engines；返回真实token/logprob | 工具case走`examples.retool_v2.tool_sandbox.execute_tool`，在Miles容器内启动临时Python子进程 | 自己的Ray/FSDP2/Adam/SGLang权重同步；不经过Uni-Agent Gateway或verl |
| Miles 4B工具adapter对照 | 同Miles多轮generator，改工具输入适配 | 同原Python执行器，adapter去完整代码围栏并显示末尾表达式 | `--debug-rollout-only`；16/16通过是工具契约对照，无参数更新 |

原始模型上的推理、Gateway轨迹采集、真实RL更新是三种不同证据，不应在图里画成全部必经的一条训练链。

## Coder30B ReAct：直接chat API

run_large_swe.py（原始文件：`scripts/run_large_swe.py`）固定`http://127.0.0.1:18082/v1`，在`ua-lab-cpu`中调用audit_swe.py（原始文件：`scripts/audit_swe.py`）。后者直接`get_task(task).run()`，没有建立Gateway session。

[ReActAgent](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/react/agent.py)在Python层维护对话和tool loop；[OpenAICompatibleChatModel](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/react/model.py)通过aiohttp发送`messages`、tool schemas、sampling参数到`{base_url}/chat/completions`，读取assistant text和structured `tool_calls`。本次api key为本地占位，不代表调用OpenAI服务。

六题配置（原始文件：`configs/swe-react-64k.yaml`）为40步、每轮2048 token、总预算55000，temperature0.2/top_p0.9；扩展配置（原始文件：`configs/swe-react-coder30b-expanded.yaml`）放宽到100步。模型服务同为serve_qwen3_coder30b.sh（原始文件：`scripts/serve_qwen3_coder30b.sh`）的BF16/TP1/64K，HIP1。不同预算不能当成一套同预算排行榜。

模型可调用的tool是`stateful_shell`、`str_replace_editor`、`submit`；注册名`stateful_shell`对模型显示为`shell`。这些是Toolbox的Python工具接口，不是Gateway HTTP端点。Docker没有原生持久shell接口，本次[shell.py](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/tools/shell.py)使用tmux-over-exec保持cwd与export；单次`Sandbox.exec`自身不保存shell状态。

SWE评分由[reward.py](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/tasks/swe_bench/reward.py)生成verifier脚本，`sandbox.write_file`后`exec_shell`执行，并解析FAIL_TO_PASS/PASS_TO_PASS。它不向Gateway POST reward。Agent输出`finished`与verifier的`resolved/reward`不同；原始六题、expanded29及test-editing标记继续分别保留。

## Claude/Mini：真实Gateway，但使用独立debug backend

run_large_blackbox.py（原始文件：`scripts/run_large_blackbox.py`）调用blackbox-swe-cases.py（原始文件：`scripts/blackbox-swe-cases.py`）。后者在同一Python进程直接创建`_GatewayActor(...)`并`await start/create_session/finalize_session/shutdown`，没有使用训练时的Ray GatewayManager池。

此路径确实运行模型。虽然后端helper来自[examples/gateway/debug_launcher.py](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/examples/gateway/debug_launcher.py)，本次明确选的是实际`AutoTokenizer`与`OpenAICompletionsBackend`，不是该文件中的`DebugFakeTokenizer/DebugFakeBackend`。

后端HTTP请求是`POST 18082/v1/completions`，字段包括：

```text
model = Qwen3-Coder-30B-A3B-Instruct
prompt = 真实token ID列表
return_token_ids = true
logprobs = 1
add_special_tokens = false
```

Gateway完成provider协议转换和token轨迹记录。这与32B/8B真实RL中注入verl `LLMServerClient`的方式不同；不能把本次blackbox的HTTP completions箭头复制到所有训练图上。

[ClaudeCodeAgent](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/claude_code/agent.py)把session base URL末尾`/v1`去掉，设置`ANTHROPIC_BASE_URL`，CLI自己再追加`/v1/messages`。本次CLI为2.1.236，绑定进题目容器，限制工具为Bash/Read/Edit/Write。传给CLI的`claude-sonnet-4-5`是协议入口模型名，实际backend固定Coder30B，不能据此称使用Anthropic模型。

[MiniSweAgentAgent](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/mini_swe_agent/agent.py)向容器内脚本传JSON；[run_agent.py](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/examples/mini_swe_agent/run_agent.py)创建Mini的`LocalEnvironment`和`LitellmModel(model_name="openai/default", api_base=gateway_url)`。`openai/default`同样只是兼容层名，实际权重由Gateway backend决定。

两者任务reward在`run_task`的`TaskResult`返回；脚本另行finalize Gateway，写`sessions/<id>/trajectories.jsonl`和debug snapshot。导出metadata明确`not_rl_training=true`。这里没有Framework评分→TQ→optimizer流水线，轨迹的存在不能替代参数更新证据。

## Docker边界：不是每一层都连接宿主默认daemon

外层脚本用宿主Docker管理`ua-lab-cpu`与模型服务；进入CPU容器后，DockerSandbox调用Docker CLI，继承：

```text
DOCKER_HOST=unix:///lab/run/docker.sock
```

start_sandbox_daemon.sh（原始文件：`scripts/start_sandbox_daemon.sh`）启动独立`ua-lab-sandbox-daemon`，内部socket为`/ua-run/docker.sock`。host `$LAB_ROOT/run`同时映射到daemon的`/ua-run`和CPU容器的`/lab/run`，三者指向同一个socket文件。任务镜像和容器属于该独立daemon；只在宿主默认daemon里拉镜像不足以让任务找到它。

[DockerSandbox](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/sandbox/docker.py)实际方法映射：

| Python接口 | 实际Docker操作 |
| --- | --- |
| `start()` | `docker run --rm -d ...`，本次固定题目image已准备、pull_policy=never |
| `stop()` | `docker rm -f <此sandbox>` |
| `exec(argv, workdir, env, timeout)` | `docker exec [--workdir/--env] ... argv` |
| `exec_shell(script)` | 上一接口包装`bash -c` |
| `read_file(path)` | 基类通过exec读base64，客户端解码 |
| `write_file(path, content)` | 本地补丁改成`docker exec -i ... cat > path`，bytes经stdin，避免argv过大 |
| `upload_file/download_file` | `docker cp` |
| 目录upload/download | 基类tar打包后走文件接口 |

题目容器、CPU服务和模型容器均按本次配置使用host network，所以容器内的`127.0.0.1:18082`可达本机服务。此事实不适用于远端Verdal sandbox；远端的localhost不会自动指回这台GPU节点。

## 独立MemAgent评测：没有穿过训练Gateway

run_large_memagent.py（原始文件：`scripts/run_large_memagent.py`）、run_stable_memagent.py（原始文件：`scripts/run_stable_memagent.py`）、run_memagent8b_stable.py（原始文件：`scripts/run_memagent8b_stable.py`）都调用官方[examples/mem_agent/infer.py](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/examples/mem_agent/infer.py)。该入口按完整JSON source做token chunking，直接建立HotpotQA Task；MemAgent复用上述OpenAICompatibleChatModel。

本次端点分别为：原32B primary18083，32B stable18084，8B stable18085，32B GPU1默认恢复18086。实际请求均为这些base URL下的`/chat/completions`，另有服务身份预检`GET /models`。Stable使用BI1/TRITON_ATTN，默认恢复使用ROCM_ATTN；不同protocol各自成对分析。

这条链读取base或已导出的BF16 HF128模型，只产生外部答题结果和评分。结果JSONL没有完整中间memory文本，也没有Gateway token ID/mask/logprob训练结构；不能由“同一个MemAgent类”推导它走了训练中的session/TQ链。

## Miles：自己的RL链和SGLang接口

miles_run.py（原始文件：`scripts/miles_run.py`）在`ua-lab-miles-gpu`启动独立Ray：head16879、dashboard18665、SGLang router18766。固定配置为`--train-backend fsdp --colocate`，HIP6/7、两个trainer rank和两个单卡rollout engine共享这两张物理卡；不是Uni-Agent的4+2分离布局。

[Miles train.py](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/train.py)先同步初始训练权重，之后按生成→训练→保存→权重同步的顺序运行。[multi_turn.generate](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/miles/rollout/generate_hub/multi_turn.py)通过HTTP `POST /generate`调用router，payload包含`input_ids`、`sampling_params`和`return_logprob=true`；[generate_endpoint_utils.py](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/miles/rollout/generate_utils/generate_endpoint_utils.py)从SGLang `meta_info.output_token_logprobs`取实际token IDs和logprobs，不靠输出文本重新tokenize模拟它们。

工具配置绑定`examples.retool_v2.tool_sandbox.execute_tool`。该模块的PythonSandbox是临时目录+`subprocess.Popen(["python3", script_path])`+超时/资源限制及输入检查，运行在Miles容器内。其名称不能解读成每次工具调用都创建Docker或Verdal sandbox。它的regex检查也不是本次验证过的强安全隔离边界。

本次`--colocate`在[FSDP actor](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/miles/backends/fsdp_utils/actor.py)选`UpdateWeightFromTensor`，不是`UpdateWeightFromDistributed`。完整权重同步接口见[update_weight_utils.py](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/miles/backends/fsdp_utils/update_weight_utils.py)和[SGLangApiClient](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/miles/backends/sglang_utils/sglang_api_client.py)：

```text
POST /pause_generation
GET  /flush_cache
POST /begin_weight_update
POST /update_weights_from_tensor   ← 多个flattened_bucket
GET  /flush_cache
POST /end_weight_update
POST /continue_generation
```

训练rank执行`gather_full_param`汇集FSDP参数，按dtype和128MiB目标bucket组织，通过Gloo `gather_object`汇集序列化IPC元数据。`update_weights_from_tensor`的HTTP请求带tensor句柄/元数据，实际权重从GPU直接拷贝，不是把所有参数数值变成普通JSON从网络上传。`weight_version`随更新发布；client读版本优先`GET /model_info`，兼容旧`GET /get_weight_version`。

这不是仅从源码猜测：历史logs/miles/run-05.log（原始文件：`logs/miles/run-05.log`）明确保留上述pause/begin/update/end/continue的HTTP200和flattened bucket日志，并有真实`POST /generate`。原基础SGLang缺begin/end端点，实际换用独立`sglang-miles` Python源码commit `32839114c4ada2ae237581405a8ff39dc0db9e25`后才闭环；“兼容SGLang”不能代替精确版本/协议校验。

GSM8K真实4个Adam step及恢复、0.6B工具case两次非零更新有参数证据；4B原始工具case为全负reward/零梯度，adapter对照为rollout-only。Miles没有接入本次Uni-Agent Gateway、TransferQueue或其32B/8B native checkpoint布局，不能把两套训练保存格式和恢复流程混为一体。
