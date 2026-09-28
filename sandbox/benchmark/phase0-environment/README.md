# Phase 0：环境基线与有限资源采样

本阶段建立可追溯的环境记录，为每轮 sandbox benchmark 配套资源曲线。环境 inventory 与资源 sampler 只进行宿主机和 VM 的 `/proc` / sysfs 读取、Kubernetes 查询、二进制 SHA256、数据库 `SHOW huge_pages` 以及 API health/list；它们不创建 sandbox、不执行其内部命令、不改变现有服务或启用后台监控。另保留总 runner 执行的独立协议烟测摘要，以及用于随后比较记录开销的补充 wrapper。

已执行控制的结论见 [REPORT.md](REPORT.md)，机器可读验收见 [summary.json](results/summary.json)，部署白名单快照见 [inventory.json](results/inventory.json)，文件校验与原始路径见 [manifest.json](results/manifest.json)。这些结果都是记录时点的状态，不能作为之后每轮测试的空闲保证。

原生观测链路仍有缺口：当前主 `telemetry.New` 未配置 collector 时为 Noop，但 API team observer 与 orchestrator sandbox observer 独立创建 exporter，仍可见空地址导出警告。本轮使用有限旁路采样，没有把原生 SDK metrics 的空结果当成 CPU=0，也没有把这些日志计作 benchmark 请求失败。机制和当前日志证据见 [阶段1日志核对](../phase1-service/results/setup-failure-log-20260920.md)。

## 采样方式

[sample_resources.py](sample_resources.py) 只依赖 Python 标准库，通过一条持久 SSH 连接把同一份采集代码送入 VM 的 Python 标准输入，不在 VM 安装文件。宿主客户端、远端采集器都有有限采样次数；正常结束会回收 SSH 子进程。

```bash
python3 -B sample_resources.py \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase0/run-resources.jsonl \
  --interval 5 --samples 120
```

这是前台约 10 分钟的有限窗口。实际 benchmark 应在启动后运行，并将采样窗口覆盖预热、测量、清理；使用新的输出路径，脚本拒绝覆盖已有文件。调用方可以把本脚本作为受控子进程，同时运行 benchmark，再发送 SIGTERM，或使用 `--stop-file /path/to/run.stop` 并在结束时创建该文件。停止后的 completion 记录为 `stopped`。不要把它注册成长期 systemd 服务。

可选参数：

- `--pid PID`：采集宿主机上的负载生成进程 CPU/RSS/I/O，可重复指定；采用 PID+starttime 避免 PID 复用，不自动汇总子进程。非本用户进程的 I/O 权限可能不足，缺失时不写成 0。
- `--per-sandbox`：在私有原始输出中加入各 sandbox cgroup 记录；默认只输出聚合。cgroup 名包含运行标识，发布结果应使用汇总。
- `--event-lag-file FILE`：读取负载生成器自身输出的诊断 JSON，例如 `{"event_loop_lag_ms":{"n":50,"p50":0.1,"p95":0.8,"p99":1.2,"max":1.8}}`。仅允许这些数值字段，并记录文件修改时间以识别过期数据。采样器不能从另一个进程推断其 event loop lag。
- `--vm-ssh PATH`：替换 SSH 封装；默认 `/data2/yuhanya/e2b-local-20260916/code/vm-ssh`。

## 输出与解释

JSONL 首行是 `record_type=metadata`，中间为 `sample`，末行为 `completion`。每个 sample 含 `host` 和 `guest`，分别给出 UTC、该机器的 monotonic 时间、实际采样间隔和采集耗时。**两台机器的 monotonic 时钟不能直接相减**；以 UTC 对齐事件，以各自间隔计算资源速率。不要将宿主 CPU 与 VM CPU 相加。

| 记录 | 字段 / 用途 |
|---|---|
| CPU | busy%、等效繁忙核数、最忙核、iowait、steal；busy 不包含 iowait/steal |
| 内存 / 压力 | MemAvailable、cache、swap、HugeTLB、CPU/memory/I/O PSI；不写入 memory.peak |
| 磁盘 | VM vda/有活动的 NBD，宿主物理 NVMe；速率、IOPS、await、队列；vda busy% 不等于物理 NVMe 饱和 |
| 网络 | en*/eth* 接口收发、错误、丢包；不声称测得端到端 RPC 延迟或带宽上限 |
| cgroup | 存在/有进程的数量、当前宿主侧内存、连续可见 cgroup 的 CPU 与 throttle 增量，以及新增/消失数 |
| 生命周期资源 | 实际 Firecracker 可执行文件数、NBD 设备/attached 数、ARP entries/table_fulls、全局 FD 与 API/orchestrator FD |
| 采集自身 | `collection_ms`、guest `sampler_schedule_lag_ms`；后者仅反映采集器调度，不能当负载生成器 lag |

Firecracker 计数检查 `/proc/PID/exe`，排除 `Z/X`，不把 wrapper 命令、残留 socket、模板目录或线程当独立 VM；权限错误另列。它可能包含模板构建/短暂生命周期进程，不能假定总数等于某个 API key 的 running 列表。

cgroup CPU 增量只统计两个采样点都在的条目，已显式记录新/消失数；短生命周期会漏掉部分 CPU，不能用这个值声称精确的全部 workload CPU。宿主和 VM 总 CPU 仍包含这段工作。cgroup 内存与 Firecracker RSS 是宿主侧记账，不等于 microVM 内应用看到的内存。

ARP `entries` 是在 CPU 行重复的全局 gauge，取 max；`table_fulls` 是各 CPU 累积计数，求和后取增量。FD 的全局计数是内核 file handle 数，不等于所有进程 fd 数之和。目录、设备仍存在不意味着实例正在工作。

