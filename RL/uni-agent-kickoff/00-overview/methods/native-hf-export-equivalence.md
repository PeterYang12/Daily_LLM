# 原生checkpoint合并后的HF配置与tokenizer验收

`memagent_32b_128_durable` 的step8从原生FP32 model shards经官方merger导出BF16 HF。这条路径不同于旧run直接保存FP32 HF的路径。新导出在CPU Transformers5.16.1、tokenizers0.23.2下通过配置与tokenizer等价检查：64个external样本、388个chunk，共1,798,330个源token，base与导出的原始token IDs、解码chunk、chunk再编码、MemAgent chat输入和final prompt输入全部一致。审计耗时18.305秒，峰值RSS约1.60GB，未初始化CUDA/HIP，没有构造模型或读取权重。

完整证据见 CPU检查报告（原始文件：`results/large-memagent/durable-step8-native-hf-export-semantics-cpu.json`） 和 路径差异与独立复核（原始文件：`results/large-memagent/durable-step8-native-hf-export-semantics-review.json`）。新导出已有的export-outcome记录707个BF16 tensor、65,524,329,392 bytes，并指向已验证的step8 native commit；本次仅交叉核对这份来源记录，没有把它当作重新做过全部权重转换验证。

实际相等项包括完整规范化tokenizer backend、词表、merges、added tokens、normalizer、pre/post processor、decoder、padding/truncation设置、special token metadata、有效chat template、架构签名和generation行为。六类固定文本输入与四类chat/tool模板探针也相同。所有config/tokenizer输入文件在审计前后SHA256不变。

raw文件保留着可解释的差异：

| 项目 | 原始base | 新native→HF导出 | 实际检查 |
| --- | --- | --- | --- |
| RoPE字段 | `rope_theta` | `rope_parameters` | 规范化后theta=1000000、type=default一致 |
| dtype字段 | `torch_dtype` | `dtype` | 规范化后均bfloat16 |
| attention layers | 默认推导 | 显式64个full_attention | 架构签名一致 |
| model config的BOS/PAD | BOS151643、PAD空 | BOS空、PAD151643 | BOS在相同generation config中保留，PAD与相同tokenizer/generation值一致 |
| generation_config | Transformers4.51.0标记 | Transformers5.9.0标记 | raw仅版本字段不同，实际generation behavior相同 |
| HF attention selector | None | None | 与旧HF-only导出不同：旧文件显式sdpa |
| tokenizer序列化 | 旧added-token/template及ByteLevel字段形式 | 新格式 | AutoTokenizer5.16.1完整backend、真实输入IDs一致 |

旧审计脚本把旧HF-only的`None, sdpa]`作为固定attention selector验收条件。本次保留了 [修改前源码（原始文件：`notes/research/audit_hf_export_semantics-before-native-merger.py`），并在 当前脚本（原始文件：`scripts/audit_hf_export_semantics.py`） 增加显式`--checkpoint-hf-attention-selector`参数，默认仍为sdpa；新native merger用none。其他等价条件没有放宽。该HF selector不控制native-vLLM的attention backend，但仍要将实际值和导出路径预期明确记录。

最终step128导出完成后，应对它再运行同样的CPU检查，使用新的输出文件名：

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/audit_hf_export_semantics.py \
  --checkpoint /lab/runs/rl/memagent_32b_128_durable/exports/global_step_128/huggingface \
  --output /lab/results/large-memagent/durable-step128-native-hf-export-semantics-cpu.json \
  --runtime-label cpu-client-tf5.16.1-native-merger-step128 \
  --checkpoint-hf-attention-selector none
```

结论适用于记录的AutoConfig/AutoTokenizer5.16.1和native-vLLM Qwen3 BF16/non-thinking协议，不推广到直接消费raw tokenizer.json或其他版本的软件。它不说明base与训练后权重、logits或能力相同；step8验收也不能代替实际final导出的再次检查。
