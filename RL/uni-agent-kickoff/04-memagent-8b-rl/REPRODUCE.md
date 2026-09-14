# 8B MemAgent RL：运行与评测步骤

[本实验总览](README.md) · [全部实验](../README.md)

先完成[共用环境准备](../00-overview/SETUP.md)。以下命令在完整实验工作区执行，所引用的模型、数据和实验脚本需另行准备。

## 追加实验：Qwen3-8B 的128步训练与完整恢复

这一章记录用户追加的8B实验，沿用主手册的固定ROCm镜像和Uni-Agent/verl源码。它与32B使用相同的512训练题、64监控题、64外部题、batch4×每题4次采样和128个globalstep。8B独立使用容器、Python overlay、run、checkpoint目录和脚本，模型不是32B蒸馏产物，也没有从32B检查点继续。

算法准确写法是：**GRPO，组内优势不除标准差、token-mean loss、KL0.01**。仅去掉标准差归一化并不构成完整DrGRPO recipe；原冻结protocol的相关简称是历史记录，不应据此改动实际loss配置。

本机8B于2026-09-12 16:39:59 UTC启动，19:14:12 UTC完整128步训练结束、exit0。官方单文件HF导出成功，但原检查脚本假设必有分片index，触发了下文记录的CPU布局恢复。最终效果以独立评测和最终报告为准。以下命令用于**已经解包v2 kit的另一台空lab节点**。本机已有同名结果，不要在本机再次执行fresh或重启。

新增路径已完成独立目录的冷重建检查（原始记录：`results/reproduction-v2-8b-cold-20260912/audit.json`）：CPU81 pins、RL118依赖、609项source/model/data hash、8B结构与配置全部通过。该检查没有GPU映射、只读挂载固定原始模型；它不能替代目标机器自己的GPU验证。

### 1. 模型和容量

| 项目 | 8B实际配置 |
| --- | --- |
| 模型 | `Qwen/Qwen3-8B` |
| 固定revision | `b968826d9c46dd6066d109eabc6255188de91218` |
| 层数/参数 | 36层，399个参数tensor，8,190,735,360参数 |
| 原始BF16权重 | 5 shards；payload16,381,470,720 bytes，约15.26GiB |
| 训练布局 | HIP2–7；4个FSDP2 trainer、2卡TP2 rollout |
| master/compute | FP32 master、BF16 compute、SDPA、gradient checkpointing |
| 完整native8实测 | 98,359,358,326 bytes，约91.6GiB，包含model/Adam/extra/data/TQ |
| native磁盘门槛 | fresh至少320GiB可用：两份96GiB预算+128GiB余量；resume至少224GiB |
| 最终HF导出 | 约15.26GiB，另计入lab数据盘；原生Adam仍在checkpoint卷 |
| 早期actor显存观察 | 单rank的Torch allocator约53–59GiB allocated、61–67GiB reserved；非整卡设备总显存，最终峰值另见报告 |
| 早期耗时观察 | 普通step约50–60秒，完整保存约1分钟/8步；初始化/monitor/评测另计 |

8B与32B的tokenizer在本机两个runtime里对全部640题、分块和8,742个memory模板prompt组合逐项相同。36层、399张量是从实际HF配置和safetensors验得，不能沿用32B的707张量或凭模型名推断层数。

### 2. 新机器沿用固定输入，下载固定模型

先执行前文CPU bootstrap，得到无GPU的 `ua-lab-cpu` 和 `/lab/envs/cpu`。v2包含 `data/rl_memagent_8b_512_64_64`、`configs/rl8_model_inventory.json`、全部 `rl8_*` helper及两份原始8B方法协议。首次v1发布时没有8B数据与helper，必须使用新增v2或完整拷贝新增输入。

下面下载到新目录，不覆盖已有模型。HF访问公开仓库，不需要Claude/OpenAI密钥：

```bash
cd "$LAB_ROOT"
docker exec -i -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python - <<'PY_DOWNLOAD_8B'
from pathlib import Path
from huggingface_hub import snapshot_download
p = Path('/lab/models/Qwen3-8B')
assert not p.exists(), 'Use a new model directory; verify an existing copy separately'
snapshot_download('Qwen/Qwen3-8B',
    revision='b968826d9c46dd6066d109eabc6255188de91218',
    local_dir=p, max_workers=4,
    allow_patterns=['*.json', '*.safetensors', '*.model', '*.tiktoken',
                    '*.jinja', '*.txt', 'LICENSE*', 'README.md'])
PY_DOWNLOAD_8B
```

已有离线完整模型时，跳过下载，直接按冻结训练协议校验每个原始文件的SHA256。该校验包括5个完整权重文件，可能读取约16.4GB：