首次 2×1 秒烟测只验证采集链路与退出；后续默认建议 5 秒。采集耗时会随进程和 sandbox 数变化，记录每轮 `collection_ms`，必要时增加间隔。该烟测没有做有/无采集器的性能 A/B，因此不声称测量扰动为零。

## 一次性部署记录

```bash
python3 -B inventory.py \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase0/new-inventory.json
```

[inventory.py](inventory.py) 从现有私有 client 配置读取认证，只保存显式白名单；不会序列化 API key、认证头、分页 token、sandbox metadata、Pod 完整环境或业务日志。API list 使用 `X-Next-Token` / `nextToken` 分页，最多 100 页并检测重复 cursor，保留完成标识。列表是当前 key 所属团队的视图；并发创建/删除期间可能跨页变化。

运行代码来源为现场脚本和本阶段实现；部署配置参考固定运行工作区与实际 Pod，二进制重新读取并做哈希。记录 Git HEAD 和 tracked dirty 状态，但它们不能取代实际部署二进制的哈希。创建阶段已检查 `/home/yuhanya`、`Daily_LLM`、`sandbox`、`benchmark` 各级 `AGENTS.md`，未发现额外指令文件。

原始资料保存在 `/data2/yuhanya/e2b-benchmark/session-20260920/phase0/`，目录与文件使用当前用户私有权限。仓库只保留脚本和白名单摘要。

## 检查

```bash
python3 -B test_sample_resources.py
```

六项检查覆盖 CPU 等待归类、PID 复用、短生命周期覆盖缺口、磁盘 await 分母、ARP gauge/counter 差异，以及可选诊断文件的字段白名单。另以真实 VM 做有限 2×1 秒只读烟测，记录存在完整的 metadata/sample/completion、权限错误计数和退出状态。

## 协议与结果计时烟测

总 runner 在独立、带 run_id 的测试 sandbox 中完成以下功能检查，每轮均确认清理成功：

| 场景 | 测量记录 | 功能结果 |
|---|---:|---|
| JSON Connect 命令输出 4 KiB | 3 | 输出、退出码、流终结检查全部成功 |
| multipart 写入 1 MiB | 8 | 全部响应成功，各 worker 最后 payload 的窗外读取校验通过 |
| 创建 / 删除循环 | 3 | 创建响应成功，关联删除记录及最终清理确认成功 |

这些数量不含 warmup，也不是性能基线样本。对应原始 JSONL 的路径、SHA256、状态和时间字段自洽检查见 [protocol-validation.json](results/protocol-validation.json)。原始计时可用于验证记录格式与边界；不从 3 或 8 个请求得出吞吐容量、稳定 P95/P99 或服务 SLA。

[test_protocol_frames.py](test_protocol_frames.py) 的四项离线检查覆盖单字节碎片和多帧合并、缺终结帧/截断、压缩或未知 flag/终结后消息，以及超大声明帧。它直接导入冻结的 phase1 runner，不读取认证、不发请求。

## 记录开销的补充对照

[overhead_probe.py](overhead_probe.py) 导入原 phase1 runner 并继承 `Bench`，不修改正式矩阵正在使用的代码。它把 `operation` 记录暂存在内存，待测量、后验与 owned cleanup 结束后，再在最终 `run` 记录之前做 JSON 序列化和文件写入。原始行 schema、每次操作的起止计时、实际工具调用、guards、结果判定、内存中的 rows 和外部资源 sampler 都保留；分组字段只有 `instrumentation` 改为 `buffered-events`。

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B overhead_probe.py \
  --adapter service --phase phase1 --scenario exec_true \
  --concurrency 32 --residents 32 --duration 20 --repeat 1 \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase0/recording-buffered-r1.jsonl
```

六个 buffered 对照已执行，并与更早完成的六个 minimal cell 做描述性比较，结果与图表见 [REPORT.md](REPORT.md)。上面保留的是复现用法。所有 runner 参数原样透传；实际 count/duration、adapter、模板、并发、resident、超时与采样安排须与对应 minimal cell 一致。本次没有交错随机运行，并存在 v1/v2 的版本差异；后续若要更严格估计记录开销，应交错比较并控制版本、时间和宿主负载。

比较总完成速率、各 repeat 的延迟统计、负载生成器 CPU/event lag 和同窗资源曲线。原 runner 的单条 duration 在写记录前结束，因此此对照主要暴露记录处理对事件循环和后续请求的间接影响；flush 时间不进入测量窗。保留逐 repeat 结果，不以三个样本声称高精度开销百分比。

这不是完整的无插桩对照：单调时钟、行对象创建、结果数组和外部 sampler 都仍存在。若进程被强制杀死，未 flush 的 operation 可能丢失；没有最终 run 记录的文件应判为不完整，不能进入性能汇总。非 operation 记录仍即时写入，所以物理文件中的记录顺序未必等于执行时间顺序，分析应使用原始时间字段。

[test_overhead_probe.py](test_overhead_probe.py) 的五项离线契约检查验证计时期间不序列化 operation、原始字段保持、final run 之前 flush 且不重复、warmup 保留，以及 CLI 参数复用。运行两组离线检查需共享隔离环境中已有的 E2B/HTTPX 依赖：

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B test_protocol_frames.py
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B test_overhead_probe.py
```

报告与 PNG/SVG 图由 [analyze_controls.py](analyze_controls.py) 生成；它拒绝未结束、未清理或零 measurement 的对照输入。[test_analyze_controls.py](test_analyze_controls.py) 验证成功完成窗、失败与排空计数。重新生成时将分析限制到 CPU 16–31：

```bash
taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B analyze_controls.py
```
