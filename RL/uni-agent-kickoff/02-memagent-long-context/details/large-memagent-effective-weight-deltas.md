# 32B pilot 的有效精度审计

32B独立pilot的step1 HF export已做CPU核对。**抽样张量存在FP32变化，转成BF16后仍保留2.25087%的元素变化**；这个结论说明权重在BF16表示下并非全部不动，不代表任务能力改善或live vLLM同步已正确完成。

输入为固定 `models/Qwen3-32B` 与 `runs/rl/memagent_32b_pilot_restart/checkpoints/global_step_1/actor/huggingface`。原始结果：pilot-effective-weight-deltas.json（原始文件：`results/large-memagent/pilot-effective-weight-deltas.json`）；执行日志：cpu-large-pilot-weight-deltas.log（原始文件：`logs/cpu-large-pilot-weight-deltas.log`）。没有修改早期小模型的effective-weight-deltas总表。

## 全部HF文件的header核对

| 项目 | 原始32B | pilot step1 |
|---|---:|---:|
| 张量数 | 707 | 707 |
| 元素数 | 32,762,123,264 | 32,762,123,264 |
| 实际safetensors dtype | 全部BF16 | 全部F32 |
| shards | 17 | 3 |
| payload字节 | 65,524,246,528 | 131,048,493,056 |
| 含header的文件字节 | 65,524,328,560 | 131,048,575,376 |

导出的真实payload约122.048GiB，验证了HF-only保存仍保留FP32 master的磁盘规划。全部tensor名称、shape、index映射、data offsets与文件长度一致。记录包含每个header SHA256、index/config SHA256和file size/mtime；没有逐字节hash全部约183GiB的模型payload。

## 九个张量的数值比较

按运行前固定规则选择第0、32、63层的q_proj、o_proj、input_layernorm，共251,673,600个元素。先将原始BF16与导出FP32无损提升为FP64比较；另将两侧分别cast BF16后再提升FP64比较。变化定义是精确数值不等，不使用epsilon阈值。

| layer / 张量 | stored变化比例 | BF16变化比例 | BF16变化元素数 |
|---|---:|---:|---:|
| 0 / q_proj | 99.97484% | 2.24476% | 941,521 |
| 0 / o_proj | 99.97514% | 2.21664% | 929,725 |
| 0 / input_layernorm | 99.98047% | 0.15625% | 8 |
| 32 / q_proj | 99.97750% | 2.45436% | 1,029,434 |
| 32 / o_proj | 99.97421% | 2.04827% | 859,105 |
| 32 / input_layernorm | 99.68750% | 0.01953% | 1 |
| 63 / q_proj | 99.97489% | 2.25260% | 944,809 |
| 63 / o_proj | 99.97492% | 2.28941% | 960,249 |
| 63 / input_layernorm | 94.88281% | 0 | 0 |

按元素数加权，stored变化251,611,035个（99.97514%），最大绝对差4.76837158203125e-6；BF16变化5,664,852个（2.25087%），最大绝对差3.814697265625e-6。97.74857%的stored变化经BF16舍入后与原始值相同。全部抽样值有限；以上比例只覆盖列出的9个张量，不代表全模型参数变化比例。

## 复现与资源

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/cpu-large-effective-weight-deltas.py \
  --checkpoint runs/rl/memagent_32b_pilot_restart/checkpoints/global_step_1/actor/huggingface \
  --output results/large-memagent/pilot-effective-weight-deltas-replay.json
```

输出必须使用新文件名，已有文件会被拒绝覆盖。脚本 cpu-large-effective-weight-deltas.py（原始文件：`scripts/cpu-large-effective-weight-deltas.py`） 复用原 cpu-effective-weight-deltas.py（原始文件：`scripts/cpu-effective-weight-deltas.py`） 的数值helper；两个脚本hash均写入结果。通过safetensors mmap逐次读一对张量，没有实例化模型、加载optimizer或GPU device。torch使用4线程，本次处理7.368秒、进程峰RSS2.432GiB，退出码0，符合CPU容器32GiB限制。

此前训练rank的Adam counter=7是另一组观察证据；counter、FP32/BF16变化、nonzero任务policy梯度和评测收益应分别报告。此pilot用于工程校准，正式128步训练仍需自己保存checkpoint与完整外部评测结果。

## 正式训练的第32步

正式运行 `memagent_32b_128` 的 step32 已完成相同CPU审计，结果在 step32-effective-weight-deltas.json（原始文件：`results/large-memagent/step32-effective-weight-deltas.json`）。该运行从原始base重新开始，没有从上述pilot继续训练。

全部707个tensor的名称/shape/header/index/文件长度一致，仍为F32 payload 131,048,493,056字节。相同的9个tensor、251,673,600个元素中，stored变化251,661,234个（99.99509%）；独立cast BF16后变化 **34,038,531个（13.52487%）**，最大绝对差 **0.0001220703125**，全部抽样值有限。

四个训练rank的Adam counter均为224，计数文件与HF差分是独立证据。该比例用于确认推理精度下存在可表示变化；监控集分数及最终external64仍须分别评估，不能用参数变化比例代替能力分数。
