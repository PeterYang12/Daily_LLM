# 在现有 lab 复跑 30B 级案例与 128-step 训练

以下命令使用已经保存在 `/path/to/uni-agent-lab` 的镜像版本、源码、模型和 Python overlay。原始实验目录受保护；复跑使用新 run 名。全新机器的依赖/资产准备另见[复现审计](../../01-cpu-sandbox-gateway/details/reproduction-audit.md)。

当前主线 `memagent_32b_128_durable` 使用专用容器 `ua-lab-rl-durable` 和 HIP 2–7，完整native保存/恢复协议见冻结文件（原始文件：`notes/rl-memagent-32b-128-durable-protocol.json`）。下面的GPU复现命令供当前任务结束后使用；查看记录本身不需要重启或连接训练进程：

```bash
cd /path/to/uni-agent-lab
cat results/large-durable-live-status.json
python3 scripts/lab_services.py status
```

## 恢复 driver 与推理服务

宿主重建后的实际恢复已经验证，保留的 overlay 无需重新解析安装依赖。

```bash
cd /path/to/uni-agent-lab
docker pull vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa
bash scripts/start_cpu_container.sh
python3 scripts/lab_services.py start sandbox coder30b --timeout 600
```

`start_cpu_container.sh` 检查实际 image ID、现有 `/lab` 挂载和包导入，不映射 GPU。sandbox 使用独立 Docker daemon 和 data2 中的镜像层。第二次重启保留了原镜像和容器，无需重新pull；恢复验收（原始文件：`results/cpu-restoration-second-20260912.json`）包含81项overlay核验与实际Docker文件往返。`lab_services.py start sandbox`会恢复新socket的用户组和660权限。若遭遇异常关机留下`docker.pid`，先确认该lab daemon已停止并保留旧PID文件；不要操作其他Docker daemon的状态。

Uni-Agent 的大文件写入修复在当前源码中已应用。若从原始源码归档新建 checkout，需要先应用[stdin 补丁](../../01-cpu-sandbox-gateway/patches/uni-agent-docker-stdin.patch)，具体回归见[修复说明](../../01-cpu-sandbox-gateway/details/docker-write-file-fix.md)。

## Coder30B 的两组原生 ReAct 案例

```bash
python3 scripts/run_large_swe.py --suite six \
  --name replay-coder30b-six --concurrency 2
python3 scripts/run_large_swe.py --suite expanded \
  --name replay-coder30b-expanded --concurrency 4
```

六题使用 40 turns / 900 秒外层上限，扩展 29 题使用 100 turns / 1800 秒。两者都保存实际任务配置、服务权重路径、依赖版本、候选 patch、原始 verifier 输出及逐题结果。入口会拒绝覆盖已有输出。

Claude / Mini 的复现参数和差异见[三种 agent 的案例记录](../../05-swe-code-agents/details/coder30b-case-audit.md)。它们使用真正的 CLI，通过 Gateway 接本机模型；这组推理命令不会执行参数更新。

## 32B 的固定 external64 与长文案例

32B 推理服务使用 HIP 0。若它先前加载了训练 checkpoint，先停止再启动基础服务，确保恢复的是原始模型；评测入口还会核验实际权重路径。

```bash
python3 scripts/lab_services.py stop 32b
python3 scripts/lab_services.py start 32b --timeout 600
python3 scripts/run_large_memagent.py --name replay-base \
  --expected-model-path /lab/models/Qwen3-32B

python3 scripts/run_large_longcontext.py --name replay-32b-longcontext
```

external64 是 temperature=0 / top_p=1 的固定最终对照，memory/答案各 1024 token。长文另用官方 temperature=1 / top_p=0.7 配置，运行同一组 8/8/4/2/1 个前缀样本，形成 23 个问题×长度 case；它们复用问题，是独立的行为诊断。两组结果不混成一项分数。

## 完整native保存的128-step训练

主线入口为`rl_durable_reproduce.sh`，默认模型、配置、六卡分配和checkpoint卷均固定。独立复现必须使用新`RL_RUN_NAME`；`fresh`拒绝已有run。不要再用旧`rl_memagent_32b_128.yaml`的HF-only配方作为当前恢复方案。

完整checkpoint写入 `/path/to/uni-agent-checkpoints/<runname>`，专用容器将该卷按同绝对路径bind mount。run下`checkpoints`是绝对symlink；仅挂`/lab`不够访问该断点。每份预算370GiB，新旧断点交替保存期间共需约740GiB，另预留128GiB，因此fresh启动门槛为868GiB可用空间。checkpoint为model、optimizer、extra(scheduler/RNG)、data和TQ，不重复导出每步HF。

已有专用容器时，可先做只读配置检查。它不启动训练、不写run产物，也不证明实际resume已经成功：

```bash
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-rl-durable /lab/envs/rl/bin/python /lab/scripts/rl_durable_launch.py \
  --run-dir /lab/runs/rl/replay-memagent-32b-128-durable \
  --checkpoint-dir /path/to/uni-agent-checkpoints/replay-memagent-32b-128-durable \
  --check-config
```

当前训练已结束且六卡空闲后，启动独立复现：

```bash
RL_RUN_NAME=replay-memagent-32b-128-durable \
  bash scripts/rl_durable_reproduce.sh fresh
```

该配置从开始即为128 global steps，512 train / 64 monitor、4步warmup、峰LR1e-6、KL0.01、组内优势不除std；monitor预定0/32/64/96/128。每8步完整保存，全部文件fsync和独立committed指针提交后才轮换旧断点。每次启动创建独立attempt配置、outcome与TensorBoard，不覆盖历史。

