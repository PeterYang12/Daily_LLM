# 实验目录：跑了什么，得到了什么

以下按实验目标整理。小样本示例、正式同题对照、推理微基准和基础设施验收分别计数，不把它们合成一个SWE分数。

| 编号 | 实验 | 实际执行 | 主要产出 |
|---|---|---|---|
| E01 | [模型与官方示例](01-model-and-official-examples.md) | 8卡基础计算；4种模型API；官方ReAct/Claude各1题；优化服务附加验证 | AMD环境和实际agent入口可运行 |
| E02 | [数据与正负控制](02-data-and-controls.md) | 固定500题源数据；六题pilot；分层30题原始/gold控制 | 合格28题与排除清单 |
| E03 | [三种agent正式对照](03-swe-agent-comparison.md) | pilot各6题；主实验各28题；所有候选独立回放 | 17/28、11/28、12/28及成本/约束指标 |
| E04 | [本地Docker sandbox](04-docker-sandbox.md) | 官方工具demo；隔离/资源/生命周期；10测试代码任务；TTL控制 | 本地可执行方案与权限边界 |
| E05 | [远端E2B sandbox](05-e2b-sandbox.md) | SDK创建/执行/读写/销毁；官方demo；ReAct代码修复 | 远端工具执行方案，10测试通过 |
| E06 | [TP与AITER性能](06-serving-tp-and-aiter.md) | TP1/TP4；eager/AITER+图执行；每组18个batch | 当前配置的多卡取舍与4.41×单卡加速 |
| E07 | [副本扩展](07-replica-scaling.md) | 1/2个优化副本，并发8/16/32，各3次 | 高并发32时约1.80×吞吐扩展 |
| E08 | [轨迹验证](08-trajectories.md) | 所有Gateway作业token/mask检查；三种agent独立logprob验收 | 明确可用的记录范围与后训练缺口 |
| E09 | [复现与验收](09-reproducibility-validation.md) | 58项CPU依赖冷重建；日志并发验证；归档解包与hash检查 | 可追溯源码/依赖/数据/命令 |

每页给出目的、配置、操作、结果、可支持的结论以及证据链接。工程适配仅在影响解释或复现时说明；本目录的主体是实验产出。
