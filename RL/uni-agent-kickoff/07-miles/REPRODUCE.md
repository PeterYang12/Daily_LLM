# Miles：环境、训练与工具实验步骤

[本实验总览](README.md) · [全部实验](../README.md)

先完成[共用环境准备](../00-overview/SETUP.md)。以下命令在完整实验工作区执行，所引用的模型、数据和实验脚本需另行准备。

### Miles：固定镜像、源码与overlay

历史SGLang镜像的immutable manifest已通过registry查询补全，config ID与当时实际镜像完全一致；2026-09-12独立CPU冷重建进一步成功重新pull该digest，没有运行模型：

```text
lmsysorg/sglang-rocm@sha256:b82c13f1ab16690ba00974d1c38885e9934665d13a3ec1d2a6a615ae3ec072cd
image ID: sha256:334898a4b0cfbecbf18d788a0687011cfbb2d817e8e9b5ea694717308cd1de67
historical tag: v0.5.18-rocm720-mi35x-20260826
```

证据为 registry pin审计（原始文件：`results/miles-image-registry-pin-audit.json`）。不要把Docker image config ID当作registry manifest digest传给`docker pull`，二者在此不同。

| 组件 | 精确选择 |
| --- | --- |
| Miles repo | `https://github.com/radixark/miles.git`，commit`50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9` |
| SGLang repo | `https://github.com/sgl-project/sglang.git`，commit`32839114c4ada2ae237581405a8ff39dc0db9e25`，保存在`src/sglang-miles` |
| Miles补丁1 | `patches/miles-sglang-triton-compat.patch`：当前Triton kernel路径/参数适配 |
| Miles补丁2 | `patches/miles-fsdp-no-megatron.patch`：未分片词表/单rank TP group的交叉熵fallback |
| 实际runtime | Python3.10.12、torch2.9.1+rocm7.2.0.git7e1940d4、Ray2.56.0、Transformers5.12.1 |
| 数学底座模型 | Qwen3-0.6B，revision`c1899de289a04d12100db370d81485cdf75e47ca` |
| 工具失败/适配对照模型 | Qwen3-4B-Instruct-2507，revision`cdbee75f17c01a7cc42f958dc650907174af0554` |
| GSM8K数据 | `zhuzilin/gsm8k`的`train.parquet`，revision`0cbd9f31d91ac21a7613dcbc7fef992adac459ae`，前64行 |

两个补丁只验证了所记录的FSDP2/BF16/singleton-TP路线，不能据此声称多rank词表并行或Megatron路线已通过。SGLang源码覆盖是必需项：镜像原SGLang缺Miles权重同步需要的begin/end weight update端点。

在新的`src/`中clone并checkout上述commit，然后各应用一次补丁。若迁移的是已经打好补丁的工作树，使用`git apply --reverse --check`确认，不能再次apply。patch SHA与source状态在`configs/miles-sources.json`。

```bash
# Kit源码已经应用这两个patch，主路线只做反向检查。
git -C src/miles apply --reverse --check "$LAB_ROOT/patches/miles-sglang-triton-compat.patch"
git -C src/miles apply --reverse --check "$LAB_ROOT/patches/miles-fsdp-no-megatron.patch"
docker pull lmsysorg/sglang-rocm@sha256:b82c13f1ab16690ba00974d1c38885e9934665d13a3ec1d2a6a615ae3ec072cd
python3 scripts/miles_env.py start --name ua-lab-miles-repro-cpu \
  --image lmsysorg/sglang-rocm@sha256:b82c13f1ab16690ba00974d1c38885e9934665d13a3ec1d2a6a615ae3ec072cd
```

`miles_env.py`若发现同名容器会直接复用，不完整校验旧image/mount/GPU参数。因此跨机或新实验优先使用新名字，并核对`docker inspect`中的Image和Mounts。新名字不影响内部固定`/lab/envs/miles`。

