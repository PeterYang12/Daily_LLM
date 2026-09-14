# 4B / 9B 黑盒代码 Agent 对照与长预算案例

## 官方 SWE-bench Verified 六例黑盒对照

后续使用主 agent 已验证的 `results/swe-data/verified-six.parquet`：`pallets__flask-5014`、`psf__requests-6028`、`pytest-dev__pytest-5262`、`pytest-dev__pytest-7432`、`pytest-dev__pytest-7521`、`pytest-dev__pytest-7982`。该六例的原始代码 baseline 为 0/6 resolved，dataset gold patch oracle 为 6/6 resolved，证据分别在 `results/swe-baseline-six/` 与 `results/swe-oracle-six/`。这保证评测环境和测试解析确实可用；仍只是人为挑选的小样本集，不能当作全量 SWE-bench Verified 成绩。

正式 driver `scripts/blackbox-swe-cases.py` 每次运行都先读取对应 oracle result，断言 `reward==1` 且 `eval_completed==true`，再执行原版 `framework.task_runner.run_task → SWEBenchTask → Agent → compute_reward`。observer hooks 只保存 stdout、patch、verifier 输出和 AgentResult，不改 agent/评分语义。Dataset 的 gold patch 和 test patch 仅在外部 driver / official verifier 中使用，未挂载进 Agent 容器，未包含在 Agent prompt。

统一运行设置：每种 agent 并发 2，最多 40 步，最多 55000 上下文 tokens，逐轮最多 2048 output tokens，temperature=0.2、top_p=0.9；agent 600 秒、verifier 300 秒。Gateway 使用后端真实 token ID 和 logprobs，不从回复文字重分词。Claude 开启 `--bare --no-session-persistence`，启用 Bash/Read/Edit/Write 四个工具；mini-SWE 使用已公开记录的工具/提交格式指引。两者都是模型的实际黑盒 Agent 运行，各自的内部提示和工具协议不相同。

4B 使用 `Qwen3-4B-Instruct-2507`、hermes parser；9B 使用官方 recipe 推荐的 `Qwen3.5-9B`、qwen3_coder parser、`hf_model_type=qwen3_5` 与 `enable_thinking=false`。两台 vLLM 服务的 max_model_len 都是 65536。Portable mini runtime 使用官方 `python-build-standalone` 3.12.13 / 20260602、mini-swe-agent 2.2.8 与 litellm 1.81.7，已经在旧版 SWE 镜像实际 import 成功；构建脚本 `scripts/blackbox-build-portable-mini.sh`，完整依赖 `results/blackbox-mini-portable-freeze.txt`。

初步已完成 Claude+4B：**resolved 0/6，finished 4/6**。所有六例均进入官方 verifier 并得到可解析报告，所有 token trajectory 的 mask/logprobs 长度对齐。四例没有留下 patch；一例修改导致 32 个 PASS_TO_PASS 回归；另两例 unfinished 中出现逐轮输出截断 / 工具 JSON 不完整。原始结果在 `results/blackbox-swe-claude4b/results.json`，每例目录有 `candidate.patch`、`agent-process.stdout`、`agent-result.json`、`verifier.stdout`、`verifier.result.json`、`result.json`，Gateway 轨迹和消息记录在 `sessions/`。这一负结果说明“成功连接 Claude Code”与“本地小模型能完成真实 SWE 任务”是不同层次。

40 步预算的最终矩阵如下，分母均为同一组六例，未重试或挑选最好结果：

| 黑盒 Agent / 本地模型 | 官方 verifier resolved | Agent finished | resolved 且 finished |
|---|---:|---:|---:|
| Claude Code / Qwen3-4B-Instruct-2507 | 0/6 | 4/6 | 0/6 |
| mini-SWE / Qwen3-4B-Instruct-2507 | 1/6 | 3/6 | 1/6 |
| Claude Code / Qwen3.5-9B | 1/6 | 0/6 | 0/6 |
| mini-SWE / Qwen3.5-9B | 3/6 | 0/6 | 0/6 |

