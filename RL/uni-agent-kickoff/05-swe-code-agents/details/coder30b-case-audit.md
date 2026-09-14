# Coder30B 六题与真实黑盒 Agent 审计

固定模型为 `Qwen/Qwen3-Coder-30B-A3B-Instruct`，revision `b2cff646eb4bb1d68355c01b18ae02e7cf42d120`。这是总参数约 30.53B 的 MoE 模型；本页是本地推理与 Agent 执行记录，没有对该模型执行 RL 更新。32B dense 的长步数训练由独立 MemAgent 记录负责。

ReAct、Claude Code 和 Mini-SWE 均使用原先已验证的同一组六题。原代码 baseline 为 0/6，gold oracle 为 6/6。下面的数字只适用于这个诊断子集，不能外推为完整 SWE-bench 分数；三种 Agent 的内置提示和工具接口不同，也不能据此推断某个组件的独立因果效果。

| Agent / Coder30B | 官方 verifier resolved | Agent finished | 两者均满足 | 外层异常 |
|---|---:|---:|---:|---:|
| ReAct | 4/6 | 5/6 | 4/6 | 0 |
| Claude Code 2.1.236 | 3/6 | 5/6 | 3/6 | 0 |
| Mini-SWE 2.2.8 | 2/6 | 1/6 | 1/6 | 0 |

| Case | ReAct reward / finished | Claude reward / finished | Mini reward / finished | 审计要点 |
|---|---|---|---|---|
| Flask-5014 | 1 / true | 1 / true | 1 / false | 三者均修复空 Blueprint name；ReAct/Claude 向已有测试文件添加测试，违背该条任务约束；它们独立 source-only 控制仍通过 |
| Requests-6028 | 0 / false | 0 / true | 0 / false | 带用户名认证 URL 的 FAIL_TO_PASS 项仍失败 |
| pytest-5262 | 1 / true | 0 / false | 0 / false | ReAct 改 `EncodedFile.mode` 表示；Claude 改底层流为 text 后触发 bytes 写入 TypeError；Mini 没留下源码修改 |
| pytest-7432 | 0 / true | 0 / true | 0 / false | ReAct/Claude 的目标 FAIL_TO_PASS 已过，但 `--runxfail` 的一个 PASS_TO_PASS 回归 |
| pytest-7521 | 1 / true | 1 / true | 1 / true | 三者均修复捕获时的换行处理；Mini 明确 Submitted |
| pytest-7982 | 1 / true | 1 / true | 0 / false | ReAct/Claude 把目录遍历改为跟随 symlink；Mini 的自写诊断循环卡住，耗尽 600 秒 |

Claude 六题合计墙时 **511.58 秒**，每个 Agent 最多 40 turns、上下文 55,000 tokens、每轮输出 2,048 tokens，temperature=0.2、top_p=0.9，Agent 600 秒、verifier 300 秒。并发 2，与同时运行的 ReAct 共用 Coder30B 服务，墙时不能当成独立性能基准。没有修改原 `blackbox-swe-cases.py` 的 prompt 或工具设置来追求高分。Claude 的 `--bare --no-session-persistence`、Bash/Read/Edit/Write 四工具及 pinned CLI 均沿用旧六题配置，所有策略模型调用发往本机 Gateway/vLLM。

ReAct 详细文件位于 `results/large-swe/coder30b-six-recovery/`；Claude 位于 `results/large-blackbox/claude-coder30b-six/`，Mini 位于 `results/large-blackbox/mini-coder30b-six/`。三个目录的 `case-audit/cases.csv` 可直接用于汇报，`case-audit/case-audit.json` 保存逐文件变更类型、测试约束标记和完整失败测试清单，`case-audit/*.content-only.patch` 去除了无内容变化的文件块。审计脚本是 `scripts/audit_swe_saved_cases.py`。

pytest7432 的镜像原本有大量 mode 差异；即使保存文件名为 `candidate.content.patch`，仍有 **538 个仅 mode 的文件块**。ReAct 的真实内容只有 `src/_pytest/skipping.py` 的 +4/−2 行，Claude 只有同文件 −2 行。不能把这些 mode 差异写成模型修改了 539 个源码文件或测试文件。普通 `git diff` 还可能漏掉未追踪的新文件，因此本审计仅将明确修改已有测试的 hunk 标记为约束违反；没有把所有 test 路径或临时新测试一概当作违规。

Claude/pytest5262 的 verifier 报告有 **78 个失败的 PASS_TO_PASS 条目**。其中一个是上游已有元数据中的 `[100%]` 字符串，不是正常测试 node ID；baseline 和 gold 控制也包含它。本页保留原 verifier 计数，不把它包装成 78 个独立测试回归。原始 stdout 可直接看到多处 `TypeError: write() argument must be str, not bytes`，其余可识别测试失败与错误的底层 text stream 修改一致。

## Mini 的提交与工具执行

