# 32B MemAgent RL：运行与恢复步骤

[本实验总览](README.md) · [全部实验](../README.md)

先完成[共用环境准备](../00-overview/SETUP.md)。以下命令在完整实验工作区执行，所引用的模型、数据和实验脚本需另行准备。

## Qwen3-32B MemAgent RL：跨机器复现细节

本节对应本lab实际执行的`memagent_32b_128_durable`配方。它从固定的Qwen3-32B原始权重开始，在Uni-Agent官方MemAgent任务上训练128个trainer globalstep；每8步保存可恢复的全状态，并在第8步主动暂停、重启进程、恢复后继续。这里的“跑通”需要同时看到真实rollout、非均匀奖励/策略梯度、参数更新、完整断点、实际resume以及本机重新测量的评估结果。

这是一份操作说明，不能当作128步已经完成的证明。实际完成状态以run的`run-outcome.json`、完整commit、native日志和最终gate为准。环境准备、扩展案例和最终评估 controller 分别见本手册的对应章节。

### 1. 验证范围与资源预算

本次实际机器是8张gfx950 GPU，PyTorch可用显存约252GiB/卡、主机内存约3TiB。早期runtime报告MI350X，用户将节点称为MI355；后续driver环境中的GPU名称字符串为空，但gfx950、显存和实际计算检查正常。记录实际探测值，不用字符串名称替代硬件验证。

已验证的布局是6卡：HIP2–5为4个FSDP2训练rank，HIP6–7为一个TP2 standalone rollout副本。训练侧4卡还会切换成两个TP2副本完成原生validation。HIP0/1留给独立推理/评估。`RL_GPU_IDS`使用物理HIP编号；它与`rocm-smi`显示行号可能不同，跨机应按PCI地址核对。单卡tiny tensor检查不覆盖RCCL、多桶权重同步、backward或KV wake，因此新机器还要执行pilot。

| 项目 | 本次规模/预算 | 用途 |
| --- | ---: | --- |
| 原始32B BF16模型 | 61.02GiB，32,762,123,264参数 | 初始化actor、reference和rollout |
| FP32主参数 | 约122GiB/全模型 | 保留小学习率更新 |
| Adam两个FP32 moments | 约244GiB/全模型 | 真实optimizer恢复 |
| 单个native完整checkpoint | 实测约366.2GiB | model+optimizer+extra+data+TQ |
| 保存期间新旧两份共存 | 约732GiB | 新断点提交前保留旧断点 |
| checkpoint启动空间门槛 | `2×370+128=868GiB`可用 | 当前keep1及128GiB余量 |
| 已有断点后的下一次保存 | 至少`370+128=498GiB`可用 | 每次保存前再次检查 |
| BF16 HF导出 | 约61GiB/份 | 独立推理、评估和便携权重 |
| 新机1步pilot HF导出 | 约122GiB | pilot原生HF保存的是FP32；不是正式BF16导出 |

本次训练峰值allocator allocated约153GiB/rank；其他GPU进程、RCCL及临时buffer还会占显存。主机3TiB内存是实际验证环境，不是最低要求；大模型初始化、CPU参数副本、validation时offload、文件page cache与HF合并会同时使用大量内存。低内存节点应先测pilot和完整8步保存，不要只按61GiB模型大小估算训练资源。

先按7–10小时级训练/保存/评估安排窗口，再用新机实测校准。此节点正常计算步骤约160–170秒，曾有old-log-prob间歇变慢至整步约200–245秒；完整fsynced保存约4–5分钟/次。下载、首次初始化、编译、人工检查和中断恢复另外计时。

### 2. 精确版本

| 层 | 固定来源 |
| --- | --- |
| Uni-Agent | `https://github.com/verl-project/uni-agent.git`，`472c875a97f9a2764c81a6ec7581167632bd8bcc` |
| 训练verl | `https://github.com/verl-project/verl.git`，`a9f2985159536a607211dcac730d3f5d55028950`，即上述Uni-Agent的gitlink |
| ROCm image | `vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa` |
| 本机该image ID | `sha256:33b992ce0f367784daf23c535ed63af3703e55ade7f5016a08e9c41a24a7a3b9` |
| image内Python/Torch | Python3.12.13，Torch`2.12.0+git6bbd260`，HIP`7.2.53211` |
| image内vLLM | `0.28.1rc1.dev516+g9ea8f3ffc` |
| 训练overlay | `configs/rl_requirements.lock`，118项；实际Transformers5.9.0、Ray2.54.1、torchdata0.11.0、tensordict0.10.0、PyArrow25.0.1 |
| TransferQueue | Git pin `fc33c979db6bd661802f6af458e75e850055fb20`，来自`https://github.com/Ascend/TransferQueue.git` |
| 模型 | `Qwen/Qwen3-32B`，revision `9216db5781bf21249d130ec9da846c4624c16137` |
| 数据 | `BytedTsinghua-SIA/hotpotqa`，revision `27275ff4fee67ac0acb6478e405e7ac07efbdc1a` |

不要用本lab另一个`src/verl`的HEAD替代`src/verl-rl`。本lab的两个实验clone的origin指向原机器的`/path/to/uni-agent`、`/path/to/verl`，不是可跨机使用的下载地址；新机用上表公共URL。

`src/verl-rl/.git`在本机还是一个含绝对路径的worktree文件，指向`/path/to/uni-agent-lab/src/verl/.git/worktrees/verl-rl`。直接拷贝它到另一目录会留下失效gitdir。kit已把这个worktree转换为独立Git仓库，可用verify_reproduction_kit.py核对。仅在不使用kit源码的互斥路线中，才独立clone并checkout上述commit。Python venv也要在固定image里重建，不把原机器`envs/`当作可移植环境。

### 3. 目录约定与迁移边界

| 宿主 | 容器 | 内容 |
| --- | --- | --- |
| `$LAB_ROOT` | `/lab` | 本lab的scripts/configs/notes、源码、模型、数据、run日志与HF导出 |
| `/path/to/uni-agent-checkpoints` | 同绝对路径 | 大型native checkpoint专用卷 |
| `$LAB_ROOT/tmp/rl-durable` | `/tmp` | 专用训练容器临时文件 |
| `$LAB_ROOT/runs/rl/<run>/checkpoints` | `/lab/runs/rl/<run>/checkpoints` | 指向上述checkpoint卷`<run>`目录的绝对symlink |

当前脚本可通过`LAB_ROOT`改变宿主lab位置，容器内仍必须叫`/lab`。`RL_RUN_NAME`、`RL_CONTAINER_NAME`和`RL_GPU_IDS`也可设置。以下两处checkpoint根路径目前是**硬编码**，没有`RL_CHECKPOINT_ROOT`参数：

- `scripts/rl_durable_reproduce.sh`的`checkpoint_volume=/path/to/uni-agent-checkpoints`；
- `scripts/rl_durable_launch.py`对checkpoint目录父路径、run basename及symlink的检查。

为原样复现，可在新主机准备同一绝对路径，并让它实际位于有足够空间的持久磁盘上。操作系统用户名不必相同，但当前宿主账户必须能创建其run子目录，Docker要bind同一绝对路径。原机器选择根盘只是因为当时data2剩余空间不足；新机可把独立大盘挂载到这个位置。用`df -hT`、`findmnt -T`确认实际文件系统，不能仅看路径名称。

若必须改checkpoint路径，应在新的实验副本同时修改host wrapper、容器bind和launcher校验，建立新的源码/hash协议并重新pilot；不要删除source guard，也不要把改过的代码当作原run的精确回放。原生resume还要求相同4个trainer rank/FSDP2逻辑world size；当前工具没有实现从4-rank optimizer到其他world size的重分片。

`finalize_large_rl.py`目前还固定使用run名`memagent_32b_128_durable`及容器`ua-lab-rl-durable`，不自动跟随`RL_RUN_NAME`。因此本手册主路线使用新的`repro-memagent-32b-128`，最终通过通用导出/评测命令完成，不直接启动这个本机专用controller。可选的原名自动化路线需先重新登记新机baseline/protocol；不要混用两条路线。

这里还假定新机器的对应checkpoint子目录为空。同一主机上换一个LAB并不会隔离硬编码的checkpoint卷：两个LAB若沿用同一run名，仍指向同一native目录，fresh guard会拒绝覆盖。并行/重复实验需一起规划run名、容器名、checkpoint子目录和最终controller。

### 4. 在新LAB准备代码、image与checkpoint卷

以下命令在**新机器宿主的普通工作账户**执行。先从本lab的复现代码包取得完整`scripts/`、`configs/`、`notes/`，其中必须包含冻结协议及maintenance ledger。仅clone上游仓库不会得到本次lab脚本。代码复制时排除`__pycache__`；不要复制旧run/result状态来冒充一次新实验。

