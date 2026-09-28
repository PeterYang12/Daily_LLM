# E2B benchmark：待执行任务

记录日期：2026-09-20 UTC。

**状态更新（2026-09-20）**：用户随后授权阶段0/1/2基线及诊断，执行材料已按阶段存放于[独立benchmark目录](../../../sandbox/benchmark/README.md)。本文件保留最初的任务规划；原生Factory专项仍未执行，SDK任务已完成。核心服务配置未修改，旧长期监控未重新启用。

用户随后将目标扩展为从 E2B 服务、SDK、真实数据回放到真实 RL profile 的完整实验。整体协议、阶段、指标、图表、稳定性和优化闭环见 [性能实验总方案](BENCHMARK-PROGRAM.md)。本文件保留原有 Task 1/2 的具体入口和准备细节。

用户已确认以下两个独立待办：

| 任务 | 内容 | 状态 |
|---|---|---|
| Task 1 | E2B 原生 `BenchmarkConcurrentResume` 启动/恢复基线 | 待执行 |
| Task 2 | 官方 SDK `performance.py`：创建、命令健康检查、两次 Python 代码执行 | 已执行3轮/60新实例，见[结果](../../../sandbox/benchmark/phase2-sdk/APPLICATION-RESULTS.md) |

后续阶段0/1/2的执行与失败记录以独立benchmark目录为准；此处的原始计划不是全部实验已经完成的声明。

## 目标

此前记录的第一版方案采用 E2B 自带的 `BenchmarkConcurrentResume`，获得可重复的底层启动/恢复延迟、整批完成时间和失败率，观察并发增加后的变化。无需模型、RL、SWE-bench 任务或额外业务脚本。下文保留这一尚待执行的原生专项；末尾的官方SDK脚本则已在后续实验中运行。

源码基线：`e7b22501988c826563bf1b3cf0d22a1183e7affe`。本地 checkout 为 `/data2/yuhanya/e2b-local-20260916/build/runtime-4k-20260920`；正式执行时记录实际源码改动及相关二进制、模板、内核版本。

主要入口：`packages/orchestrator/benchmarks/concurrent_benchmark_test.go`。
辅助串行入口：`packages/orchestrator/benchmarks/benchmark_test.go` 中的 `BenchmarkBaseImageLaunch`。

优先用并发基准的 `N=1` 作为串行基线，使各并发档位走相同代码和配置。两个独立 benchmark 的默认页模式及 cgroup 实现有差异，不直接拼接结果。

## 第一版拟定矩阵

| 项目 | 第一阶段 |
|---|---|
| 页模式 | 普通页，`DISABLE_HUGE_PAGES=true` |
| 基础镜像 | 上游默认 `e2bdev/base`；执行时固定并记录实际镜像 digest |
| Sandbox 规格 | 上游默认 2 vCPU、512 MiB RAM、2 GiB 磁盘 |
| 并发档位 | 1、2、3、4、8、16、32 |
| 缓存状态 | 按上游逻辑先准备模板并预热一次；报告为热恢复 |
| 重复 | 先小规模检查，再按档重复多轮；100 轮可作为初始候选，报告实际样本数 |
| 扩展阶段 | 基线完成后再考虑 4 GiB 模板、大页对照；尚未执行或承诺结果 |

调整模板规格、镜像或页模式时使用明确的新模板构建标识/独立缓存，不能沿用旧 build ID 而误命中另一种规格。不要直接把现有 `base-4k-4g` 服务端别名当作 benchmark 内部构建标识。

## 实际执行流程及计时范围

1. 准备本地内核、Firecracker/envd、网络与 NBD 池；模板未缓存时先构建。准备与构建不计入后续恢复延迟。
2. 从基础模板恢复一个 sandbox，再关闭，用作显式预热。
3. 每一轮先准备 N 个并行任务，用同一个起跑信号同时调用 `Factory.ResumeSandbox()`。
4. 内部恢复磁盘/内存快照、配置网络、启动 Firecracker，并等待 sandbox 内 envd 初始化成功。
5. 记录每个调用耗时及整批完成耗时。成功与失败分别计数；延迟分位数来自成功样本，失败不能从报告中隐藏。
6. 关闭本批 sandbox，再进入下一轮。清理耗时不计入启动指标。

每轮没有额外的训练、CPU 压力、文件读写或用户命令负载。模板初次构建的 `StartCmd` 为 `echo ... && sleep .1 && echo ...`；它不是每轮基准另行执行的任务。envd 就绪检查也不是用户首条工具命令的端到端测试。

该基准直接调用 runtime，绕过 8088 API、SDK、团队配额和线上“同时启动/恢复 3 个”的准入。结果用于底层恢复性能，不能标成当前线上 API 吞吐或 256/512 个真实业务实例的容量。

## 大页开关的准确含义

```go
disableHP, _ := strconv.ParseBool(os.Getenv("DISABLE_HUGE_PAGES"))
useHugePages := !disableHP
```

| 并发 benchmark 环境变量 | Sandbox guest RAM 模式 |
|---|---|
| `DISABLE_HUGE_PAGES=true` | 禁用显式 HugeTLB，使用普通页 |
| 未设置，或 `DISABLE_HUGE_PAGES=false` | 上游默认启用大页 |

