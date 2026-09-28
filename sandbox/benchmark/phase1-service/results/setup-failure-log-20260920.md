# 2026-09-20：256 resident 准备阶段失败的日志核验

失败 run 为 `4d06b8cef8014b10bdeaac71d18e9e0a`，cell 为
`exec-resident256-c8-r1`。本报告只读核验日志、源码及进程身份，没有创建
sandbox、修改服务设置或重启。结构化证据、原始路径和源码 SHA-256 见
[JSON](setup-failure-log-20260920.json)；独立资源核验见
[资源报告](setup-failure-20260920.md)。

## 已确认的直接错误

`11:25:02.813 UTC`，`POST /sandboxes` 返回 HTTP 500，服务端 handler 耗时
89.670485 ms。内部错误为：

```text
failed to start FC: error setting fc metrics:
Put "http://localhost/metrics":
dial unix /tmp/fc-irt46sdttlkwfkbl2gfqa-yxnwla89wnyocfiwjbmk.sock:
connect: connection refused
```

目标 sandbox 为 `irt46sdttlkwfkbl2gfqa`。这是 Firecracker 管理 socket 的
首次配置请求失败，发生在实例准备阶段；本 cell 没有测量操作。它不是配额
拒绝，也不是 20 秒 SDK 请求超时。

该 run 的 API 日志在失败前有 166 个创建 201，包含已删除的一个模板验证
实例。因此已有 165 个 resident 创建成功，下一个失败。两秒资源采样记录的
峰值为 163 个 FC/NBD，与 API 的精确创建计数不冲突。

## 时间线与解释边界

| UTC | 事件 |
|---|---|
| 11:24:04.717701 | run 开始准备 |
| 11:25:02.719021 | 最后一个成功创建的 API 响应 |
| 11:25:02.741331 | 目标 sandbox 加入网络映射 |
| 11:25:02.771273 | Firecracker v1.14.4 输出启动日志 |
| 11:25:02.786400 | Firecracker 输出 API socket Listening 日志 |
| 11:25:02.789734 | 清理路径记录 `fc process already exited` |
| 11:25:02.809–.813 | orchestrator/API 报告 socket connection refused，API 500 |
| 11:25:05.810985 | 目标 sandbox 网络映射移除 |
| 11:25:05.850023 | 资源采样 FC/NBD attached 均为 0 |

源码的 `fc/process.go:329` 调用 `socket.Wait`，而
`pkg/sandbox/socket/socket.go:12` 只轮询 `os.Stat` 判断文件存在，没有验证
socket 可接受连接。之后 `fc/process.go:597` 直接调用首次 `PUT /metrics`。
因此，socket 文件已经创建但 API 尚未可连接的短暂就绪窗口是首要怀疑。

这个根因仍是推断。日志没有记录可归于该实例的 Firecracker panic 或异常
退出码，也不能单凭清理中的 `already exited` 判断它在请求前自行崩溃。
失败后的清理本身可能终止进程。应记录这个边界，不能把可疑竞态写成已唯一
证明的原因。

资源窗口内可用内存最低 1463.13 GiB、CPU 峰值 9.09%、ARP entries 峰值
499/16384、ARP 溢出增量 0、orchestrator FD 峰值 3064。事后同 PID 的
API/orchestrator `RLIMIT_NOFILE` 均为 1048576；它不是事发前的连续采样。
事后检查服务健康、核心 Pod 没有本次重启、NBD/cgroup 已清空，运行配额仍
512、NBD 1024、HugeTLB 池为 0。

原始 orchestrator 证据来自 VM 内轮转日志
`/var/log/pods/e2b_e2b-0_54303174-0d59-4fa2-ab94-3c67087e0e62/orchestrator/0.log.20260920-112505`。
API 证据来自同 Pod 的 `api/0.log`。这些日志可能继续轮转，因此本报告保留
脱敏摘录与引用，不对持续变化的大日志宣称固定 hash。

## OTel 表述纠正