```bash
# 宿主；沿用本手册开头唯一LAB_ROOT，kit已解包且CPU已bootstrap。
: "${LAB_ROOT:?Set LAB_ROOT once in the opening section}"
export RL_RUN_NAME="${RL_RUN_NAME:-repro-memagent-32b-128}"
export RL_CONTAINER_NAME="${RL_CONTAINER_NAME:-ua-lab-rl-durable}"
export RL_GPU_IDS=2,3,4,5,6,7

mkdir -p "$LAB_ROOT"/{src,models,data,envs,cache,tmp,home,runs/rl,results,logs,controllers}
cd "$LAB_ROOT"

# scripts/configs/notes由主复现文档所述代码包复制到上述LAB后：
test -f scripts/rl_durable_reproduce.sh
test -f configs/rl_requirements.lock
test -f notes/rl-memagent-32b-128-durable-protocol.json
test -f notes/rl-durable-maintenance-amendments.json

docker pull vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa
docker image inspect vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa \
  --format '{{.Id}} {{json .RepoDigests}}'

# Kit路线：验证现有checkout和已应用patch，不再次clone或apply。
test "$(git -C src/uni-agent rev-parse HEAD)" = 472c875a97f9a2764c81a6ec7581167632bd8bcc
test "$(git -C src/verl-rl rev-parse HEAD)" = a9f2985159536a607211dcac730d3f5d55028950
git -C src/uni-agent ls-tree HEAD verl
git -C src/uni-agent apply --reverse --check "$LAB_ROOT/patches/uni-agent-docker-stdin.patch"
git -C src/verl-rl apply --reverse --check "$LAB_ROOT/scripts/rl_verl_rocm.patch"

test -d /path/to/uni-agent-checkpoints
test -w /path/to/uni-agent-checkpoints
df -hT "$LAB_ROOT" /path/to/uni-agent-checkpoints
findmnt -T /path/to/uni-agent-checkpoints
```

checkpoint目录应由新机管理员事先创建/挂载并交给当前账户使用。上述命令不更改已有他人目录的权限。采用普通用户在宿主clone，也避免容器root创建Git目录后触发宿主Git的dubious ownership检查。补丁只改ROCm下vLLM worker的HIP/CUDA可见设备同步；不改变loss或模型结构。

#### 不用kit源码时的互斥替代路线

下面仅供还没有`src/uni-agent`和`src/verl-rl`的独立空源码目录使用；走过kit解包路线则跳过整块。仍需从kit获得lab脚本/locks/协议。CPU/debug verl还要单独固定到`10db40...`，不能用训练verl替代。

```bash
test ! -e src/uni-agent
test ! -e src/verl-rl
git clone https://github.com/verl-project/uni-agent.git src/uni-agent
git -C src/uni-agent checkout --detach 472c875a97f9a2764c81a6ec7581167632bd8bcc
git clone https://github.com/verl-project/verl.git src/verl-rl
git -C src/verl-rl checkout --detach a9f2985159536a607211dcac730d3f5d55028950
git -C src/uni-agent apply "$LAB_ROOT/patches/uni-agent-docker-stdin.patch"
git -C src/verl-rl apply "$LAB_ROOT/scripts/rl_verl_rocm.patch"
```

### 5. 创建准备容器，重建训练overlay

准备容器与durable训练容器使用不同名字。**不要用`rl_container.sh`预创建同名`ua-lab-rl-durable`**：旧通用容器入口没有checkpoint卷bind，durable入口会拒绝其mount不一致。

```bash
# 宿主，cwd=$LAB_ROOT；创建ua-lab-prepare，容器cwd=/lab。
LAB_ROOT="$LAB_ROOT" RL_CONTAINER_NAME=ua-lab-prepare \
  bash scripts/rl_container.sh

# 容器CPU操作；脚本使用image内uv和系统ROCm torch。
docker exec -e HIP_VISIBLE_DEVICES= ua-lab-prepare \
  bash /lab/scripts/rl_setup_env.sh

docker exec -i -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-prepare /lab/envs/rl/bin/python - <<'PY'
import torch, transformers, ray, pyarrow, tensordict, transfer_queue
import uni_agent, verl
assert torch.version.hip
print('torch', torch.__version__, 'HIP', torch.version.hip, torch.__file__)
print('transformers', transformers.__version__)
print('ray', ray.__version__, 'pyarrow', pyarrow.__version__)
print('tensordict', tensordict.__version__)
print('transfer_queue', getattr(transfer_queue, '__version__', None))
print('uni_agent', uni_agent.__file__)
print('verl', verl.__file__)
PY
```

`rl_setup_env.sh`实际执行`uv venv --system-site-packages --python /usr/bin/python3`，再用`uv pip install --no-deps -r configs/rl_requirements.lock`安装overlay。Torch和vLLM由固定image提供，应用依赖覆盖在`/lab/envs/rl`。不要在这个环境另装PyPI torch或用`pip install -U`替换已验证版本。

训练入口使用`PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl`，而非安装一个未知版本的上游wheel。`verl.__file__`必须落到`/lab/src/verl-rl`。

### 6. 下载并固定训练数据

```bash
# 宿主发起，容器CPU执行；产物在$LAB_ROOT/models与data/hotpotqa。
docker exec -e HIP_VISIBLE_DEVICES= ua-lab-prepare \
  /lab/envs/rl/bin/python /lab/scripts/download_assets.py \
  --models 32b --hotpotqa train

docker exec -e HIP_VISIBLE_DEVICES= ua-lab-prepare \
  /lab/envs/rl/bin/python /lab/scripts/download_assets.py \
  --models 32b --hotpotqa train --verify-only

# kit已提供固定split，直接核对，不能在原目录重新生成。
python3 - <<'PY_SPLIT'
from pathlib import Path
import hashlib
root = Path('data/rl_memagent_512_64_64')
expected = {
 'manifest.json': '7c9025d39cb39c5555066fdf777639bd813d21d59afd506f81526202d4ff6348',
 'train.parquet': 'a342be4ca7ee5e03bfc0c1649b5149fa7a97f0b7d29a4efdccb22d49171727b8',
 'val.parquet': '84cc1d62e3ebc26b33419a71c82f83a0230de0107331db45a032f4ae0e0db748',
 'external.parquet': '439cf32881a714bc29fd1f56800e84eb83885167ddc4dbcefcb65c24d8746bf3',
 'external.json': '5cb13eb0e47a83d72542e87be69655c884731e1f9fc162bdad91b348eb1be8eb',
}
for name, wanted in expected.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest() == wanted, name
print('Frozen 512/64/64 split matches')
PY_SPLIT
```

产物与含义：

- `models/Qwen3-32B/`：固定HF revision的模型、tokenizer与template。
- `data/hotpotqa/hotpotqa_train_32k.parquet`、`hotpotqa_dev.parquet`：固定dataset revision的原始文件。
- `data/rl_memagent_512_64_64/train.parquet`：原训练文件前512行；不是按模型得分筛题。
- `val.parquet`：dev行0–63，trainer monitor使用；`external.parquet`与`external.json`：dev行64–127，独立评估使用。
- `manifest.json`：每源行index/SHA256、输出parquet/hash、context未截断标记，以及固定tokenizer的5000-token chunk数量。
- `results/assets-manifest.json`：下载来源、revision与文件存在性记录。

`download_assets.py --verify-only`检查所需文件及可用的config revision元数据，不是完整61GiB payload的逐字节hash；它不下载，但仍更新`results/assets-manifest.json`。离线转移模型/数据时另保留源侧校验清单，复制完整模型目录，包括tokenizer、vocab/merges、template和generation config。`rl_prepare_mem_data.py`会写指定输出目录，所以只在独立新LAB的准备阶段执行；不要用它改正在训练的固定数据目录。

#### 需要从原始parquet再生数据时

这块是数据审计的可选分支，kit主路线跳过。始终写到新的`rl_memagent_rebuilt`目录，随后核对上面的内容/文件hash；不要替换正在使用的固定split。不同PyArrow序列化若导致文件hash不同，应核对逐行内容并注册新输入协议，不能绕过训练guard。

```bash
test ! -e data/rl_memagent_rebuilt
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-prepare /lab/envs/rl/bin/python /lab/scripts/rl_prepare_mem_data.py \
  --train-rows 512 --val-rows 64 --external-rows 64 --external-offset 64 \
  --out-dir /lab/data/rl_memagent_rebuilt \
  --tokenizer /lab/models/Qwen3-32B
```

### 7. 新机先验证GPU和32B单步pilot

确保待用GPU没有其他未协调任务，再在准备容器做小量真实计算。此命令枚举全部8张物理HIP GPU，临时清除冲突的可见设备变量。