**不要直接把完整`miles_environment.lock`交给pip，也不要在此镜像安装新的torch。** 该文件记录了镜像包和editable/local路径，不是干净的overlay安装列表。原`miles_setup.py`使用部分未固定版本的`miles_requirements.txt`，并会重写历史environment.lock；适合回看当时操作，不足以单独保证未来完全固定版本。

本次新增 miles_overlay_reproduction.lock（原始文件：`configs/miles_overlay_reproduction.lock`），从实际overlay的40个dist-info中排除editable Miles、pip22.0.2、setuptools59.6.0，保留37个精确版本。镜像torch/SGLang等不在其中。来源记录为 overlay锁审计（原始文件：`results/miles-overlay-reproduction-lock-audit.json`）。下述`.pth`与安装方式已在原先没有`envs/miles`的独立目录中重建成功：37/37版本来自新overlay，ROCm torch前后保持一致，两个源码import来源及核心/补丁模块导入通过。见冷重建审计（原始文件：`results/miles-cold-rebuild-v3-20260912/audit.json`）；历史lock及其原注释保持原hash，新验收状态以该审计为准。

```bash
docker exec -i ua-lab-miles-repro-cpu python3 - <<'PY'
import subprocess, sys
from pathlib import Path
p = Path('/lab/envs/miles')
if not (p / 'bin/python').exists():
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(p)], check=True)
site = p / 'lib/python3.10/site-packages'
(site / 'miles_base_image.pth').write_text("import site; site.addsitedir('/opt/venv/lib/python3.10/site-packages')\n")
(site / '00_sglang_miles.pth').write_text("import sys; sys.path.insert(0, '/lab/src/sglang-miles/python')\n")
PY
docker exec ua-lab-miles-repro-cpu /lab/envs/miles/bin/python -m pip install \
  --no-deps -r /lab/configs/miles_overlay_reproduction.lock
docker exec ua-lab-miles-repro-cpu git config --global --add safe.directory /lab/src/miles
docker exec ua-lab-miles-repro-cpu /lab/envs/miles/bin/python -m pip install \
  --no-deps -e /lab/src/miles
```

editable安装前的`git config`在新容器内执行，只允许精确的`/lab/src/miles`路径，不使用`*`，不改变宿主Git配置。冷重建首次editable构建确实遇到了`dubious ownership`，添加该容器内配置后重试成功；原失败与重试均已保留。

不要对SGLang执行`pip install -e src/sglang-miles/python`：该pin的隔离构建曾尝试获取torch2.13.0，`--no-deps`不会禁止build-system依赖。上述源码`.pth`覆盖保留镜像native ROCm库，正是本次实际跑通的组合。

冷重建后先用CPU检查`torch.__version__`/`torch.version.hip`，以及`sglang.__file__`来自`/lab/src/sglang-miles/python`。再使用`miles_probe.py`、`miles_plan.py`做导入/参数检查；这些probe有固定报告输出路径，若拷贝了原日志，应先将历史证据放到独立归档目录，避免重写原报告。

CPU/import通过与依赖声明完整是两项结论：基础镜像`pip check`有TileLang缺`torch-c-dlpack-ext`及其`apache-tvm-ffi`版本声明冲突；overlay另显示Miles声明的`mcp`、`memray`、`nvidia-resiliency-ext`、`onnxscript`、`qwen-vl-utils`、`ring-flash-attn`、`torchft-nightly`缺失，以及系统`pygobject`缺`pycairo`。本次保留固定overlay，没有增包消除这些诊断。所选FSDP/core路径的导入通过，不代表全部Miles功能依赖完整，也不代表GPU数值或训练重新验收。

### Miles数据、训练和实际工具调用