之前“当前无 endpoint 时所有遥测均 Noop，空地址错误只属于旧部署”的表述
过于宽泛，现予以纠正。

主入口 `telemetry.New` 在没有 `OTEL_COLLECTOR_GRPC_ENDPOINT` 时确实返回
Noop。但另有两个独立 observer 绕过该入口：

- API `internal/metrics/team.go:36` 的 `NewTeamObserver`，由
  `internal/orchestrator/orchestrator.go:224` 启动。
- orchestrator `pkg/metrics/sandboxes.go:78` 的 `NewSandboxObserver`，由
  `pkg/factories/run.go:712` 启动。

它们直接调用 `NewMeterExporter` 和 `NewMeterProvider`，各自配置五秒周期。
`telemetry/metrics.go:43` 显式把空字符串传给 OTLP/gRPC endpoint。
OpenTelemetry exporter 格式化 `failed to upload metrics`，PeriodicReader
通过 `otel.Handle` 输出到标准 stderr。主客户端的 Noop 不会关闭这些独立
exporter。

本次还核验到当前 API `11:35:54 UTC` 和 orchestrator `11:36:01 UTC` 的
stderr 都继续输出 `invalid target address "": missing address`。对应正在
运行的二进制 SHA-256 与本次自编译发布一致，进程环境中 endpoint 未设置，
所以这些是当前部署仍存在的遥测错误。

建议文档写成：**未配置 endpoint 时，主 OTel 客户端关闭；独立的 API team
和 orchestrator sandbox 指标 observer 仍启动 exporter，并产生空 endpoint
导出错误。** 这与本次 Firecracker 本地 `/metrics` socket 配置失败属于不同
路径，不能因名称都含 metrics 而混为一个故障。此次没有修复或改动 daemon。

## 后续验证

保留失败 cell，按精确 run metadata 对账清理并确认服务空闲，再用独立 run
重试；不得盲重发响应不明确的创建 POST。未来记录 setup 的 HTTP 状态、
错误内容及部分创建计数。

若后续修复 runtime，最小候选是 FC 专用的 API-ready 检查，或首次配置阶段
只对尚未建立连接的 `ECONNREFUSED` 做有界重试。不要直接修改通用
`socket.Wait` 的协议行为，因为它还用于 UFFD socket。该候选需独立复现与
验证，不应在冻结的主基线中悄然修改。

## Case 2：第三轮准备阶段的 envd 初始化超时

后续 `exec-resident256-c8-r3`、run
`077071962a184787bea0869efeaae70c` 于 11:50:26.387 UTC 开始，使用
`v2-error-diagnostics` runner。161 个 resident handle 已创建后，下一个
sandbox `im7vhbk8ii0r0v2u3t83i` 返回 500；没有进入测量阶段。客户端保留了
73.284 秒处的 `create_error` 和最终 `cleanup_ok=true`。

**第二次是不同的直接错误。** API 在 11:51:38.564 UTC 返回 500，handler
耗时 11.686493153 秒，错误链为：

```text
failed to wait for sandbox start:
failed to init new envd: failed to init envd:
context canceled with cause: syncing took too long
```

该实例在 11:51:26.930 已输出 `API server started`，11:51:26.963 完成
`load snapshot` 请求（24.852 ms）。因此它已经经过第一次失败的 FC API
配置阶段。11:51:36.965 的日志记录 `failed to init envd after retries`，
共 **138 次尝试**。源码 `WaitForEnvd` 使用独立等待预算；默认总预算 10 秒，
单次 init 请求默认 50 ms。这里没有修改这些默认值。

第二轮的资源表现不能套用第一轮的“未见 CPU 压力”：等待 envd 的窗口内，
VM CPU 多次达到约 61–75%，峰值 **74.80%（约143.61个核）**，CPU PSI
`some avg10` 峰值 **13.71**。同时 FC/NBD attached 峰值162（包含正在启动
的实例），可用内存最低1462.44 GiB、ARP entries峰值496、溢出增量0，
orchestrator FD峰值3116。