```bash
# 宿主；容器内/opt/rocm/bin工具，无需假定宿主安装rocm-smi。
docker exec ua-lab-prepare /opt/rocm/bin/rocm-smi --showuse --showmemuse

docker exec -i ua-lab-prepare env -u ROCR_VISIBLE_DEVICES -u CUDA_VISIBLE_DEVICES \
  HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 /lab/envs/rl/bin/python - <<'PY'
import torch
assert torch.version.hip and torch.cuda.device_count() == 8
for i in range(8):
    p = torch.cuda.get_device_properties(i)
    x = torch.ones((16,16), device=f'cuda:{i}', dtype=torch.bfloat16)
    value = (x @ x).float().mean().item()
    assert value == 16.0
    print(i, p.name, p.gcnArchName, p.total_memory / 2**30, value)
PY

# 宿主cwd=$LAB_ROOT；pilot独立run和独立容器，仍使用大模型和6卡。
# 直接调用已固定的launcher；不走会重写512/64/64数据的旧reproduce wrapper。
export UA_PILOT_NAME=memagent_32b_newnode_pilot
test ! -e "runs/rl/${UA_PILOT_NAME}"
LAB_ROOT="$LAB_ROOT" RL_CONTAINER_NAME=ua-lab-rl-pilot bash scripts/rl_container.sh

docker exec -i -e HIP_VISIBLE_DEVICES= ua-lab-rl-pilot \
  /lab/envs/rl/bin/python - <<'PY_PILOT_DATA'
from pathlib import Path
import pyarrow.parquet as pq
p = Path('/lab/data/rl_memagent_32b_pilot')
p.mkdir(exist_ok=True)
target = p/'val.parquet'
assert not target.exists(), 'Keep an existing pilot input; do not overwrite it'
pq.write_table(pq.read_table('/lab/data/rl_memagent_512_64_64/val.parquet').slice(0,4), target)
print('Prepared a separate four-question pilot validation input')
PY_PILOT_DATA

set -o pipefail
flock -n "$LAB_ROOT/runs/rl/reproduce.lock" \
  docker exec -e RL_GPU_IDS=2,3,4,5,6,7 ua-lab-rl-pilot \
  bash /lab/scripts/rl_run.sh --config /lab/configs/rl_memagent_32b_pilot_restart.yaml \
  --run-dir "/lab/runs/rl/${UA_PILOT_NAME}" \
  2>&1 | tee "$LAB_ROOT/runs/rl/${UA_PILOT_NAME}.log"

docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-rl-pilot /lab/envs/rl/bin/python /lab/scripts/rl_analyze.py \
  "/lab/runs/rl/${UA_PILOT_NAME}" --base /lab/models/Qwen3-32B \
  --checkpoint "/lab/runs/rl/${UA_PILOT_NAME}/checkpoints/global_step_1/actor/huggingface"

docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-rl-pilot /lab/envs/rl/bin/python /lab/scripts/rl_analyze_memagent.py \
  "/lab/runs/rl/${UA_PILOT_NAME}"
```

此处用相同已验证pilot配置直接调用launcher，只新建四题pilot val，不改固定512/64/64输入。旧`rl_reproduce.sh`的32B分支会无条件重跑数据preparer，因此新机主路线绕过该wrapper。

这个真实pilot验证32B forward/backward、一个训练批次、4题post-validation、多桶single-sender权重同步和KV wake。其配置是1 globalstep、无warmup、rollout utilization0.4；它保存HF+extra，通常生成约122GiB FP32 HF，**没有Adam moments**，不能拿来作为正式optimizer resume起点。它没有before-validation，所以单次post分数不是学习增量。正式durable run会重新从base开始，使用0.3 utilization与4步warmup。

pilot产物在`runs/rl/memagent_32b_newnode_pilot/`及同名`.log`。必须等入口及后处理结束、查看`run-outcome.json`和实际step1、非零任务信号/权重差异，再释放这个专用容器的GPU。pilot若刚好奖励全均匀，流程成功不等于有效学习；随后8步真实resume验收仍不可省略。

```bash
# 仅在上述专用pilot完整结束之后执行。
docker stop ua-lab-rl-pilot
```

### 8. 正式YAML的重要字段

文件为`configs/rl_memagent_32b_128_durable.yaml`；任务本体配置为`configs/rl_memagent_long_task.yaml`。launcher会覆盖模板里`/lab/runs/rl/smoke/...`这些输出占位路径，不要绕过launcher直接照搬占位路径。

| 部分 | 字段/值 | 实际作用 |
| --- | --- | --- |
| 模式 | `trainer.use_v1=true`，`separate_async` | 原生V1、训练/rollout分离并预取 |
| 同步 | `num_warmup_batches=1`，`parameter_sync_step=1` | 一个prompt batch预取，每globalstep更新rollout权重 |
| 布局 | trainer4 GPU，rollout2 GPU，TP2；Ray6 GPU/32 CPU | 4个FSDP2 rank与1个standalone TP2副本 |
| 总量 | `total_epochs=1`、`total_training_steps=128` | 4题/globalstep，目标512个训练prompt组 |
| 初始/周期评估 | `val_before_train=true`、`test_freq=32` | step0/32/64/96/128的固定monitor64 |
| 数据 | batch4、seed42、worker0、raw chat | 固定数据游标与可恢复StatefulDataLoader |
| shuffle | data.shuffle=True，validation_shuffle=False；actor.shuffle=False | 固定选前512行后按seed洗牌；内部PPO batch不额外shuffle |
| 截断 | prompt7168、response1024、`truncation=error`、不过滤长prompt | 超预算时报错；不默默挑掉题目 |
| memory任务 | chunk5000、memory1024、final1024、max_chunks16/max_steps17 | 保留原context，逐块压缩后回答；本数据通常5–6个memory块 |
| 模型 | `Qwen3-32B`、no-thinking | 同一个可训练模型完成memory与最终回答 |
| 精度 | FP32 master、BF16 forward、FSDP2 | 小LR积累；4-rank分片承担参数/grad/Adam |
| backend | SDPA、`use_remove_padding=false`、`use_fused_kernels=false`、compile=false | 已在该ROCm image验证的计算路径 |
| 激活 | gradient checkpointing=true | 用重算降低activation峰值 |
| PPO批次 | mini_batch4、micro_batch_per_gpu1 | logical batch经n4与多context展开，可能形成多个Adam minibatch |
| PPO默认项 | ppo_epochs1，clip_ratio/low/high0.2，grad_clip1.0 | 单个展开batch一个PPO epoch，裁剪范围来自resolved config |
| GRPO | `adv_estimator=grpo`、`norm_adv_by_std_in_grpo=false` | 组内居中，不除以reward标准差 |
| loss聚合 | `loss_agg_mode=token-mean` | 会按有效生成token量影响各session的实际权重 |
| 奖励 | 官方boxed-answer token-LCS | TaskResult中得分，广播到session各context |
| KL/entropy | actor loss内low_var_kl0.01，reward KL=False，entropy_coeff0但calculate_entropy=True | reference仍是原始base；entropy保留为诊断 |
| 优化器 | LR1e-6、weight_decay0、constant scheduler、warmup4 | warmup单位为trainer globalstep；首步LR为0 |
| rollout | n4、temperature1、top_p0.7 | 每题4条独立agent session |
| validation | n1、temperature0、do_sample=false | 固定monitor协议；不是训练采样参数 |
| vLLM预算 | max_model_len8192、max_batched_tokens16384、max_seqs16、utilization0.3 | memory+chunk窗口与KV预留 |
| gateway/task | gateway1、agent worker1、max_concurrent_sessions16、ray_task | 原生Task→Gateway→LLM→TQ路径 |
| 轨迹 | `trajectory_selection=all`、`mask_unfinished_episode=false` | 收集多context；该布尔值不是框架通用“必为True”的默认 |
| 权重同步 | backend nccl、8192MiB bucket、`multi_sender=false` | 已验证的多桶单发送端路线 |
| TQ | SimpleStorage2 units、total_storage_size4096 | async prefetch、完整checkpoint/恢复 |
| 保存 | 每8步`model/optimizer/extra`；load同三项 | V1另外保存data和TQ；每次不重复导出HF |
| 轮换 | worker max_keep=null；callback keep1 | 全run commit之后才删旧断点，读锁保护导出 |

GRPO不需要单独value model；日志中的`critic/rewards`等通用字段不代表本配方训练了critic。`calculate_log_probs=true`会重新计算old log-probabilities；当前resolved config的额外rollout IS/rejection设置为空，日志中`rollout_corr/*`是诊断，不能据此声称开启了完整off-policy correction。

