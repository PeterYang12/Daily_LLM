# 复现指南：从现有lab重跑，以及迁移到新环境

## 0. 路径和前提

原完整实验目录是 `/home/yuhanya/uni-agent-lab`。本总结目录保存文档、轻量证据和脚本快照，不包含权重、完整源码checkout、Python环境或Docker镜像层。

下面命令以原lab布局为前提，**不要直接在本文档仓库根目录运行其复制的launcher**。模型推理、agent与评分都在Docker里执行；宿主命令只管理容器与文件。再次运行要换新的结果目录，不覆盖历史数据。

## 1. 查看现有服务

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py status
```

这一步只查看。要运行实验，先检查GPU是否可用，再按需要启动模型。launcher的默认GPU/端口是固定实验配置，不会自动挑空闲卡。

CPU容器与daemon若尚未启动：

```bash
python3 scripts/labctl.py up
```

`up`管理CPU、专用daemon和janitor。容器名固定；若同名容器属于其他lab根目录，launcher拒绝复用，以免误在旧目录运行。

## 2. 官方单题示例

启动64K eager模型并确认API ready：

```bash
python3 scripts/labctl.py model --which tp1
curl --fail http://127.0.0.1:18082/v1/models
```

ReAct官方入口：

```bash
docker exec -e NUM_WORKERS=1 ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/src/uni-agent/examples/inference/parallel_infer_api.py \
  --data-path /lab/results/data/pilot-six.parquet \
  --task-config /lab/configs/swe-react.yaml \
  --base-url http://172.30.90.3:8000/v1 \
  --model Qwen3-Coder-30B-A3B-Instruct --api-key EMPTY \
  --concurrency 1 --limit 1 \
  --log-dir /lab/results/replay-official-react/logs \
  --result-path /lab/results/replay-official-react/summary.json
```

Claude Code将config换成`swe-claude.yaml`，并使用另一个新输出目录。这两个官方示例直接调用vLLM，不经过Gateway。

## 3. 判题控制先于模型比较

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/stratified-thirty.parquet --mode baseline \
  --output /lab/results/replay-baseline --concurrency 4

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/stratified-thirty.parquet --mode oracle \
  --output /lab/results/replay-oracle --concurrency 4
```

迁到新机器必须重新检查每题是否满足负控制失败、gold成功。本机已保存有效28题；不能复制成功JSON替代新机验收。对照规则与原始名单见[数据文档](../experiments/02-data-and-controls.md)。

## 4. 经Gateway运行三种agent

```bash
python3 scripts/labctl.py model --which 128k
curl --fail http://127.0.0.1:18083/v1/models

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/validated-twenty-eight.parquet \
  --mode react --config /lab/configs/swe-react-128k.yaml \
  --output /lab/results/replay-react --gateway \
  --base-url http://172.30.90.5:8000/v1 \
  --context-length 131072 --max-tokens 4096 \
  --agent-timeout 1800 --concurrency 3
```

其余两组分别换为：

| mode | config | 新输出目录示例 |
|---|---|---|
| `claude` | `/lab/configs/swe-claude-128k.yaml` | `/lab/results/replay-claude` |
| `mini` | `/lab/configs/swe-mini-128k.yaml` | `/lab/results/replay-mini` |

当前复跑脚本已显式开启session logprob；历史主实验启动时未开启。两版都在本目录保存：[已执行driver](scripts/executed/run_swe.py)、[后续复跑版本](scripts/run_swe.py)。这项区别不改变历史分数，也不意味着历史logprob被补齐。

## 5. 统一候选归因与独立回放

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/regrade_swe.py \
  --input /lab/results/replay-react --output /lab/results/replay-react-graded \
  --data /lab/results/data/validated-twenty-eight.parquet --concurrency 3
```

对Claude/Mini使用各自的输入和新输出目录。此脚本不调用模型，只重放已保存候选，处理镜像初始差异并独立测试。已有完成结果会跳过；不要同时启动两个写入同一输出目录的回放进程。

检查最终每组都包含完整预定题号，分别读取resolved、finished、eval_completed和测试修改记录。固定版本与配置也不保证每次生成相同补丁；温度、批处理和运行时数值差异都会影响采样。

## 6. 本地与远端sandbox例子

本地官方demo：

```bash
docker exec -e DEBUG_MODE=1 -e SANDBOX_PROVIDER=docker \
  -e IMAGE=ua-lab/demo:20260912 ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/src/uni-agent/examples/quickstart/sandbox/demo.py
```

隔离检查使用新输出路径：

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/sandbox_checks.py \
  --output /lab/results/replay-sandbox-checks
```

已准备好E2B配置时，可运行：

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_e2b_demo.py

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_sandbox_agent.py \
  --output /lab/results/replay-sandbox-agent
```

后者依次运行Docker与E2B代码任务，并直接调用64K模型服务，因此需要先启动`tp1`。E2B密钥只放在lab的 `secrets/e2b.env`（权限600），不写进本文档或Git；变量名参考[配置模板](configs/e2b.env.example)。

## 7. 性能实验

| 对照 | 先启动的服务 | 实际脚本 |
|---|---|---|
| TP1/TP4 eager | `tp1`、`tp4` | `benchmark_serving.py` |
| eager/AITER | `tp1`、`aiter` | `benchmark_aiter.py` |
| 一个/两个优化副本 | `aiter`、`aiter-replica` | `benchmark_replicas_v2.py` |

脚本使用固定原输出路径并拒绝覆盖。复跑应复制为新脚本并只调整结果目录，记录这个变化。重测之前确认模型API就绪以及没有其他客户端在相同服务上发请求；微基准的结果不和agent任务wall time混算。

## 8. 新环境重建

完整轻量包位于原lab：

```text
deliverables/uni-agent-rocm-reproduction-kit-20260912.tar.gz
SHA256: 5851f6fae73178e49f5eddaad66137f9232052271cd1c12281c368dd29e7f200
```

先校验归档，在空目录用GNU tar解包，保留相对符号链接与空目录。包内已有固定源码和本地patch。按包内手册依次执行：

1. `labctl.py up`，创建CPU与独立daemon。
2. `docker exec ua-lab-cpu bash /lab/scripts/bootstrap_locked.sh`，按锁重建环境。
3. `download_model.py`下载固定revision。
4. 准备任务镜像；带有原镜像manifest时用`prepare_images.py --locked`按digest恢复。
5. 准备tmux、固定Claude二进制、portable Mini运行时。
6. 运行API、sandbox、负正控制，再运行agent。

若只使用本目录的脚本快照，还需检出对应Uni-Agent和verl源码、准备数据与全部大资产；本目录不是已安装的lab环境。训练环境也需另外准备，不能将推理依赖直接视为完整RL环境。

迁移时需检查固定容器名、GPU编号、子网、DNS与bind源路径。`run_swe.py`及部分YAML使用原宿主缓存路径，换lab根目录要同步改写；daemon解释的是它能看到的bind源，不能随意把源路径写成CPU容器内的`/lab/cache`。

## 9. 结束与状态

```bash
python3 scripts/labctl.py status
python3 scripts/labctl.py stop-models
python3 scripts/labctl.py stop
```

停止不删除模型、镜像、日志或结果，不需要全局Docker prune。正常任务通过context销毁；最终版本另有独立owner/TTL回收。脚本/配置快照见本目录`scripts/`与`configs/`，精确来源见[证据索引](../references/02-evidence-index.md)。
