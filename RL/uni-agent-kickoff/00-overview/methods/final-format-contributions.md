# Final external 的格式贡献拆分

`scripts/audit_final_format_contributions.py`只分析本实验预声明的四个 external base/final128 配对，复用未修改的 `audit_durable_monitor32.classify`、`tex_space_only_lcs.py` 与官方 reward。它输出观测 LCS 差值的算术分类，不产生新 reward、语义正确率、checkpoint 选择或训练效果的因果解释。

输入必须已有完整 final 对照；当前代码验收只使用真实 base 结果作负例和临时测试，尚未生成真实 final 格式报告。验证记录见 fixed-guard-review.json（原始文件：`results/final-format-cpu-review-20260912/fixed-guard-review.json`）。

新增必填参数为 `--protocol primary|stable` 与 `--pair first|repeat`。保留 `--comparison`、`--before`、`--after`、`--output`；before/after 路径必须对应以下有序配对，输出文件必须不存在。容器中使用 `/lab/...`；从宿主调用时，`/lab/...` 会映射到脚本所在 lab 根目录，相对路径也按该根目录解释。comparison 中保存的宿主路径或 `/lab` 路径由逻辑 phase 后缀与内容 SHA 共同核验。

| protocol / pair | before phase | after phase | comparison 目录 |
| --- | --- | --- | --- |
| primary / first | `base-durable` | `final-durable-step128` | `comparison-durable-step128` |
| primary / repeat | `base-durable-repeat` | `final-durable-step128-repeat` | `comparison-durable-step128-repeat` |
| stable / first | `base-durable-stable` | `final-durable-stable-step128` | `comparison-durable-stable-step128` |
| stable / repeat | `base-durable-stable-repeat` | `final-durable-stable-step128-repeat` | `comparison-durable-stable-step128-repeat` |

四组均完成后，可在 CPU 容器按以下命令分别生成报告：

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_final_format_contributions.py \
  --protocol primary --pair first \
  --comparison /lab/results/large-memagent/comparison-durable-step128/comparison.json \
  --before /lab/results/large-memagent/base-durable/results.jsonl \
  --after /lab/results/large-memagent/final-durable-step128/results.jsonl \
  --output /lab/results/large-memagent/final-format-contributions/primary-first.json

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_final_format_contributions.py \
  --protocol primary --pair repeat \
  --comparison /lab/results/large-memagent/comparison-durable-step128-repeat/comparison.json \
  --before /lab/results/large-memagent/base-durable-repeat/results.jsonl \
  --after /lab/results/large-memagent/final-durable-step128-repeat/results.jsonl \
  --output /lab/results/large-memagent/final-format-contributions/primary-repeat.json

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_final_format_contributions.py \
  --protocol stable --pair first \
  --comparison /lab/results/large-memagent/comparison-durable-stable-step128/comparison.json \
  --before /lab/results/large-memagent/base-durable-stable/results.jsonl \
  --after /lab/results/large-memagent/final-durable-stable-step128/results.jsonl \
  --output /lab/results/large-memagent/final-format-contributions/stable-first.json

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_final_format_contributions.py \
  --protocol stable --pair repeat \
  --comparison /lab/results/large-memagent/comparison-durable-stable-step128-repeat/comparison.json \
  --before /lab/results/large-memagent/base-durable-stable-repeat/results.jsonl \
  --after /lab/results/large-memagent/final-durable-stable-step128-repeat/results.jsonl \
  --output /lab/results/large-memagent/final-format-contributions/stable-repeat.json
```

校验复用 `plot_final_external.validate_pair`：分别处理 primary 直层与 stable 双层 provenance，核对 status/profile/mode、固定 phase 与 base/final 模型根、原始结果和 provenance SHA、完整 64 题、数据及源码 hash、实际记录的 endpoint/runtime/参数、CSV 的题目顺序与 reward，并重算 20,000 次 question-level paired bootstrap 核对原 CI。包装脚本另核对 CLI 输入是这些 canonical 文件，分类后的 delta 等于已验证的原生 delta，写出前再核对所有输入 SHA。实际 GPU/模型加载及训练完成证据仍由上游 final gate 和评测流程负责。

每个输出明确写入 `protocol`、`pair`、`phases`、`comparison_statistics` 与 `protocol_signature`。单次调用只验证这一对；汇总同 profile 的 first/repeat 时应检查两个 `protocol_signature` 一致，最终绘图的 `validate_inputs` 已包含该跨配对检查。primary 与 stable 独立呈现，不合并题数、不选择分数较高的重复结果。

分类不变：`tex_space_only`、`integer_thousands_comma_only`、`simple_text_wrapper_rendering_only`、`same_year_range_separator` 计入窄格式净贡献，其余仍按原规则记录。`format_fraction_of_positive_net_gain` 仅在总净收益为正时给出；它是带符号净差值的算术比率，不能解释为“学到知识的比例”，也不能把剩余项统一称为知识提升。
