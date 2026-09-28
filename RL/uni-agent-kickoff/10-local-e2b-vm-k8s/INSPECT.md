# 查看本机 E2B、Firecracker 与 Sandbox 状态

这些命令用于手动只读查看，不启动 benchmark，不启用后台监控，不重启或清理实例。`watch`、`pidstat` 和日志跟随用 Ctrl+C 退出。

## 进入外层 VM

在物理宿主机 `/home/yuhanya` 所在机器执行：

```bash
/data2/yuhanya/e2b-local-20260916/code/vm-ssh
```

登录后主机名为 `e2b-vm`。VM 由原生 QEMU/systemd 管理，不注册在 libvirt 中，因此不用 `virsh list` 找它。

## VM 内：看 E2B 服务组件

```bash
sudo k3s kubectl -n e2b get pods -o wide
```

`e2b-0` 是 E2B 核心服务，`e2b-gateway-...` 是网关。microVM 由 E2B/Firecracker 创建，不是每个 sandbox 对应一个 Kubernetes Pod。

手动跟随服务日志，可分别在两个终端运行：

```bash
sudo k3s kubectl -n e2b logs e2b-0 -c api --tail=50 -f
```

```bash
sudo k3s kubectl -n e2b logs e2b-0 -c orchestrator --tail=50 -f
```

API 日志适合看创建/删除请求及状态；orchestrator 日志适合看恢复、启动、失败和清理。不把日志中的任意 `failed` 都当成业务失败，需要看对应模块和请求。

## VM 内：看 Firecracker 实例数量和进程

快速查看名为 `firecracker` 的非僵尸进程数：

```bash
ps -C firecracker -o stat= | awk '$1 !~ /^[ZX]/ {n++} END {print n+0}'
```

每 2 秒刷新这个计数：

```bash
watch -n 2 "ps -C firecracker -o stat= | awk '\$1 !~ /^[ZX]/ {n++} END {print n+0}'"
```

查看实例进程的 PID、存活时长、RSS 和启动参数：

```bash
sudo ps -C firecracker -o pid,stat,etime,rss,args
```

参数中的 API socket 通常是 `fc-<sandboxID>-<instanceUUID>.sock`，可用于定位对应 sandbox。需要核对某个 PID 时：

```bash
sudo cat /proc/<PID>/cgroup
```

真实 cgroup 路径通常为 `/e2b/sbx-<sandboxID>-<instanceUUID>`。

快速命令按进程名筛选。需要严谨核对时，检查 `/proc/PID/exe` 的 basename 为 `firecracker`、排除 Z/X 状态，并用 `/proc/PID/stat` 的启动时间识别 PID 重用；固定 runtime 的 `packages/orchestrator/pkg/metrics/firecracker_proc.go` 已实现这一方法。本次在线核验也使用实际 exe 扫描，避免把命令行中包含 firecracker 字样的 shell 当成实例。

不要把遗留 socket 文件、空 cgroup 目录数量当成存活实例数。Firecracker 进程还可能属于模板构建或临时快照处理，因此它和团队 API 列表的数量不要求时时相等。

## VM 内：看哪些实例正在消耗 CPU/内存

```bash
sudo pidstat -u -r -C firecracker 1
```

每秒输出一次，重点看 `%CPU`、`RSS`、缺页速率；默认未启用 `-I` 时，100% CPU 约对应一个外层 VM 的 vCPU。RSS 单位为 KiB，是外层 VM 看到的进程驻留内存，不等于 sandbox 内应用的准确内存用量。

查看某个实例的各个线程，把 `<PID>` 替换为真实数字：

```bash
sudo pidstat -t -u -p <PID> 1
```

也可以运行 `sudo htop`，按 F4 搜索 `firecracker`，按 F6 选择 CPU 排序。

`ps` 的 `R/S` 是瞬时线程调度状态，不是业务状态。主线程显示 S 时 vCPU 线程仍可能忙；低 CPU 也可能在等网络、磁盘、模型或下一条指令。E2B 的 `running` 表示实例已运行，不代表里面正在执行用户命令。

## 宿主机：通过 SDK 查看团队的 running/paused 列表

退出 VM 后，在物理宿主机运行下面的代码。它只查询并翻完所有分页，凭据从私有配置读取，不打印凭据：

```bash
/data2/yuhanya/e2b-local-20260916/client-env/bin/python - <<'PY'
import json
from collections import Counter
from pathlib import Path
from e2b import Sandbox, SandboxQuery, SandboxState

cfg = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
pager = Sandbox.list(
    query=SandboxQuery(state=[SandboxState.RUNNING, SandboxState.PAUSED]),
    limit=100, request_timeout=15, retries=0, **cfg,
)
rows = []
while pager.has_next:
    rows.extend(pager.next_items())
state = lambda x: getattr(x.state, 'value', str(x.state))
print('Endpoint:', cfg['api_url'])
print('Counts:', dict(Counter(state(x) for x in rows)), 'Total:', len(rows))
print('SANDBOX_ID  STATE  VCPU  RAM_MiB  TEMPLATE_ID')
for x in rows:
    print(x.sandbox_id, state(x), x.cpu_count, x.memory_mb, x.template_id)
PY
```

列表是当前 API key 所属团队的视图；paused 实例保留恢复状态，但通常没有持续运行的 Firecracker 进程。实例变化时，API 列表和进程扫描属于不同时间点。

## 看 sandbox 内正在执行的命令

外层 VM 的 `ps` 看到的是 Firecracker，无法直接列出 microVM 内 Linux 的所有进程。已有 SDK sandbox 对象的调用端可以只读查看 envd 管理的运行中命令和 PTY：

```python
for process in sbx.commands.list(request_timeout=10):
    print(process.pid, process.cmd, process.args)
```

这里的 `sbx` 是调用端已有的 sandbox 对象，不新建实例。命令列表不是 microVM 的全部 Linux 进程，也不能单靠它判断某个 Agent episode 是否仍在进行。不要打印 ProcessInfo 的全部字段，以免包含任务环境变量。

`Sandbox.connect()` 对 paused 实例会触发恢复，对运行实例可能更新超时，因此不把它包装成纯只读盘点命令。

## 本次核验快照

2026-09-20 08:35–08:36 UTC：核心 Pod 8/8 Ready，gateway 1/1 Ready；当前团队 API running/paused 列表总数为 0，外层 VM 中扫描真实可执行文件得到存活 Firecracker 为 0，sandbox cgroup 为 0。只代表这一时点；后续应以实时查询为准。
