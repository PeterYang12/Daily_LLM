# 环境与简化复现步骤

本轮使用已经部署好的本机E2B服务。该目录不包含VM镜像、E2B安装包或凭据；服务部署细节见[已有部署记录](../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/README.md)。先读[本轮结论](REPORT.md)及各场景边界。

## 当前R2训练：检查现有任务

R2位于 `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2`。原始训练和2026-09-20 20:24:52 UTC的检查点恢复已经启动；当前计数以 [Phase 4 STATUS](phase4-rl/STATUS.md) 为准。从本目录执行以下只读命令即可检查现有任务：

```bash
sudo -n python3 -B phase4-rl/status_snapshot.py \
  --root /data2/yuhanya/e2b-rl-pathfinding-20260920-r2
```

该命令读取记录后退出，不启动训练或采集器。R2的 [归档恢复配置及哈希](phase4-rl/configs/resume-20260920T202452Z-d696dc09/manifest.json)、[运行代码链接](phase4-rl/code/runtime) 和大文件路径见 [Phase 4 README](phase4-rl/README.md)。62/16任务子集、检查点继承和评估安排见 [RUN-PLAN](phase4-rl/RUN-PLAN.md)。不需要重新执行原始启动或恢复命令。

以下环境安装和负载命令保留用于阶段0–2服务/SDK实验的独立复现，不是当前R2训练的启动步骤。

## 1. 准备客户端环境

在此目录执行：

```bash
python3 -m venv /data2/yuhanya/e2b-benchmark/session-20260920/env
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -m pip install \
  -r phase2-sdk/configs/client-requirements.lock
```

本机已准备该环境，无需重复安装。当前脚本使用私有 `/home/yuhanya/.config/e2b-local-20260916/client.json`，包含api_key/api_url/sandbox_url。其他机器需准备自己的私有配置并核对脚本默认路径、VM SSH helper及CPU编号。

## 2. 先做阶段0盘点

```bash
python3 phase0-environment/inventory.py \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase0/new-inventory.json
```

检查服务、模板、内存、配额、页模式和其他业务实例。采样器是有限前台程序；不会自动开启旧监控服务。完整选项见[阶段0](phase0-environment/README.md)。

## 3. 小规模验证一个协议场景

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \
  phase1-service/code/runner.py \
  --adapter service --phase phase1-service --scenario exec_true \
  --concurrency 1 --residents 1 --count 20 \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase1-service/new-smoke.jsonl
```

SDK版本把adapter改为sdk，phase改为phase2-sdk。使用新的输出文件；脚本拒绝覆盖，结果包括归属清理状态。测量和准备失败要分开处理。

## 4. 执行矩阵

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python run_stages.py matrix.json
```

总执行器自动给每个单元附有限资源采样并串行运行，协议/SDK匹配点交错。它遇到准备失败会停止，已完成单元不会覆盖；不要删除失败文件来重跑。本轮的原始失败、独立重跑和继续执行分别在matrix、continuation-matrix、last-retry-matrix及相应manifest中留档。

补充场景由 `supplemental-matrix.json` 管理；四进程对照由 `run_multigenerators.py` 管理。它们应与其他测量串行，不能并行制造未记录的共享负载。其他机器需调整run_stages中的runtime路径和taskset CPU集合。

## 5. 运行官方SDK场景

本机新模板为 `benchmark-ci-minimal-20260920`。其他机器需要先按[模板适配说明](phase2-sdk/README.md)准备同类普通页模板，不能把任意base模板当作可执行run_code的解释器模板。

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \
  phase2-sdk/code/official_sdk_baseline.py \
  --template benchmark-ci-minimal-20260920 --iterations 20 --repeat 1 \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase2-sdk/new-official.jsonl
```

## 6. 从记录重建结果

各结果目录的冻结输入清单记录原始路径和SHA。主矩阵使用统一 `analysis/summarize.py`、`analysis/resources.py`，统计方式见[分析说明](analysis/README.md)。来源目录可以包含补充实验，因此重建主矩阵时应使用冻结清单中的精确文件，不把其他场景混入。

独立场景提供直接的再分析入口：

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python phase0-environment/analyze_controls.py
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python phase2-sdk/code/analyze_application.py
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python phase1-service/code/analyze_multigenerator.py
```

这些分析脚本只读取保存的数据，不调用服务。生成图表需要matplotlib；PNG均有SVG版本。源码/环境/输入变化应产生新的实验记录，不覆盖已有结果。