Mini 的原 40-turn、55,000 上下文、每轮 2,048 output tokens、600 秒 Agent/300 秒 verifier 预算和既有三次 bash 提交指引全部保留。它并发 2，与扩展 ReAct 任务并发 4 共享服务，总墙时 **947.40 秒**。Flask、Requests、pytest5262、pytest7432 都是 40 次 API calls 后 `LimitsExceeded`；pytest7521 在 35 次 calls 后 `Submitted`；pytest7982 是工具卡住导致 Agent timeout。0 个外层异常不代表每个 Agent 正常结束，必须同时看这些终止状态。

Flask 轨迹有 7 次非零工具返回，其中 3 次明确 `/bin/sh: Syntax error`。模型最后才用 shell 插入正确源码，随后到达预算上限，没有发出提交命令。Mini 的六题没有观察到已有测试文件内容修改；Requests、pytest5262、pytest7982 没留下 tracked source patch。

pytest7982 的实时只读进程观察保存于 `live-hang-observation.json`。模型写了如下逻辑，每次循环重建目录迭代器，从同一个首项重新开始：

```python
entry = next(os.scandir("test_symlinks"))
while entry.name != "symlink_dir":
    entry = next(os.scandir("test_symlinks"))
```

这条工具执行一直等到原 600 秒 Agent 预算耗尽；没有外部终止、额外提示或重试。task.log 保存 `rc=-1`、`exit_status=error` 与随后官方 verifier reward=0。Gateway 中有 18 条 assistant 消息、17 条工具观测，最后一条工具调用没有正常返回。这体现了代码 Agent 的工具执行和终止策略对效果的实际影响。

## Gateway 轨迹证据

Claude 六个真实 session 全部通过 token ID 非负整数、response mask 长度与二值性、logprobs 长度与有限性、上下文上限检查。合计 prompt 11,978 tokens，response 段 145,320 tokens，其中 mask=1 的模型输出 26,253 tokens；其余 response 段包含工具观测或其他被屏蔽的内容，不能把全部 response tokens 当成模型生成量。检查文件为 `trajectory-validation.json`。

Mini 六个 session 也全部通过同样检查：prompt 11,470 tokens、response 段 72,204 tokens、mask=1 的模型输出 22,035 tokens。两套真实黑盒新增共 12 个被审计 session；原先 4B/9B 的 25 个 session 记录保持独立。

两套原始 Gateway `Trajectory.reward_score` 和 `Trajectory.finished` 仍为 `null`，这是手动 Gateway 调试入口的输出；真实 task 的 reward 与 finished 在逐题 `TaskResult` 中保存。各自的 `reward-finished-join.json` 按精确 session_id 将两者并排关联，**没有回写原始轨迹，也没有执行 trainer 的 reward attachment、unfinished mask 或 optimizer update**。例如 Flask 的 Claude CLI 是 12 turns，Gateway 记录 14 turns；两种计数含义不同，不能混用。

实际运行软件版本和宿主内核保存在 `runtime.json`，输入哈希、命令、模型 revision、CLI 哈希在 `provenance.json`。本轮宿主是 kernel `6.8.0-38-generic`、amdgpu module `6.16.13`；服务和 CPU client 的 Transformers 均为 `5.16.1`。与早期 4B/9B 记录比较时应保留环境变化这一限制。

## Flask source-only 功能控制

ReAct 和 Claude 都向已有 `tests/test_blueprints.py` 增加了测试。这一约束违反保留在主结果中；既不静默删除模型改动，也不改写 reward。另在两个新的、相同 digest 的原干净 Flask 镜像中，只应用各自 `src/flask/blueprints.py` 的 source hunks，剥离全部测试 hunks，再执行同一个官方 verifier。

| 保存的 source patch | 模型调用 | verifier reward | FAIL_TO_PASS | PASS_TO_PASS |
|---|---:|---:|---:|---:|
| ReAct source-only | 0 | 1 | 1/1 | 59/59 |
| Claude source-only | 0 | 1 | 1/1 | 59/59 |

两份 source patch 字节不同：ReAct 同时拒绝空白字符串，Claude 拒绝空字符串。因此实际分别执行了两次 CPU 控制，各约 3 秒。它们说明本次官方 verifier 通过来自保存的源代码修复，不依赖模型添加的测试；这不是新的模型 attempt，不进入六题分母。

证据在 `results/flask-source-only-audit-20260912-v2/`，含每份 source-only patch、原 patch SHA256、应用前后路径、verifier 输出及结果。最初控制的启动检查错误地要求 image HEAD 与 dataset base_commit 相同；官方镜像额外带有空的 `SWE-bench` 提交，实际代码树完全相同。该检查在应用补丁和执行 verifier 前退出，原失败记录保留于无 `-v2` 的目录；修正为验证完整代码 diff 为空与 worktree 干净后，才执行上述两份控制。

复现黑盒评测需使用新输出名字：

```bash
python3 scripts/run_large_blackbox.py \
  --agent claude --name claude-coder30b-six-new --concurrency 2
python3 scripts/run_large_blackbox.py \
  --agent mini --name mini-coder30b-six-new --concurrency 2
```

原输出入口拒绝覆盖已有证据。运行依赖恢复的 `ua-lab-cpu`、独立 sandbox daemon，以及本地 `18082/v1` 的固定 Coder30B 服务。