计数要分层：4问题×n4=16sessions/globalstep，每个session又有多次memory/answer调用。当前实测32 globalsteps消耗128题、512sessions、3444真实context及140padding，各rank Adam224；四rank的224属于同一个分布式优化器进度，不能相加成896。context数量会影响内部minibatch数，最终Adam计数以实际state为准，不预先承诺128一定等于固定倍数。

### 9. 静态预检，然后用独立session启动fresh

先用准备容器检查配置与精确source guard。两者都是CPU操作，不会证明GPU训练已成功。

```bash
# 宿主cwd=$LAB_ROOT；容器身份ua-lab-prepare。
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-prepare /lab/envs/rl/bin/python /lab/scripts/rl_durable_launch.py \
  --run-dir /lab/runs/rl/${RL_RUN_NAME} \
  --checkpoint-dir /path/to/uni-agent-checkpoints/${RL_RUN_NAME} \
  --check-config

docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-prepare /lab/envs/rl/bin/python -c \
  'from pathlib import Path; from rl_durable_launch import verify_sources; x=verify_sources(Path("/lab")); print(x["status"], len(x["observed_source_sha256"]))'
```

正式入口是`bash scripts/rl_durable_reproduce.sh fresh`。它创建带checkpoint bind的专用容器、检查Git pin/补丁、取得`reproduce.lock`、检查没有并发launcher、安装锁定overlay，然后运行128步配方，但传入`--pause-after-step 8`做实际resume验收。

长跑用以下**宿主Python独立session**启动方式。将`UA_RL_ACTION`设为`fresh`或`resume`复用同一段代码；不要让长跑依赖临时交互工具会话。该方式已用于本机第三次中断后的attempt003。

```bash
# 宿主cwd=$LAB_ROOT；前面四个export变量仍有效。
export UA_RL_ACTION=fresh
python3 - <<'PY'
import os, json, socket, subprocess
from pathlib import Path
from datetime import datetime, timezone
lab = Path(os.environ['LAB_ROOT']).resolve()
run = os.environ['RL_RUN_NAME']
action = os.environ['UA_RL_ACTION']
assert action in ('fresh', 'resume')
directory = lab / 'controllers'
directory.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
prefix = directory / f'{run}-{action}-{stamp}'
with prefix.with_suffix('.log').open('xb') as output:
    p = subprocess.Popen(
        ['bash', str(lab/'scripts/rl_durable_reproduce.sh'), action],
        cwd=lab, env=dict(os.environ), stdin=subprocess.DEVNULL,
        stdout=output, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
record = dict(started_utc=datetime.now(timezone.utc).isoformat(),
              hostname=socket.gethostname(), pid=p.pid, pgid=os.getpgid(p.pid),
              sid=os.getsid(p.pid), run=run, action=action,
              log=str(prefix.with_suffix('.log')))
prefix.with_suffix('.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record, indent=2))
PY
```

独立session降低控制终端/工具会话退出的影响，不防宿主宕机或外部SIGTERM；恢复依靠完整checkpoint。查看这次新PID的`ps -p PID -o pid,ppid,pgid,sid,etime,args`，父Python退出后应见控制进程PPID1及独立session。静态PID记录本身不是“仍在运行”的证据，也不能拿从另一台机器拷来的旧PID去操作本机进程。

内部标准日志仍由入口tee写到`runs/rl/<run>-fresh-<UTC>.log`或`...-resume-<UTC>.log`，供多attempt reader识别。外层`controllers/*.log`是独立控制台副本。

### 10. 第8步暂停的实际验收和resume

预期状态为`paused_for_resume_proof`、退出75。这是主动验收停点，128步目标仍未完成。先确认`rollout/1.jsonl`至`8.jsonl`齐全、`planned-pause.json`存在、最新独立commit为8，再运行完整检查。

```bash
# 宿主发起，专用训练容器内CPU检查；不能换成没有checkpoint bind的普通CPU容器。
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python -c \
  'import sys; from rl_durable_checkpoint import validate_committed_checkpoint; m=validate_committed_checkpoint(sys.argv[1]); print({k:m[k] for k in ("global_step","world_size","total_bytes","optimizer_steps_by_rank","scheduler_epochs_by_rank")})' \
  "/lab/runs/rl/${RL_RUN_NAME}/checkpoints/global_step_8"

docker exec "$RL_CONTAINER_NAME" cat /lab/runs/rl/${RL_RUN_NAME}/run-outcome.json
docker exec "$RL_CONTAINER_NAME" pgrep -af '[r]l_durable_launch.py'
```

最后一条在launcher退出后应没有匹配，返回1是pgrep的“未找到”。本机step8四rank各707 states，Adam56/scheduler8；检查文件存在和探针只是前半步，随后要真实load。

在此停点且没有CPU导出等本容器其他工作时，重启**这个专用容器**清除残留Ray/vLLM进程。然后把上一节的`UA_RL_ACTION`改为`resume`，再次执行同一段独立session启动代码。

```bash
# 宿主；仅在已确认planned pause/失败停点执行。
docker restart "$RL_CONTAINER_NAME"
export UA_RL_ACTION=resume
# 再执行第9节的Python独立session启动代码。
```

resume从独立`committed_checkpoint.json`选择完整断点，创建新的attempt配置/状态文件；`val_before_train`设为False，避免重复、覆盖已完成monitor。它保持原目标128、LR/scheduler/算法/数据配置不变。若有上次已执行但未提交的后缀，入口将大于commit step的rollout/validation/agent日志带SHA归档，并隔离未提交checkpoint目录；保留原始stdout和TensorBoard，按`retained_global_step_max`排除被回滚部分。

实际验收至少包括：

1. `resume_audits/attempt-002/rank-0.json`至rank3均`matches_saved_state=true`，counter/scheduler以及固定模型/Adam probes匹配。
2. 日志有原生model、optimizer、rng、scheduler load，以及`Restored 4 ... prompt groups`。
3. 有新的canonical`rollout/9.jsonl`和native step9指标，LR未重新warmup；下一次保存的Adam计数继续增长。
4. 第9步消费的UID/问题恰对应checkpoint8缓存的4组，未重新消费之前的prompt。

本机已有独立17项step8→9审核和16项step32→33审核。native恢复会保留finished轨迹、重发pending/running prompt；vLLM未来随机生成状态与异步调度不是逐bit回放，因此这里保证的范围是原生训练状态/数据恢复，不承诺迁机后未来输出或最终分数逐字相同。

### 11. 监控、断点内容与恢复常见误读

```text
runs/rl/<run>/
  resolved_config.yaml              首次配置
  attempts/attempt-NNN.{yaml,json} 每次进程配置/结果；旧失败保留
  source_audits/                    source guard结果
  rollout/N.jsonl                 已消费的第N步数据
  validation/N.jsonl              monitor，多context行需按episode归并
  agent_logs/step_N/               生成时刻标签，可能含未消费预取
  tensorboard/attempt-NNN/         每attempt独立events
  resume_boundaries/               回滚、日志缺口、归档清单
  rolled_back_attempts/            被回滚的原始文件
  checkpoint_commits/              小型commit记录，轮换后仍保留
  optimizer_step_audits/           小型counter记录，不含Adam moments
  state_fingerprints/              保存时固定probes
  resume_audits/                   实际load后的对照
  checkpoints -> /path/to/uni-agent-checkpoints/<run>
  exports/global_step_N/huggingface BF16推理导出
```

native checkpoint卷中的每个`global_step_N`有4份`actor/model_world_size_4_rank_R.pt`、4份`optim_...pt`、4份`extra_state_...pt`，以及`fsdp_config.json`、`data.pt`、完整`transfer_queue/`、各rank audit、`checkpoint-commit.json`。`actor/huggingface/`此时主要是配置和tokenizer；目录存在不代表已经包含可推理的HF权重。

原生`latest_checkpointed_iteration.txt`不是本方案的最终提交依据。callback检查model/optimizer/extra/data/TQ完整性，fsync全部文件/目录，发布独立原子commit pointer后才轮换旧断点。大文件用精确长度与固定head/middle/tail探针，不宣称是完整payload hash。导出拿共享read lease，删除旧源前拿独占lease，防止边读边删。

启动监控reader时也采用独立session，或在可靠的长期终端执行以下命令；同输出只允许一个writer：

```bash
# 另一个宿主终端/独立session，cwd=$LAB_ROOT；不联系GPU/Ray。
# 原固定名自动controller硬读large-durable-live-status.json，其他run用独立文件。
if [[ "$RL_RUN_NAME" == memagent_32b_128_durable ]]; then
  UA_LIVE_JSON="$LAB_ROOT/results/large-durable-live-status.json"
  UA_LIVE_CSV="$LAB_ROOT/results/large-durable-live-steps.csv"
else
  UA_LIVE_JSON="$LAB_ROOT/results/${RL_RUN_NAME}-live-status.json"
  UA_LIVE_CSV="$LAB_ROOT/results/${RL_RUN_NAME}-live-steps.csv"
fi
python3 scripts/live_durable_status.py \
  --run "$RL_RUN_NAME" --output "$UA_LIVE_JSON" --steps-csv "$UA_LIVE_CSV" \
  --watch --interval 60
```