| Case | Claude 4B | mini 4B | Claude 9B | mini 9B |
|---|---|---|---|---|
| Flask-5014 | 错误/未完成 | 错误/未完成 | **正确/未完成** | **正确/未完成** |
| Requests-6028 | 错误/已完成 | 错误/已完成 | 错误/未完成 | 错误/未完成 |
| pytest-5262 | 错误/已完成 | 错误/已完成 | 错误/未完成 | **正确/未完成** |
| pytest-7432 | 错误/已完成 | 错误/未完成 | 错误/未完成 | 错误/未完成（600s） |
| pytest-7521 | 错误/未完成 | 错误/未完成 | 错误/未完成 | **正确/未完成** |
| pytest-7982 | 错误/已完成 | **正确/已完成** | 错误/未完成 | 错误/未完成 |

“正确”只表示本次官方 verifier 的 `resolved=true`；“已完成”来自真实 Agent 的 termination 状态。9B 的正确 patch 在这组 40 步设置下全部未正常结束。查看其轨迹，可见模型在修复后继续检查边界条件和重复测试，而没有及时结束/提交；并非 Gateway 系统性无法解析工具。mini9B/pytest7432 则达到 agent 的 600 秒时间上限。这些数字不能直接推广为模型能力排名，尤其这里同时受限于 40 步和逐轮 2048 输出 tokens；原版 Mini recipe 的默认 step_limit 是 100。

完整机读汇总：`results/blackbox-swe-summary.json`，重建汇总命令：`docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/blackbox-summarize.py`。24 条正式 trajectory 都通过 token ID 整数性、二值 mask、mask/logprobs 对齐、logprobs 有限性、配置上下文预算的检查，报告 `results/blackbox-trajectories-validation.json`。wall_seconds 在共享 GPU 的并发运行中测得，不作为吞吐/性能基准。

建议第一次复现真实 SWE 成功路径选 `mini + Qwen3-4B-Instruct-2507 + pytest-dev__pytest-7982`：该次 9 API calls、约 30 秒、Submitted、官方 verifier 通过。使用 `scripts/blackbox-swe-cases.py --instances pytest-dev__pytest-7982` 可只跑该例；完整封装入口是 `bash scripts/blackbox-run-swe.sh mini 4b <新的run-tag>`。

## 独立 128-turn 长预算审计

额外从干净 Flask5014 sandbox 运行一次 Claude Code + Qwen3.5-9B：128 turns、60000 上下文 tokens、每轮 4096 输出 tokens、1800 秒 agent timeout；使用与正式实验相同的任务提示，没有追加新的终止提示。这次单独保存在 `results/blackbox-swe-claude9b-long/`，不纳入上面的六例统计。两次 Flask 结果来自独立采样，不是延长同一条轨迹。

结果：248.1 秒，CLI `error_max_turns`，`num_turns=129`（CLI 在第 129 次检查触发 128 的上限），`finished=false`、verifier reward=0、candidate patch 为空。Gateway 轨迹是 prompt 1412 + response 34121 = 35533 tokens，没有耗尽 60000 的上下文预算；没有 parser / transport 错误。

具体失败机制可以直接在 `sessions/bb-claude9b-long-pallets__flask-5014/debug_snapshot.json` 看到：后期反复执行同一个 `python -c "import flask; ..."`，反复收到 `ModuleNotFoundError: No module named 'flask'`，却未按任务提示激活 testbed conda 环境，也未改源码。增加步数没有让这条新采样轨迹摆脱错误恢复循环。

后续改进应先把黑盒 Agent 的解释器环境绑定到启动配置，例如 ClaudeCodeConfig.extra_env 的 PATH 以 `/opt/miniconda3/envs/testbed/bin` 开头，并设置 `CONDA_PREFIX=/opt/miniconda3/envs/testbed`、`CONDA_DEFAULT_ENV=testbed`，再验证停止行为。Mini 的官方启动命令已经做了这个环境绑定，Claude 当前默认实现主要依赖模型执行 shell 激活命令。这项建议未用于改写本轮成绩，也没有在记录外悄悄重跑。