```bash
docker exec -i -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python - <<'PY_CHECK_8B'
import hashlib, json
from pathlib import Path
root = Path('/lab')
p = json.loads((root/'notes/rl-memagent-8b-128-protocol.json').read_text())
checked = []
for name, expected in p['source_sha256'].items():
    if not name.startswith(('models/Qwen3-8B/', 'data/rl_memagent_8b_512_64_64/')):
        continue
    with (root/name).open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    assert actual == expected, name
    checked.append(name)
inventory = json.loads((root/'configs/rl8_model_inventory.json').read_text())
assert inventory['tensor_count'] == 399
assert inventory['total_elements'] == 8190735360
assert inventory['config']['num_hidden_layers'] == 36
print('Fixed 8B model/data checked:', len(checked), 'files')
PY_CHECK_8B
```

不要在已有v2数据目录上再运行 `rl8_prepare_data.py`。该脚本是首次创建独立copy时用的，会拒绝覆盖；重新生成copy-receipt的时间字段也会使本次冻结SHA变化。带走固定数据即可。若要换数据，建立自己的新protocol及source guard，不能将新输入伪装为原配方。

### 3. 建立8B专用CPU预检环境

宿主checkpoint卷可以选择目标机的大容量本地盘，容器以相同绝对路径挂载；不要沿用本机用户名路径。此卷与lab数据盘可以不同。

```bash
export RL8_CHECKPOINT_VOLUME="$LAB_ROOT/native-checkpoints"
export RL8_CONTAINER_NAME=ua-lab-rl8-durable
export RL8_RUN_NAME=memagent_8b_128_durable
export RL8_GPU_IDS=2,3,4,5,6,7
mkdir -p "$RL8_CHECKPOINT_VOLUME" "$LAB_ROOT/logs"
df -h "$RL8_CHECKPOINT_VOLUME" "$LAB_ROOT"

# 在无GPU的CPU容器内先创建独立8B训练overlay。
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  bash /lab/scripts/rl8_setup_env.sh

docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-cpu \
  /lab/envs/rl8/bin/python /lab/scripts/rl8_cpu_preflight.py \
  --output /lab/results/newnode-8b-cpu-preflight.json

docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-cpu \
  /lab/envs/rl8/bin/python /lab/scripts/rl8_durable_selfcheck.py \
  --output /lab/results/newnode-8b-checkpoint-selfcheck.json

docker exec -i -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-cpu \
  /lab/envs/rl8/bin/python - <<'PY_SOURCE_8B'
from pathlib import Path
from rl8_durable_launch import verify_sources
r = verify_sources(Path('/lab'))
assert r['status'] == 'passed'
print('Full frozen source guard passed:', len(r['observed_source_sha256']))
PY_SOURCE_8B
```

`rl8_setup_env.sh`使用与32B相同的118项锁，`--system-site-packages`与`--no-deps`保留镜像内ROCm Torch。运行前检查所用训练verl的HEAD为 `a9f2985159536a607211dcac730d3f5d55028950`，CPU源码的HEAD不同是本次设计，不要把CPU verl覆盖到训练路径。

### 4. 持久启动，自动step8暂停与恢复到128