`logged step`、`committed step`和`native exit`是三件事。保存发生在当步正常metrics日志之前，可能短暂出现committed32/logged31；大文件fsync阶段长时间没有新日志也不自动代表失败。反过来，SIGKILL/宿主故障可能来不及写outcome，旧`status=running`也不证明进程仍活着。

当前V1在save之后才清理已消费TQ轨迹。resume后的TQ可保留历史无prompt的轨迹，它们不会被按finished prompt采样；不要将“所有无prompt轨迹都必须属于最新step”作为通用判据。最终128步还可能预取下一epoch的4题，data游标出现epoch wrap。训练消费目标仍是512组/2048sessions，额外预取不算已训练。

### 12. 每阶段和最终验收

中途分析写独立snapshot，不能提前写最终`analysis.json`。下面整块是**可选中途观测**，仅在step32对应完整checkpoint仍被保留时执行；若已经跑完128，直接跳到下一章通用收尾，不再执行32导出。若controller已负责同一产物，只读其结果，避免重复producer。

```bash
# 宿主发起；专用容器CPU分析。示例N=32，其他阶段改成64/96/128。
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python /lab/scripts/rl_analyze.py \
  /lab/runs/rl/${RL_RUN_NAME} --base /lab/models/Qwen3-32B \
  --max-step 32 --output /lab/runs/rl/${RL_RUN_NAME}/analysis_snapshots/step_32.json

docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python /lab/scripts/rl_analyze_memagent.py \
  /lab/runs/rl/${RL_RUN_NAME}

# 完整committed源存在时CPU导出；helper拒绝覆盖既有导出。
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python /lab/scripts/rl_export_committed.py \
  --run-dir /lab/runs/rl/${RL_RUN_NAME} --step 32
```

`rl_analyze_memagent.py`按episode取最后一个context、逐题重算官方reward并核对广播一致，输出`memagent_validation.json`和`validation_curve.csv`。前者会随新validation更新；阶段性副本另存`analysis_snapshots/step_N_validation.json`。原始指标是token-LCS，`LCS=1`不自动等于标准HotpotQA EM。全局梯度非零可能只有KL项；还要看组内reward差异、adv/PG、FP32及统一BF16参数变化和最终独立评估。

官方FSDP merger显式将FP32 shards cast成BF16，因此本工具导出约61GiB；native FP32/Adam仍保留在checkpoint卷。HF导出应有707 tensors、32,762,123,264参数、完整index/shards及对应tokenizer。它可以跨服务加载，但不是optimizer checkpoint。

最终必须等`run-outcome.json`为completed/exit0、canonical1..128 rollout和scalar齐全、完整global128 commit和四rank scheduler128一致，再生成最终分析。本机专用controller将分析写到独立临时文件、检查128 steps/2048sessions/512groups后原子安装为`analysis.json`；新机通用步骤采用同一发布规则，不要让另一个脚本同时写这个文件。新机通用路线按下一章完整命令做HF语义检查、最终服务回载及base/final配对；它不读取原机器的stable协议。只有选择固定run名且完成新机重新登记的可选路线时，才启动专用controller。

换机器应重新测该机器的base/final同协议结果。此lab同一host、同权重、temperature0的64题重复已经出现约0.0384 raw-LCS波动；训练温度1和异步调度更不应期待逐字复现。保留primary与预登记repeat，不能选择较高的一次替换baseline或挑最好checkpoint汇报。

### 13. 已有完整断点迁机续跑

迁移时带走固定代码/协议、原始base模型、原始和固定split数据，以及run历史和完整已提交checkpoint。只带HF导出不够；reference KL及engine初始化仍需要原始`models/Qwen3-32B`，不能把model.path改成已训练HF来冒充resume。

建议在明确paused/completed状态复制，保留symlink本身，单独复制checkpoint卷的完整目录，避免`-L`跟随链接意外复制数百GiB多份。需要迁移的记录包括attempt YAML/JSON、rollback边界、canonical rollout/validation、TensorBoard、commit/audit、maintenance ledger。新机内部仍使用`/lab`和固定checkpoint绝对路径；先校验版本、manifest、文件长度/探针、数据hash，再执行相同`resume`入口。

同一trainer world size和checkpoint格式是当前native loader的前提。若改变卡数、FSDP策略、模型结构、tokenizer、LR或数据，需要新实验/专门的迁移验证，不绕过source/semantic guard。跨机接续已有训练时，旧机器的评估结果保留为历史，在新机器重新建立可比的base/final评估记录，并按主复现文档另行冻结评估路径。

### 14. 实际遇到的问题

| 现象 | 本次处理/判断 |
| --- | --- |
| `--check-config`通过，但GPU尚未训练 | 它只验证配置；后面仍需要真实32B pilot及8步resume |
| 初始多桶权重同步停滞 | 保存栈；使用已验证single-sender配置；宿主状态也变化过，不把一个flag当唯一已证明根因 |
| HF-only保存后宿主中断 | 无Adam moments，旧46步只能保留为中断试验；新的durable从base重新128 |
| `torch.save`很快、之后长时间等待 | 实测63秒写缓存，完整保存约286秒；fsync必须算入保存成本 |
| source guard拒绝启动 | 检查完整代码包、commit、补丁、冻结协议及ledger；不删除guard |
| 普通CPU容器找不到native checkpoint | 绝对symlink指向未bind的checkpoint卷；改用训练容器CPU模式或正确挂载 |
| 恢复后看到多份旧step日志 | 旧后缀归档/旧TB保留；按attempt retained boundary读canonical数据 |
| 相同model重复分数波动 | 保留repeat诊断，不能把单次小增量全归因RL |
| `perf/mfu/actor=0` | 当前设备理论FLOPs识别缺失的诊断值，不代表没有GPU计算 |
| oldLP间歇慢、actor/ref正常 | 只读定位到GPU等待/不同活动图样；没有已证实根因，未改数学/power设置 |
| rocprof动态attach立即失败 | 目标缺启动期`ROCP_TOOL_ATTACH=1`背景线程；没有kernel trace，不为profile重启正式训练 |
| 非reboot的SIGTERM令长跑终止 | 保存SystemExit/SIGTERM证据，从完整32恢复；控制进程改为独立session、DEVNULL输入、文件stdout |

本次没有把失败trial删掉、把中间32替代最终128、把warm start叫optimizer resume，或把format变化全部解释成推理能力提升。跨机复现也采用同样的记录标准。

## 新机器通用收尾：128步检查、HF导出、final与repeat

这一章是新机主路线的完整收尾，沿用唯一的`LAB_ROOT`、`RL_RUN_NAME=repro-memagent-32b-128`与`RL_CONTAINER_NAME=ua-lab-rl-durable`。环境章节已经在新机器生成`base-durable`和`base-durable-repeat`，现在用预定的最终step128做独立对照。这里不调用本机固定名字的final controller，也不读取旧stable辅助协议中的历史baseline hash。**若选择了前面的固定原名stable自动化替代路线，本章只作参考，不再运行这些手动producer；由已登记的新机controller统一收尾。**

如果前面采用了其他baseline名字，只改下面的`UA_BASE_NAME`；不能根据哪次分数更高决定选择哪一个。第一遍与repeat各自保留，配对关系在运行前确定。

### 1. 等真正完成128步，再验收native状态

训练进程已经结束、没有失败outcome，才执行这一节。普通CPU容器没有native checkpoint卷的bind，因此以下检查用专用训练容器。