包括这个额外长案例在内的 **25 条**正式 token trajectory 均通过结构检查。CPU / 25 个黑盒案例的执行均已结束，任务 Docker 容器当时自动清理。中断后保留的是源码、环境目录和实验文件；主 `ua-lab-cpu` 已恢复，原 `ua-lab-cpu-replay` 容器需重建，不能把历史容器状态当作当前状态。

```bash
docker exec -e DOCKER_HOST=unix:///lab/run/docker.sock \
  -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/blackbox-swe-cases.py \
  --agent claude --model Qwen3.5-9B \
  --base-url http://127.0.0.1:18081/v1 --tokenizer /lab/models/Qwen3.5-9B \
  --tool-parser qwen3_coder --tag bb-claude9b-long-replay \
  --output /lab/results/blackbox-swe-claude9b-long-replay \
  --instances pallets__flask-5014 --concurrency 1 \
  --steps 128 --context-limit 60000 --max-tokens 4096 --agent-timeout 1800
```

## 两模型短协议对照

在不重启服务、不重复 SWE 的条件下，额外运行 `scripts/blackbox-protocol-probe.py`：相同四组 messages、temperature=0、top_p=1、seed=20260911，分别给 128 和 512 两档 output budget。4B 与 9B 使用各自现有 vLLM 服务，均关闭 thinking。每个模型每档四次调用，总共 **16/16 按预期通过**，0 次截断、0 次请求错误、0 次重复检测命中。

| 提示 / 历史 | 两模型实际输出 | finish_reason |
|---|---|---|
| `17 + 25`，要求只答整数 | `42` | `stop` |
| 要求只答一个词 | `READY` | `stop` |
| 已有一次工具调用和 `6 passed` observation，要求结束 | `DONE`，没有继续调用工具 | `stop` |
| 要求调用无参 submit | 单次 `submit`，arguments 可解析且严格为 `{}` | `tool_calls` |

两档预算下结论一致。普通答案实际使用 2–3 个 completion tokens；submit 使用 4B 的 15 tokens / 9B 的 14 tokens。请求和原始响应逐个保存在 `results/blackbox-protocol-probe/`，机读汇总为 `summary.json`，日志为 `logs/blackbox-protocol-probe.log`。早期已有 `results/qwen9-submit-probe.json`，但缺对应原始请求，所以本次保留了成对的请求/响应来补足对照。

这排除了“9B 服务连最简单结束/空参数工具调用都做不到”的笼统解释。该探针走直连 vLLM 的 OpenAI chat path；它不能代替 Gateway token-completion path、Claude 内部提示或长上下文 Agent 行为测试。长预算 SWE 失败仍应依据实际循环、环境激活和停止策略诊断。

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/blackbox-protocol-probe.py \
  --output /lab/results/blackbox-protocol-probe-new
```

## 黑盒 coding toy 准备

`scripts/blackbox.Dockerfile` 从固定 digest 的 Python 3.12 slim 构建仅 721 MB 的镜像 `ua-lab-blackbox:20260911`，安装官方 recipe 固定的 mini-swe-agent 2.2.8、litellm 1.81.7，以及 git/tmux。Claude native binary 2.1.236 和官方 mini `run_agent.py` 分别仅挂载单个只读文件；没有挂载源码仓库、模型、Docker socket、home 或任何凭据进 coding sandbox。

`scripts/blackbox-toy-case.py` 使用原版 `DockerSandbox`、`ClaudeCodeAgent` / `MiniSweAgentAgent`、`_GatewayActor` 与真实 Qwen4B。任务是在一个新建小仓库修复 `stats.mean` 的整除 bug，先跑 baseline 证明确有错误，Agent 修复后再注入六个独立 verifier 检查。保存 patch、AgentResult、verification、token trajectories 与 normalized message snapshot。它验证黑盒工具执行和轨迹收集，**不是 SWE-bench 成绩，也不是 RL 参数更新**。

| 黑盒 coding case | Agent 状态 | 独立 patch 验证 | 轨迹 |
|---|---|---|---|
| Claude Code，默认任务说明，12-turn 上限 | **finished=true**，exit 0 | **6/6 通过**，只将 `//` 改为 `/`，测试文件未修改 | 1 条；prompt 1153、response 509、其中可训练 token 257；mask/logprobs 长度对齐 |
| mini-SWE，默认任务说明，12-step 上限 | **finished=false**，`LimitsExceeded` | **6/6 通过**，代码已修复，但提交格式失败 | 1 条；prompt 1209、response 1730、其中可训练 token 846；mask/logprobs 长度对齐 |
| mini-SWE，任务补充明确 bash tool 提交指引，20-step 上限 | **finished=true**，`Submitted`，实际 6 API calls | **6/6 通过**，生成正确 patch | 1 条；prompt 1284、response 1036、其中可训练 token 647；mask/logprobs 长度对齐 |

