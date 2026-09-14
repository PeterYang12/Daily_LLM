# SWE-bench 本机实验记录

[本实验总览](README.md) · [全部实验](../README.md)

固定 Uni-Agent `472c875`，官方数据集 `princeton-nlp/SWE-bench_Verified`，使用官方 `build_swe_bench_verified` 预处理。全部任务环境运行于 Docker；模型运行于 ROCm vLLM 容器。

## 先验证奖励，再比较模型

初选 Flask 5014 与 Requests 2317 / 5414 / 6028。官方 parallel API 示例成功运行 4 个 episode，4B 模型修复 Flask 5014 并通过官方 FAIL_TO_PASS 与 PASS_TO_PASS 检查。其余结果需要进一步区分：

- Requests 2317：gold oracle 在 300 秒评测超时；4B 还生成了语法错误的候选补丁。两类问题均存在，不能把本次 oracle 失败视为模型能力结论。
- Requests 5414：gold oracle 的 FAIL_TO_PASS 通过，但镜像未安装 `pytest-httpbin` / `httpbin`，导致 158 个 fixture 错误及其他回归测试失败。原始 stdout 位于 `results/swe-oracle-audit/psf__requests-5414/verifier.stdout`。
- Requests 6028：oracle 成功；初始 16K 模型服务下工具输出令下一轮请求超长，ReAct 收到 HTTP 400。ReAct 的 token 预算检查没有提前计入新增的工具输出。

因此扩展到 **6 个可校验样本**：Flask 5014、Requests 6028、pytest 5262 / 7432 / 7521 / 7982。每个都做负正对照：

- 未修改仓库：**0/6 通过**，`results/swe-baseline-six/`。
- 数据集标准补丁：**6/6 通过**，`results/swe-oracle-six/`。

该子集用于后续 ReAct / Claude Code / Mini-SWE 对照。它是按镜像成本与验证可运行性挑选的少量 smoke case，**不是随机采样的全量 SWE-bench 分数**。

## 运行器与审计

- `scripts/run_swe_react.sh`、`scripts/run_swe_oracle.sh` 直接运行官方 Ray parallel 示例，初始日志与结果保留。
- `scripts/audit_swe.py` 使用原版 Task / Agent / reward，只有观察性 hook：保存 AgentResult、候选补丁、原始 verifier stdout/stderr 与最终结果。没有改变判分。
- `--baseline` 跳过 Agent / 标准补丁，在原始仓库直接调用原版 verifier。
- 初始配置 `configs/swe-react-4b.yaml` 为 16K；正式比较配置 `configs/swe-react-64k.yaml` 为 64K 服务、40 轮上限、每轮最多 2048 输出 token、temperature=0.2。
- 4B 模型：`Qwen/Qwen3-4B-Instruct-2507`，HF revision `cdbee75f17c01a7cc42f958dc650907174af0554`，hermes tool parser。
- 9B 模型：`Qwen/Qwen3.5-9B`，HF revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`，qwen3_coder tool parser，禁用 thinking，language-model-only。只验证文本代码任务。

## 已发现的统计注意点

官方 API runner 的 `timeout_or_error=0` 并不保证 Agent 没有失败：ReAct 内部捕获 API 错误并返回未完成的 AgentResult，Task 仍可继续 verifier。必须同时读取 Agent `finished`、`info`、终止日志和 verifier 结果。

官方 verifier 的 shell 最后执行 `git checkout`，其 exit code 可以覆盖 pytest 的失败状态；`eval_completed=true` 不能替代 `resolved` 和逐测试状态。保留 stdout 对审计特别重要。

最终模型 / harness 结果已完成，见 [本实验总览](README.md)、[Coder30B 逐题核对](details/coder30b-case-audit.md) 和 [扩展 29 题](details/swe-expanded.md)。

## 原始补丁里的 file mode 噪声

pytest-7432 基础镜像自身已经有大量 tracked file 的 100644→100755 mode 差异；baseline 的 raw candidate.patch 也能看到，并非都是模型操作。原版 verifier 使用 `git -c core.fileMode=false diff`。审计 runner 后续同时输出原始 `candidate.patch` 和忽略 fileMode 的 `candidate.content.patch`，比较代码内容时以后一种为准；较早结果保留原始文件，不能按 diff 路径数推断模型改了多少源码。
