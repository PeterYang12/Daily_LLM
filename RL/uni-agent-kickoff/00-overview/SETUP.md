# 跨机器复现手册：Uni-Agent、Qwen3-32B/8B Agentic RL 与30B代码Agent

> 仓库阅读版：保留方法、步骤、结果和图表。源码链接为固定上游版本参考；未收录的原始实验文件仅以路径引用。 本目录不含完整实验工程或复现包；使用下文命令前需另行准备所引用的脚本、依赖与固定输入。`/path/to/...` 表示目标机器上需替换的目录。

这份手册对应2026-09-11至13日在本机实际执行的实验，重点是 **Docker + ROCm、Qwen3-32B与Qwen3-8B的128步全参数MemAgent RL、Qwen3-Coder-30B-A3B代码修复、Claude Code黑盒接入**，并补充Miles。它按准备、运行、验收和恢复顺序组织；所用源码、补丁、依赖锁与小型固定输入随复现包提供。

**先按所选实验完成环境与数据检查，再进入对应目录的复现步骤。** [32B](../03-memagent-32b-rl/REPRODUCE.md) 和 [8B](../04-memagent-8b-rl/REPRODUCE.md) 分别给出训练与评测路径；[Miles](../07-miles/REPRODUCE.md) 使用独立环境。下文的 32B 模型、服务名和容量预算属于 32B 路线，其他实验以各自手册为准。只运行 [Verdal 基础示例](../08-verdal-sandbox/REPRODUCE.md) 可直接使用 CPU Docker。整个手册不要求使用Anthropic模型：Claude Code案例使用真实CLI，其模型请求通过Uni-Agent Gateway指向本机Qwen。

**同一新lab要依次复现32B和8B时，有一项必须提前做：在任何训练开始前，先完成8B训练章第1–3节的模型/CPU准备，再完成8B评测章第1–3节的新节点登记。** 登记器刻意拒绝已有`runs/rl`历史；登记后可以先跑32B，再跑8B。只复现32B可以跳过8B准备。已经完成32B后才决定追加8B时，此登记路线需另解包空lab，不能删除旧训练历史来通过检查。

本机长训练的完成证据、最终分数与历史中断见[实验总结](RESULTS.md)、[32B完整结果](../03-memagent-32b-rl/REPORT.md)和[README](../README.md)。本手册描述复现方法，完成与否必须检查实际outcome及产物。跨机器的生成文本、补丁和分数不保证逐字相同；必须在目标机器重新建立自己的base对照，不能直接拿本机分数当作新机器的baseline。

本手册配套材料已在本机**独立空目录、独立CPU Docker容器**中实际重建：约1.4万个打包成员连续两遍校验通过，5份源码的revision/local diff/Git对象一致；CPU81项依赖锁、RL117项精确版本加1项TransferQueue commit通过；官方LocalSandbox的持久shell、编辑、读写和上传下载通过；durable配置与13项源码guard通过，GPU未映射或初始化。完整证据见隔离重建验收（原始文件：`results/reproduction-portability-validation.json`）。随后另完成了Miles37项overlay及所选FSDP/core模块的CPU冷重建，见Miles补充验收（原始文件：`results/miles-cold-rebuild-v3-20260912/audit.json`）；其完整项目声明仍有pip check缺项，未重验GPU训练。本机这些隔离验收均不能代替第二台物理GPU节点的128步验收。

本版补充8B训练/评测及Miles冷重建。8B新增路径也已从预检包在独立目录冷安装CPU81与RL118项依赖、验证模型结构和609项source/model/data hash，通过独立验收（原始记录：`results/reproduction-v2-8b-cold-20260912/audit.json`）。两种规模的训练源码与方法冻结记录原样保留。已交付v1压缩包保持原SHA；v2使用不同文件名，不覆盖v1。

## 阅读顺序与命令约定

| 阶段 | 要完成的事 | 结束时应看到什么 |
| --- | --- | --- |
| 1. 准备节点与复现包 | Docker、ROCm设备、目录、源码和输入校验 | 8卡可用；kit校验通过；没有外部Git worktree路径依赖 |
| 2. 建立CPU与训练overlay | 固定镜像、`--no-deps`精确安装、导入检查 | 保留ROCm torch；CPU容器无GPU；依赖版本正确 |
| 3. 准备模型与数据 | 固定HF revision，固定512/64/64划分 | 权重shards齐全；数据行和manifest SHA256一致 |
| 4. 做训练前对照 | 启动HIP0上的32B服务，运行external64和repeat | 各64题完成、无错误、模型root/runtime一致 |
| 5. 跑长训练与恢复 | 4卡FSDP2训练+2卡TP2 rollout；每8步完整保存 | model/Adam/RNG/data/TQ已commit；真实resume通过 |
| 6. 导出与最终对照 | 固定最终step128，BF16导出、重新载入、final+repeat | 128步完整证据、配对分析、格式与重复性诊断 |
| 7. 独立8B对照 | 独立run/container/overlay，按8B章节执行128步与匹配评测 | 完整native恢复、相同数据、单独base/final，成本用相同口径 |
| 8. 扩展案例 | 30B代码Agent、Terminal-Bench、Miles | 各自的环境正负对照、轨迹与结果，不混成RL收益 |

没有标注其他位置时，命令从**宿主机的lab根目录**执行。`docker exec ...` 后的Python、shell、文件路径在容器里执行，统一通过 `/lab` 访问本实验目录。宿主Python主要编排Docker和读写报告，训练与模型推理在Docker中执行。

文中的`repro-*`、`replay-*`是新实验名。先检查相应输出不存在；很多入口会主动拒绝覆盖已有结果。服务名和端口仍有固定约定，同一节点只运行一套这些服务名。新机器可以使用同名容器，本机已有训练期间不要直接照抄重启命令。

## 本机硬件、容量与时间预算