Claude Code 的实际工具链为 Bash 找文件 → Read stats.py → Edit 将 `//` 改为 `/` → Bash 跑 unittest → 文本总结。原始证据在 `results/blackbox-toy/blackbox-claude-toy/`；CLI reported `num_turns=5`，Gateway `num_turns=7` 的定义不同，比较 Agent 时不要混用这两个计数。`response_ids` 包括中间 observation，而 `response_mask=1` 只选模型输出；这就是 response token 总数大于可训练 token 数的原因。

mini-SWE 原始结果在 `results/blackbox-toy/blackbox-mini-toy/`。Qwen 有时把 bash command 当作不存在的工具名（第一次调用 `find`），有时把最终提交命令输出为 markdown 而非 bash tool call，导致没有 `Submitted`。这例即使 verifier reward=1，也必须按 `finished=false` 看待；**开启 `mask_unfinished_episode=True` 时**，未完成 episode 的 loss mask 会被清零。当前 `examples/mini_swe_agent/run_train.sh` 默认开启，而框架本身及通用 quickstart 默认关闭，不能泛化成全框架的默认行为。该次初版 harness 的 `result.json.passed=true` 仅表示独立 verifier 通过；后续 harness 已把该字段收紧为 verifier 通过且 episode finished，同时明确记录 `patch_verified` 与 `episode_finished`。

补充提示版本在 `results/blackbox-toy/blackbox-mini-toy-guided/`，增加的是交互/提交格式指引，不是修复答案。没有改 mini-SWE 源码，没有模拟模型输出；模型实际在 6 次 API 调用内完成修复和提交。这个对照说明小模型在黑盒 agent 上需要可靠地遵循 action/termination protocol；判断 rollout 的训练用途时，还应同时检查 finished 状态、mask 配置和 reward。

CLI 输出包含 `total_cost_usd` / `costUSD`，这是 Claude Code 根据前端名 `claude-sonnet-4-5` 计算的估算，不是本次账单。本实验的全部模型请求均由本机 Qwen 提供，未注入/调用 Anthropic 真实 API 凭据。原始代码通过没有掺入 train reward，因此 exported trajectories 的 reward 字段仍为 None；harness 单独保存 verifier reward，避免混淆“轨迹收集”和“完成 RL 训练”。

```bash
# 镜像只构建一次，保存在 /data2 中的独立 Docker daemon
cd /path/to/uni-agent-lab
docker -H unix:///path/to/uni-agent-lab/run/docker.sock build \
  --network host -f scripts/blackbox.Dockerfile \
  -t ua-lab-blackbox:20260911 scripts

# 真正的 Claude Code 工具工作流
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  export DOCKER_HOST=unix:///lab/run/docker.sock
  python /lab/scripts/blackbox-toy-case.py \
    --agent claude --session-id blackbox-claude-toy-replay
'

# mini-SWE 默认任务
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  export DOCKER_HOST=unix:///lab/run/docker.sock
  python /lab/scripts/blackbox-toy-case.py \
    --agent mini --session-id blackbox-mini-toy-replay
'

# mini-SWE 增加明确 tool 格式和提交流程的任务提示
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  export DOCKER_HOST=unix:///lab/run/docker.sock
  python /lab/scripts/blackbox-toy-case.py \
    --agent mini --session-id blackbox-mini-toy-guided-replay \
    --step-limit 20 --mini-explicit-tools
'
```