模型可复用CPU下载器，不把下载和Miles训练环境混在一起：

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/download_assets.py --models 0.6b 4b
docker exec ua-lab-miles-repro-cpu /lab/envs/miles/bin/python /lab/scripts/miles_prepare_data.py
docker exec ua-lab-miles-repro-cpu /lab/envs/miles/bin/python /lab/scripts/miles_prepare_retool.py
```

`miles_prepare_data.py`产出`data/miles/gsm8k-train-64.parquet`并检查官方math reward；已有parquet必须与固定源表内容相同。`miles_prepare_retool.py`生成8条明确Python整数表达式及gold答案，包含官方tools schema，是工具集成测试数据，不是数学泛化benchmark。4B加强提示分支另需`data/miles/retool-arithmetic-8-raw-python.jsonl`；该文件应迁移，不能假定普通prepare脚本会重建所有后续变体。

只有确认历史卡号6,7空闲时才创建GPU容器；另一机器可复制JSON配置到新文件修改GPU/端口，并保留新的实际配置，不能隐式继承本机正在训练的布局：

```bash
python3 scripts/miles_env.py start --name ua-lab-miles-repro-gpu --gpu-ids 6,7 \
  --image lmsysorg/sglang-rocm@sha256:b82c13f1ab16690ba00974d1c38885e9934665d13a3ec1d2a6a615ae3ec072cd
docker exec ua-lab-miles-repro-cpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --config /lab/configs/miles_smoke.json --run-name replay-miles-smoke --check-outputs
docker exec ua-lab-miles-repro-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --config /lab/configs/miles_smoke.json --run-name replay-miles-smoke
docker exec ua-lab-miles-repro-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --submit-only --config /lab/configs/miles_resume.json --run-name replay-miles-smoke
```

这里smoke先跑2个rollout，resume把**总终止轮数**推进到4，恢复同名字的model/Adam/LR，不是额外4轮。新run名同时重写save、load、debug dump和日志路径。已完成resume会返回`already_complete`，无剩余轮次不是失败；缺少checkpoint元数据则会拒绝静默回退base。

第一次GPU命令启动专用Ray；后续同一容器的`--submit-only`复用它。如果重建/重启容器导致Ray消失，下一次不能直接submit-only，应使用不带该参数的入口重新启动私有Ray。端口为head16879、dashboard18665、node/agent16880–16885、worker17000–17100、SGLang router18766；当前`_start_ray`只预检查部分端口，启动前仍需确认整组空闲。同一个两卡colocated Ray中按顺序运行这些jobs，不并发提交。

Miles原生command utility有进程清理逻辑；必须在专用Docker PID namespace内执行，并保留wrapper的`MILES_SCRIPT_EXTERNAL_RAY=1`。不要把`miles_run.py`迁移到宿主直接执行。

工具RL与4B对照各用新名字：

```bash
docker exec ua-lab-miles-repro-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --submit-only --config /lab/configs/miles_retool.json --run-name replay-miles-retool
docker exec ua-lab-miles-repro-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --submit-only --config /lab/configs/miles_retool_4b.json --run-name replay-miles-retool-4b
docker exec ua-lab-miles-repro-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --submit-only --config /lab/configs/miles_retool_4b_contract.json --run-name replay-miles-retool-4b-contract
docker exec ua-lab-miles-repro-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py \
  --submit-only --config /lab/configs/miles_retool_4b_adapter.json --run-name replay-miles-retool-4b-adapter
