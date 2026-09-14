# 共用排障与验收

先完成[共用环境准备](SETUP.md)。以下命令在完整实验工作区执行，所引用的模型、数据和实验脚本需另行准备。

## 收尾、排障与跨机器差异

### 复现验收清单

| 层次 | 查看什么 | 不足以证明什么 |
| --- | --- | --- |
| 依赖 | 镜像digest、torch.version.hip、实际import路径与lock | 仅`pip install`成功不代表GPU内核或训练可运行 |
| 推理服务 | `/health`、`/v1/models`真实root、短生成与工具调用 | 容器Up或端口开放不表示正确模型已经载入 |
| 任务环境 | 同题baseline失败、gold/oracle通过，verifier日志完整 | Agent自述“修好了”不能替代判题 |
| Agent结束 | `finished`、停止原因、最后一个工具是否实际执行 | resolved与正常结束应分别统计 |
| 训练数据 | rollout中的question/episode/context计数；排除synthetic padding | 异步agent日志中的预取样本不等于训练器已消费样本 |
| 参数更新 | native Adam计数、梯度/优势、固定tensor变化 | optimizer计数上升或KL-only变化不等于任务学习收益 |
| 完整断点 | 独立committed指针、model/optimizer/extra/data/TQ | HF权重文件不包含可精确接续的Adam状态 |
| 恢复 | 实际4-rank load对照、下一批UID/问题组和真实下一步 | 只检查文件存在或成功启动进程不算resume完成 |
| 最终效果 | 同机器同协议的完整base/final、逐题配对、重复与格式诊断 | 训练reward曲线上涨不等于独立任务能力提高 |

### 本机遇到的问题与定位顺序

| 现象 | 本机事实 / 应检查的位置 | 本次处理与边界 |
| --- | --- | --- |
| `torch.version.hip`为空或变成CUDA torch | pip自动依赖解析可能替换镜像torch | 使用固定ROCm镜像+system-site-packages overlay+精确`--no-deps`安装 |
| vLLM / 模型导入不兼容 | 训练TF5.9.0与推理/CPU TF5.16.1承担不同路径 | 保留两个overlay/运行时；不在已有服务中随意升级 |
| 容器Up但API不通 | `sleep infinity`只说明容器存活；模型进程可能未启动或已经退出 | 查具体服务stdout、实际进程和`/v1/models`；避免重复启动vLLM |
| Docker sandbox找不到镜像 | 宿主daemon与独立sandbox daemon的镜像空间不同 | 对`unix:///lab/run/docker.sock`对应daemon准备并检查镜像 |
| sandbox不能连模型API | 子容器网络与本次localhost服务不一致 | 本机任务容器使用host network；默认bridge/iptables不是这次方案 |
| 大文件write_file出现E2BIG | 用长shell argv/环境传文件触及系统上限 | 应用stdin补丁；1/16MiB及UTF-8/NUL/路径/错误情况已验证 |
| 32B多桶参数同步或ROCm路径问题 | CPU与训练verl commit不同；需本次vLLM兼容patch | 固定训练gitlink与单sender checkpoint engine；先跑32B pilot |
| 最后一次KV wake/validation OOM | 4B SWE早期试验曾保存checkpoint后在final validation失败 | checkpoint存在不等于run完成；本次32B中间验证已通过，最终仍以128完整outcome为准 |
| `perf/mfu/actor=0` | 设备FLOPs识别未覆盖当前设备 | 不把它解释成GPU没工作；查看实际step耗时、显存与利用率 |
| old_log_prob偶发变慢 | 本机有快慢step交替，具体根因未定 | 记录阶段耗时；没有声称已通过调参修复，也不改冻结数学协议 |
| profiler attach失败 | 进程未用`ROCP_TOOL_ATTACH=1`启动 | 此次attach在真正附加前失败，不能把它当作已有kernel profiling证据 |
| 默认greedy重复结果不同 | 同body温度0调用和完整64题重复均观察到差异 | 主协议保留；`VLLM_BATCH_INVARIANT=1`作为单独冻结辅助协议，backend也会变化 |
| Greedy稳定辅助更慢 | 当前ROCm镜像切到TRITON_ATTN，完整64题约19分钟/遍 | 作为一致性与耗时取舍记录，不与默认ROCM_ATTN分数混配 |
| step分数涨但答案内容没变 | TeX空格、千位逗号等会影响固定LCS | 保留官方主指标；格式诊断另列，不据此重写训练奖励 |
| 答案从`Bill Clinton`变成长名后升分 | 标签和token重合度也会影响LCS | 逐题看原始答案与标签，不能一概归因为新增知识 |
| 停电/重启/SIGTERM | 本次发生过环境中断及进程终止 | 每8步完整commit；保护旧commit，按已提交边界恢复，回滚日志保留 |
| 实时JSON仍显示running | writer本身也可能停止，文件留下旧状态 | 同时检查`updated_utc`、writer PID、实际launcher和run-outcome |
| HF导出宿主不可读 | Docker root写出的safetensors可能是mode600 | 本次专用owner helper仅调整生成的HF shard归属；不改payload、mode、mtime或原生断点 |
| 8B merger成功但找不到HF index | 固定Transformers5.9默认max_shard_size=50GB；8B小于阈值，合法singleton没有index | 本次原export/helper错误假设一定分片，保留其failed记录；仅从真实399项header派生标准index，完整SHA确保payload不变，再运行独立CPU收尾 |
| Miles启动依赖缺失 | 历史setup有未锁包，SGLang源码还需要指定分支 | 使用补齐的精确overlay锁、固定SGLang-Miles commit及最小兼容patch |
| Miles global_step metadata看起来为0 | 某些保存元数据字段滞后 | 按真实optimizer counter和checkpoint内容验收，不只读单个metadata字段 |

