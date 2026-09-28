# 正式训练期间的被动性能监控

**2026-09-21 新一轮监控已启动**：独立记录目录为
`/data2/yuhanya/e2b-local-20260916/monitoring/overnight-20260921-155118/`，
监控 unit 为 `e2b-overnight-20260921-155118.service`，计划于 9 月 22 日 09:51 UTC
停止。包含 `dashboard.html`、`metrics.prom`、分钟 CSV 和完整原始序列；客户端为
10.145.64.66。当前只确认初始化阶段，warmup/正式训练通过明确时间或 run_id 标记，
不按流量自动猜测。此次没有更改或重启 CPU sandbox 服务。以下保留 9 月 16 日历史监控说明。

**监控已于 2026-09-16 23:52 UTC 停止，定时器和采集服务已禁用自动启动；全部历史数据保留，E2B 与 VM 未停止。**

监控已部署在宿主机，独立于聊天会话运行。正式训练期间不再创建合成测试沙箱，不重启 E2B 核心组件，不自动清理数据。API key 只从现有私有配置读取；本目录不包含密钥和原始业务日志。

## 本次采集配置（已停止）

| 任务 | 周期 | 内容 |
|---|---|---|
| `e2b-monitor.timer` | 约 1 分钟 | API health/鉴权、活动 sandbox、VM/K8s Ready、磁盘、采集器是否持续更新 |
| `e2b-performance.service` | 约 5 秒 | VM 总 CPU、192 个 vCPU、iowait/steal、PSI、进程、磁盘、网卡、sandbox cgroup |
| 同一性能服务 | 约 5 秒 / TCP 30 秒 | 宿主机 QEMU 各线程、物理 NIC/NVMe、8088 连接 TCP RTT 与 TCP_INFO |
| `e2b-events.timer` | 约 1 分钟 | API 和网关请求延迟、Firecracker 阶段日志、原生 cgroup 历史、下载/缓存/构建事件 |
| `e2b-performance-summary.timer` | 约 15 分钟 | 最近一小时汇总、分钟 CSV、构建时间线和候选瓶颈提示 |

数据在 **`/data2/yuhanya/e2b-local-20260916/monitoring/`**，没有把大日志或包放到报告仓库。

```text
monitoring/
  latest.json                    当前健康与容量状态
  history.jsonl                  健康历史
  session-metadata.json          范围、代码指纹、已知数据缺口
  performance/
    latest.json                  最近一次 VM/宿主机性能采样
    samples.jsonl[.*.gz]          CPU、I/O、网络、进程、cgroup 原始序列
  events/
    api.jsonl[.*.gz]              API handler 延迟与状态
    gateway.jsonl[.*.gz]          实际 HTTP/RPC 请求时间与客户端 IP
    orchestrator.jsonl[.*.gz]     Firecracker 计时及已分类诊断事件
    sandbox_host_stats.jsonl[.*.gz] 原生每 sandbox CPU/内存历史
    build_events.jsonl[.*.gz]     下载、缓存、解包、构建相关日志
    collection-health.jsonl      采集完整性与错误记录
  reports/
    latest-summary.json          最近一小时统计
    minute-timeseries.csv        对齐后的分钟序列
    build-event-timeline.csv     构建相关事件时间线
    latest-findings.md           候选瓶颈提示
```

性能及事件日志按每段 32 MiB 轮转，旧段 gzip 压缩，每类保留 48 段。保留时间取决于事件量，不能当作固定天数保证。采集采用超时和单实例锁；只有采集服务会在自身故障时重连，不会因此重启 sandbox 服务。异常只写本地记录和 journal，未配置外部消息推送。

## 明天分析

生成正式训练时间段的完整汇总，使用单独输出目录，避免和自动的一小时汇总混在一起：

```bash
/data2/yuhanya/e2b-local-20260916/client-env/bin/python -B \
  /data2/yuhanya/e2b-local-20260916/code/summarize_performance.py \
  --since 2026-09-16T14:45:00+00:00 \
  --output-dir /data2/yuhanya/e2b-local-20260916/monitoring/reports/formal-run
```

14:45 是用户宣布正式训练已开始时的观察窗口，不代表已核实的训练启动时间。连续 CPU 采样实际从 14:55:58 UTC 开始，初始试跑到后台运行之间约有 68 秒设置间隙。日志和 ClickHouse 尝试回补了当时尚可读取的 30 分钟历史；已经轮转丢失的日志不能恢复。报告会显示首末样本及大于 15 秒的采样缺口。

查看状态：