```bash
: "${LAB_ROOT:?Use the LAB_ROOT from the opening section}"
: "${RL_RUN_NAME:?Use the run name from the training section}"
: "${RL_CONTAINER_NAME:?Use the dedicated training container name}"
cd "$LAB_ROOT"
export UA_BASE_NAME=base-durable
export UA_FINAL_NAME=repro-final32b-step128
export UA_EXPORT_CONTAINER="/lab/runs/rl/${RL_RUN_NAME}/exports/global_step_128/huggingface"

docker exec -i -e HIP_VISIBLE_DEVICES= -e UA_REPRO_RUN="$RL_RUN_NAME" \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python - <<'PY_FINAL_NATIVE'
import hashlib, json, os
from pathlib import Path
from live_durable_status import AttemptReader
from rl_durable_checkpoint import validate_committed_checkpoint
root = Path('/lab/runs/rl') / os.environ['UA_REPRO_RUN']
outcome = json.loads((root/'run-outcome.json').read_text())
assert outcome['status'] == 'completed' and outcome['exit_code'] == 0
assert outcome['requested_global_steps'] == 128
pointer = json.loads((root/'checkpoints/committed_checkpoint.json').read_text())
assert pointer['global_step'] == 128
expected_checkpoint = root/'checkpoints/global_step_128'
assert Path(pointer['checkpoint_dir']).resolve() == expected_checkpoint.resolve()
commit_path = expected_checkpoint/'checkpoint-commit.json'
assert hashlib.sha256(commit_path.read_bytes()).hexdigest() == pointer['manifest_sha256']
manifest = validate_committed_checkpoint(pointer['checkpoint_dir'])
assert json.loads((root/'checkpoint_commits/global_step_128.json').read_text()) == manifest
assert manifest['global_step'] == 128 and manifest['world_size'] == 4
assert manifest['scheduler_epochs_by_rank'] == [128] * 4
reader = AttemptReader(root)
assert set(reader.steps) == set(range(1,129))
assert {int(p.stem) for p in (root/'rollout').glob('*.jsonl')} == set(range(1,129))
print(json.dumps({'status':'native128_verified', 'attempt':outcome['attempt_id'],
                  'native_bytes':manifest['total_bytes'],
                  'adam_steps_by_rank':manifest['optimizer_steps_by_rank']}, indent=2))
PY_FINAL_NATIVE
```

确认最后monitor、native日志和进程退出后，生成最终训练分析。它会按attempt的retained边界排除回滚后缀。分析与导出是CPU工作，不要提前停止这个容器。

```bash
test ! -e "runs/rl/${RL_RUN_NAME}/analysis.json"
test ! -e "runs/rl/${RL_RUN_NAME}/analysis-newnode.tmp.json"
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python /lab/scripts/rl_analyze.py \
  "/lab/runs/rl/${RL_RUN_NAME}" --max-step 128 \
  --output "/lab/runs/rl/${RL_RUN_NAME}/analysis-newnode.tmp.json"

docker exec -i -e UA_REPRO_RUN="$RL_RUN_NAME" \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python - <<'PY_FINAL_ANALYSIS'
import json, os
from pathlib import Path
root = Path('/lab/runs/rl') / os.environ['UA_REPRO_RUN']
tmp, final = root/'analysis-newnode.tmp.json', root/'analysis.json'
a = json.loads(tmp.read_text())
c = a['consumed_training_rollouts']
assert set(c['by_optimizer_global_step']) == {str(i) for i in range(1,129)}
assert c['episodes'] == 2048 and c['prompt_groups'] == 512
assert a['optimization_evidence']['logged_global_steps'] == 128
assert a['trajectories']['errors'] == []
os.link(tmp, final)  # 原子发布；final已存在时拒绝覆盖。
tmp.unlink()
print('Final analysis committed:', final)
PY_FINAL_ANALYSIS

docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python /lab/scripts/rl_analyze_memagent.py \
  "/lab/runs/rl/${RL_RUN_NAME}"
```

如果已经有经过验收的完整`analysis.json`，只检查它并跳过生产步骤；不要启动第二个writer。这里没有要求最终分数上涨才能继续，失败、零收益和回退也应完整保留。

### 2. 从完整native128导出BF16 HF

至少给lab数据盘留80GiB空闲。只导出预定的最终128，不依据中间monitor挑一个高分checkpoint。

```bash
docker exec -e HIP_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl \
  "$RL_CONTAINER_NAME" /lab/envs/rl/bin/python /lab/scripts/rl_export_committed.py \
  --run-dir "/lab/runs/rl/${RL_RUN_NAME}" --step 128

cat "runs/rl/${RL_RUN_NAME}/exports/global_step_128/export-outcome.json"
```

成功结果应为`completed/exit_code=0`、707个BF16 tensors、32,762,123,264参数、约65.52GB十进制的safetensors。`source_commit_sha256`绑定原生commit，完整Adam仍在native卷中。导出器已采用共享reader lease，配合keep1的删除锁保护源文件。

Docker创建的shards可能为root mode600。需要宿主用户读取、迁移导出时，仅调整此新run生成shards的归属，保留payload、permission bits、mtime及旧模型：

```bash
docker exec -i -e UA_EXPORT_CONTAINER="$UA_EXPORT_CONTAINER" \
  -e UA_HOST_UID="$(id -u)" -e UA_HOST_GID="$(id -g)" \
  ua-lab-cpu /lab/envs/cpu/bin/python - <<'PY_EXPORT_OWNER'
import json, os
from pathlib import Path
p = Path(os.environ['UA_EXPORT_CONTAINER'])
assert p.resolve() == p and p.is_relative_to('/lab/runs/rl')
assert p.name == 'huggingface' and p.parent.name == 'global_step_128'
assert p.parent.parent.name == 'exports'
e = json.loads((p.parent/'export-outcome.json').read_text())
assert e['status'] == 'completed' and e['global_step'] == 128 and e['target_dir'] == str(p)
for f in p.glob('*.safetensors'):
    assert f.is_file() and not f.is_symlink()
    before = f.stat()
    os.chown(f, int(os.environ['UA_HOST_UID']), int(os.environ['UA_HOST_GID']))
    after = f.stat()
    assert (before.st_size,before.st_mtime_ns,before.st_mode) == (after.st_size,after.st_mtime_ns,after.st_mode)
print('Generated export shards belong to the lab user')
PY_EXPORT_OWNER
```

### 3. 导出精度、配置与tokenizer检查

以下检查用不映射GPU的CPU容器。它们分别检查全部header/index/shape及固定9个tensor的数值变化，以及配置/tokenizer/chat输入等价；不把这两类检查混成模型能力结论。

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/cpu-large-effective-weight-deltas.py \
  --checkpoint "$UA_EXPORT_CONTAINER" \
  --output "/lab/results/large-memagent/${RL_RUN_NAME}-step128-weight-deltas.json"

docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/audit_hf_export_semantics.py \
  --checkpoint "$UA_EXPORT_CONTAINER" \
  --output "/lab/results/large-memagent/${RL_RUN_NAME}-step128-hf-semantics.json" \
  --runtime-label new-node-cpu-tf5.16.1-native128 \
  --checkpoint-hf-attention-selector none
```

数值审计必须有限且文件覆盖完整，HF语义审计必须`required_checks_passed=true`。显式`none`对应native merger路径，不能沿用旧HF-only导出的`sdpa`预期。固定9tensor的变化率不是全部32B参数的变化率，也不是GPU上实际载入权重的完整逐字节对比。

### 4. 回载final128并完成两次external64

HIP0用于这组评测。回载入口验证目录处于本lab内、config和shards存在，复用base的相同服务脚本。两遍final之间保持同一个服务进程，不重启、换参数或改batch。

```bash
python3 scripts/start_32b_checkpoint.py --checkpoint "$UA_EXPORT_CONTAINER"

python3 scripts/run_large_memagent.py --name "$UA_FINAL_NAME" \
  --expected-model-path "$UA_EXPORT_CONTAINER" --concurrency 8

python3 scripts/run_large_memagent.py --name "${UA_FINAL_NAME}-repeat" \
  --expected-model-path "$UA_EXPORT_CONTAINER" --concurrency 8
```

每次应产生`results/large-memagent/<name>/`，含`results.jsonl`、`summary.json`、`provenance.json`和`infer.log`。检查64/64完成、errors=0、实际root指向本run step128，runtime、任务文件和采样协议与本机base相同。若发生错误，保留失败目录，不以覆盖或挑选成功题代替完整评测。

### 5. 配对、重复性和格式诊断

第一遍base与第一遍final为主比较；repeat与repeat作预定重复对照。同模型的两遍final只作重复性诊断，不计算“学习收益”。脚本会复算逐题原生reward并验证输入/运行时；缺题或协议不同会失败。

```bash
python3 scripts/analyze_large_memagent.py \
  --before "results/large-memagent/${UA_BASE_NAME}/results.jsonl" \
  --after "results/large-memagent/${UA_FINAL_NAME}/results.jsonl" \
  --output-dir "results/large-memagent/${RL_RUN_NAME}-primary-comparison"

python3 scripts/analyze_large_memagent.py \
  --before "results/large-memagent/${UA_BASE_NAME}-repeat/results.jsonl" \
  --after "results/large-memagent/${UA_FINAL_NAME}-repeat/results.jsonl" \
  --output-dir "results/large-memagent/${RL_RUN_NAME}-repeat-comparison"

python3 scripts/audit_same_model_repeat.py \
  --first "results/large-memagent/${UA_FINAL_NAME}/results.jsonl" \
  --repeat "results/large-memagent/${UA_FINAL_NAME}-repeat/results.jsonl" \
  --expected-model-path "$UA_EXPORT_CONTAINER" \
  --output-dir "results/large-memagent/${RL_RUN_NAME}-final-repeatability"

