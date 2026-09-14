# 脚本快照的用途

这些文件从原lab复制，用于查看实验实现和在完整lab布局下复跑。它们依赖容器中的`/lab`路径、固定Uni-Agent源码、Python环境、模型、任务镜像和配置；不能在本目录直接执行就得到完整环境。

- `executed/`：正式ReAct主作业当时保存的driver与Docker封装，保留历史调用状态，未启用session logprob。
- 当前层：后续验收和复跑使用的脚本，包括logprob、TTL等补充。
- `labctl.py`：宿主侧管理外层容器。
- `run_swe.py`：CPU容器内的agent任务driver。
- `regrade_swe.py`：只回放已有补丁，不再次采样模型。
- `e2b_provider.py`与`run_sandbox_agent.py`：远端适配及独立代码任务。
- `benchmark_*.py`：固定工作负载的服务微基准。

完整操作见[复现指南](../02-replay-guide.md)。复制文件的来源和hash见[证据清单](../../evidence/source-manifest.json)。