原始 `cgroup_summary` 进一步表明：VM 峰值对应记录中，162 个连续存在的
sandbox/Firecracker cgroup 合计使用 **107.96核**，没有新建/退出 cgroup，
没有 CPU 配额 throttling；Python load generator 同期接近0核。其余若干
记录中 sandbox cgroup 合计也达到100–120核。因此突发的大部分落在沙箱的
cgroup中，不能归为单个 Python 压测进程打满。

这些 `/proc/stat` 和 cgroup 计数不是同时读取，受采样迟到与赶进度影响，
个别记录的 cgroup 值可能高于全 VM 值。不可将单个时刻两者相减，当成精确的
“剩余服务开销”；JSON 同时保留逐记录值和连续 cgroup 窗口的加权结果。
现有数据仍不能区分具体 guest 后台任务、Firecracker/KVM 工作或其因果链，
更不能直接证明 CPU 突发就是 envd 初始化超时的唯一原因。

11:51:41.511 的资源采样已显示 FC/NBD attached 为0，最后一个清理后样本
在11:52:39.509仍为0。清理行为与所有原始样本保留在 JSON 的
`additional_cases[0]`；第一轮内容保持不变。授权的额外十个只读 `/proc`
进程 CPU 样本实际覆盖11:58:44–11:58:54，未捕获同类突发，不能用于归因
11:51的原始故障。

后续应把它作为第二种 setup 失败独立报告。可在空闲/归属检查后做单独标记的
一次重试；诊断方向应是 guest/Firecracker CPU来源、envd 每次初始化请求与
UFFD/启动阶段时序。当前证据不足以用调大配额、修改启动等待时间或修复第一轮
socket 等待逻辑来宣称解决第二轮故障。此次调查没有发起基准请求或改动 daemon。

## Case 3：独立重试再次发生 envd 初始化超时

`exec-resident256-c8-r3-retry2`、run
`21eb50b68d0140709301fe988c5d18a5` 的 runner 记录开始于
11:56:04.519797 UTC。216 个 resident handle 创建成功后，下一个
`i5cyyl4vgxbvflg7ciaiy` 在准备阶段失败；客户端在96.149秒处记录500，最终
`cleanup_ok=true`，没有测量操作。

直接错误与 Case 2 相同：envd 初始化耗尽启动等待预算。FC API server 在
11:57:27.137启动，`load snapshot` 在11:57:27.156成功结束（16.268 ms）；
11:57:37.282记录 **151次init尝试，单次timeout_ms=50**，最后原因仍是
`syncing took too long`。API于11:57:39.553返回500，handler耗时
**12.469755067秒**。它不是 Case 1 的首次metrics socket连接拒绝。

这轮再次伴随 CPU 突发：VM峰值71.46%（137.21核），同记录217个连续
sandbox cgroup合计124.39核、无cgroup CPU throttling，Python generator
约0.0069核。CPU PSI `some avg10` 峰值22.49；可用内存最低1459.61 GiB、
ARP entries峰值661、溢出增量0。与 Case 2 一样，只能据此指向沙箱cgroup内
的CPU工作，尚不能区分具体guest后台活动、Firecracker或KVM开销及其因果关系。
计数非原子读取的限制仍适用。

11:57:42.521记录该sandbox网络映射移除；11:58:39.665最后的清理后资源
样本中FC/NBD attached均为0。原始输入、SHA及完整时间线保存在JSON的
`additional_cases[1]`，其余案例未覆盖。

当前N256/C8材料应共同披露：**2个有效测量重复，3个准备阶段失败尝试**。
失败不构成“容量为0”或“吞吐为0”的数据点，也不能仅保留成功重试而隐藏它们。
后续不再快速重试此cell；完成其他阶段后，可用单独标记的慢速建立空闲池实验
进一步区分建池速度、存量后台工作及envd启动长尾。此次仅补充读取目标日志，
没有新增/proc采样、基准请求或daemon变更。