python3 scripts/audit_tex_space_only.py --input \
  "results/large-memagent/${UA_BASE_NAME}/results.jsonl" \
  "results/large-memagent/${UA_BASE_NAME}-repeat/results.jsonl" \
  "results/large-memagent/${UA_FINAL_NAME}/results.jsonl" \
  "results/large-memagent/${UA_FINAL_NAME}-repeat/results.jsonl" \
  --output "results/large-memagent/${RL_RUN_NAME}-tex-space-only.json"
```

`comparison.json`和`paired.csv`给出64题配对、均值变化、LCS=1转移和20,000次question-level paired bootstrap。它衡量这组固定题与运行结果的差异，不估计跨种子训练方差，也不覆盖所有默认greedy数值波动。TeX空格诊断只处理预先固定的窄格式规则，不替换原生主指标；其他标点、别名或标签范围差异仍要结合原始答案审阅。

`summarize_lab.py`是整套历史lab的可选汇总器，轻量新节点没有本机小模型/Miles/旧case结果时会出现pending；这不应作为本章32B训练是否完成的判断。新机以本章native检查、两份完整paired comparison、重复性与CPU审计为验收。

最后只关闭自己的空闲实验服务；保留最终native128和HF128、全部原始日志、基线、repeat以及失败记录。若还计划继续训练，先决定是否需要完整Adam/data状态，再安排磁盘，不要仅因为有HF导出就丢掉native卷。


### 6. 可选：原始32B的长文分块记忆案例

这组属于训练前模型的行为诊断，使用50/200/800/3200/6400篇文档的固定文件，各取8/8/4/2/1个样本，共23个“问题×长度”case，底层只有8个问题重复出现在不同长度。采样为temperature1、top_p0.7，memory/final各1024，chunk5000；不与greedy external64混成一个指标。

应在HIP0空闲、其他评测已经完成时运行。入口核对服务必须载入原始base：

```bash
python3 scripts/start_32b_checkpoint.py --checkpoint models/Qwen3-32B
python3 scripts/run_large_longcontext.py --name replay-32b-longcontext
```

需事先下载`download_assets.py --hotpotqa eval`的五档JSON，见环境章节。最长案例的原始材料接近90万token，由逐块记忆完成，不能解释为模型具有原生百万token注意力窗口。这组输出是单次采样诊断，不自动证明训练收益。
## 可选替代路线：新节点注册stable辅助与固定名称自动收尾

**这是一条在开始训练前选择的替代路线，不是主通用`repro-memagent-32b-128`路线跑完后的追加步骤。** 主路线的手工base/final链不依赖这里的helper或旧stable协议。可选路线在全新kit解包lab内沿用原固定训练名`memagent_32b_128_durable`、容器`ua-lab-rl-durable`与固定评测phase名，以使用已有自动controller。

注册器是新增的register_new_node_stable.py（原始文件：`scripts/register_new_node_stable.py`）。它不修改任何冻结helper，不运行QA评测或训练。`--check-only`只读；`--execute --start-base-service`才允许在条件满足时创建/启动owned HIP1的Qwen3-32B base服务，并登记本节点的runtime和hash。

### 前置条件：必须是当前kit之后的新结果

先完成总手册的kit校验、CPU bootstrap、固定模型/数据准备，沿用同一个`LAB_ROOT`。这时不要启动32B pilot、fresh训练、Miles训练或final controller；注册器拒绝已有训练history、controller记录/进程以及stable/final输出。仅有新的primary baseline和repeat是允许且必须的。

两次primary必须在**该kit的创建时间之后**真实执行并完整结束，系统UTC时间应正确。复制原机器baseline，即使runtime字符串相同，也会因为时间/完整性或来源条件被拒绝。它不按分数高低选择结果。

```bash
: "${LAB_ROOT:?先完成总手册开头的kit解包与LAB_ROOT设置}"
cd "$LAB_ROOT"
# 前一环境章节已经运行这两遍；这里读取验收，不重复生成同名结果。
python3 - <<'PY_PRIMARY_READY'
import json
from pathlib import Path
for name in ('base-durable','base-durable-repeat'):
    path = Path('results/large-memagent') / name
    summary = json.loads((path/'summary.json').read_text())
    provenance = json.loads((path/'provenance.json').read_text())
    assert summary['selected'] == summary['completed'] == 64 and summary['errors'] == 0
    assert provenance['status'] == 'completed'
    assert provenance['expected_model_path'] == '/lab/models/Qwen3-32B'
print('Both new-node primary baselines are complete')
PY_PRIMARY_READY
```

如果尚无这两份新结果，先返回环境章节执行一次baseline与repeat；不要复制历史结果或重复向同名输出运行。以上验收通过后才继续。Primary服务保持在HIP0/18083，实际model root仍须是base；不能先换成checkpoint。Primary进程不得设置`VLLM_BATCH_INVARIANT`，连显式字符串`0`也不接受，以与后续main final guard一致；也不能额外指定attention backend。

HIP1须留给stable。不要让Coder30B、旧9B或其他任务同时占该卡；注册器不会替你终止这些工作。它检查known HIP1容器是否仍在运行，并检查stable容器的固定image、lab mount、`owner=YOUR_LAB_OWNER`、`purpose=uni-agent-greedy-stability`及HIP1环境。这里的owner标签是recipe约定，不代替Linux UID/GID权限。

### 检查并显式注册

```bash
# 这个flag在check-only下只是预告允许后续启动，不会加载模型。
python3 -B scripts/register_new_node_stable.py --check-only --start-base-service

# 仅在上一条检查通过后执行；这一步可能加载HIP1上的61GiB base与KV cache。
python3 -B scripts/register_new_node_stable.py --execute --start-base-service
```

若已经按同一recipe准备好owned stable base服务，可以省略`--start-base-service`。复用现有ready服务也检查ownership，不能把另一lab同名服务当作本次服务；对已有但配置不符的模型进程，helper会拒绝，不会自动杀掉或重启它。

Helper的主要检查/动作顺序：

1. 检查kit marker、kit中原protocol与helper的hash、14项固定评测源码/数据/模型metadata；检查无训练、stable/final、controller history和活动controller。
2. 对新primary64与repeat按固定ID/问题/答案/chunk重算原生LCS，检查phase、完整率、固定全部推理参数、两个独立文件、顺序执行时间。然后核验实际primary进程argv、GPU/profile环境及当前runtime。
3. 需要且显式允许时，在HIP1创建或启动专用stable base服务，用新的独立stdout日志。配置为BF16/TP1/eager/16384、max_num_seqs16、prefix cache开启、`VLLM_BATCH_INVARIANT=1`；从实际进程和日志确认TRITON_ATTN。它不会发送额外模型评测请求。
4. 再次检查fresh状态和primary证据未变化，将kit旧protocol逐字保存到`notes/rl-memagent-32b-stable-auxiliary-protocol.kit-template.json`，fsync后原子替换为新节点注册的同recipe protocol。
5. 保存`reproduction/stable-registration.json`，记录helper/kit/template/protocol hash和实际primary/stable状态。失败attempt和服务日志留在`reproduction/new-node-stable-registration/`，不伪装成已注册。

注册后`notes/rl-memagent-32b-stable-auxiliary-protocol.json`的runtime、primary结果hash与注册信息会按设计变化。Kit应在这之前验证；之后的这项差异由模板备份和registration记录解释，14项固定输入仍须匹配。不要再改protocol的status或hash字段，已有provenance会引用它。

如果发生中途注册失败，先看attempt record、是否已经留下模板备份/registration；helper拒绝隐式覆盖和重复注册。不要删除这些保护文件来冒充首次执行。当前原lab已有历史和活动controller，在任何Docker或注册动作之前就会被拒绝。

### 本节点stable64与repeat

注册完成后按固定顺序执行，两份完整结果都保留：

```bash
python3 scripts/run_stable_memagent.py --name base-durable-stable
python3 scripts/run_stable_memagent.py --name base-durable-stable-repeat
python3 scripts/analyze_stable_memagent.py \
  --before results/large-memagent/base-durable-stable/results.jsonl \
  --after results/large-memagent/base-durable-stable-repeat/results.jsonl \
  --mode repeatability --output-dir results/large-memagent/stable-base-repeatability