| 项目 | 实际记录 / 复现要求 |
| --- | --- |
| CPU架构 | x86_64；本机384 logical CPUs |
| GPU | 用户称MI355；初始驱动报告MI350X，实际8×gfx950，每卡约252GiB可见显存。按实际架构、设备与显存记录判断兼容性 |
| 宿主内存 | 本机约3TiB；没有验证小内存节点能原样运行完整配方。训练、rollout与CPU合并均需内存预算 |
| 训练GPU峰值 | 已观测每trainer rank约153–154GiB allocated、约164GiB reserved；不能把这套4-rank配方原样套到80GiB卡 |
| GPU布局 | HIP0：32B主评测；HIP1：Coder30B或32B稳定辅助评测；HIP2–7：4 trainer + 2 rollout |
| 模型盘 | Qwen3-32B约61.04GiB，Coder30B约56.89GiB；小模型可按选定案例另下载 |
| Hotpot源数据 | 本机顶层文件合计约2.79GiB；固定512/64/64输入约48MiB，随kit提供 |
| 原生完整检查点 | 单份约366.2GiB；保留旧commit直到新commit完成，两份峰值约732.4GiB |
| fresh磁盘门槛 | 当前入口要求完整断点卷至少868GiB可用，含两份370GiB预算及128GiB余量 |
| 推理导出 | 每份BF16 HF约61GiB；保留8/32/64/96/128五份需额外约305GiB；只保留最终导出则约61GiB |
| Docker镜像 | 基础推理镜像、独立daemon层与SWE任务镜像另外占空间，尤其是扩展29题；不要只按模型大小估磁盘 |
| 时间 | 当前32B普通step约160–240秒，完整保存另外约4–5分钟/8步，monitor另外耗时；128步是数小时到约十小时级工作，下载、重启及最终评测另计 |

容量数字用于预算，不是经过穷尽测试的最低要求。GPU换成MI300X或其他型号、宿主驱动变化、改成CUDA时，应先做该设备上的小规模32B验证，再冻结新协议；本次直接验收的是上述gfx950环境。

## 原实验配套材料：复现包与大资产

原实验手册对应 `reproduction/uni-agent-lab-kit-v2-20260913.tar.gz`（未随本仓库收录） 及相邻的 `.manifest.json`；原 `uni-agent-lab-kit-20260912.tar.gz` 是保留不变的v1。v2包内包含：

- `REPRODUCE.md`、相关说明、全部lab脚本、配置、补丁和依赖锁。
- 5份实际源码快照及Git对象：Uni-Agent、CPU verl、训练verl、Miles、SGLang-Miles。训练verl的绝对worktree指针已在打包时改为独立Git目录，源码内容不改；每份仓库的revision和本地diff都有校验。
- 固定MemAgent 512/64/64输入、SWE六题及expanded29的parquet/metadata、Miles小型固定输入，以及镜像与模型revision清单。
- Git配置只包含公开上游地址。复现包不携带用户认证目录或API密钥。

模型权重、Docker镜像层、Python venv、训练检查点和历史run通过各自步骤准备。这样可在目标机器重新建立容器与环境；直接复制宿主venv容易留下解释器或editable路径依赖。若目标机不能访问镜像/HF服务，可另外转移`docker save`产物和完整固定revision模型目录，仍需核对本页的版本与文件清单。

新机器先把包和相邻manifest放到一个下载目录。下面只核验下载包并解压到**空lab目录**，不调用GPU：

```bash
# 宿主机；这里的目录要位于空间足够的数据盘。
export LAB_ROOT="/data2/$(id -un)/uni-agent-lab"
export LAB_KIT="$PWD/uni-agent-lab-kit-v2-20260913.tar.gz"

python3 - "$LAB_KIT" <<'PY'
import hashlib, json, sys
from pathlib import Path
p = Path(sys.argv[1])
m = json.loads(Path(str(p) + '.manifest.json').read_text())
h = hashlib.sha256()
with p.open('rb') as f:
    while b := f.read(1024 * 1024):
        h.update(b)
assert h.hexdigest() == m['sha256']
assert p.stat().st_size == m['bytes']
print('Archive SHA256 and size match:', m['sha256'])
PY

python3 - "$LAB_ROOT" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.mkdir(parents=True, exist_ok=True)
assert not any(p.iterdir()), 'Choose an empty lab directory; do not merge into an existing run'
PY

tar --no-same-owner -xzf "$LAB_KIT" -C "$LAB_ROOT"
cd "$LAB_ROOT"
python3 scripts/verify_reproduction_kit.py --root "$LAB_ROOT"
```

校验器检查每个打包文件的SHA256，以及五份源码的HEAD、tracked diff、独立Git目录和公开origin。包的hash只能与一起交付的manifest核对，它不是独立签名。验证后新增日志、venv或下载模型是正常行为；若修改了包内源文件，重新校验会按设计报差异。

```bash
# 宿主机；目标机器尚不存在ua-lab-cpu，且lab里尚没有CPU venv。
CPU_CONTAINER=ua-lab-cpu bash scripts/bootstrap_reproduction_cpu.sh

# 成功产物：
cat results/bootstrap-cpu-verification.json
# 安装细节：
tail -n 50 logs/bootstrap-cpu-install.log
```

`bootstrap_reproduction_cpu.sh`面向已经解包源码的新目录；`start_cpu_container.sh`用于恢复已有overlay；旧`cpu-reproduce.sh`则会自己展开旧源码归档，不能拿来覆盖已经解包的kit。三者的用途不同，下文逐项展开。

## 复现结果怎样记录

为新机器保留独立的运行记录，至少包含：GPU架构和可见显存、驱动/内核、镜像digest、源码revision和patch、依赖lock、模型revision、数据manifest、完整启动命令、每次attempt、原始日志、最终checkpoint与base/final逐题结果。

完成标准分开检查：服务可响应、Agent能执行工具、verifier有效、轨迹可训练、optimizer确实更新、断点真实恢复、最终任务表现提高，是不同的证据。某一项通过不能代替其他项。后文给出每一层的实际入口与验收文件。

## 换一台ROCm八卡节点：环境、文件与推理服务的复现细节

本节说明原实验所用脚本、Git 状态、依赖锁和固定输入。命令面向**另一台空节点/新的空lab目录**；当前原实验目录已有训练与已冻结结果，不应在其中重复执行初始化。本文没有启动新GPU服务或final评测。

