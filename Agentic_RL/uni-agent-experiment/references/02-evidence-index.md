# 证据索引与文件来源

## 本目录与原实验的关系

原lab：`/home/yuhanya/uni-agent-lab`。实验执行日为2026-09-12；本目录于2026-09-14整理，读取已完成实验数据，没有重新执行模型实验。

本目录采用三种材料：

1. **原样复制**：主汇总、CSV、性能原始统计、配置与脚本等；记录源/目标SHA。
2. **轻量提取**：官方示例summary、逐题结果、验收日志末尾；保留源路径，完整记录仍在lab。
3. **解释与绘图**：由上述数据生成表格/图表，写出配置、指标含义与适用边界。

远端服务地址在副本中脱敏，真实密钥没有被读取或复制到本目录。原数据与原实验文件不被修改。

## 实验到证据的映射

| 实验/内容 | 本目录证据 | 原lab来源 |
|---|---|---|
| 最终三agent统计 | [final-summary.json](../evidence/summary/final-summary.json) | `results/final-summary.json` |
| 84行逐题结果 | [CSV](../results/per-case-results.csv)、[可点击矩阵](../results/03-per-case-matrix.md) | `results/per-case-results.csv`、`results/regraded-main-*` |
| 30题选择 | [manifest](../evidence/summary/manifest.json) | `results/data/manifest.json` |
| 有效28题 | [validated-manifest](../evidence/summary/validated-manifest.json) | `results/data/validated-manifest.json` |
| 镜像来源 | [image-preparation](../evidence/summary/image-preparation.json) | `results/data/image-preparation.json` |
| 官方ReAct / Claude示例 | [ReAct](../evidence/summary/official-api-react.json)、[Claude](../evidence/summary/official-api-claude.json) | `results/official-api-*/summary.json` |
| 模型API功能 | [eager chat](../evidence/summary/model-api-eager/chat.json)、[tool](../evidence/summary/model-api-eager/tool.json)、[Anthropic](../evidence/summary/model-api-eager/anthropic.json)、[token IDs](../evidence/summary/model-api-eager/token_ids.json) | `results/model-smoke/`；优化版在`model-smoke-aiter/` |
| Docker官方demo | [完整成功日志](../evidence/sandbox/docker-official-demo.txt) | `logs/sandbox-official-demo-attempt-02.log` |
| E2B官方demo | [脱敏成功日志](../evidence/sandbox/e2b-official-demo.txt) | `logs/e2b-official-demo-attempt-02.log` |
| Docker隔离 | [验证JSON](../evidence/sandbox/docker-isolation.json) | `results/sandbox-checks-v3/summary.json` |
| TTL回收 | [验证JSON](../evidence/sandbox/janitor-validation.json) | `results/janitor-validation.json` |
| 两条sandbox代码任务 | [Docker](../evidence/sandbox/docker/result.json)、[E2B](../evidence/sandbox/e2b/result.json) | `results/sandbox-agent/` |
| TP1/TP4 | [summary](../evidence/performance/serving-benchmark/summary.json)、[raw](../evidence/performance/serving-benchmark/raw.json) | `results/serving-benchmark/` |
| AITER | [summary](../evidence/performance/serving-aiter-benchmark/summary.json)、[raw](../evidence/performance/serving-aiter-benchmark/raw.json) | `results/serving-aiter-benchmark/` |
| 双副本 | [summary](../evidence/performance/serving-replicas-benchmark-v2/summary.json)、[raw](../evidence/performance/serving-replicas-benchmark-v2/raw.json) | `results/serving-replicas-benchmark-v2/` |
| 轨迹审计 | [audit](../evidence/trajectories/audit.json) | `results/trajectory-audit.json` |
| 版本/源码/权重 | [runtime](../evidence/provenance/runtime.json)、[模型文件hash](../evidence/provenance/model-files.json) | `results/provenance/` |
| 环境与包验收 | [CPU冷重建](../evidence/provenance/reproduction-cold-check.log.json)、[包验收](../evidence/provenance/reproduction-kit-verification.json) | `logs/reproduction-cold-check.log`、`deliverables/verification.json` |
| 社区资料 | [API快照摘要](../evidence/summary/community-snapshot-20260912.json) | `results/community/` |

## 每题怎样追查

本目录的 `evidence/cases/<agent>/<instance>/result.json`包含最终状态、测试统计、源文件SHA和原始路径。完整测试名称列表、命令输出、CLI stdout和token数组保留在原lab。

原lab的相关层次：

```text
results/main-<agent>-128k/<instance>/
  agent-result.json / candidate.patch / task.log / claude.stdout（适用时）
results/main-<agent>-128k/sessions/<instance>/
  trajectories.jsonl / debug_snapshot.json
results/regraded-main-<agent>/<instance>/
  image-baseline.patch / candidate-delta.patch / candidate-source-only.patch
  result.json / verifier/ / source-only-verifier/（适用时）
```

主要统计使用`regraded-main-*`，原始`main-*`保留生成历史和首次回放。二者不能不加区分地混合统计。

## 源码证据

- [原始主实验driver](../reproduce/scripts/executed/run_swe.py)：实际调用 `TaskConfigResolver`、`get_task`、`task.build_agent().run`、`_GatewayActor`和SWE `compute_reward`。
- [原始Docker封装](../reproduce/scripts/executed/lab_runtime.py)：扩展上游DockerSandbox保存证据与传输文件。
- [最新复跑driver](../reproduce/scripts/run_swe.py)：补充显式logprob保存等配置。
- [E2B适配器](../reproduce/scripts/e2b_provider.py)、[本地/远端ReAct任务](../reproduce/scripts/run_sandbox_agent.py)。
- [Docker服务launcher](../reproduce/scripts/labctl.py)、[租约回收](../reproduce/scripts/sandbox_janitor.py)。
- [API机器清单](../evidence/api-inventory.json)：固定源码的公开名称、签名、路由、hash以及安装版E2B SDK的REST/RPC契约；对应[Uni-Agent接口文档](03-uni-agent-api-reference.md)和[Sandbox接口文档](04-sandbox-and-verdal-api-reference.md)。这是2026-09-14的静态源码整理，不增加远端兼容性实验结论。

Uni-Agent原始版本为[固定commit](https://github.com/verl-project/uni-agent/tree/10743439dd0a19da44a94cccad069b135d957bf1)，本地补丁为[logging-fork.patch](../reproduce/patches/uni-agent-logging-fork.patch)。

## 文档可复算性

[复制清单](../evidence/source-manifest.json)记录原样/脱敏副本的SHA；轻量提取文件标注源路径。图由标准Matplotlib工具生成，拓扑/时序由Mermaid生成，源码均保留在`tools/`及`assets/diagrams/`。

阅读时优先使用本目录相对链接；需要完整历史记录时再回到原lab。不要将本文档的整理日期理解为重新跑了一轮实验。