此开关控制 Firecracker 为整个 sandbox guest RAM 使用的内存 backing，并同步到模板构建配置；不控制某个 Python/训练任务是否自己申请大页，也不改变外层 QEMU VM 的内存 backing。

当前服务所在 VM 的 HugeTLB 池为 0、sandbox 使用普通 4 KiB 页，因此**页模式上可以运行普通页基准**；不需要先恢复大页池。大页模式需要在测试所在系统准备相应资源及匹配模板，不能默认在当前零大页池环境可运行。

它是 benchmark 自己读取的变量，不是现有 E2B daemon 的通用环境变量。现有服务的本地补丁使用另一个变量 `E2B_SANDBOX_HUGE_PAGES`。

## 执行前准备

- 确定与在线 E2B 服务隔离的测试环境及资源范围。基准会自行管理网络/NBD，并监听固定 sandbox proxy 端口 5007，不直接与线上 daemon 共享这些资源并跑。
- 检查 Linux/root、KVM、UFFD、NBD、cgroup、Go 1.26.8、Firecracker/envd 路径和 guest kernel。仅设置大页开关不代表全部环境已就绪。
- 确认最大并发下的池容量和设备数量。源码注释提到池容量，但当前实现实际使用网络池常量和 `cfg.NBDPoolSize`，不能假定随 N 自动扩容。
- 当前长期监控保持停止。需要的短期采集在正式执行时按本次 benchmark 单独安排。
- 大型缓存、二进制、原始日志和运行结果放在 `/data2`；本目录仅保留方案、复现说明、汇总和图表。

## 预期输出

- 各档实际轮数、成功/失败数及失败原因。
- 单实例平均值、P50/P95/P99、最小/最大恢复耗时；标明成功样本数与统计方法，小样本尾分位仅作参考。
- 整批平均耗时及按成功数/测量时间计算的恢复吞吐。
- 并发—延迟、并发—吞吐曲线及可选资源采样/OTel trace。
- 后续普通页/大页对照保持相同规格、版本、缓存状态、并发和计时范围。

本记录不包含新测量结果。此前 512 容量验收的 SDK 时长、历史大页的 API handler 时长与本基准口径不同，不能直接当作对照组。

## Task 2：官方 SDK 性能测试

2026-09-20 扩大检索到官方 SDK 仓库后，找到实际调用 SDK 的性能脚本。此前对 Runtime 仓库的检索不能推导为 E2B 没有 SDK benchmark。

- 仓库：`e2b-dev/E2B`。
- 核对 commit：`ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b`。
- 脚本：[packages/code-interpreter-python/tests/performance.py](https://github.com/e2b-dev/E2B/blob/ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b/packages/code-interpreter-python/tests/performance.py)。
- SDK：`e2b_code_interpreter.Sandbox`，基于 E2B SDK 真实调用服务，不调用模型。
- 默认轮数：20，通过 `E2B_TESTS_BENCHMARK_ITERATIONS_COUNT` 修改。
- 默认模板：`code-interpreter-v1`，通过 `E2B_TESTS_TEMPLATE` 修改。

每轮串行执行：

1. `Sandbox.create(template=template)`，记录客户端创建耗时。
2. `sbx.commands.run("curl http://0.0.0.0:49999/health")`，记录完整命令调用耗时。
3. 第一次 `sbx.run_code("print('Hello, world!')")`，记录代码执行调用耗时。
4. 第二次运行同一代码，记录第二次调用耗时。
5. `sbx.kill()`，清理该实例；销毁没有独立计时指标。

输出四类耗时的最小/最大/平均/中位数，逐轮曲线及箱线图 `performance_plot.png`。代码使用客户端 `time.time()` 计时；首次/第二次运行可反映初始化与复用差异，但不是经过完整控制变量的冷缓存/热缓存实验。原脚本没有并发矩阵、P95/P99、持续负载或文件读写专项。

对本机的准备事项：

- 核对本地 SDK API/沙箱 endpoint 配置，避免沿用云服务默认地址。
- 准备包含代码解释服务（49999 端口）的普通页模板。2026-09-20 迁移后的模板名称清单中未见默认 `code-interpreter-v1`；名称清单不足以证明其他模板具备该服务，需实际核对。任意通用 `base` 模板不能直接假定支持 `run_code`。
- 规划时原客户端环境只有`e2b==2.49.1`。随后已在独立benchmark环境固定安装Code Interpreter SDK和matplotlib，并准备了本地精简模板，详见[阶段2环境](../../../sandbox/benchmark/phase2-sdk/README.md)。
- 实际执行前补齐明确的结果校验与异常清理；原脚本没有 `try/finally` 清理保障。保留原始脚本及适配差异，计时宜改用单调时钟，并导出逐样本数据。
- 这条路线调用现有服务，页模式由服务端和所选模板决定；不读取 runtime benchmark 的 `DISABLE_HUGE_PAGES` 变量。

对于“本机真实SDK调用、固定简单任务、先有可重复数据”的目标，这份官方脚本更贴近服务使用方式。用户将其列为Task 2，现已执行；Task 1原生启动基准保留为后续专项。