```

0.6B工具路线使用原生`multi_turn.generate`、`examples.retool_v2.tool_sandbox`的schema/executor/reward、qwen25工具解析、最多4轮、每轮512输出、1800上下文。4B adapter只做代码围栏解包和末尾表达式显示，再调用原执行器；配置显式`--debug-rollout-only`，没有optimizer更新。不要把历史0/32到adapter16/16说成训练提升。

### Miles验收应读哪些证据

| 路线 | 已有实测事实 | 新运行的验收重点 |
| --- | --- | --- |
| GSM8K smoke+resume | 共64真实samples，Adam1/2/3/4；后三次权重变化 | model/optimizer/LR恢复，连续checkpoint差分，实际非零梯度，rollout token/logprob/mask对齐 |
| 0.6B retool | 32samples、50次工具执行、7个正确且有stdout、两次非零更新 | 真实tool消息和stdout、最终答案、mask排除工具观察，参数确实变化 |
| 4B原工具 | 32samples全部reward−1、零梯度、权重不变 | 保留真实失败；原始生成经常把Python放进Markdown围栏 |
| 4B加强提示 | 16samples仍全部失败 | 不将改prompt当成修好执行器 |
| 4B adapter | rollout-only16/16、16次执行 | 只说明接口适配，明确无training checkpoint和optimizer更新 |

CPU分析自己的新run：

```bash
docker exec -e OMP_NUM_THREADS=4 ua-lab-miles-repro-cpu /lab/envs/miles/bin/python \
  /lab/scripts/miles_analyze.py --checkpoint-dir /lab/checkpoints/replay-miles-smoke \
  --logs /lab/logs/replay-miles-smoke --model-dir /lab/models/Qwen3-0.6B
```

工具run更换checkpoint/log目录；4B相应使用4B模型目录。分析器会写该run的`analysis.json`，所以只指向新实验目录。它只抽样q_proj参数，不能代表全部tensor逐位验收。

Miles原生`meta.json`中的global_step/micro_step在本pin中会留在0，应看`iteration`、rollout编号、实际Adam state.step和参数差分。`trajectory-N.jsonl`在reward前保存，其reward可能null；reward来自同sample的`rollout-N.pt`，角色消息/工具stdout则来自JSONL。将二者按sample index及occurrence配对，不能把字段缺失当作0次工具调用。所有outer命令stdout也应保存到新的run日志，以便将梯度、Ray job终态、begin/update/end/continue权重同步与checkpoint对应。

第一阶段完整证据索引在`results/miles-artifacts-manifest.json`、`configs/miles-sources.json`及`notes/miles.md`。宿主重建后Miles GPU未重新执行；这些记录是既有成功事实，不是对任意新驱动和新包组合的自动背书。

### 已定位的迁移缺口与处理范围

1. 只迁移models/和Python源码会遗漏SWE任务镜像、六题oracle结果、Claude binary和portable Mini；30B黑盒wrapper都会用到。
2. `blackbox-run-swe.sh`仅支持4b/9b，30B使用`run_large_blackbox.py`；不要在旧shell入口里猜测`30b`参数。
3. `blackbox-run-swe.sh`、部分toy工具有宿主`/path/to/uni-agent-lab`默认值；设`UNI_AGENT_HOST_LAB_ROOT`。30Bwrapper会从自身位置计算并显式传入。
4. 六题、扩展集、Terminal-Bench上层入口使用固定规范数据/control目录；`--name`通常只改变输出。需要完全独立的control链时用新lab，或显式使用下层接受路径参数的runner。
5. 不建议直接复制`sandbox-docker/`的overlay2数据目录作为跨机镜像迁移方式。按记录digest重新pull，或在daemon停止且满足Docker迁移条件时另行规划；本文命令采用digest pull。
6. Miles setup原依赖未完全固定、完整freeze包含镜像/local内容；37项overlay锁与immutable image已通过本机独立CPU冷安装及所选FSDP/core导入校验。`pip check`诊断和GPU未重验的范围仍须保留，不将导入通过写成全功能/新节点训练通过。
7. `miles_env.py`复用同名容器时未完整验证配置；用新名字并检查镜像/挂载。Miles的GPU和端口同时在容器参数与JSON中，必须协调一致。
8. HF权重有的由Docker root以0600生成；迁移工具必须能读取并保留其内容。新RL导出已用专用owner调整器处理，这与本页base模型/SWE/Miles资产不是同一项操作；不要把“目录可列出”当作大文件可读取。

本页命令的技术依据是实际脚本、锁文件和原始结果。正式重现应先通过baseline/oracle、依赖来源和数据指纹，再接受模型/训练结果；不要求随机模型轨迹与旧报告逐字相同，也不跳过失败记录。