```bash
systemctl status e2b-performance.service --no-pager
systemctl list-timers e2b-monitor.timer e2b-events.timer e2b-performance-summary.timer
cat /data2/yuhanya/e2b-local-20260916/monitoring/events/latest-collection.json
```

## 如何判断瓶颈

- **CPU**：看总量、逐核、进程和 QEMU 线程。192 vCPU 上的 1% 总使用率约等于 1.92 个核；总量低时仍可能有串行线程满载。5 秒采样会漏掉极短峰值，原生 cgroup 分段计数可补充。
- **磁盘**：将 VM 的 vda await、队列、I/O PSI 与宿主机 NVMe 指标对齐。宿主机指标包含其他任务，不能全部归到 E2B。虚拟盘 busy% 也不等于物理 NVMe 达到吞吐上限。
- **网络**：同时看吞吐、重传、丢包、TCP RTT 和 QEMU 线程。吞吐很低也可能有延迟问题；TCP RTT 包含 ACK/调度影响，不等于应用请求耗时。不能用它单独证明链路带宽不足。
- **镜像与启动**：OCI 镜像下载/解包通常属于模板构建；普通 Sandbox.create 更常走快照恢复。冷缓存和热缓存必须分开。模板 API 返回 202 只是接受任务，不是构建完成；Firecracker 的微秒级 resume 也不等于整个 sandbox 创建时间。
- **远端 Uni-Agent**：需要它自己的 episode、LLM 等待、tool call、重试及文件传输计时。当前没有改动远端，也没有采集到其完整 span，所以暂不能把本机闲置直接归因于 LLM 或网络。

以后在两端保留 `run_id / episode_id / sandbox_id / tool_call_id / request_id / attempt`，用各端单调时钟测时，并同步 UTC 时钟。唯一 ID 放在日志或 trace 中，避免直接作为 Prometheus 高基数标签。异步并行操作按关键路径分析，不能把所有调用耗时简单相加。

## E2B 原生 metrics 与当前缺口

**2026-09-20 状态补充与纠正**：当前自编译 runtime 未配置 `OTEL_COLLECTOR_GRPC_ENDPOINT` 时，主 `telemetry.New` 使用 Noop；但 API 的 team observer 和 orchestrator 的 sandbox observer 另行直接创建 exporter，仍会向空地址导出并产生 `missing address`，本次已在当前版本日志中确认。因此不能把这类错误一概归为旧版本。下文保留 9 月 16 日历史采集现场；后续观测补齐、采样开销和跨端关联设计见 [性能实验总方案](../BENCHMARK-PROGRAM.md)。没有为此修改服务配置。

E2B 有原生指标。SDK `Sandbox.get_metrics()` 对应 `/sandboxes/{id}/metrics`，有 CPU、内存、磁盘字段；服务也内置 OTel 指标和 trace。但当前 `OTEL_COLLECTOR_GRPC_ENDPOINT` 未设置，导出器向空地址连接，产生 `failed to upload metrics ... missing address`。这是一条遥测故障，不能把日志中每条 failed 当作 sandbox 业务失败。

本次核对发现该 SDK metrics 接口虽然 HTTP 请求成功，但返回零数据点，ClickHouse `metrics_gauge` 也为空，不能把空结果解释成 CPU=0。另一张原生 `sandbox_host_stats` 表持续有数据，含 cgroup CPU 累积/增量、采样区间、当前内存和 VM 类型，今晚已直接采集它。

另外，当前 kernel 拒绝重置 `memory.peak`，日志会写 `interval peak semantics degraded`。当前内存仍可使用；区间峰值不应当作准确数据。旁路采样只读峰值，不尝试重置。

这里的 cgroup 内存和 Firecracker RSS 是宿主侧指标，不等于 microVM 内应用看到的内存使用量；HugeTLB 还可能由独立控制器或 UFFD 进程计费。全局 HugePages 用量单独记录。在 envd metrics 链路未接通之前，不把 cgroup 内存除以模板内存当作准确的客户机内存利用率。

正式训练期间不为修复 OTel 重启 `e2b-0`。后续维护窗口再增加 collector 并配置正确的导出地址，验证收到真实指标，而不是仅关闭错误日志。当前采集能保存已有下载/构建事件，但缺少显式开始/结束事件的阶段不会虚构精确耗时。

现有网关日志未包含路由头和统一 request ID。`GET /health` 有时是已销毁 sandbox 的健康请求，出现 502 不一定是 E2B API 故障；状态检查使用无 sandbox 路由头的 API health。采集器的 `event_id` 是去重 hash，不是跨端 trace ID。
