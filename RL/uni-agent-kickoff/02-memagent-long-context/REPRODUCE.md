# 长文 MemAgent 推理：运行步骤

[本实验总览](README.md) · [全部实验](../README.md)

这里复现原始模型的长文推理，不执行 128 步 RL。先完成 [公共环境准备](../00-overview/SETUP.md)，准备完整实验工程中的 `scripts/run_memagent.sh`、官方 `examples/mem_agent/infer.py`、模型服务与数据；这些完整运行材料未随本目录收录。

## 1. 固定输入与模型服务

使用 `BytedTsinghua-SIA/hotpotqa` 的 revision `27275ff4fee67ac0acb6478e405e7ac07efbdc1a`。只使用有真实字符串 context 的 50 / 200 / 800 / 3200 / 6400 文档档位，先检查数据格式；`eval_12800.json` 的整数数组不属于本实验有效长文输入。

按公共准备启动选定的 Qwen/vLLM，核对 model name、tokenizer、上下文长度和服务地址。原 4B 示例服务使用端口 18080；该值是示例运行约定，目标机器需与 runner 保持一致。

## 2. 用已验证的入口运行

在完整 lab 根目录执行，run 名称应为尚不存在的新名字：

```bash
# 参数顺序：文档数、样本数、新 run 名、模型目录名、服务端口。
bash scripts/run_memagent.sh 50 8 replay-docs50 Qwen3-4B-Instruct-2507 18080
```

入口会通过实验容器运行官方推理流程。五档原协议的样本数依次为 8 / 8 / 4 / 2 / 1；选择其他模型时同时核对模型目录与对应服务，而不只修改显示名称。完整来源和已测结果见 [REPORT.md](REPORT.md)。

## 3. 保持实验参数与记账一致

- chunk 约 5000 token，memory 与最终回答各最多 1024 token。
- temperature=1、top_p=0.7；每题/档位只运行一次随机采样。
- 保存 response、reward、num_chunks、num_contexts、total_steps 和耗时。
- 验收输入仍是原始文本、所有预定样本完成、没有 API error，再解释答案与记忆轨迹。

不同档位样本数不同，且会复用题目前缀；不要当作独立的长度因果实验。该随机协议也不能与 RL 最终 external64 的 greedy 对照混算。

## 4. 看结果与失败原因

先读 [逐块记忆失败案例](details/memagent-case-study.md)，再看 [32B 的长文案例](details/32b-memory-case-study.md) 与 [成功案例](details/32b-memory-success-case.md)。图片位于 [figures](figures/)。完成逐块读取并不保证回答正确，也不代表模型具有原生百万 token attention。

早期 pilot 的参数变化诊断用于验证训练闭环；正式 128 步训练分别见 [03](../03-memagent-32b-rl/README.md) 和 [04](../04-memagent-8b-rl/README.md)。