新入口`build_reproduction_bundle.py`与`bootstrap_reproduction_cpu.sh`用于重建环境：先打包/解包完整lab材料，再新建CPU overlay，解决旧CPU bootstrap与已解包源码冲突的问题。已在独立解包目录重建CPU与RL overlays：CPU81项锁、RL117项精确版本加1项TransferQueue git pin通过，官方LocalSandbox demo通过；源码包连续两遍完整校验通过。它们是本机隔离冷重建，尚不等于另一台物理GPU节点完成训练。最终包名、SHA及详细验收以总文档和其新记录为准。

2026-09-12又完成独立Miles CPU冷重建：从没有`envs/miles`的目录安装37个精确pins，固定镜像、ROCm torch、源码优先级、所选FSDP/core及补丁模块导入通过。`pip check`有保留的依赖声明缺项；此次未启动模型、Ray或GPU训练。新增记录不改变已经交付的v1 kit归档及其SHA，后续living文档与新版kit由总文档单独标识。

### 1. 复现需要哪些层，为什么只clone Uni-Agent不够

| 层 | 必需内容 | 不包含什么 |
| --- | --- | --- |
| 宿主 | x86_64 Linux、可工作的AMD内核驱动、八张目标GPU、Docker daemon与CLI、足够本地盘 | 应用Docker镜像不安装宿主amdgpu驱动 |
| 固定Docker镜像 | Uni-Agent/CPU/vLLM/RL共用固定ROCm image；任务环境用独立dind image | 镜像不包含本次模型、数据、lab脚本及训练checkpoint |
| 代码与lab材料 | 五个固定源码版本、工作树patch、`scripts/`、`configs/`、`patches/`、依赖锁、输入清单、协议材料 | 上游Uni-Agent仓库没有本lab的ROCm兼容、durable保存恢复、gate、推理复测脚本 |
| 模型与数据 | 固定HF revision的权重和tokenizer；原始/派生数据与manifest | 源码包不包含约118GiB的两份30B级模型 |
| 任务环境 | SWE case镜像、portable Mini运行时、固定Claude二进制、baseline/gold正负控制 | 主机Docker里有镜像不等于独立sandbox daemon里也有 |
| 恢复既有训练 | 完整run目录和外置native checkpoint目录、独立committed指针、attempt和回滚记录 | 只有HF权重不能恢复Adam、scheduler/RNG、DataLoader与TQ |

当前核对清单是reproduction-environment-inventory.json（原始文件：`results/reproduction-environment-inventory.json`）：39项关键文件hash和4个实际已应用patch的reverse-check。打包器会另外生成更完整的`reproduction/kit-manifest.json`；不要把本页39项清单误当成整个lab的文件清单。

### 2. 宿主与磁盘前检

本机最初驱动工具返回MI350X/gfx950，用户将节点称为MI355；实验记录按实际返回值保存。每卡约252GiB可见显存，约3TiB主内存、384 logical CPUs。后续宿主变化后设备名字可能为空，但gfx950和GPU计算已验证。当前host kernel是`6.8.0-38-generic`、amdgpu module version是`7.1.0.31500000`；image内torch的HIP版本是`7.2.53211`。这些字段是不同层，不应写成一个“ROCm版本”。

新机器先记录，不要靠型号口述猜测：

```bash
uname -a
id
docker version
docker info --format '{{.DockerRootDir}} {{.Driver}} {{.CgroupVersion}}'
cat /sys/module/amdgpu/version
ls -l /dev/kfd /dev/dri
lspci -nn
```

本方案使用rootful Docker和有权限映射`/dev/kfd`、`/dev/dri`的账号。`docker ps`必须能正常访问daemon。AMD驱动已存在是前提；安装/升级宿主驱动取决于节点镜像与管理员配置，不由下面的Python overlay脚本完成。ROCm PyTorch仍使用`torch.cuda`命名空间，这是API名称，并不表示装了NVIDIA CUDA torch。

磁盘按**实际写入卷**检查：

- Qwen3-32B原始BF16约61.02GiB，Coder30B约56.87GiB；若加历史0.6B/4B/9B，另需约27GiB。
- 一份32B full native checkpoint实测约366.2GiB，包含FP32 model、Adam双moments及小型状态。保留一份时，新旧仍须共存直到完整commit。launcher首次启动门槛是868GiB可用空间；resume门槛为498GiB，不能只预留一份大小。
- 每份native合并BF16 HF约61GiB。controller计划保留8/32/64/96/128五个导出，约305GiB；导出器单次还要求数据卷至少80GiB可用。
- Docker image layers、SWE任务镜像、Python缓存、原始Hotpot与轨迹日志另占空间。SWE扩展镜像按唯一layer统计，本次额外预算150GB；不能把`docker images SIZE`简单相加当真实占盘。
- 如果所有内容在一个卷，建议按2TiB以上可用空间规划主实验；加入全部历史权重、Miles镜像或更多SWE任务要继续增加。最终以`df`和launcher实际空间检查为准。

```bash
df -h /data2 /path/to/uni-agent-checkpoints
```

### 3. 路径、UID/GID和容器约定

主机lab默认是`/path/to/uni-agent-lab`，容器一律挂成`/lab`。模型、venv、脚本内部路径大多基于`/lab`，保留这个容器路径能减少迁移改动。

外置native checkpoint卷是硬编码的`/path/to/uni-agent-checkpoints`，训练容器必须把该卷**按同一绝对路径**挂进去。`run/checkpoints`是绝对symlink，指向该卷的run子目录。只复制lab、只挂`/lab`会得到失效symlink；CPU权重审计和推理需要的是lab内`exports/.../huggingface`，不是该外置卷。

