# 独立核对训练器真正消费的源题与文章chunk

`audit_canonical_train_coverage.py`将固定`train.parquet`的512个源row作为依据，先核对整个parquet及全部512个row的manifest SHA256，再处理直接位于`run/rollout/1.jsonl`至指定步数的canonical文件。它不遍历旧attempt归档、agent_logs或TQ预取内容。

每个真实prompt UID必须对应一个唯一源题；同一个源row不能在另一步或另一个UID下再次消费。每组必须有rollout index0/1/2/3，且每个session必须完整覆盖该源题的全部记忆chunk，再有一个final context。gold逐行匹配固定源数据，最终boxed答案的原始reward重新计算后与FP32训练reward广播核对。只有严格匹配`pad<32位hex>_<rollout>_<context>`的UID才作为synthetic padding单独计数。

文章分块在CPU使用固定Qwen3-32B tokenizer和原生`split_context_into_token_chunks(..., chunk_size=5000)`重新构造。每个实际训练输入中的source section必须与重建chunk逐字相同，并保存各chunk SHA256。原始问题仅允许边界空白归一化；本次prefix实际没有发生这种差异。源问题经过这一规则仍是512个唯一问题，因此身份映射没有歧义。

已完成的1–96检查使用Transformers5.9.0、tokenizers0.22.2、Qwen2Tokenizer，耗时17.04秒：

| 观测项 | 已通过的prefix结果 |
| --- | ---: |
| canonical步骤 | 1–96 |
| 不同源row / 真实prompt组 | 384 / 384 |
| 每源题session数 | 恰4 |
| 真实session | 1536 |
| 真实context，包括final | 10388 |
| synthetic padding行 | 364 |
| attempt001 / 002 / 003的源题组 | 32 / 96 / 256 |
| 问题边界空白差异 | 0 |
| 与既有96步analysis的计数 | 一致 |

这一结果明确为`prefix_coverage_verified`、`full_training_coverage_verified=false`。还有128个固定源row未在被检查的prefix出现，这不是完整训练的遗漏结论。完整512题按已冻结源chunk数量计算，四个session的全部记忆context加final应为 **13872条真实context**；padding以实际canonical日志单独统计，不预先假设每个batch固定需要多少padding。

prefix JSON保留每个源row的全行hash、真实prompt UID、global step/attempt、四个session的context UID与实际chunk hash；CSV列出全部512个源row，清楚区分已消费与尚未出现在prefix的row：

- 1–96覆盖审计（原始文件：`results/large-memagent/durable-train-source-coverage-through96.json`）
- 512源row覆盖表（原始文件：`results/large-memagent/durable-train-source-coverage-through96.csv`）

最终128的独立审计在真实`run-outcome.json`为completed/exit0、canonical1–128全部写出后执行，使用新的输出名。完整模式还要求目录中没有多出的canonical步骤、512个源row各消费一次；它不会把额外的下一epoch预取或回滚归档算成训练数据。

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/rl/bin/python /lab/scripts/audit_canonical_train_coverage.py \
  --through-step 128 \
  --analysis /lab/runs/rl/memagent_32b_128_durable/analysis.json \
  --output /lab/results/large-memagent/durable-train-source-coverage-final128.json
```

上面的`--analysis`用于额外交叉核对最终分析计数，应等待唯一producer发布该文件。如果先独立证明源覆盖，可以省略这一可选参数，但不能省略真实128结束条件。该审计不加载模型或optimizer权重、不调用external评测，也不把数据覆盖等同于每步都有有效学习或最终能力提高。

2026-09-12 16:13:58 UTC，真实训练以 `completed` / `exit_code=0` 结束。随后使用唯一 producer 发布的最终 `analysis.json` 完成了全 128 步独立核对，耗时 22.53 秒，`full_training_coverage_verified=true`，`errors=[]`。

| 观测项 | 最终通过结果 |
| --- | ---: |
| canonical 步骤 | 恰好 1–128 |
| 不同源 row / 真实 prompt 组 | 512 / 512，每个源 row 恰一次 |
| 每源题 session 数 | 恰 4 |
| 真实 session | 2048 |
| 真实 context，包括 final | 13872 |
| synthetic padding 行 | 464 |
| attempt001 / 002 / 003 的源题组 | 32 / 96 / 384 |
| 问题边界空白差异 | 0 |
| 与最终 analysis 的组 / session / context / padding 计数 | 全部一致 |

每个源 row 的 hash、gold、全部 5000-token 分块原文以及最终 reward 广播都已核对。下一 epoch 的预取、回滚归档和未消费的 agent 日志没有进入这些计数。

- 最终 128 步完整源覆盖审计（原始文件：`results/large-memagent/durable-train-source-coverage-final128.json`）
- 全部 512 个源 row 的最终覆盖表（原始文件：`results/large-memagent/durable-train-source-coverage-final128.csv`）
