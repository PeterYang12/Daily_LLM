# 2026-09-20：256 resident 准备失败的资源核对

**这次失败发生在准备 sandbox 阶段，没有开始性能测量。** 请求是 256 resident、8 个工具并发，run_id 为 `4d06b8cef8014b10bdeaac71d18e9e0a`；原始 runner 记录 `SandboxException`、0 条 measurement operation，最终 `cleanup_ok=true`。不能把它记作“256 并发吞吐为零”，也不能据此把容量上限定为 163。

本核对只重读已落盘数据，没有调用服务或修改配置。机器可读指标、原始路径和 SHA256 见 [setup-failure-20260920.json](setup-failure-20260920.json)。

## 失败窗口

资源文件有 31 个约 2 秒样本，覆盖 **11:24:05.852–11:25:05.850 UTC**。11:25:01.853 的采样点记录 163 个 Firecracker、163 个 populated cgroup、163 个 attached NBD。11:25:03.851 已进入清理；11:25:05.850 三项全部为 0。进程扫描权限错误为 0。

| 指标 | 本窗口观测 | 可支持的判断 |
|---|---:|---|
| Firecracker / cgroup / NBD 采样峰值 | 163 / 163 / 163 | 采样峰值，不是准确创建数或硬上限 |
| VM 最低 MemAvailable | 1463.13 GiB | 没有物理内存耗尽迹象 |
| VM CPU 平均 / 峰值 | 4.31% / 9.09% | 峰值约 17.46 个核；没有 192 核总量饱和 |
| CPU / memory / I/O PSI some avg10 峰值 | 0.18% / 0% / 0.36% | 此采样窗口未见持续内存压力 |
| ARP entries 峰值 / gc_thresh3 | 499 / 16384 | 未达到邻居表阈值 |
| ARP table_fulls 新增 | 0 | 没有复现旧 ARP overflow 现象 |
| Orchestrator / API FD 峰值 | 3064 / 41 | 事发采样未录 RLIMIT；事后同 PID 检查为 1,048,576，见下文 |
| VM 全局 file handles 峰值 | 13472 | 与进程 FD 上限不是同一指标 |
| 网络错误和丢包计数新增 | 0 | 不能据此排除延迟或短暂连接错误 |

宿主 CPU 峰值为 5.11%，负载生成器采样 CPU 峰值为 0.14 核；宿主指标包含其他进程。VM cgroup 当前内存峰值为 6.25 GiB。采集器单次 wall time 最大约 142.8 ms，不能当作零扰动观察。

## 前后对照

| 已保存窗口 | FC / cgroup / NBD 峰值 | 最终三项 | VM CPU 峰值 | ARP 溢出新增 |
|---|---:|---:|---:|---:|
| exec-resident64-c8-r1 | 64 / 64 / 64 | 0 / 0 / 0 | 10.02% | 0 |
| exec_true-c64-r1-service | 64 / 64 / 64 | 0 / 0 / 0 | 54.86% | 0 |
| create-churn-c1-r1 | 1 / 2 / 2 | 0 / 0 / 0 | 4.00% | 0 |
| create-churn-c2-r1 | 2 / 2 / 3 | 0 / 0 / 0 | 3.86% | 0 |
| create-churn-c3-r1 | 3 / 4 / 3 | 0 / 0 / 0 | 6.40% | 0 |
| create-churn-c8-r1 | 3 / 5 / 5 | 0 / 0 / 0 | 9.55% | 0 |
| create-open-rate20-r1 | 3 / 4 / 3 | 0 / 0 / 0 | 10.00% | 0 |
| exec_true-c64-r1-sdk | 64 / 64 / 64 | 0 / 0 / 0 | 54.43% | 0 |

这些窗口全部在末次采样看到 FC/cgroup/NBD 清零，支持“未观察到跨轮累积的这三类资源泄漏”。SDK C64 的原始窗口仍保留 2 次 ConnectError；资源清零不代表每个业务请求成功。churn 中少量 FC、cgroup、NBD 差异来自不同时间点的顺序读取以及创建/清理的短暂重叠，不能当作泄漏。

[日志与源码核对](setup-failure-log-20260920.md)另统计出本轮 166 次创建返回 201，其中 1 次为 probe、165 次为 resident；这与本报告的 2 秒采样峰值 163 不矛盾。该报告还保留 11:25:02 附近的 API 500 与 Firecracker socket 连接拒绝证据，应结合它分析失败原因，不能从资源曲线独立推导根因。

清理后 orchestrator FD 为 288、API FD 为 39。ARP entries 为 481，全局 file handles 为 13088，均不能单凭未回到窗口首值就认定泄漏：它们还包含缓存、其他服务或内核分配。需要后续同条件的稳定窗口才可判断长期增长。

## 结论边界

**高置信度：没有开始测量；采样中没有持久 FC/NBD/cgroup 残留；没有观察到 RAM、整机 CPU 或 ARP 表耗尽。**

资源曲线不能解释这次 SandboxException 的具体原因。日志核对另在事后同一 PID 未重启的条件下检查 orchestrator 15070 和 API 15761，两者 Max open files soft/hard 均为 1,048,576，远高于本窗口采样峰值；这进一步不支持持续的进程 FD 上限耗尽。该限制值不是事发前的连续采样，其他限制、短暂进程退出和瞬时峰值仍不能全部排除；细节以日志报告的采集时间与原始证据为准。

V1 在 setup 失败时仍写出计划中的 measurement 起止字段，但没有任何 measurement operation；统计脚本必须按失败阶段和 operation 数排除本 cell 的性能分数。失败记录应作为容量探索结果保留。