| 项目 | 当前行为 |
| --- | --- |
| `LAB_ROOT` | 多个host shell入口支持；`lab_services.py`等Python入口按脚本真实路径推导root |
| CPU源码bootstrap | `bootstrap_reproduction_cpu.sh`支持`LAB_ROOT`、`CPU_CONTAINER`；拒绝已有venv或同名container |
| CPU恢复 | `start_cpu_container.sh`要求已有`envs/cpu`，并要求主机路径在`/data2/`下；不是空节点bootstrap |
| RL run名/container | `RL_RUN_NAME`、`RL_CONTAINER_NAME`可指定；durable checkpoint卷路径仍固定 |
| GPU选择 | `HIP_VISIBLE_DEVICES`使用HIP index；不要拿`rocm-smi`展示顺序直接代替。训练入口设置HIP并清除ROCR；兼容patch保证verl收窄CUDA selector时HIP同步 |
| 服务名/端口/部分run名 | `lab_services.py`、primary/stable profile和final controllers有固定名称，不能在同一daemon并行起同名第二套lab |
| owner label | 部分控制器显式检查`owner=YOUR_LAB_OWNER`及purpose标签；这是本recipe的container归属标签，实际文件权限还取决于真实UID/GID |

`YOUR_LAB_OWNER` 是文档占位符，实际取值需与所用控制器的归属校验一致。迁移时不能只修改容器标签而不更新对应校验规则。

容器默认root，生成文件可能root-owned。Docker native merger曾生成宿主普通用户不可读的HF safetensors，host gate实际因此失败；新controller已经为该导出目录处理读取权限。迁移后应确认当前账号能读HF shard、JSON和日志。不要把权限问题当模型或checkpoint损坏；只处理本lab相应目录，不改别人的Docker数据。

新机器若沿用原容器路径和默认run名，可在新的空lab中使用同样recipe。若改主机checkpoint路径，必须在**新副本**统一检查shell常量、container bind、launcher路径校验及absolute symlink。若改run名/端口，primary/stable profile、controller、final HF路径与协议也须一致；现有自动final入口是这一次实验的固定入口，并非任意run的通用调度器。

原机器已冻结的14项辅助源文件、primary输入与既有protocol JSON不能为迁移而修改。新节点应产生自己的runtime、baseline与注册记录；不要复制旧机器的baseline/provenance来冒充新实验结果。driver/kernel变化会被严格配对检查拒绝，greedy也不能保证跨机器相同输出。

### 4. 固定镜像与Docker启动参数

```bash
docker pull vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa
docker image inspect vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa --format '{{.Id}}'
# 应为 sha256:33b992ce0f367784daf23c535ed63af3703e55ade7f5016a08e9c41a24a7a3b9

docker pull docker@sha256:aa3df78ecf320f5fafdce71c659f1629e96e9de0968305fe1de670e0ca9176ce
```

| 容器用途 | 实际关键参数 |
| --- | --- |
| CPU driver | 无GPU devices；`--network host --cpus 8 --memory 32g --shm-size 2g`；挂lab、独立`/tmp`和`/root`缓存、只读host Docker CLI；设置独立`DOCKER_HOST` |
| 32B/Coder30B推理 | `/dev/kfd`、`/dev/dri`、video/render groups；`--ipc=host --network=host --security-opt seccomp=unconfined`；memlock无限、stack67108864；24CPU/160GB host RAM上限；HIP0或1 |
| durable RL | `--init`、两个GPU device目录、video group、host IPC/network、seccomp unconfined、SYS_PTRACE、memlock无限；lab和外置checkpoint双挂载；HIP2–7 |
| sandbox daemon | `--privileged --network host`；入口必须`/usr/local/bin/dind dockerd`；独立socket/data-root；overlay2；关闭bridge/iptables/ip6tables/ip-masq |
| 每题sandbox | CPU容器、独立任务文件系统；本机使用host network到本地模型/Gateway，无GPU映射 |

不要给ROCm容器使用NVIDIA的`--gpus all`替代device映射。新节点video/render的数值GID可能不同；核对device权限和container groups。当前image以root运行并使用上述已验证参数，不能把它直接当成rootless Docker或普通用户容器的验收。

可在已预留的空节点做一次轻量GPU计算检查；这不是模型训练验收：

```bash
docker run --rm -i --device=/dev/kfd --device=/dev/dri \
  --group-add video --group-add render --ipc=host --network=host \
  --security-opt seccomp=unconfined -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  --entrypoint python \
  vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa - <<'PY_GPU'
import torch
assert torch.version.hip and torch.cuda.device_count() == 8
for index in range(8):
    value = torch.eye(32, device=f'cuda:{index}', dtype=torch.bfloat16)
    torch.testing.assert_close(value @ value, value)
    print(index, torch.cuda.get_device_properties(index))
PY_GPU
```

离线迁移Docker layer可以使用`docker save/load`，但load未必恢复wrapper需要的`repo@digest`引用。必须重新确认`docker image inspect <固定digest>`成功且image ID正确；必要时联网pull同一digest补齐manifest引用。一个同名tag不足以证明镜像完全相同。不要复制整个宿主`/var/lib/docker`，其中可能还有别人的容器。

### 5. 代码包、五个checkout与四个patch

推荐拿root生成的复现kit解包，而不是只复制几个shell文件。打包器包含当前tracked源码和Git objects/index/shallow，将linked worktree转换为独立`.git`，只在archive里写public origin；源仓库不变。它拒绝外部Git alternates和未审阅的外部symlink。源码包不含venv、模型、训练checkpoint、home/auth状态、Docker data-root和runtime sockets。

已完成总手册开头的解包/kit校验后，本章沿用同一个`LAB_ROOT`，不再解包到第二个目录。独立阅读本章时，先完成`REPRODUCE.md`开头的包校验与解包步骤。

```bash
: "${LAB_ROOT:?先完成总手册开头的LAB_ROOT设置与kit解包}"
UA_LAB="$LAB_ROOT"
cd "$UA_LAB"
```

解包后还应按`reproduction/kit-manifest.json`逐文件核对SHA，并核对以下HEAD与本地改动。bundle里patch已应用，不要再次重复apply或reset掉本地改动。

| checkout | Public origin | HEAD |
| --- | --- | --- |
| `src/uni-agent` | `https://github.com/verl-project/uni-agent.git` | `472c875a97f9a2764c81a6ec7581167632bd8bcc` |
| `src/verl`，CPU/debug | `https://github.com/verl-project/verl.git` | `10db40d0da4d59150bb389960b77585f81a89b8d` |
| `src/verl-rl`，实际训练 | 同上 | `a9f2985159536a607211dcac730d3f5d55028950` |
| `src/miles`，可选 | `https://github.com/radixark/miles.git` | `50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9` |
| `src/sglang-miles`，可选 | `https://github.com/sgl-project/sglang.git` | `32839114c4ada2ae237581405a8ff39dc0db9e25` |

