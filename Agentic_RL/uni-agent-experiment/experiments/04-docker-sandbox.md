# E04：本地Docker sandbox方案

## 目的

验证代码agent所需的可持续文件状态、命令执行、隔离、资源限制和清理机制，并用实际修复任务证明它可用于模型工具调用。

## 实际搭建

可信CPU编排容器通过专用Unix socket管理独立的DinD daemon；每题使用一个任务容器。任务容器不持有Docker socket，不映射GPU，也不挂载宿主完整用户目录或认证目录。Claude/Mini工具运行时只读挂载。

**只把模型服务放进Docker不等于隔离工具执行。** 本次隔离的是agent实际读写和运行的任务仓库，并给判题另外创建容器。[部署图](../architecture/01-deployment.md)与[Docker API图](../architecture/03-apis-and-lifecycle.md)展示了层次。

## 三组实际验证

### 1. 官方sandbox工具demo

使用Uni-Agent原始 `examples/quickstart/sandbox/demo.py`，Docker后端运行于准备好tmux/numpy的`ua-lab/demo:20260912`镜像。验证流程：

1. 通过shell检查环境、使用依赖。
2. editor创建Python文件，shell运行，得到`sum = 7`。
3. editor替换实现，再运行得到`product = 8`。
4. shell切换目录，下一次调用仍保持该目录。
5. 文件write/read与upload/download内容往返一致。

这证明shell与editor共享同一sandbox文件系统，且状态能跨工具调用保持。准备工具镜像属于环境构建；实际工作流仍使用上游demo。

### 2. 隔离、限制与清理

| 项目 | 观测 |
|---|---|
| 不可见的资源 | 宿主实验目录、Docker socket、`/dev/kfd`、`/dev/dri`均不在任务环境中 |
| 跨sandbox文件隔离 | A写入的marker在B不存在 |
| 状态持续性 | 文件在同一sandbox后续调用中可读 |
| 大文件与二进制 | 约1.3MB、含NUL/中文的内容往返一致 |
| 网络禁用 | `--network none`下外连失败 |
| 权限 | `CapEff=0`，`NoNewPrivs=1` |
| 资源 | `memory.max=268435456`、`pids.max=128`、`cpu.max=100000 100000` |
| 命令超时 | 0.3秒预算返回超时错误 |
| 容器销毁 | 退出上下文后，两个验证容器均不可inspect |

[完整验证JSON](../evidence/sandbox/docker-isolation.json)。测试容器使用256MiB/1CPU/cap-drop ALL；正式SWE容器为2CPU/8GiB/pids512，并按旧仓库环境需要允许少量文件权限/用户切换能力。不能把更严格测试配置当作所有SWE任务的实际配置。

本次发现上游通用文件写入把大内容放进命令行，因此实验封装改用Docker原生文件传输。它提供大补丁/二进制传输能力；具体实现见[lab_runtime.py](../reproduce/scripts/lab_runtime.py)。

### 3. 30B ReAct实际代码任务

给出有缺陷的`merge_intervals`，要求正确处理重叠、相接、嵌套、空输入、负数、重复点、无序输入，不修改调用方数据，对反向区间报错。先运行独立测试确认原始代码只有1/10通过，再移除验证脚本及缓存，让agent修复；结束后重新放入测试。

本地sandbox配置为无网络、1CPU、1GiB、pids128、cap-drop ALL。模型请求在本地CPU侧发出，因此工具环境禁网不妨碍推理。

| 结果 | 数值 |
|---|---:|
| 修复前 | 1/10测试通过 |
| 修复后 | **10/10测试通过** |
| Agent状态 | finished=true |
| ReAct步数/工具调用 | 8 / 8 |
| 记录wall time | 48.38秒 |

[任务结果](../evidence/sandbox/docker/result.json) · [修复后的函数](../evidence/sandbox/docker/interval_utils.py) · [验证输出](../evidence/sandbox/docker/verifier.json)。这是独立构造的功能任务，不计入SWE分数。

## 独立TTL回收

为覆盖worker中断后的生命周期，增加单独的janitor容器。它每30秒访问专用daemon，只回收指定owner、带有效TTL且过期的任务容器。默认TTL3600秒，可为长任务调整。

| 控制 | 结果 |
|---|---|
| 本实验owner，TTL已到期 | 删除 |
| 本实验owner，TTL未到期 | 保留 |
| 其他owner，即使TTL已到期 | 保留 |

[TTL验证](../evidence/sandbox/janitor-validation.json) · [回收程序](../reproduce/scripts/sandbox_janitor.py)。它在实验后段增加，最终脚本/配置已包含标签；不追溯声称所有历史任务都使用了它。

## 能支持的结论

本机具备可运行的任务级隔离、持久工具状态、资源控制和生命周期回收方案。命令返回超时不等于进程已消失，所以任务销毁与独立回收仍有必要。

Docker共享宿主Linux内核；外层DinD daemon是使用privileged的可信控制面。正式SWE任务允许网络，只有专门禁网实验使用network none。上述实验不等价于强多租户安全认证；生产部署还需将控制面配置权限、网络访问策略和故障处理纳入设计。
