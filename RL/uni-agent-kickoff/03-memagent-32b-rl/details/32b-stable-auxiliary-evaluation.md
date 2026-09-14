# 32B external64：独立batch-invariant辅助协议

此辅助实验于2026-09-12追加，用于检查完整多chunk任务的重复性；它不替换冻结primary，也不根据分数选择较高的一次。固定prompt的62次一致性只覆盖一个短输入，不能直接推广为64题稳定。

辅助协议（原始文件：`notes/rl-memagent-32b-stable-auxiliary-protocol.json`）在首次辅助64模型调用前冻结。先顺序运行`base-durable-stable`与`base-durable-stable-repeat`，两组均完整保留；最终模型预定另跑`final-durable-stable-step128`与其repeat，当前阶段不执行final。两组已于06:20–06:59 UTC完整完成；各64/64、0错误，严格profile与数据完整性核验全部通过。

## 已完成的两次完整结果

| 指标 | base-durable-stable | base-durable-stable-repeat |
| --- | ---: | ---: |
| 完整题数 / 错误 | 64 / 0 | 64 / 0 |
| 原生boxed-answer LCS | **0.42090773809523807** | **0.42090773809523807** |
| LCS=1 | **20/64** | **20/64** |
| memory chunks / 全部生成steps | 388 / 452 | 388 / 452 |
| server日志内实际HTTP200生成请求 | 452 | 452 |
| 官方infer墙钟时间 | 1134.094秒 | 1136.609秒 |
| wrapper subprocess墙钟时间 | 1138.477秒 | 1140.979秒 |

两遍的 **64/64最终response逐字相同，64/64 reward相同**，没有升分或降分题，平均LCS差为0。LCS=1沿用本项目scorer定义，不冒充HotpotQA标准EM。两次都是同一base模型，因此这些数据证明本次重复性观察，不是RL收益。

完整对照JSON（原始文件：`results/large-memagent/stable-base-repeatability/comparison.json`）与64题first/repeat CSV（原始文件：`results/large-memagent/stable-base-repeatability/paired.csv`）保留全部题；最终验证记录（原始文件：`results/large-memagent/stable-auxiliary-validation.json`）核对了14项冻结输入、4项primary保留hash、同一API进程及每遍452次实际HTTP200生成请求。原始结果分别见第一遍summary（原始文件：`results/large-memagent/base-durable-stable/summary.json`）与repeat summary（原始文件：`results/large-memagent/base-durable-stable-repeat/summary.json`），各目录保留provenance、JSONL与infer.log。

完整response一致性只针对每题最终回答；未逐条比较中间memory原始API内容或logits，也没有测试服务重启后的相同输出。本次64题的两遍一致不构成任意输入、并发或设备的全局保证。与[primary同base复测](32b-base-repeat-diagnostic.md)是不同推理profile，不能按分数择优覆盖primary。

## 实际服务与核验边界

数据仍为固定external64，5000-token chunk，memory/final各1024 token，greedy，concurrency8。服务为HIP1、18084、`ua-lab-infer32-stable`，实际`VLLM_BATCH_INVARIANT=1`、TRITON_ATTN、prefix cache开启、max_num_seqs16，BF16/TP1/eager/16384/非thinking。两次base在同一服务进程中顺序执行，不重启、不清缓存；独立记录实际耗时。

使用新运行入口（原始文件：`scripts/run_stable_memagent.py`）和独立profile核验（原始文件：`scripts/stable_memagent_profile.py`），从真实API进程读取argv、HIP环境、进程start ticks与stdout路径；从该保留日志核实engine model、dtype、seed和TRITON后端。运行前后均核验，记录实际服务/客户端runtime及冻结源码hash。原`run_large_memagent.py`仍保留18083的primary逻辑。

新分析入口（原始文件：`scripts/analyze_stable_memagent.py`）复用原64题ID、原生LCS重算、chunk覆盖和重复成功拒绝逻辑，并严格检查辅助profile、所有推理参数、环境、runtime、phase配对顺序以及保留日志prefix的hash/原文。primary结果即使题目相同也被拒绝。同模型repeat只报告first/repeat的完整response一致数、reward一致数和描述性分数差异；不输出RL提升CI。只有未来合法base-final配对才使用原paired bootstrap函数。

9项元数据guard验证（原始文件：`results/large-memagent/stable-auxiliary-preflight/guard-validation.json`）包括实际服务metadata通过、primary混入拒绝、错误endpoint/task配置、关闭batch-invariant、冲突GPU selector、错误backend与log hash拒绝。它是metadata层的合成负对照，不是额外模型评测。

复现当前已冻结辅助phase的入口为：

```bash
python3 scripts/run_stable_memagent.py --name base-durable-stable --check-service
python3 scripts/run_stable_memagent.py --name base-durable-stable
python3 scripts/run_stable_memagent.py --name base-durable-stable-repeat
```

这些固定输出名拒绝覆盖，已完成后不能直接重跑覆盖原证据。需要新一轮实验时应新建明确协议与phase名称；不能暗中改变现有profile的源码、数据或推理参数。


完整repeat分析入口：

```bash
python3 scripts/analyze_stable_memagent.py \
  --before results/large-memagent/base-durable-stable/results.jsonl \
  --after results/large-memagent/base-durable-stable-repeat/results.jsonl \
  --mode repeatability --output-dir results/large-memagent/stable-base-repeatability
```

上述目录已存在，入口会拒绝覆盖。未来final128辅助服务应使用`stable_memagent_profile.py`的`serve_argv(FINAL_MODEL)`与`ENVIRONMENT`生成同一配置，模型HF路径已经固定在协议内；使用新的独立stdout日志，不能覆盖baseline/probe的保留日志。`run_stable_memagent.py`会从实际进程和新日志核验profile；最终两遍和base-final分析留待root后续执行，当前均未启动。

辅助冻结协议JSON和纳入hash的源码保持不变；进度、结果及补充说明写入独立状态/分析文件，避免使已保存provenance的协议hash失效。固定prompt阶段服务日志对应的精确hash前缀已额外留存；64题阶段只追加同一日志，见日志前缀留存（原始文件：`results/large-memagent/greedy-stability-configs/append-only-log-preservation.json`）。
