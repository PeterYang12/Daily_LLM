# 8B 独立审计入口

8B 使用独立的 `memagent_8b_128_durable` run、`rl8` 环境、checkpoint 目录和 Docker 容器。训练开始前已确认 Qwen3-8B 的 399 个参数张量、8,190,735,360 个参数元素和 0/18/35 层探针；训练 runtime 的 meta 模型构造与下载模型的所有 name / shape 对应。完整原生 model + 两组 Adam moment 的理论体积约 91.54 GiB，配置按 96 GiB 估算单份 checkpoint。

独立源审计使用 `scripts/rl8_coverage_audit.py`，模型 tokenizer、源 parquet 和 canonical run 均固定到 8B 路径。它只处理训练器实际消费的 `rollout/1..N.jsonl`，每源题四个 session，逐个验证原生 5000-token source chunk、gold、最终 reward 与广播、真实源 row 唯一性及 padding。最终完整模式仍要求真实 128 步正常退出和 512 个源 row 全覆盖。

独立监控审计 `scripts/rl8_monitor_audit.py` 支持 0→32、0/32→64、0/64→96 和 0/96→128。它直接从固定 64 题的完整原始 context 重建 source chunk，并与每一轮实际输入逐字对照；计数由 8B manifest 推导，不复用模型规模常量。窄格式归类只复用冻结的纯分类函数，指标仍为原生 LCS；不读取任何 32B 评测结果。

`scripts/rl8_live_status.py` 使用新的 `RL8_ATTEMPT_ID`，识别真实 8B attempt，排除回滚后废弃的日志后缀。主脚本 hash 和共用的纯日志 parser hash 都会写入 status。8B 真正启动后，已确认其 fresh 日志被识别为 `attempt-001`。

| CPU 预检查 | 结果记录 |
| --- | --- |
| 训练 runtime 的 8B meta name / shape / FP32 / 九个探针 | meta-shape-independent-review.json（原始文件：`results/qwen3-8b-preparation-20260912T1530Z/meta-shape-independent-review.json`） |
| 新 attempt 身份、回滚后缀、缺步及 append-only 日志解析 | live-status-independent-preflight.json（原始文件：`results/qwen3-8b-preparation-20260912T1530Z/live-status-independent-preflight.json`） |
| source chunk、缺 context、错 gold、错广播、NaN、错 step 与重复 UID 守卫 | audit-source-guards-preflight.json（原始文件：`results/qwen3-8b-preparation-20260912T1530Z/audit-source-guards-preflight.json`） |
| 两个 runtime、两个模型全部固定源输入与 chat template token 等价 | cross-runtime-comparison.json（原始文件：`results/qwen3-8b-preparation-20260912T1530Z/cross-runtime-comparison.json`） |

source 守卫的预检查明确使用原有 32B step 0 日志作为 CPU 解析 fixture，并对相同原始题目按 8B tokenizer 重建全部 64 题、451 个 context；损坏输入只存在于临时目录。这证明解析和拒绝逻辑，不是 8B 模型的输出或学习结果。两个 runtime 的完整 tokenizer 报告另行确认了源输入和 chunk 的等价性。

8B 的自动 controller 在真实 step 8 暂停后保存完整 checkpoint 验证和 async boundary 审计，记录 32 个已消费源题、cursor 36 及下一批四个真实 prompt UID。它完成 CPU 分析后重启自己的容器并执行 native resume。独立观察者 `scripts/independent_rl8_resume_step9.py` 在实际第 9 步出现后，比较四个 rank 的完整 save/load state 元数据、归档 commit 指纹、399 个 Adam step counter、scheduler / RNG hash，并核对第 9 步确实消费这四个已缓存的 prompt。

下面两个命令只在真实第 9 步日志已写出后执行；观察者是新增独立 CPU 脚本，不修改冻结的训练或 controller 源文件：

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/rl8/bin/python /lab/scripts/rl8_coverage_audit.py \
  --through-step 9 \
  --output /lab/results/rl8-memagent/durable-train-source-coverage-through9.json

docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/rl8/bin/python /lab/scripts/independent_rl8_resume_step9.py \
  --coverage /lab/results/rl8-memagent/durable-train-source-coverage-through9.json \
  --output /lab/results/rl8-memagent/durable-resume-step9-independent-audit.json
```

第 8 步的整数 dataloader / TQ 检查不能直接套用到 final 128，因为最终 checkpoint 可以包含下一 epoch 的预取。完整源覆盖审计只计算 canonical 1–128，并明确排除这些预取。真实 save/load 恢复检查也不声称异步生成在重启后可以逐 bit 重放。

最终 controller 会在分析、导出和 native/effective BF16 参数差异完成后自动运行全源覆盖及最终监控审计。外部效果需继续使用各模型相同的冻结 serving 协议、原生 reward 和独立 repeat；本页的预检查不代替这些结果。

2026-09-12 的真实 step 8→9 独立验收已经通过。四个 rank 的保存与恢复 state JSON 完全相同，每个 rank 均有 399 个已初始化 Adam 状态，counter 56、scheduler epoch 8；完整 RNG hash、模型 / moment 探针及有效 BF16 探针均匹配。归档 fingerprint 的文件大小和确定性 probes 与原生 step 8 commit 记录一致。

第 8 步保存时已消费 32 道题，dataloader cursor 为 36，另四个 prompt 均为 `finished`。真实 canonical 第 9 步准确消费了这些 UID 对应的源 index 485、662、337、47，产生 16 个 session 和 112 个 context。第 9 步记录的 PG loss 为 0.0148335262，gradient norm 为 0.3826972454，KL loss 为 0.0272130485，learning rate 为 1e-6，advantage 范围为 [−0.375, 0.125]。这些是实际恢复后的训练观测。

独立的 prefix 1–9 源覆盖同时通过：36 道源题各一次，144 个 session，968 个真实 context，40 条 padding；源 row hash、gold 和每个文章 chunk 逐项匹配。这仍是 prefix 验证，不能称为 128 步完整完成。

控制器另行完成了真实 step 8 的 CPU native 参数差异检查：九个完整张量共 100,675,584 个元素，其中 100,660,240 个 FP32 元素、4,534,376 个转换为 BF16 后的元素与 base 不同，数值有限。它说明更新并非只停留在 FP32 量化误差以下，不能据此推断最终任务效果。

- 实际 native 8→9 恢复独立审计（原始文件：`results/rl8-memagent/durable-resume-step9-independent-audit.json`）
- 实际 prefix 1–9 完整源核对（原始文件：`results/rl8-memagent/durable-train-source-coverage-through9.json`）
- 512 源 row 的 prefix 覆盖表（原始文件：`results/rl8-memagent/durable-train-source-coverage-through9.csv`）
- 控制器的 step 8 native / effective BF16 差异（原始文件：`results/memagent-8b/native-step8-weight-deltas.json`）
