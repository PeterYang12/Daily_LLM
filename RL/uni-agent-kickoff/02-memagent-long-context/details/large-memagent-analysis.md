# 32B MemAgent 外部配对评估

`scripts/analyze_large_memagent.py` 只分析固定的 **64 道 external 题**（原 dev parquet 第 64–127 行）。训练中的 dev 0–63 行、pilot 的 4 题 dev 和训练 rollout 均不进入这里。它用宿主 Python 标准库运行，仅通过文件路径载入已固定的、无第三方依赖的原生 LCS reward 函数，不载入模型或占用 GPU。

运行完整对照：

```bash
cd /path/to/uni-agent-lab
python3 scripts/analyze_large_memagent.py \
  --before results/large-memagent/base-durable/results.jsonl \
  --after results/large-memagent/final-durable-step128/results.jsonl \
  --output-dir results/large-memagent/comparison-durable-step128
```

输出目录必须不存在。当前协议选择新`memagent_32b_128_durable`的最终step128，HF先导出至run下`exports/global_step_128/huggingface`。`base-durable`已在driver7.1.0.31500000上完成64/64，LCS0.4362723214285714、LCS=1为19/64；只与同runtime的`final-durable-step128`配对。旧`base-recovery`属于driver6.16.13，原`base`仅30/64，均作为历史保留，不能拼接或混入当前对照。

只核对当前 baseline 的完整性：

```bash
python3 scripts/analyze_large_memagent.py \
  --before results/large-memagent/base-durable/results.jsonl \
  --output-dir results/large-memagent/base-durable-audit-replay
```

每次运行产出 `comparison.json` 和 `paired.csv`。CSV 按固定 external 数据顺序逐题排列，包含完整问题、ground truths、两次最终响应、LCS、LCS=1 标志、context/step 数和配对变化。缺题仍在 CSV 留行。分析器不更改评测原始 JSONL、provenance 或训练目录。

完整性的要求包括：

- 正好覆盖冻结的 64 个 `sample_key`，不接收额外题；`id/index/source_index/question/answers` 与冻结数据逐一一致。
- 每次成功结果的 chunk 数与 tokenizer/5000-token chunk audit 一致，`num_contexts` 和 `total_steps` 均为 chunk 数加最后回答的一次调用。
- reward 必须在 [0,1]，与原生 reward 函数按最终响应重算一致。官方 infer 中名为 `exact_match` 的字段这里只称 **LCS=1**，不解释成标准 HotpotQA EM。
- 失败后追加一个成功结果的正常 resume 可接受，所有失败尝试保留在 `retry_history`；同题多次成功、成功后又重跑均报错，避免通过挑选成功轨迹改变结果。
- 两个 run 目录各有 `provenance.json`，external/manifest/config/infer/serve 脚本 hash 一致，任务、tokenizer、chunk/model 参数一致；记录的 model root 与服务返回信息相符，base/final model root 不同。服务 context 固定为 16384。
- 两次 provenance 都有 `runtime`，包含 host kernel、amdgpu module version、service/client 的 image ID/reference 和包版本；该对象必须完全相同。未知 driver version 可以是 `null`，但会单独记录 unknown，两个 `null` 不能证明驱动一致。runtime 缺失或不同将阻止正式配对统计。

若任一 run 缺题、存在 unresolved error、ID/reward/context 不一致，或协议核对失败，`comparison` 是 `null`，退出码为 **2**。它不会用已完成部分估算 64 题最终效果。成功的单独 baseline 审计退出码为 0；完整配对审计也为 0。provenance 的原 `status` 只记录，不会把 `running` 擅自改成 `completed`。

完整配对用每题一次的 `final LCS - base LCS` 计算平均差值，并以**题目为重采样单位**做 paired percentile bootstrap：默认 Python `random.Random(20260912)`，20,000 个重复，2.5%/97.5% 分位点采用相邻顺序统计量线性插值。JSON 还记录逐题 LCS 增/减/平、LCS=1 的两次计数及获得/失去/两次均为1/两次均不足1四种配对情况。

这个 CI 描述固定小型 external 子集上的题目差异，不包含重复训练 seed 的方差；它不把 context/token 当独立样本，也不能从单次训练证明稳定可推广收益。训练过程可以看 dev 曲线，但不能用 external 选择 checkpoint/改超参数。即使 CI 偏正，也应连同具体分母、实验范围与单次运行限制一起报告。

实现已做 9 项合成数据验收，记录在 analysis-validation.json（原始文件：`results/large-memagent/analysis-validation.json`）：包括已知 +0.5 配对差值的退化 CI、无序 JSONL 的 ID 配对、30/64 缺题不产聚合值、成功重复拒绝、错误重试保留、元数据与 reward 篡改拒绝，以及固定 seed 重复一致。真实中断 baseline 的 完整性审计（原始文件：`results/large-memagent/analysis-base-interrupted/comparison.json`） 核对通过已有 30 题的 ID/reward/context，但缺 34 题，因此 `comparison=null`；这不是最终效果。该早期审计生成时尚未加入 mandatory runtime 检查；旧 baseline 没有 runtime snapshot，现版本也会将其标成协议不完整。

新增 runtime 检查另有 6 项验收（原始文件：`results/large-memagent/analysis-runtime-validation.json`）：相同 runtime 可比但 null driver 仍未知，kernel/package/image 差异、缺失 runtime、缺必需包版本均阻止配对。

服务脚本 hash、`/models` 和 runtime 元数据仍不能独立证明实际载入 tensor 或源码 overlay 内容完全相同。正式报告还需结合模型/检查点来源、训练日志与有效 BF16 权重差分记录；未知 driver version 如实保留。分析器不将任意一个不同文件路径自动解释成“RL 已有效学习”。