原lab的Uni-Agent/verl origin指向原主机`/path/to/...`，`verl-rl`还有linked-worktree路径问题；裸拷贝`.git`指针不能跨机工作。若不用kit，必须分别从上表public URL clone、fetch固定commit并detach checkout，再应用下表patch；不要用CPU的verl版本代替训练gitlink。

| patch | 应用到 | 作用 |
| --- | --- | --- |
| `patches/uni-agent-docker-stdin.patch` | `src/uni-agent` | Docker write_file走stdin，解决大文件argv E2BIG |
| `scripts/rl_verl_rocm.patch` | `src/verl-rl` | verl收窄CUDA_VISIBLE_DEVICES时同步HIP selector |
| `patches/miles-sglang-triton-compat.patch` | `src/miles` | pinned SGLang新kernel路径与BF16 scale参数 |
| `patches/miles-fsdp-no-megatron.patch` | `src/miles` | FSDP/unsharded vocab使用原生CE；不能外推任意TP |

```bash
git -C src/uni-agent rev-parse HEAD
git -C src/verl-rl rev-parse HEAD
git -C src/uni-agent apply --reverse --check "$UA_LAB/patches/uni-agent-docker-stdin.patch"
git -C src/verl-rl apply --reverse --check "$UA_LAB/scripts/rl_verl_rocm.patch"
git -C src/miles apply --reverse --check "$UA_LAB/patches/miles-sglang-triton-compat.patch"
git -C src/miles apply --reverse --check "$UA_LAB/patches/miles-fsdp-no-megatron.patch"
```

对于手工建立的干净clone，先`git apply --check`，再去掉`--check`应用；上面的`--reverse --check`是**确认已经应用**的只读检查。

Durable能力主要来自lab文件，不能只带四个patch：至少保留`rl_durable_launch.py`、`rl_durable_checkpoint.py`、`rl_durable_rank_audit.py`、`rl_checkpoint_audit.py`、`rl_checkpoint_lease.py`、`rl_export_committed.py`、`live_durable_status.py`、两个final controller及`final_model_gate.py`，对应configs和maintenance/source-guard材料也要带。推理辅助还依赖`stable_memagent_profile.py`、`run_stable_memagent.py`、`analyze_stable_memagent.py`。最稳妥的包边界是完整`scripts/ configs/ patches/ notes/`，不手工挑掉看似不重要的helper。

### 6. CPU与RL overlay：保留image内ROCm torch

新kit的初始化入口是`bootstrap_reproduction_cpu.sh`，在总手册开头执行一次即可。本章接着检查它已经产生的记录，不重复创建同名CPU容器或venv：

```bash
test -x "$UA_LAB/envs/cpu/bin/python"
cat "$UA_LAB/results/bootstrap-cpu-verification.json"
```

如果独立阅读本章且尚未初始化，先在已解包的同一`LAB_ROOT`执行总手册开头的bootstrap命令；已有venv/container应走恢复流程。

它创建`--system-site-packages`的`/lab/envs/cpu`，以`--no-deps`安装81项CPU覆盖层，再以`--no-deps -e`安装已解包Uni-Agent和CPU/debug verl，执行核心导入、81版本一致性及GPU不可见检查。它不会下载模型、运行SWE/RL或验证GPU。

旧`cpu-reproduce.sh`是另一种CPU-only路线：从两个git archive解出源码到**全新空目录**，再建venv。它不能用于已有src的kit目录；其Uni-Agent archive还不含后加的Docker stdin patch，需在新副本另行应用。`start_cpu_container.sh`则仅恢复已有overlay。

训练overlay由以下脚本建立，依赖已在正确固定镜像内创建的训练容器：

```bash
docker exec ua-lab-rl-durable bash /lab/scripts/rl_setup_env.sh
```

`rl_setup_env.sh`使用`uv venv --system-site-packages --python /usr/bin/python3`与`uv pip install --no-deps -r configs/rl_requirements.lock`，锁118项，其中Transformers是5.9.0、Ray2.54.1，TransferQueue固定git commit `fc33c979db6bd661802f6af458e75e850055fb20`。Torch不在该覆盖锁里；它必须继续来自固定ROCm image。CPU/独立推理使用image中的Transformers5.16.1，不能把两个环境版本合成一个。

`rl_run.sh`显式设置`PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl-rl`、选择RL venv、关闭W&B、设置HIP列表。RL源码通过PYTHONPATH进入；仅pip版本字符串无法区分两个verl checkout，应同时打印module.__file__与Git HEAD。

不要在这个环境直接执行无约束`pip install -U torch`、`pip install verl[...]`、`uv sync`或用普通CUDA镜像替代ROCm image。`--no-deps`不会自动解决缺依赖，完整锁和固定base image必须一起使用。`transferqueue @ git+...`还需要Git及对应commit可下载；版本锁不是离线wheelhouse。

可选环境各自独立：`rl-swe`用29项覆盖锁并用`.pth`引入RL overlay；`rl-plots`用6项锁；`report-setup.sh`在`envs/report`装matplotlib3.10.8，不修改CPU env；离线HTML构建工具在`envs/browser-audit`。只有看已有HTML无需安装browser工具。

### 7. 固定模型与Hotpot数据

在CPU Docker内准备30B主模型，不在宿主临时Python里安装一套依赖：

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/download_assets.py \
  --models 32b coder30b --hotpotqa train eval

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/download_assets.py \
  --models 32b coder30b --hotpotqa train eval --verify-only