```

这里分析同一个base的重复性，输出first/repeat的一致性和描述性差值，不输出RL收益CI，也不选择较高的分数。原机器64/64最终response相同不是新机器必须或必然得到的结果；若不同，完整保留再诊断，不换一组题覆盖。

上述四份base评测全部完成后，可以停止两个推理容器释放HIP0/1，日志与结果保留。后续final loader能够启动已停但保留的owned容器：

```bash
docker stop ua-lab-infer32-stable ua-lab-infer32
```

只在评测已经结束时执行停止命令。若随后做Coder案例，用HIP1；自动final开始前需停止Coder，保持HIP0/1空闲。

### 接固定名称128步路线

可选自动路线使用以下名字，不能把主通用路线的新run名原样混入：

```bash
export RL_RUN_NAME=memagent_32b_128_durable
export RL_CONTAINER_NAME=ua-lab-rl-durable
export RL_GPU_IDS=2,3,4,5,6,7
```

继续使用训练章节的准备容器、只读配置检查和durable fresh→step8暂停→resume。注册发生在任何训练history之前；后续不能再运行注册器。注意旧`rl_reproduce.sh ...pilot...`会隐式重写固定512/64/64数据，不能在已注册输入上盲目执行；若做额外pilot，应按总手册采用不重写这些数据的独立pilot入口，或以durable前8步的保存/恢复验收作为本路线的实际检查。

在step8真实恢复和新step9已通过、无需再重启训练容器、两套base+repeat已齐全后，立即运行可选controller，须赶在step16提交并轮换掉native8之前。它的固定快照列表包含8；若已经错过且未导出8，不能靠删除历史掩盖缺口，应改走通用final收尾。

先按训练章启动唯一的live writer；原固定run名时必须产生`results/large-durable-live-status.json`和对应CSV，这是controller的固定读取入口。然后按以下独立session方式启动：

先单独检查：

```bash
test -s results/large-durable-live-status.json
python3 -B scripts/finalize_large_rl.py --check-only
```

尚未到128时，返回2且`waiting`非空是预期；应确认`errors`为空，不改JSON伪造完成。然后在新lab用独立session启动watch，避免依赖临时交互会话：

```bash
python3 - <<'PY_STABLE_WATCH'
import json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
lab = Path(os.environ['LAB_ROOT']).resolve()
folder = lab / 'controllers'
folder.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
log = folder / f'final-controller-{stamp}.log'
with log.open('xb') as output:
    proc = subprocess.Popen(
        [sys.executable, '-B', str(lab/'scripts/finalize_large_rl.py'), '--watch', '--with-stable'],
        cwd=lab, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        start_new_session=True, close_fds=True)
record = dict(pid=proc.pid, started_utc=datetime.now(timezone.utc).isoformat(), log=str(log))
log.with_suffix('.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record, indent=2))
PY_STABLE_WATCH
```

它会按固定8/32/64/96/128保留导出和CPU审计，并等待强制final128 gate。通过后primary在HIP0、stable在HIP1各跑final与repeat；stable两次在同一进程串行运行，使用新stdout，不覆盖base日志。

Stable最终输出固定为`final-durable-stable-step128`与`final-durable-stable-step128-repeat`。配对固定为first base→first final、base repeat→final repeat，另做final内部重复性；primary与stable保持两个协议，不交叉配分数。

### 当前验收范围

新注册器已经做只读/内存mock/合成文件系统的guard检查：本机现有状态在check-only与execute路径均于subprocess/注册前拒绝，历史baseline被时间边界拒绝，已有训练/稳定结果/模板备份拒绝；另核验primary显式batch-invariant字符串0会拒绝、stable复用ownership先于capture/start检查。记录在`results/register-new-node-stable-guard-validation.json`和`results/register-new-node-stable-review-regression.json`。

这些检查没有在本机实际启动服务或写入新protocol，也没有验证另一个物理节点的完整正向注册/训练。正向路径仍须按实际新节点输出和日志验收；主通用手工final路线始终可独立使用。
## 附录：本机128步主线完整YAML

以下内容原样来自提供的配置文件，用于审阅。运行时直接使用包内文件，launcher会重写run输出路径；不要把其中smoke占位路径当作实际run目录。

```yaml
# Fresh-base durable 128-step protocol. Native model/optimizer/extra + full-run commit callback.
trainer:
  use_v1: true
  v1:
    trainer_mode: separate_async
    separate_async:
      num_warmup_batches: 1
      parameter_sync_step: 1
  nnodes: 1
  n_gpus_per_node: 4
  total_epochs: 1
  total_training_steps: 128
  project_name: uni_agent_mi355_lab
  experiment_name: memagent_32b_128_durable
  logger:
  - console
  - tensorboard
  val_before_train: true
  test_freq: 32
  save_freq: 8
  resume_mode: disable
  default_local_dir: /lab/runs/rl/smoke/checkpoints
  rollout_data_dir: /lab/runs/rl/smoke/rollout
  validation_data_dir: /lab/runs/rl/smoke/validation
  log_val_generations: 3
  max_actor_ckpt_to_keep: null
  checkpoint_callback_class: rl_durable_checkpoint.DurableCheckpointCallback
  lab_checkpoint:
    keep_committed: 1
    expected_checkpoint_gib: 370
    min_free_gib: 128
    pause_after_step: null
data:
  train_files: /lab/data/rl_memagent_512_64_64/train.parquet
  val_files: /lab/data/rl_memagent_512_64_64/val.parquet
  train_batch_size: 4
  val_batch_size: 64
  max_prompt_length: 7168
  max_response_length: 1024
  return_raw_chat: true
  filter_overlong_prompts: false
  truncation: error
  dataloader_num_workers: 0
  apply_chat_template_kwargs:
    enable_thinking: false
  custom_cls:
    path: pkg://examples.mem_agent.dataset
    name: HotpotQAMemAgentDataset
  context_chunk_size: 5000
  seed: 42
algorithm:
  adv_estimator: grpo
  use_kl_in_reward: false
  norm_adv_by_std_in_grpo: false
actor_rollout_ref:
  model:
    path: /lab/models/Qwen3-32B
    use_remove_padding: false
    use_fused_kernels: false
    enable_gradient_checkpointing: true
    override_config:
      attn_implementation: sdpa
  actor:
    strategy: fsdp2
    ppo_mini_batch_size: 4
    ppo_micro_batch_size_per_gpu: 1
    use_dynamic_bsz: false
    use_torch_compile: false
    use_kl_loss: true
    calculate_entropy: true
    optim:
      lr: 1.0e-06
      weight_decay: 0.0
      lr_warmup_steps: 4
      lr_warmup_steps_ratio: 0.0
      lr_scheduler_type: constant
    fsdp_config:
      strategy: fsdp2
      use_torch_compile: false
      model_dtype: fp32
    checkpoint:
      save_contents:
      - model
      - optimizer
      - extra
      load_contents:
      - model
      - optimizer
      - extra
    kl_loss_coef: 0.01
  rollout:
    name: vllm
    mode: async
    n: 4
    tensor_model_parallel_size: 2
    gpu_memory_utilization: 0.3
    enforce_eager: true
    free_cache_engine: true
    max_model_len: 8192
    max_num_batched_tokens: 16384
    max_num_seqs: 16
    calculate_log_probs: true
    log_prob_micro_batch_size_per_gpu: 1
    checkpoint_engine:
      update_weights_bucket_megabytes: 8192
      backend: nccl
      engine_kwargs:
        nccl:
          multi_sender: false
      custom_backend_module: rl_durable_rank_audit
    temperature: 1.0
    top_p: 0.7
    val_kwargs:
      temperature: 0
      do_sample: false
      n: 1
    multi_turn:
      enable: true
      format: hermes
      max_parallel_calls: 1
    agent:
      num_workers: 1
      agent_loop_manager_class: uni_agent.framework.entry.AgentFrameworkRolloutAdapter
    custom:
      agent_framework:
        gateway_count: 1
        log_dir: /lab/runs/rl/smoke/agent_logs
        mask_unfinished_episode: false
        agent_runners:
          task:
            runner_fqn: uni_agent.framework.task_runner.run_task
            dispatch_mode: ray_task
            max_concurrent_sessions: 16
            trajectory_selection: all
            runner_kwargs:
              task_config_path: /lab/configs/rl_memagent_long_task.yaml
              model_name: Qwen3-32B
    nnodes: 1
    n_gpus_per_node: 2
    engine_kwargs:
      vllm:
        disable_custom_all_reduce: true
  ref:
    log_prob_micro_batch_size_per_gpu: 1
    strategy: fsdp2
    fsdp_config:
      strategy: fsdp2
      use_torch_compile: false
reward:
  num_workers: 2
  reward_manager:
    name: naive
  custom_reward_function:
    path: pkg://uni_agent.framework.task_runner
    name: score_from_runner_result
transfer_queue:
  enable: true
  backend:
    SimpleStorage:
      num_data_storage_units: 2
      total_storage_size: 4096
ray_kwargs:
  ray_init:
    num_cpus: 32
    num_gpus: 6
    include_dashboard: false
    _temp_dir: /lab/tmp/ray-rl
    object_store_memory: 4294967296
    runtime_env:
      env_vars:
        VERL_LOGGING_LEVEL: INFO
```