在执行本节前，先完成[8B评测章](REPRODUCE.md#qwen3-8b-的独立外部评测新节点登记两组配对与单文件恢复)第1–3节的新节点登记，并重新通过609项source guard。该登记器要求整个`runs/rl`无历史，包括32B；同lab跑两规模时，应在任何训练之前完成此准备，再选择先跑32B或8B。

本节使用本机真实执行过的 `rl8_controller.py`，所以在新lab沿用固定run/container名字。它只接受HIP2–7，不是任意GPU数的通用调度器。先按环境章节检查目标机设备以及已有作业，确认这6张卡确实可以用于本实验。原32B还在这些卡上训练时，须等它及导出CPU工作结束。

Controller要求一份资源交接记录，这是防止同时占用同一批卡的机器条件，不是需要远程审批。**应在新节点确认设备安排后创建新的记录，不复制本机的“32B已完成”证明。**

```bash
python3 - <<'PY_RELEASE_8B'
from datetime import datetime, timezone
from pathlib import Path
import json, os
root = Path(os.environ['LAB_ROOT']).resolve()
assert not (root/'runs/rl/memagent_8b_128_durable').exists()
assert not (root/'results/rl8-controller-status.json').exists()
path = root/'results/newnode-8b-training-resource-release.json'
path.parent.mkdir(parents=True, exist_ok=True)
record = {
    'status': 'released_by_root', 'physical_gpus': [2,3,4,5,6,7],
    'run_name': 'memagent_8b_128_durable',
    'released_utc': datetime.now(timezone.utc).isoformat(),
    'scope': 'New-node operator resource assignment; not original-node 32B evidence'
}
with path.open('x') as f:
    json.dump(record, f, indent=2)
print(path)
PY_RELEASE_8B

python3 - <<'PY_START_8B'
from datetime import datetime, timezone
from pathlib import Path
import json, os, subprocess, sys
root = Path(os.environ['LAB_ROOT']).resolve()
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
log_path = root/'logs'/f'rl8-controller-newnode-{stamp}.log'
with log_path.open('x') as log:
    p = subprocess.Popen([
        sys.executable, str(root/'scripts/rl8_controller.py'),
        '--lab-root', str(root), '--resource-release-record',
        str(root/'results/newnode-8b-training-resource-release.json')
    ], cwd=root, env=os.environ.copy(), stdin=subprocess.DEVNULL,
       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(json.dumps({'controller_pid':p.pid, 'log':str(log_path)}))
PY_START_8B
```

它将创建专用GPU容器，以固定镜像映射 `/dev/kfd`、`/dev/dri` 和HIP2–7，使用host network/IPC、`/lab`与checkpoint卷bind，以及独立HOME/cache/tmp。只在其自身step8完整提交且验收通过后重启自身容器；不会重启宿主或其他实验服务。

原controller的计划步骤是：fresh→真实step8 commit→计划退出75→原生状态/数据预取边界/9完整tensor数值检查→自身容器restart→从step8 resume→128 native→BF16 HF导出→完整训练分析/权重对照/512题源覆盖/monitor审计。**在本固定8B栈上，实际执行会在导出后的index验收处停止，需要第6节的单文件恢复，再接续剩余CPU检查。** Step8的75退出码是预定暂停；导出检查错误是另一个实际问题，不能混为同一种暂停。

不要再手动运行同名 `rl8_durable_reproduce.sh fresh`、resume或第二个controller。wrapper持有整个lab共用的RL锁，阻止32B/8B并发占用同一训练布局。

### 5. 看进度与验证恢复

```bash
python3 - <<'PY_STATUS_8B'
from pathlib import Path
import json
for name in ('results/rl8-controller-status.json',
             'results/rl8-durable-live-status.json',
             'runs/rl/memagent_8b_128_durable/run-outcome.json'):
    path = Path(name)
    if not path.exists():
        print(name, 'not yet created')
        continue
    d = json.loads(path.read_text())
    keys = ('status','observed_status','phase','updated_utc','pid','monitor_pid',
            'completed_global_step_from_metrics','latest_committed_global_step',
            'attempt_id','resume_from_global_step','ended_utc','exit_code')
    print(name, {k:d[k] for k in keys if k in d})
PY_STATUS_8B

docker top ua-lab-rl8-durable -eo pid,ppid,etime,args
tail -n 5 results/rl8-durable-live-steps.csv
```

`controller-status.updated_utc`只在阶段变化时更新；长时间停在resume命令本身不算失败。live writer每分钟写入，需要连同实际controller/writer/训练进程核对。已完成globalstep与已完整commit的step不同，例如51步日志、48步commit是正常状态。

Step9实际执行以后，可另开CPU审计，输出路径单独命名。这是只读observer，不会改变训练：

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-rl8-durable \
  /lab/envs/rl8/bin/python /lab/scripts/rl8_coverage_audit.py \
  --through-step 9 --output /lab/results/rl8-memagent/newnode-coverage-through9.json

docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-rl8-durable \
  /lab/envs/rl8/bin/python /lab/scripts/independent_rl8_resume_step9.py \
  --coverage /lab/results/rl8-memagent/newnode-coverage-through9.json \
  --output /lab/results/rl8-memagent/newnode-resume-step9.json
```

该审计核对四rank真实save/load状态相同、399个Adam states、step8 counter56/scheduler8、固定0/18/35层model/moment/有效BF16/RNG probes，以及实际step9消费的4个缓存UID组。满128目标对应896个Adam counter，而非128；每个globalstep内部有多个optimizer minibatch，四rank是同一分布式进度，不能乘4。

### 6. 处理本次已知的单文件HF导出

固定训练Transformers5.9的`save_pretrained`默认按50GB分片。32B的BF16文件超过阈值，有index；8B约16.38GB，官方merger正常返回0，只写`model.safetensors`。原冻结`rl8_export_committed.py`随后读取不存在的`model.safetensors.index.json`，将export与controller记成failed；native训练与完整checkpoint仍是completed。

仅在**这同一种错误**下，保持专用训练容器运行，执行以下CPU修复。入口要求原失败记录、真实native128以及唯一单文件存在，按完整399项header核对名字/shape/BF16/连续offset/长度，再派生标准index；它不重新生成、修改或量化权重。原failed文件保留原路径和字节，另写成功的`export-recovery-outcome.json`。宿主读取权限仅通过chown这个新导出给lab用户解决，原mode0600与mtime保持不变。

```bash
cd "$LAB_ROOT"
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-rl8-durable \
  /lab/envs/rl8/bin/python /lab/scripts/recover_rl8_single_file_export.py

# 只运行一次；已有完成记录时直接验收，不再覆盖。
bash -o noclobber -c \
  'exec python3 scripts/continue_rl8_finalization.py > logs/newnode-8b-finalization-recovery.log 2>&1'

cat results/rl8-finalization-recovery/status.json
```

Continuation只调用原冻结的source guard、训练分析、完整9张量FP32/BF16/导出一致性、源题覆盖和monitor检查，不重新训练，也不改变reward或Adam计数。成功终态为`status=completed, phase=postprocessing_complete`。原`results/rl8-controller-status.json`和`export-outcome.json`仍为failed，不能为了让旧入口通过而把它们改成成功。

原8B final loader检查的是旧controller/export成功字段，所以此恢复必须与评测章节的新final gate/loader附件配套使用；不能直接运行旧final loader忽略报错。失败若属于缺权重、截断文件、native128未完成或不同报错，应先定位，不能套用此index恢复。

### 7. 结束条件与产物

| 产物 | 应满足的条件 |
| --- | --- |
| `run-outcome.json` | completed、exit0、128、有效rollout1..128 |
| `checkpoints/committed_checkpoint.json` | 独立指针step128，manifest一致，四rank完整model/optimizer/extra/data/TQ |
| 原controller/export状态 | 本次保留failed；只有记录的singleton-index原因适用上述恢复，不能删掉或覆写 |
| `results/rl8-finalization-recovery/status.json` | completed、phase=postprocessing_complete；绑定原失败SHA并完成全部CPU收尾 |
| `exports/global_step_128/export-recovery-outcome.json` | completed，399 BF16 tensors、8,190,735,360参数；绑定native128、原失败与完整payload SHA |
| `analysis.json` | 512源题组、2048session，真实context与padding分开 |
| `results/rl8-memagent/durable-train-source-coverage-final128.json` | 完整512源题各一次，源chunk逐字匹配，无重复/遗漏 |
| `results/memagent-8b/native-step128-weight-deltas.json` | 9完整tensor有限，导出等于原生FP32 cast BF16，记录两种精度的变化 |
| `results/rl8-memagent/durable-step128-monitor-independent/audit.json` | 64题monitor和原始reward一致，格式变化单列 |

训练完成不意味着外部base/final评测完成。下一章的评测服务是单独流程，使用固定step128。Controller没有按monitor分数选checkpoint；也不会因分数没有上涨而隐藏终点。

新机器如果只想手动训练，可以使用wrapper的fresh/resume入口，但必须自行完成step8原生验收、数据边界、恢复证据及全部收尾，且不能同时运行自动controller。若发生外部中断，保留旧controller状态、日志和attempt证据，在真正停止后使用`--mode resume`启动新controller；它会拒绝覆盖未归档的旧controller状态。完成128后入口会拒绝再次resume，延长训练需要另建协议。

当前这些参数来自本机可运行配方。把trainer减少到2张、改LoRA、换另一代GPU或降低内存均属新配方，应重新验证；本次尚未测得这些变体的最低配置。

## Qwen3-8B 的独立外部评测：新节点登记、两组配对与单文件恢复

这一章给出8B的完整跨机评测路线。外部集固定为官方dev第64–127行，共64题；训练monitor使用dev第0–63行，两者分开。每道题先逐块更新memory，再生成最终答案，使用原始boxed-answer token LCS判分。`LCS=1`表示该指标满分，不等同于对所有语义正确答案做人工判定。

**新机器必须在8B训练章节第3节完成后、第4节启动controller前，先完成本章第1–3节的登记。** 此入口要求整个`runs/rl`没有历史，包括32B；因此同一个新lab要跑两种规模时，在任何训练开始前先准备并登记8B，或单独为8B解包新lab。训练协议已绑定外部评测方法的SHA；若先训练再改新机runtime记录，训练source guard会正确拒绝。登记只用于解包v2 kit后的空lab。不要在本机已有128步结果上运行，也不要从旧实验的`results/memagent-8b`复制任何完成或资源释放状态。

本章新节点路径使用实际生产中的原runner、原reward和原分析器，并复用本次已执行的单文件导出恢复与final driver。新增登记器的逻辑已完成15项CPU临时目录预检（原始文件：`results/reproduction-8b-node-registration-preflight-v2.json`），包括拒绝当前有历史lab、缺失checkpoint卷设置、已有32B历史或native状态，检查唯一个训练source条目变化，以及由原生产profile校验新lock。登记器尚未在第二台物理GPU节点上完成全流程训练/推理验收；第二台机器的结果应记录为新实验。

### 1. 先理解固定路径和实验配方

| 项目 | 本路线的固定值 |
| --- | --- |
| 模型 | `Qwen/Qwen3-8B`，revision `b968826d9c46dd6066d109eabc6255188de91218` |
| 基线权重 | `/lab/models/Qwen3-8B`；完整5 shard、399 tensors、8,190,735,360参数 |
| 最终权重 | `/lab/runs/rl/memagent_8b_128_durable/exports/global_step_128/huggingface` |
| 服务与客户端 | `ua-lab-infer8-stable`、`ua-lab-cpu8-stable`；客户端不映射GPU |
| GPU与API | 物理HIP1，容器可见logical0；`http://127.0.0.1:18085/v1` |
| 推理 | BF16、TP1、eager、16,384 model window、最多16 sequences |
| 稳定性模式 | `VLLM_BATCH_INVARIANT=1`；必须在实际日志看到`TRITON_ATTN` |
| 请求 | greedy：temperature0、top_p1；seed0；concurrency8 |
| memory/final | 每次最多1,024 token；source按5,000 token分块，遍历全部块后再final |
| 配对 | base→final128；base-repeat→final128-repeat；不选得分较好的一遍 |
| CI | 64道题上的配对bootstrap，20,000次；相同题的repeat不算新增独立样本 |

8B训练占HIP2–7；这个评测服务占HIP1，可以在训练继续时评测不可变base。所有方法、数据、超参和最终step128在首baseline前固定。不能看完baseline或monitor再调整训练参数、改reward或选中间checkpoint后仍称同一实验。

下文保留当前runner需要的固定容器名、run名、GPU编号和端口。若新机这些名字/端口被占用，使用另一台节点或新的独立环境；先记录分配。要改名称、GPU或端口，应创建自己的新profile并冻结完整调用链，不能仅给外层命令换`HIP_VISIBLE_DEVICES`，因为runner会校验实际argv、进程环境与PCI身份。

### 2. 创建两个专用容器，只运行sleep

前提：主环境章CPU bootstrap已完成，8B训练章第1–3节已完成，模型及609项source检查通过；`LAB_ROOT`和`RL8_CHECKPOINT_VOLUME`都指向新节点目录。登记器要求显式设置绝对路径`RL8_CHECKPOINT_VOLUME`，同一个值须一直保留到训练启动；不接受省略后在不同入口使用不同默认盘。使用固定digest，不用浮动tag。以下是宿主命令，Docker CLI连接目标节点的主daemon。

先核对目标HIP1的使用情况，确认其可分配给本实验。不要停止其他用户进程或reset共享GPU。专用starter还会在加载模型前要求至少95%空闲设备显存；该检查是资源诊断，不会替你清理别人的作业。

```bash
cd "$LAB_ROOT"
export LAB_ROOT="$(realpath "$LAB_ROOT")"
export RL8_CHECKPOINT_VOLUME="$(realpath "$RL8_CHECKPOINT_VOLUME")"

# 两个名字必须均未被使用；docker create遇到重名会停止，不能直接rm旧容器。
docker ps -a --filter name=ua-lab-infer8-stable
docker ps -a --filter name=ua-lab-cpu8-stable
ss -H -ltn 'sport = :18085'

mkdir -p "$LAB_ROOT/logs" "$LAB_ROOT/results/memagent-8b" \
  "$LAB_ROOT/cache/memagent8b-eval-home" \
  "$LAB_ROOT/cache/memagent8b-eval-tmp" \
  "$LAB_ROOT/cache/memagent8b-client-home" \
  "$LAB_ROOT/cache/memagent8b-client-tmp"

docker create --name ua-lab-infer8-stable \
  --label purpose=uni-agent-reproduction-8b-stable \
  --network host --ipc host --device=/dev/kfd --device=/dev/dri \
  --group-add video --group-add render \
  --security-opt seccomp=unconfined --cap-add SYS_PTRACE \
  --entrypoint /bin/bash \
  -v "$LAB_ROOT:/lab" \
  -v "$LAB_ROOT/cache/memagent8b-eval-home:/root" \
  -v "$LAB_ROOT/cache/memagent8b-eval-tmp:/tmp" \
  -e HIP_VISIBLE_DEVICES=1 -e VLLM_BATCH_INVARIANT=1 \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn -e TOKENIZERS_PARALLELISM=false \
  -e HF_HOME=/lab/cache/huggingface -w /lab \
  vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa \
  -lc 'exec sleep infinity'

docker create --name ua-lab-cpu8-stable \
  --label purpose=uni-agent-reproduction-8b-client \
  --network host --cpus 8 --memory 64g --shm-size 2g \
  --entrypoint /bin/bash \
  -v "$LAB_ROOT:/lab" \
  -v "$RL8_CHECKPOINT_VOLUME:$RL8_CHECKPOINT_VOLUME" \
  -v "$LAB_ROOT/cache/memagent8b-client-home:/root" \
  -v "$LAB_ROOT/cache/memagent8b-client-tmp:/tmp" \
  -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e OMP_NUM_THREADS=2 -e OPENBLAS_NUM_THREADS=1 \
  -e HF_HOME=/lab/cache/huggingface -e TOKENIZERS_PARALLELISM=false \
  -w /lab \
  vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa \
  -lc 'exec sleep infinity'

docker start ua-lab-infer8-stable ua-lab-cpu8-stable
docker top ua-lab-infer8-stable -eo pid,ppid,etime,args
docker top ua-lab-cpu8-stable -eo pid,ppid,etime,args
```

CPU客户端必须额外挂载checkpoint卷，因为run里的`checkpoints`是绝对路径symlink；只挂`/lab`不能访问另一个宿主盘上的native状态。容器内的CPU overlay已由bootstrap放在共享`/lab/envs/cpu`，这里不用重新pip install。`/lab/envs/rl8`只属于训练及相应CPU恢复操作。

如果镜像内不存在`video`或`render`组名，使用目标节点`getent group video`、`getent group render`得到的数值GID；记录实际docker inspect。主机须已有可用ROCm驱动，镜像不会安装或替换宿主内核驱动。

### 3. 登记新节点的runtime与GPU身份，再启动训练

本机的原method、amendment、runtime-lock绑定了原宿主kernel/driver、PCI地址和冻结时间，不能复制完成状态后当作新节点测量。新脚本register_reproduction_8b_eval.py（原始文件：`scripts/register_reproduction_8b_eval.py`）先检查lab没有任何训练或8B评测历史，再查询两个sleep容器的软件版本及实际HIP1 PCI。

它允许新宿主kernel/driver与原机器不同，并将变化明确写入新记录；容器image ID/reference及相关package版本必须仍与固定栈一致。宿主驱动变化意味着仍需新GPU验证，不保证和原机器逐token一致。

```bash
cd "$LAB_ROOT"

# 只读预检：包括完整609项SHA，可能顺序读取约16.4GB权重。
# 会查询实际GPU PCI，但不加载模型、训练或运行题目。
python3 scripts/register_reproduction_8b_eval.py

# 在任何训练与baseline之前执行一次；无历史的空lab才会接受。
python3 scripts/register_reproduction_8b_eval.py --execute

cat reproduction/newnode-8b-registration/receipt.json

# 再使用训练自己的正式入口逐项检查609项，确保新method已正确绑定。
docker exec -i -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl ua-lab-cpu \
  /lab/envs/rl8/bin/python - <<'PY_REGISTERED_SOURCE_GUARD'
from pathlib import Path
from rl8_durable_launch import verify_sources
result = verify_sources(Path('/lab'))
assert result['status'] == 'passed'
assert len(result['observed_source_sha256']) == 609
print(result['protocol_sha256'])
print('Registered new-node training source guard passed: 609 files')
PY_REGISTERED_SOURCE_GUARD
```

登记会保留4份模板的原字节到`reproduction/newnode-8b-registration/original/notes/`，将新登记版本另存`registered/notes/`。该目录的`environment.json`记录新runtime、PCI、登记器SHA和原协议SHA，`receipt.json`记录新4份协议SHA。新实验的身份由这些新SHA区分，profile的数学配方和runner名称保持一致。

新训练protocol只有`external_protocol_sha256`及609项中的对应method SHA随依赖改变，另加登记时间和溯源说明；训练代码、512/64/64输入、原模型完整文件、loss、optimizer和step128选择均不改。原模板中关于本机历史CPU验证的文字被明确标为模板来源，不能代替本节新source检查与前一章新CPU预检。

登记生成`results/memagent-8b/gpu1-release.json`，其中`released_by_root`是现有runner需要的字段值，正文明确是**新节点操作者的设备分配**，不声称原机器32B作业曾在此运行或完成。原HIP0拥堵原因只保留在归档模板，不冒充新机观测。

重复登记会被拒绝。若登记过程因磁盘/权限等在中途失败，保留`newnode-8b-registration`中的原始和待登记字节，先处理具体失败；不要在已启动训练或baseline后补写新hash，也不要删除历史后重试。最清楚的恢复方法是重新解包另一个空lab，再按顺序登记。

完成以上步骤后，回到8B训练章第4节，创建**独立的HIP2–7训练资源记录**并启动controller。本节HIP1的评测分配记录不能传给训练controller。接下来可以在训练进行时完成base两遍；也可以等native128与CPU恢复完成后再评测，参数不变。

### 4. 加载base，连续完成两遍完整64题

以下命令在宿主执行。首条命令只检查等待条件，带`--execute`才真实加载。启动器会完整核对原始5个权重文件SHA、容器image/devices/mount、PCI、GPU空闲显存、服务日志及实际后端。它不下载模型，也不占用训练的HIP2–7。

```bash
cd "$LAB_ROOT"
python3 scripts/start_memagent8b_stable.py --model-stage base
python3 scripts/start_memagent8b_stable.py --model-stage base --execute

python3 scripts/run_memagent8b_stable.py --name base-stable --check-service
python3 scripts/run_memagent8b_stable.py --name base-stable
python3 scripts/run_memagent8b_stable.py --name base-stable-repeat

python3 scripts/analyze_memagent8b_stable.py \
  --before results/memagent-8b/base-stable/results.jsonl \
  --after results/memagent-8b/base-stable-repeat/results.jsonl \
  --mode repeatability \
  --output-dir results/memagent-8b/base-stable-repeatability
```

base与base-repeat必须使用同一个服务进程，runner会核对PID、启动ticks、argv、环境和真实日志前缀hash。两遍完成后**保留base服务进程运行**，直到本章第6节的final driver接手；它要求仍是基线记录中的原base进程，再只重启这个专用容器切换模型。

每一遍的产物位于各自phase目录：

| 文件 | 作用 |
| --- | --- |
| `results.jsonl` | 每道题的最终response、reward、chunk/调用计数；独立分析器重新按原reward判分 |
| `summary.json` | 完成数、错误数、平均reward、调用统计；不能只看单个均值跳过原始题目 |
| `provenance.json` | 前后runtime、method/operations SHA、实际服务进程、数据/source SHA、资源记录 |
| `infer.log` | 客户端执行与异常日志 |
| `logs/memagent8b-stable-base-*.log` | 服务端初始化、真实TRITON_ATTN及engine配置；分析器会核对已保存前缀 |

任何phase已有目录时runner都会拒绝覆盖。若真实失败，应保留失败目录和原因，另立清晰的恢复phase/protocol；不要删掉低分结果后在同名phase重跑。若600秒ready等待超时，先读原service-start记录和日志，并检查真实进程/API；超时本身不证明模型永远无法加载。

### 5. 完成128步与单文件HF恢复，验收最终模型

训练章已记录实际遇到的布局问题：固定Transformers5.9默认按50GB分片，8B官方merger合法生成约16.38GB的单个`model.safetensors`，没有分片index；旧导出验收脚本仍读取index，从而把controller/export记成failed。native128本身完成、exit0。

先完整执行训练章第6节的两个命令：`recover_rl8_single_file_export.py`与`continue_rl8_finalization.py`。前者根据完整399-tensor header派生标准index，核对整文件SHA前后相同；后者只接续原冻结的6个CPU收尾producer。原失败JSON保留原字节。

验收如下。此时最终checkpoint卷仍须可读，CPU客户端容器仍运行；不需要训练GPU继续占用，但训练容器内的CPU收尾必须先全部结束。

```bash
cd "$LAB_ROOT"
python3 scripts/verify_memagent8b_layout_recovery.py
cat results/memagent-8b/final128-load-gate-layout-recovery.json
cat results/rl8-finalization-recovery/status.json
```

新gate同时检查：真实native128及独立commit指针、四rank完整保存清单/长度/固定位置probe、原controller/export失败字节与新恢复绑定、399 tensors/shape/BF16/payload完整覆盖、最终单文件全SHA、9个完整tensor与native FP32 cast BF16逐元素相同，以及model/config/tokenizer语义一致。`accepted_layout_recovery=true`与`original_export_controller_still_failed=true`是正常成功字段。

这里native checkpoint的完整性仍按原事务协议使用长度与head/middle/tail probe，不宣称已给约91.6GiB整个native checkpoint做逐字节全SHA。最终约15.26GiB HF权重有整文件SHA。

只有同一种已记录的合法singleton缺index故障适用此恢复。若native不足128、原始HF权重缺失/截断、tensor不齐、恢复与原失败SHA不匹配，gate应拒绝。不要手工将旧failed改为completed来绕过它。

### 6. 切换final128，运行final两遍和三份分析

使用finalize_memagent8b_layout_recovery.py（原始文件：`scripts/finalize_memagent8b_layout_recovery.py`），这是本次实际使用的恢复driver。它先核对base两遍与仍在运行的原base进程，通过上一节CPU gate后，只重启`ua-lab-infer8-stable`，加载固定final128模型，再调用原冻结runner完成两遍final与三份分析。原starter文件不改，只在新driver进程内替换与旧失败状态不兼容的模型验收回调，其余GPU/runtime/argv/后端检查仍由原starter执行。

用下面命令在宿主持久启动，避免SSH断开打断评测：

```bash
cd "$LAB_ROOT"
python3 - <<'PY_FINAL_DRIVER'
from datetime import datetime, timezone
from pathlib import Path
import json, os, subprocess, sys
root = Path(os.environ['LAB_ROOT']).resolve()
control = root/'results/memagent-8b/finalpair-layout-recovery-20260913'
assert not (control/'status.json').exists(), 'Preserve the existing final driver outcome'
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
log_path = root/'logs'/f'memagent8b-final-newnode-{stamp}.log'
with log_path.open('x') as log:
    process = subprocess.Popen([
        sys.executable, str(root/'scripts/finalize_memagent8b_layout_recovery.py')
    ], cwd=root, env=os.environ.copy(), stdin=subprocess.DEVNULL,
       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(json.dumps({'pid':process.pid, 'log':str(log_path), 'control':str(control)}))
PY_FINAL_DRIVER
```

控制目录名中的`20260913`是此driver版本的固定路径，本次模板沿用它；内部所有执行时间和PID都来自新节点实际运行。它拒绝覆盖已有status。不要同时手动运行final runner或启动第二个final driver。

固定执行顺序与分析产物为：

1. `final-stable-step128`：完整64题。
2. `final-stable-step128-repeat`：同一个final服务进程，再做完整64题。
3. `comparison-stable-step128`：base→final，原LCS及配对CI。
4. `comparison-stable-step128-repeat`：base-repeat→final-repeat，单独配对CI。
5. `final-stable-step128-repeatability`：final两遍的一致性；不标成学习收益、不再算学习CI。

查看当前状态：

```bash
cd "$LAB_ROOT"
cat results/memagent-8b/finalpair-layout-recovery-20260913/status.json
docker top ua-lab-infer8-stable -eo pid,ppid,etime,args
python3 - <<'PY_PHASE_PROGRESS'
from pathlib import Path
import json
for phase in ('base-stable','base-stable-repeat','final-stable-step128','final-stable-step128-repeat'):
    folder = Path('results/memagent-8b')/phase
    raw = folder/'results.jsonl'
    rows = sum(bool(line.strip()) for line in raw.read_text().splitlines()) if raw.exists() else 0
    provenance = folder/'provenance.json'
    status = json.loads(provenance.read_text()).get('status') if provenance.exists() else 'not_started'
    print(phase, 'rows=',rows, 'status=',status)
PY_PHASE_PROGRESS
```

driver成功终态是`status=evaluations_completed`，不是`completed`；每一遍provenance以及三份comparison本身应为`completed`。运行时状态文件只在阶段变化时更新，仍需结合真实进程、日志时间与已完成行数，不能拿昨晚的旧JSON判断现在仍在运行。

### 7. 做独立验收，整理报告后释放自己的服务

两组配对都完成后，执行新增独立validator。它重新读取64题原始结果、原reward、CSV、phase/provenance和CI，隔离子进程中的8B常量，防止污染32B分析器。

```bash
cd "$LAB_ROOT"
python3 scripts/audit_8b_final_results.py \
  --first results/memagent-8b/comparison-stable-step128/comparison.json \
  --repeat results/memagent-8b/comparison-stable-step128-repeat/comparison.json \
  --output results/memagent-8b/final-independent-audit-newnode.json
```

报告至少保留以下各层证据：

| 层次 | 应报告什么 |
| --- | --- |
| 真训练 | 128个globalstep、四rank Adam计数、非零任务优势步/组、参数变化、实际step8→9恢复 |
| 数据覆盖 | 512源训练题各一次、2,048 sessions；真实context与padding分开 |
| 训练monitor | 初始及32/64/96/128曲线，明确没有按monitor挑checkpoint |
| 独立external | 两组各64题的base/final LCS、满分数、改善/回退/持平与配对CI |
| 同模型稳定性 | base两遍与final两遍的逐字response一致数、reward一致数；64重复题不合并成128独立题 |
| 格式与内容 | TeX空格、千分位、wrapper等窄格式变化单列；剩余差值也不能直接叫知识收益 |
| 资源与成本 | 普通step、记录内迭代总耗时、checkpoint/HF容量与actor allocator；中断墙钟另外列 |
| 故障恢复 | 原failed状态、新recovery、原始/新source SHA及不改权重payload的证据 |

比较32B与8B时，使用相同stable/TRITON配方下各自base→final的配对结果。不要把32B原默认ROCM_ATTN服务的某一遍base接到8B final，也不要挑两个模型各自得分最高的一遍做结论。即使两次repeat逐字相同，这里仍只有64道独立外部题和每个规模一条训练轨迹。

当前外部评测记录保留最终response及调用计数，没有完整中间memory序列；它能支持最终答题变化分析，不能单凭最终答案叙述证明memory内部机制。长文分块实验也应称滚动memory处理长source，不是模型原生百万token attention。

把服务日志一起归档，分析器后续仍要验证其已保存前缀SHA。完成所有结果审计、报告和必要只读检查后，可以仅停止本实验自己的两个服务容器：

```bash
docker stop --time 30 ua-lab-infer8-stable
docker stop --time 30 ua-lab-cpu8-stable
```

保留容器inspect、日志、四phase、comparison、源协议、新节点登记receipt及训练native/HF路径。容器停止不会删除挂载目录里的证据；不要用清空results、改成功字段或删checkpoint代替正常结束记录。
## 附录：8B追加实验完整YAML

原样来自 `configs/rl_memagent_8b_128_durable.yaml`；独立run、checkpoint与overlay按8B章节建立。

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
  experiment_name: memagent_8b_128_durable
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
  checkpoint_callback_class: rl8_durable_checkpoint.DurableCheckpointCallback
  lab_checkpoint:
    keep_committed: 1
    expected_checkpoint_gib: 96
    min_free_gib: 128
    pause_after_step: null
data:
  train_files: /lab/data/rl_memagent_8b_512_64_64/train.parquet
  val_files: /lab/data/rl_memagent_8b_512_64_64/val.parquet
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
    path: /lab/models/Qwen3-8B
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
      custom_backend_module: rl8_rank_audit
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
              task_config_path: /lab/configs/rl_memagent_8b_task.yaml
              model_name: Qwen3-8B
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
    _temp_dir: /lab/tmp/ray-rl8
    object_store_memory: 4294967296
    runtime_env:
      env_vars:
        VERL_LOGGING_LEVEL: INFO
```