```

| 资产 | revision |
| --- | --- |
| Qwen/Qwen3-32B | `9216db5781bf21249d130ec9da846c4624c16137` |
| Qwen/Qwen3-Coder-30B-A3B-Instruct | `b2cff646eb4bb1d68355c01b18ae02e7cf42d120` |
| Qwen/Qwen3-0.6B，可选历史case | `c1899de289a04d12100db370d81485cdf75e47ca` |
| Qwen/Qwen3-4B-Instruct-2507，可选 | `cdbee75f17c01a7cc42f958dc650907174af0554` |
| Qwen/Qwen3.5-9B，可选 | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| BytedTsinghua-SIA/hotpotqa | `27275ff4fee67ac0acb6478e405e7ac07efbdc1a` |

下载器包含JSON、safetensors、tokenizer/model文件和`*.jinja`。尤其9B缺chat_template.jinja会改变/破坏请求格式；不能只带权重。保留各模型目录内的revision metadata，例如`models/Qwen3-32B/.cache/huggingface/download`，以及`results/assets-manifest.json`。当前`--verify-only`验证必需文件与可用revision metadata，**不完整hash每个大权重payload**；它还会合并写assets-manifest，不能把它称纯只读命令。

Hotpot下载包含`hotpotqa_train_32k.parquet`、`hotpotqa_dev.parquet`和文本版50/200/800/3200/6400文档档。12800档是整数ID context，当前缺验证过的解码链路，不用于本报告的文本实验。

Kit已包含固定`data/rl_memagent_512_64_64`派生数据：512 train、dev0–63 monitor、dev64–127 external。若要独立重新生成，先完成RL env，再在**新的空输出目录**执行：

```bash
docker exec -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl-rl \
  ua-lab-rl-durable /lab/envs/rl/bin/python /lab/scripts/rl_prepare_mem_data.py \
  --train-rows 512 --val-rows 64 --external-rows 64 --external-offset 64 \
  --out-dir /lab/data/replay-rl-memagent-512-64-64 --tokenizer /lab/models/Qwen3-32B
```

该preparer会写文件，没有非空目录保护。先对比row hashes、context保留情况、chunk counts及manifest，再决定是否作为新实验输入；不要原地覆写正在用的冻结parquet/JSON。`rl_durable_reproduce.sh`假设这套数据已准备好，并不代替本步骤。

### 8. CPU、独立sandbox daemon与正负控制验收

```bash
python3 scripts/lab_services.py start sandbox --timeout 60
docker --host "unix://$UA_LAB/run/docker.sock" info
```

Daemon把同一lab按主机绝对路径挂入，是为了让driver提交的host bind路径在dind内也能解析；socket另挂`/ua-run`。CPU driver的`DOCKER_HOST=unix:///lab/run/docker.sock`与`UNI_AGENT_HOST_LAB_ROOT=$UA_LAB`必须相配。重启dockerd会重建socket，`lab_services.py`在ready后恢复当前GID/660权限。不要把主机默认Docker socket当作任务daemon使用。

Dind必须经`/usr/local/bin/dind dockerd`处理nested cgroup；直接把entrypoint写成dockerd曾导致失败。此daemon关闭bridge/iptables，所以任务sandbox显式host network。底层shell只等socket不构成完整ready证据，最终看`docker info`。

CPU overlay不等于所有OS工具都已安装。LocalSandbox的持久shell路径可能按需安装tmux；冷容器要有可用apt源。先确认本容器绑定的scratch `/tmp`权限，本机最初的apt/tmux失败就发生在这里：

```bash
docker exec ua-lab-cpu chmod 1777 /tmp
docker exec ua-lab-cpu bash -lc 'command -v tmux || true; stat -c "%a %n" /tmp'
```

这只改变该lab绑定的scratch目录；安装了额外OS包时，应把包版本和命令日志一起保存。


CPU核心测试示例，新JUnit文件名：

```bash
docker exec ua-lab-cpu bash -lc '
  export PATH=/lab/envs/cpu/bin:$PATH
  export PYTHONPATH=/lab/src/uni-agent:/lab/src/verl
  python -m pytest tests/uni_agent -m "cpu and level0" \
    --ignore=tests/uni_agent/deployment/test_host_runtime.py \
    --junitxml=/lab/results/cpu-level0-new-node.xml -q
'
```

本机历史结果685 passed、12 deselected；排除的deployment测试引用固定commit已删除模块。新机必须记录自己的执行结果，不能把历史XML算成新测试。

Sandbox demo需要独立daemon内有固定Python镜像：

```bash
docker --host "unix://$UA_LAB/run/docker.sock" pull \
  python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

docker exec -e SANDBOX_IMAGE=python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea \
  ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/sandbox-docker-demo.py

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_docker_write_file.py \
  --output /lab/results/docker-write-file-new-node.json
```

最后一项包括真实16MiB往返、二进制/UTF8/path字面量、mode/symlink行为；原始未修实现的E2BIG控制也保留。若cache绑定的`/tmp`权限导致apt/tmux失败，应在该实验目录内确认1777，而不是改整个宿主/tmp。

SWE六题输入和image digest来自`results/cpu-reproduction-inputs.json`；新预处理器固定SWE-bench Verified revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`。

```bash
bash scripts/replay_swe.sh prepare
bash scripts/replay_swe.sh check
```

`prepare`按digest拉到独立daemon再恢复parquet要求的tag。`check`只是文件/镜像存在检查；baseline/gold实际判题在后文代码案例章节执行一次，本环境章节不重复生成结果。扩展29题使用`results/swe-expanded-v1/validated-final.parquet`和final-manifest的image记录；必须把相应镜像装入同一daemon，保持3题排除规则，不按新模型分数筛题。

扩展29题只按已冻结manifest的`usable`选择镜像，不重新按模型分数筛选。可在新lab用结构化参数安装其固定digest并核对image ID：

```bash
python3 - <<'PY_IMAGES'
import json, subprocess
from pathlib import Path
root = Path.cwd().resolve()
daemon = ['docker', '--host', f'unix://{root}/run/docker.sock']
manifest = json.loads((root/'results/swe-expanded-v1/final-manifest.json').read_text())
for row in manifest['records']:
    if not row['usable']:
        continue
    subprocess.run(daemon + ['pull', row['repo_digest']], check=True)
    subprocess.run(daemon + ['tag', row['repo_digest'], row['image']], check=True)
    actual = subprocess.check_output(daemon + ['image', 'inspect', row['image'], '--format', '{{.Id}}'], text=True).strip()
    assert actual == row['image_id'], row['instance_id']
