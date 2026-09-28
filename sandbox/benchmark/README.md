# E2B Sandbox Benchmark：服务、SDK与RL路径验证

**2026-09-21 更新：R2 已按用户要求停止，完成 6/100 个原生训练 step，checkpoint 6 已保存；第 7 步被主动中断。** 训练容器、监督进程和采样器均已退出，sandbox 清理无残留。已完成的实验、配置问题与 sandbox 服务问题的证据，统一见 [停止后复盘](REVIEW-20260921.md)。下方较早的运行说明保留供追溯。

[后续决策记录](DECISIONS-20260921.md)：未来每 20 step 保存、仅保留最新 checkpoint；先讨论并发/超时与基础设施方案。本轮只记录，尚未实施任何配置修改或清理。

阶段0–2在本机通过现有 E2B 网关测量服务协议与 Python SDK，并记录实验环境、逐请求结果、资源曲线和复现代码。这些服务/SDK结果限于本机到 VM 的链路与声明的工作负载；真实RL的测量与进度单独记录在阶段4。

当前RL实验是 `ua-e2b-pathfinding-20260920-r2`，使用62个训练任务和16个固定评估任务。原始启动和检查点1后的恢复已经开始；实时进度见 [Phase 4 STATUS](phase4-rl/STATUS.md)，测量结论见 [RL报告](phase4-rl/REPORT.md)，代码、归档配置和只读检查命令见 [Phase 4 README](phase4-rl/README.md)。

阶段0–2的服务/SDK结论见 [原有基准报告](REPORT.md)；分阶段复现说明见 [REPRODUCE](REPRODUCE.md)。下方客户端版本与负载矩阵属于阶段0–2，不是R2训练环境的版本说明。

| 目录 | 内容 |
|---|---|
| [phase0-environment](phase0-environment/README.md) | 环境/二进制/配置基线，有限资源采样，协议校验与测量开销验证 |
| [phase1-service](phase1-service/README.md) | HTTP/Connect 协议客户端、控制面与命令/文件基准、到达率/驻留数测试 |
| [phase2-sdk](phase2-sdk/README.md) | 与协议场景配对的公开 SDK 调用，以及官方 Code Interpreter 性能场景 |
| [phase3-replay](phase3-replay/RESULTS.md) | 保留的真实工具重放结果与边界 |
| [phase4-rl](phase4-rl/README.md) | 当前R2训练、实时状态、首批曲线、恢复配置与代码链接 |
| [phase5-optimization](phase5-optimization/RESULTS.md) | 启动门控对照、取消协议修复和检查点兼容性证据 |
| [analysis](analysis/README.md) | 统一的统计、质量检查、资源对齐和 PNG/SVG 图表生成 |

本轮历史实验的 API 入口为 `http://10.145.64.67:8088`。外层 VM 为 192 vCPU、约 1486.84 GiB 可见内存；Sandbox guest RAM 使用普通 4 KiB 页，HugeTLB 池为 0，节点/团队运行配额 512，实验时启动准入为 3。实际实验版本和二进制 hash 以阶段 0 的 inventory 为准。服务在 9 月 21 日另行[部署了启动准入 1024](../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/STARTING-CONCURRENCY-20260921.md)，不能将新配置套用于本轮历史结果。

## 文件与运行材料

本目录保留可复用脚本、版本配置、统计汇总、分析报告和图表。大型逐请求 JSONL、资源样本、模板构建记录留在：

```text
/data2/yuhanya/e2b-benchmark/session-20260920/
  env/                  # 独立 Python 环境
  phase0/               # 环境与功能检查原始数据
  phase1-service/       # 协议客户端逐请求、资源、命令manifest
  phase2-sdk/           # SDK逐请求、资源、命令manifest
```

连接凭据只从 `/home/yuhanya/.config/e2b-local-20260916/client.json` 读取，没有写入报告或代码。迁移到其他机器时需自行提供兼容 endpoint 与私有凭据配置。

## 环境和复现

客户端使用 Python 3.12，固定 `e2b==2.49.1`、`e2b-code-interpreter==2.10.0`、`httpx==0.28.1`、`matplotlib==3.10.8`；完整依赖版本在 [lock](phase2-sdk/configs/client-requirements.lock)。

在已有可用 E2B/VM 的同类机器上建立环境：

```bash
python3 -m venv /data2/yuhanya/e2b-benchmark/session-20260920/env
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -m pip install \
  -r phase2-sdk/configs/client-requirements.lock
```

运行前修改/核对脚本中的凭据路径、VM SSH helper、endpoint、模板和 CPU affinity。现场 QEMU 使用宿主 CPU 192–383，发生器固定在 0–15；其他机器不得照抄 CPU 编号。该目录不是整个 E2B 服务安装包，现有服务复现另见 [部署材料](../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/README.md)。

实验总矩阵见 [matrix.json](matrix.json)，每阶段另保留相应子矩阵。正式主矩阵共 105 个单元：匹配协议/SDK交错运行，主要点重复三次；包含短命令、1 KiB读写、1 MiB读取、较高命令并发、创建销毁、固定到达率和驻留实例锚点。稳态单元为 20/30 秒，生命周期使用固定操作数；这些是有重复和逐样本数据的短窗口表征，不是数小时 soak 或 SLO 容量认证。

按记录的交错顺序执行：

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \
  run_stages.py matrix.json
```

执行器串行运行每个单元，并启动有限采样子进程；每个单元完成后停止采集。不会启用常驻 systemd 监控。结果文件拒绝覆盖，已确认完成且清理通过的单元可跳过；不完整文件需先审查和清理，不自动盲目重跑。

每个 benchmark 使用独立 run ID 和 metadata。开始/周期性检查其他业务实例，检测到外来任务停止新负载并只清理本次实例；VM 可用内存低于 128 GiB 时停止。此检查不是独占资源锁，采样和报告仍需检查共享宿主机干扰。

## 统计口径

每个单元保留明确的测量窗口、调度/完成量、成功/拒绝/超时/校验失败、清理状态及事件循环迟到。吞吐按墙钟窗口计算，不累加请求耗时充当总时长。延迟包含在窗口内发出但在排空阶段才完成的请求；完成吞吐按完成时间落入窗口的记录计算。

请求层面的 attempt_count 表示本地应用调用次数。SDK底层连接重试没有逐次插桩，不能把它解释成全部物理网络尝试次数。主要场景关闭了可配置的 API 重试。

协议模式的测量使用 HTTPX，SDK 使用自身的 HTTP/Connect 传输实现。配对比较体现这两套完整客户端路径，不能把差值全部归为 SDK 包装开销，也不能在发生器已经饱和时把曲线平台当作服务端容量。

创建用例是有界的 **create-delete churn**：创建延迟仅计创建调用，循环吞吐包含两次创建之间的删除。暂停/恢复和官方 SDK 序列分别提供更细的阶段记录。

文件写按 worker 独占目标串行覆盖，payload 包含操作序号；记录 API 返回耗时，测量窗后验证每个 slot 最后确认的内容。它没有对每次写入额外执行一遍读取；验证范围在记录中单独说明。

原始事件可用 `analysis/summarize.py` 重建统计/图表，资源数据用 `analysis/resources.py` 与实际测量窗口对齐。保存结果时保留质量告警、样本数、重复间差异和失败点。
