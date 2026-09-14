# E09：环境、实现与复现交付验收

## 目的

确认实验不仅能在一个已有环境里运行，也能解释清楚版本、依赖、数据、代码和结果来源，并为再次运行保留材料。

## 环境重建

使用固定vLLM ROCm基础镜像，在新建、无GPU映射的CPU容器里创建Python覆盖环境，按锁安装58项覆盖依赖，再安装Uni-Agent与随附verl源码。核心agent、Gateway、SWE verifier和E2B导入通过，并验证控制器可见GPU数为0。

这里的58项是覆盖层，不是镜像里的全部Python依赖。保留基础镜像提供的ROCm Torch，避免普通依赖解析将它替换成其他构建。

完整verl训练声明与本次推理所用Transformers版本存在范围差异。验证的是选定推理/Gateway路径，未完成完整RL训练依赖验收。

[冷重建验收记录](../evidence/provenance/reproduction-cold-check.log.json) · [覆盖依赖锁](../reproduce/configs/cpu-overlay.lock) · [bootstrap脚本](../reproduce/scripts/bootstrap_locked.sh)。

## 实验所需的实现补充

| 补充 | 获得的能力 | 验证与范围 |
|---|---|---|
| Docker证据封装 | 保存命令输出、容器配置、候选；用原生文件传输支持大文件 | sandbox状态/二进制往返通过；基于上游DockerSandbox扩展 |
| E2B provider适配器 | 将Uni-Agent Sandbox接口接到用户远端服务 | 官方demo与真实ReAct任务通过 |
| 统一候选归因 | 把agent改动与镜像预置改动分开 | 所有正式84个候选按同一规则重放，没有重采样模型 |
| 日志fork并发补丁 | 避免日志writer与子进程创建相互阻塞 | 确定性前后对照、8个logging测试、官方例子复验 |
| 独立租约回收 | 不依赖单个worker存活即可回收过期任务 | 过期删除、未过期保留、其他owner保留 |

本次只对Uni-Agent上游源码增加一个日志并发补丁；其余主要在实验driver/适配器层。ReAct算法、模型权重和SWE评分逻辑未因该日志补丁改变。

日志并发的确定性测试在原版中观察到子进程3秒仍未结束；补丁后约0.21秒结束。原有8项logging测试通过，独立重建环境也复验该补丁。主28题作业早于补丁启动，未为此重采样。

这些实现是交付所需的工程支持，不计成模型能力收益。

## 固定资产与校验

| 资产 | 保留内容 |
|---|---|
| Uni-Agent/verl | 精确commit、已执行脚本快照、当前复跑版本、唯一上游本地patch |
| 模型 | 固定HF revision、完整16个BF16 shard及其他模型文件的SHA256 |
| 数据 | 官方源revision、选题seed、30题名单、合格28题、parquet SHA |
| 镜像 | 基础/daemon digest；每个任务镜像的digest和ID |
| 实验结果 | 每题结果、候选delta、测试路径审计、完整原始日志及轨迹 |
| 文档材料 | 汇总JSON/CSV、图、复现步骤与原始证据索引 |

专用sandbox daemon的一次磁盘统计为36个镜像、81.56GB，含任务和工具镜像；模型文件约57GiB，另有基础镜像、CLI、运行时与日志。实际存储成本不能只按模型权重计算。

## 复现包

原lab保留约25MB的轻量复现包，包含固定源码、配置、脚本、报告、小型数据和关键证据，不含权重、Docker层、CLI二进制、Python环境和真实凭据。

已经在独立空目录用GNU tar解包并核对：

- **3,137个成员**，其中**2,735个普通文件、5个符号链接**，其余为目录。
- 普通文件大小和SHA256、符号链接目标、两个Git commit全部符合清单。
- 解包后的Uni-Agent只有预期的日志patch，verl工作树没有额外修改。
- 运行脚本hash与provenance一致。

[复现包验收JSON](../evidence/provenance/reproduction-kit-verification.json)。这是本机独立重建与解包验收，没有声称已在第二台物理GPU节点完整复跑28题或后训练。

## 复现应怎样判断成功

先依次确认设备、模型API、官方单题、任务环境控制、完整agent任务、独立判题、轨迹字段。每一层有自己的可观察结果，不用“命令exit0”代替全部完成。

完整步骤见[复现指南](../reproduce/02-replay-guide.md)。本目录主要保存轻量、可阅读的证据；原始lab目录保留完整运行资产。