## step8受控暂停与实际resume

`fresh`预定在step8断点提交、该步正常metrics/rollout日志及异步dump写完后退出 **75**，outcome为`paused_for_resume_proof`。这不是异常失败或8步终点，128步目标和scheduler不变。先确认此次确为预定暂停，再做文件完整性检查：

```bash
cat runs/rl/replay-memagent-32b-128-durable/run-outcome.json
cat runs/rl/replay-memagent-32b-128-durable/checkpoints/committed_checkpoint.json

docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-rl-durable /lab/envs/rl/bin/python -c \
  'import json; from pathlib import Path; from rl_durable_checkpoint import validate_committed_checkpoint; root=Path("/lab/runs/rl/replay-memagent-32b-128-durable/checkpoints"); pointer=json.loads((root/"committed_checkpoint.json").read_text()); m=validate_committed_checkpoint(pointer["checkpoint_dir"]); print({k:m[k] for k in ("global_step","world_size","total_bytes","optimizer_steps_by_rank","scheduler_epochs_by_rank")})'
```

恢复入口同时检查独立指针、manifest hash、各文件长度及固定head/middle/tail探针。探针不是每个大文件的完整hash，文件完整性检查也不是GPU状态恢复验收。确认同一run暂停、专用容器没有正在运行的训练后，清理该容器Ray残留并恢复：

```bash
docker restart ua-lab-rl-durable
RL_RUN_NAME=replay-memagent-32b-128-durable \
  bash scripts/rl_durable_reproduce.sh resume
```

`resume`载入原生model、Adam、scheduler/RNG、DataLoader与TQ，保留原始step0 monitor并关闭重复初始验证。四rank必须在实际load返回后核对所有Adam counters和固定模型/一二阶moments探针等状态，并产生`RESUME_STATE_VERIFIED`；还需真正执行step9及后续更新。状态审计写入run下`resume_audits`，不能只看启动成功就写resume通过。异步pending/running prompts可能重新生成，本方案不承诺逐bit轨迹复现。

若真实故障发生在未提交步骤，resume保留原日志并归档回滚后缀，隔离未完成checkpoint目录；当前有效步骤按attempt边界统计。只读监控会同时展示完整metrics步数、committed步数和resume audit，独立复现用独立输出：

```bash
python3 scripts/live_durable_status.py \
  --run replay-memagent-32b-128-durable \
  --output results/replay-durable-status.json \
  --steps-csv results/replay-durable-steps.csv --watch --interval 60
```

## 最终BF16导出、回载与配对分析

当前主线完整base为 `results/large-memagent/base-durable/`：driver7.1.0.31500000，LCS **0.4362723214285714**，LCS=1 **19/64**。新run的monitor基线是 **0.49445684523809524**，属于另64题。旧`base-recovery`和旧run的0.516629→0.601278只作历史，不接入新主线对照。

下面是独立复现命令；应使用前文自己的`replay-base`和新run。训练成功完成、step128 committed且数据卷仍有至少80GiB可用空间后，CPU官方merger从native断点导出约61GiB BF16 HF。导出保留原生FP32及Adam状态，逐tensor核验707个tensor与参数总量：

```bash
docker exec -e HIP_VISIBLE_DEVICES= ua-lab-rl-durable /lab/envs/rl/bin/python \
  /lab/scripts/rl_export_committed.py \
  --run-dir /lab/runs/rl/replay-memagent-32b-128-durable --step 128
```

导出目录在lab内`runs/rl/replay-memagent-32b-128-durable/exports/global_step_128/huggingface`，与根盘native训练断点分开。HIP0空闲后再回载并评测：

```bash
python3 scripts/start_32b_checkpoint.py \
  --checkpoint runs/rl/replay-memagent-32b-128-durable/exports/global_step_128/huggingface
python3 scripts/run_large_memagent.py --name replay-final-durable-step128 \
  --expected-model-path /lab/runs/rl/replay-memagent-32b-128-durable/exports/global_step_128/huggingface
python3 scripts/analyze_large_memagent.py \
  --before results/large-memagent/replay-base/results.jsonl \
  --after results/large-memagent/replay-final-durable-step128/results.jsonl \
  --output-dir results/large-memagent/replay-comparison-durable-step128
```

**当前主线**对应的固定终点是`memagent_32b_128_durable`的step128，输出名`final-durable-step128`，配对源为`base-durable`；可直接复制的主线命令见[当前交接点](../RESULTS.md)。导出路径不能误用旧`checkpoints/global_step_128/actor/huggingface`。

回载入口把实际HF路径传入Docker环境，复用base相同的BF16、TP1、eager、16384窗口和非thinking配方。分析器要求完整无错64×2、相同输入/协议及一致runtime；缺题或版本不一致不会输出正式效果差。bootstrap以问题为重采样单位。

`python3 scripts/summarize_lab.py`默认选择当前durable主线、base-durable和预定final-durable-step128；不存在的final产物及未完成长训练保持pending。独立复现可显式传`--large-rl-run`、`--large-base-name`与`--large-final-name`，旧中断结果仍保留。图表、原始轨迹和配对分析说明见[汇报图片](../../05-swe-code-agents/details/large-figures.md)、[旧长训练历史](../../03-memagent-32b-rl/details/rl-32b-long-run.md)和[配对分析说明](../../02-memagent-long-context/details/large-memagent-analysis.md)。