PY_IMAGES
```

黑盒driver默认读`results/swe-oracle-six/<instance>/result.json`。Kit不复制历史benchmark runs，新机需先生成该布局，或显式传底层driver的`--oracle-dir`；`replay_swe oracle`的时间戳目录不会自动变成该固定目录。高层`run_large_blackbox.py`没有oracle-dir选项。后文代码案例章节用`python3 scripts/replay_swe.py oracle --output results/swe-oracle-six`生成该规范布局，同时保存baseline控制。本章不提前执行第二份同名oracle，以免后文因输出目录已存在而中止。独立重跑控制时使用新目录，并显式传给底层driver。


### 9. Claude与portable Mini运行时

主模型调用都走本地Qwen/Gateway，不需要为了这些case填写Anthropic模型凭据。当前Claude native binary为2.1.236，缓存文件`cache/blackbox-claude-2.1.236`约335MB，SHA256：

`6c8818fa22187aa555c242be4abbacc44d6b71a32ac9631ee7b2b5d12f51f752`

可以迁移该二进制并核对hash，或从官方版本URL下载同一linux-x64文件后再验证；旧`blackbox-claude-install-upstream.sh`会先取latest installer，即使传目标版本也不是完全固定下载链路。不要把它当成无需复核的byte-identical安装器。CPU容器内把固定binary链接到`/root/.local/bin/claude`并运行`--version`；实际case使用lab driver配置本地Gateway。

新CPU容器中尚无该文件时，固定下载与核验示例：

```bash
docker exec ua-lab-cpu bash -lc '
  set -euo pipefail
  mkdir -p /lab/cache /root/.local/bin
  test ! -e /lab/cache/blackbox-claude-2.1.236
  curl --retry 3 -fL https://downloads.claude.ai/claude-code-releases/2.1.236/linux-x64/claude \
    -o /lab/cache/blackbox-claude-2.1.236
  printf "%s\n" "6c8818fa22187aa555c242be4abbacc44d6b71a32ac9631ee7b2b5d12f51f752  /lab/cache/blackbox-claude-2.1.236" | sha256sum --check
  chmod +x /lab/cache/blackbox-claude-2.1.236
  ln -s /lab/cache/blackbox-claude-2.1.236 /root/.local/bin/claude
  /root/.local/bin/claude --version
'
```

已从源机复制该文件时只核对hash，不覆盖下载。这里没有启动Claude模型会话。


Mini工具环境使用python-build-standalone 3.12.13/20260602，archive SHA：

`191b5188b42886fb8a14968d714571e8f3d1cef92ac7ad4c7e24cc4d0929b194`

```bash
docker exec ua-lab-cpu bash /lab/scripts/blackbox-build-portable-mini.sh
```

脚本校验archive，按`results/blackbox-mini-portable-freeze.txt`用`--no-deps`安装，再复制固定Uni-Agent的`examples/mini_swe_agent/run_agent.py`。缺freeze时会走传递依赖解析，不能称精确复现。只复制宿主venv不能代替这个会被挂入SWE任务容器的portable运行时。

### 10. Primary、Coder30B与stable推理启动顺序

| profile | 容器 | HIP | endpoint | 核心设置 |
| --- | --- | ---: | --- | --- |
| primary32B | ua-lab-infer32 | 0 | 18083/v1 | BF16/TP1/eager/16384、hermes、qwen3、非thinking、max_num_seqs=16 |
| Coder30B | ua-lab-coder30 | 1 | 18082/v1 | BF16/TP1/eager/65536、qwen3_coder、max_num_seqs=16 |
| stable32B辅助 | ua-lab-infer32-stable | 1 | 18084/v1 | primary数值预算，加VLLM_BATCH_INVARIANT=1，实际TRITON_ATTN；prefix仍开、max_num_seqs=16 |
| 旧4B/9B | ua-lab-infer / ua-lab-infer9 | 0 / 1 | 18080 / 18081 | 历史小模型case，启动前先检查GPU资源 |

HIP1的Coder/9B与stable会争用同一GPU；不要一次启动所有服务。主线六卡RL使用2–7，两个独立评测槽是0/1。

```bash
python3 scripts/lab_services.py status
python3 scripts/lab_services.py start 32b --timeout 600
curl --fail http://127.0.0.1:18083/health
curl --fail http://127.0.0.1:18083/v1/models
```

`start_*_container.sh`通常只创建`sleep infinity`容器；`lab_services.py start`才继续启动vLLM并等ready。2026-09-12曾出现container一直Up但所有模型进程被终止、端口拒绝连接，所以必须检查实际进程和API，而不只看`docker ps`。

Primary新机base与repeat应各用新输出，实际model root必须是base：

```bash
python3 scripts/run_large_memagent.py --name base-durable \
  --expected-model-path /lab/models/Qwen3-32B --concurrency 8
python3 scripts/run_large_memagent.py --name base-durable-repeat \
  --expected-model-path /lab/models/Qwen3-32B --concurrency 8