### 对新机器允许调整什么

宿主lab根目录、日志输出名、缓存位置、容器访问用户和空闲GPU映射可以按机器安排，但要同步检查脚本中的固定路径、mount和端口。改变驱动、镜像、模型revision、tokenizer、数据、batch、采样、上下文预算、KL、学习率、trainer数量或attention backend时，应保存为一套新的运行协议，建立匹配base，不把结果接在本机原曲线上。

本次专用的 `finalize_large_rl.py`、`finalize_stable_memagent.py`、`final_model_gate.py`、`rl_export_owner.py`含**固定run名和固定base/final目录**。它们用于本机`memagent_32b_128_durable`的自动收尾；新机器使用新的`RL_RUN_NAME`时，优先执行本手册给出的通用导出/服务/评测命令。若复制自动controller，应复制成新脚本、改完整路径常量、注册自己的baseline/protocol并完成检查，不要只改一处字符串就运行。

稳定辅助协议的JSON还保存了本机runtime、base结果名、source hashes和日志证明。它是本次已注册实验的记录，不是可以直接移植到任意机器的空白模板。新机器要复现稳定辅助路径，应在新base运行前记录该机器自己的固定设置和版本。

### 保存、迁移与恢复完整训练状态

仅搬走最终BF16 HF目录，适合继续推理、做评测或从该权重重新开始训练。需要保留当前Adam和数据位置继续训练时，必须同时搬走：

1. 该run完整native checkpoint卷中的最后一个已committed目录及指针。
2. run下的attempts、checkpoint_commits、source_audits、resume_boundaries、有效日志/rollout与resolved_config。
3. 相同固定模型、源码、依赖与数据。修改并验证新机器的绝对挂载路径，尤其是run下的`checkpoints`绝对symlink。

复制大断点之前，先确认完整commit并固定其生命周期；不要在keep1轮换删除同一目录时直接裸`rsync`。本次导出器用共享reader lease，retention使用同一锁的独占lease；完整训练迁移也需要停止在已提交边界，或采用等价的一致性保护。

当前durable入口用于恢复到原定128步目标，并拒绝对已经达到目标的断点再次resume。若要增加总步数/epoch，必须另建协议与状态迁移验证；仅保留完整native断点不等于本入口已实现任意步数扩展。

不将旧run中断前的46步、当前run回滚的33/34步和恢复后步数相加成128。本机真实计数以每次attempt的retained边界与训练器已消费rollout为准。异步pending/running任务恢复时可能重新生成，完整状态恢复不承诺未来轨迹逐bit重现。

### 如何提交一次自己的复现记录

建议建立一份自己的`notes/run-record.md`，填写以下字段，然后保留原始产物：

```text
节点 / 日期 / 操作者：
GPU架构、数量、单卡可见显存：
宿主kernel、amdgpu driver、Docker版本：
基础镜像digest及实际image ID：
5份源码revision和本地patch SHA256：
模型名、HF revision、shards清单：
CPU / RL / optional Miles环境lock和实际版本：
数据manifest SHA256与512/64/64划分：
run名称、完整命令、GPU分配、全部协议改动：
fresh开始时间、step8暂停、实际resume验收：
总global steps、真实session/context/padding、Adam counter：
native commit、HF导出、最终精度/配置/tokenizer验收：
base / base-repeat / final / final-repeat名称、完整率、LCS与LCS=1：
逐题改善/回退、格式诊断、重复性及不确定性：
错误、重试、回滚和未完成项：
```

完整记录应能让另一个人从命令找到原始日志，从分数找到逐题输出，从模型找到对应native commit，并分清哪些是工程跑通、哪些有实际学习信号、哪些还有待验证。