```

以上固定名字只能在没有旧输出的新lab使用。若只是当前lab重跑，选新namespace并按总文档登记新配对，不覆盖原结果。

Stable不是把`--base-url`改一下就够了：原primary wrapper写死18083与primary container。主通用路线只依赖自己的primary base/final，不要求stable。若选择原固定run/phase名的可选自动化路线，应在任何pilot/训练history之前按[新节点stable注册章节](../03-memagent-32b-rl/REPRODUCE.md#可选替代路线新节点注册stable辅助与固定名称自动收尾)执行新增`register_new_node_stable.py`；它可在显式执行模式准备owned HIP1服务、记录实际argv/env/backend/runtime，并保留kit协议模板。不要借旧probe结果目录冒充新节点服务验收。

可选注册器要求本节点的primary64与repeat都完整且晚于kit创建；不存在旧stable/final结果和controller。随后才运行独立`run_stable_memagent.py`与`analyze_stable_memagent.py`。原机器协议对host runtime和历史baseline hash有严格绑定，直接复制旧JSON后硬跑会被拒绝。注册器不修改14项冻结输入，不按分数挑结果；registered JSON完成后也不能改status，因为已有provenance引用其hash。主通用手工final章节绕开本机专用controller，不依赖该可选协议。

本机stable62次固定输入与64题两遍最终response均一致，是此环境下的实测；新机器需重做自己的检查。不同mode会改变数值路径，不能挑较高分覆盖primary。完整辅助命令和验收见[stable记录](../03-memagent-32b-rl/details/32b-stable-auxiliary-evaluation.md)。

### 11. Miles可选部分的迁移

registry核验已补齐匹配原历史image ID的digest，随后本机独立CPU冷重建成功重新pull该镜像：

`lmsysorg/sglang-rocm@sha256:b82c13f1ab16690ba00974d1c38885e9934665d13a3ec1d2a6a615ae3ec072cd`

对应config image ID为`sha256:334898a4b0cfbecbf18d788a0687011cfbb2d817e8e9b5ea694717308cd1de67`。见registry pin审计（原始文件：`results/miles-image-registry-pin-audit.json`）与独立CPU冷重建审计（原始文件：`results/miles-cold-rebuild-v3-20260912/audit.json`）。后者覆盖本机全新overlay安装与CPU导入，不等于另一台物理GPU节点的数值或训练验收。

Miles与Uni-Agent使用不同image/Python/torch组合，不能共用venv。Miles基于Python3.10、torch2.9.1 ROCm7.2，`00_sglang_miles.pth`把`src/sglang-miles/python`放在base包前面，原image native库仍被复用。该SGLang源码的build依赖曾试图安装torch2.13：即便`pip install --no-deps -e`，隔离build环境仍会解析build依赖，因此本lab使用源码路径overlay。

当前新增`configs/miles_overlay_reproduction.lock`从真实overlay提取，不含base torch/SGLang和editable Miles；按注释用`--no-deps`安装在固定image上。旧`miles_setup.py`的`miles_requirements.txt`存在范围版本，属于最初环境建立流程，不能冒充完整依赖锁。完整包清单、源码overlay与两个patch必须一起保留。

实际冷重建在`/path/to/uni-agent-reproduction-check-v3-20260912`完成，37/37 pins来自新venv。Python为3.10.12，torch运行时为`2.9.1+rocm7.2.0.git7e1940d4`，其distribution metadata另带`.lw`；二者均未因安装而变化。`sglang.__file__`指向`/lab/src/sglang-miles/python`，Miles指向`/lab/src/miles`；两个patch的reverse-check及主实验14项冻结输入不变性校验通过。editable构建前需要在新容器内执行`git config --global --add safe.directory /lab/src/miles`，只加入此精确路径，不使用`*`或改宿主配置；完整命令见[Miles迁移章节](../05-swe-code-agents/REPRODUCE.md#跨机器复现coder30b案例claudemini与miles)。

验收分成三层记录：所选FSDP/core/补丁模块的CPU导入已通过；`pip check`仍报告基础TileLang依赖冲突、Miles的7个声明缺项及系统`pycairo`缺项；本轮没有任何GPU模型、Ray初始化或训练。完整诊断、两次构建修正及适用范围见[Miles冷重建记录](../07-miles/details/miles-cold-rebuild-v3-20260912.md)，不将第一层通过扩写为后两层通过。

`data/miles`的固定GSM8K64 parquet及retool8题JSONL很小，但要精确复现原样本应一并携带；不能只拷artifact manifest。Miles GPU默认6/7，与现在六卡Uni-Agent冲突，待对应GPU释放后按Miles独立说明启动。Python工具adapter只处理真实模型代码格式，原适配对照是rollout-only，不要把它计作新的RL更新。

### 12. 最小迁移清单与不能从kit得到的内容

| 目的 | 至少迁移/准备 |
| --- | --- |
| 重建主环境并fresh训练 | root生成kit；两个主模型或重新按revision下载；原Hotpot或kit中的固定派生数据；固定Docker镜像；新的CPU/RL overlays；可用外置checkpoint卷 |
| 复跑SWE/黑盒 | 上述kit中的SWE parquet/manifests；独立daemon内对应digest镜像；重做baseline/gold；固定Claude二进制；portable Mini archive/freeze/runtime |
| 精确resume当前run | 整个run目录与当前完整committed native checkpoint树、外置卷、pointer/commit、attempt/config/rollback/source维护记录；相同软件和四trainer分片布局；不要只带HF |
| 只做HF推理 | 完整HF目录，包括全部shard/index/config/tokenizer/generation config；原始出处、commit/export审计；正确服务profile |
| 复现历史报告 | 历史results/logs/figures与原始trajectory、candidate patch/verifier输出另打证据包；源码kit明确不含这些完整运行目录 |

Venv、`cache`和Docker layers可以作为加速材料，但不是唯一规范输入；解释器路径、ABI、image和`/lab`不一致时应从锁重建。Runtime socket、PID文件、Ray临时进程、用户home/auth状态不作为新节点启动输入。完整native恢复已在本机实际通过，跨机器仍应检查四rank恢复audit和下一步实际更新，不宣称逐bit重放异步轨迹。

CPU81项锁、RL118项锁及新Miles覆盖锁均没有随本kit自动变成全离线wheelhouse。离线机器还需另行携带所需wheels、VCS依赖、容器manifest/layers、模型/数据，校验其hash；只带锁文件不够。

仅在完成新机注册的可选固定名称路线中，最终自动化是两级；主通用路线使用独立的手工final章节。可选路线的`finalize_large_rl.py --watch --with-stable`保留固定snapshot导出、CPU审计并等待；`final_model_gate.evaluate_final_gate`强制真实128 complete exit0、canonical metrics1..128、完整analysis、native pointer/manifest/probes、BF16 HF与两份CPU审计绑定。`finalize_stable_memagent.py --check-only`是非GPU预检，`--execute`仅在全部gate通过后才可加载final、顺序运行final+repeat及固定配对。新节点必须等全部gate通过后再触发final；不能通过改JSON把等待或失败状态改成完成。
