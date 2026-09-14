# 代码 Agent：SWE 六题与扩展集运行步骤

[本实验总览](README.md) · [全部实验](../README.md)

先完成[共用环境准备](../00-overview/SETUP.md)。以下命令在完整实验工作区执行，所引用的模型、数据和实验脚本需另行准备。

<a id="跨机器复现coder30b案例claudemini与miles"></a>

## Coder30B：原六题、Claude/Mini 与扩展诊断

本文补充主复现文档，重点是**真实入口、必要资产、固定版本和验收方法**。命令按一个新的lab目录描述；本机正在训练时不要直接执行占用相同GPU的示例。源码、既有结果和资产经独立核查，没有重新运行长case或修改冻结源码。2026-09-12另在本机独立目录完成Miles的37项精确overlay冷安装与所选FSDP/core模块CPU导入校验；`pip check`仍有未满足声明，未重验GPU训练，也不等于另一台物理节点完成训练。详细范围与失败记录见[冷重建记录](../07-miles/details/miles-cold-rebuild-v3-20260912.md)。

### 目录、Docker和GPU约定

宿主lab可以放在任意有空间的绝对路径，**容器内继续统一挂为`/lab`**。多数host wrapper用`Path(__file__).resolve().parents[1]`定位lab；Python任务、模型、venv和配置中大量`/lab/...`是容器内部约定，不应机械替换为宿主路径。

```bash
: "${LAB_ROOT:?Use the LAB_ROOT from the opening section}"
cd "$LAB_ROOT"
export UNI_AGENT_HOST_LAB_ROOT="$LAB_ROOT"
```

独立sandbox daemon需要把宿主lab按**同一个宿主绝对路径**挂进daemon容器，另外CPU/model容器把它挂为`/lab`。原因是Docker bind mount的`src=`由daemon解析；黑盒CLI/portable Python要从宿主真实路径bind到任务容器，不能把`/lab/cache/...`误当daemon看得见的路径。

| 角色 | 固定容器 / 端点 | GPU或运行时 |
| --- | --- | --- |
| CPU driver | `ua-lab-cpu` | 无GPU映射，`/lab/envs/cpu/bin/python` |
| sandbox daemon | `ua-lab-sandbox-daemon` | 宿主socket`$LAB_ROOT/run/docker.sock`；CPU内`/lab/run/docker.sock` |
| Coder30B | `ua-lab-coder30`，`127.0.0.1:18082/v1` | HIP1，BF16 TP1，65536窗口，qwen3_coder parser |
| 32B主评估 | `ua-lab-infer32`，18083 | HIP0；与本文SWE/Miles入口分开 |
| 32B稳定性辅助评估 | `ua-lab-infer32-stable`，18084 | 也占HIP1；不能与Coder30B同时占该卡 |
| Miles CPU | 建议新名字`ua-lab-miles-repro-cpu` | SGLang ROCm镜像，Python3.10 |
| Miles GPU | 建议新名字`ua-lab-miles-repro-gpu` | 历史配置HIP6,7；Ray/SGLang与Uni-Agent独立 |

`start_large_infer_container.sh`和`serve_qwen3_coder30b.sh`把Coder卡号固定为1；只改容器环境而不改serve脚本不一定改变实际卡号。换卡应在新的实验副本中同时调整并记录，不能修改仍在运行的冻结配方。Miles JSON里的`physical_gpu_ids`也须与容器`HIP_VISIBLE_DEVICES`逐字相同，否则launcher主动拒绝。

CPU/推理镜像固定为：

```text
vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa
image ID: sha256:33b992ce0f367784daf23c535ed63af3703e55ade7f5016a08e9c41a24a7a3b9
```

sandbox daemon使用`docker@sha256:aa3df78ecf320f5fafdce71c659f1629e96e9de0968305fe1de670e0ca9176ce`，通过`/usr/local/bin/dind dockerd`进入正确的cgroup nesting。当前daemon关闭bridge/iptables，任务容器使用`--network host`，无需修改宿主Docker网络。

### 必须带走或重新准备的资产

| 资产 | 固定版本 / 来源 | 是否运行时必需 |
| --- | --- | --- |
| Uni-Agent | `472c875a97f9a2764c81a6ec7581167632bd8bcc` + `patches/uni-agent-docker-stdin.patch` | 是；此stdin补丁修复大文本Docker写入，不能仅复制原commit遗漏它 |
| CPU/debug verl | `10db40d0da4d59150bb389960b77585f81a89b8d` | 是；与RL专用verl目录区分 |
| CPU overlay | `results/cpu-overlay-requirements.lock`，81项实际overlay | 是；使用`--no-deps`，继承镜像ROCm torch/vLLM |
| Coder30B | `Qwen/Qwen3-Coder-30B-A3B-Instruct`，revision`b2cff646eb4bb1d68355c01b18ae02e7cf42d120` | 是；完整16个BF16 shards及tokenizer/template，约57GiB |
| SWE数据 | `princeton-nlp/SWE-bench_Verified`，revision`c104f840cc67f8b6eec6f759ebc8b2693d585d4a` | 是；最终使用保存的parquet |
| 六题输入 | `results/swe-data/verified-six.parquet` | 是；SHA256为`fc2d06cff9f08a1821a5770af7257ea5836117b029a122d092fae898fac25a7a` |
| 六题镜像清单 | `results/cpu-reproduction-inputs.json`的`swe_images` | 是；包含tag、repo digest和image ID |
| 六题oracle记录 | `results/swe-oracle-six/<instance>/result.json` | Claude/Mini driver硬性读取，不能只带parquet漏带它 |
| Claude CLI | `cache/blackbox-claude-2.1.236` | Claude路径必需；334,645,552 bytes，SHA256`6c8818fa22187aa555c242be4abbacc44d6b71a32ac9631ee7b2b5d12f51f752` |
| Portable Mini | `cache/blackbox-mini-tool/`或其重建材料 | Mini路径必需；Python3.12.13 + mini-swe-agent2.2.8 + litellm1.81.7 |
| Portable Python压缩包 | `cache/blackbox-python.tar.gz` | SHA256`191b5188b42886fb8a14968d714571e8f3d1cef92ac7ad4c7e24cc4d0929b194` |
| Mini精确包锁 | `results/blackbox-mini-portable-freeze.txt` | 构建脚本存在该锁时使用`--no-deps`安装 |
| 扩展SWE29题 | `results/swe-expanded-v1/validated-final.parquet`、`final-manifest.json`、`image-preparation.json`、`selection.json` | 仅扩展套件必需；完整记录32组控制中排除的3个xarray环境 |
| Terminal-Bench三题 | `results/tbench-v1/selected-three.parquet`、selection/policy/prepared/controls及任务镜像 | 仅可选Terminal-Bench必需 |

不要仅拷贝venv目录就认为完成迁移：venv中的解释器、`.pth`和editable引用依赖指定镜像及`/lab`布局。优先按主文档重建CPU环境，或保持完全相同镜像、Python版本和内部路径后验证包来源。kit主路线的CPU冷重建入口是`bootstrap_reproduction_cpu.sh`；旧独立归档路线另有`cpu-reproduce.sh`。源码归档、其SHA和overlay锁位于`results/cpu-reproduction-inputs.json`及`results/cpu-reproduce-inputs.sha256`。历史源码归档还需核对是否已经包含后续Docker stdin补丁，不能把“能解压”当作当前工作树一致。

### Coder30B原六题：先判题控制，再模型

六题固定为Flask5014、Requests6028和pytest5262/7432/7521/7982。新增机器先保证CPU环境与sandbox daemon工作；模型下载和服务启动使用现有入口：

```bash
docker pull vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa
bash scripts/start_cpu_container.sh
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/download_assets.py --models coder30b
python3 scripts/lab_services.py start sandbox --timeout 600
python3 scripts/replay_swe.py prepare
python3 scripts/replay_swe.py check
```

这里`prepare`按digest拉取并恢复parquet引用的tag，**进入独立sandbox daemon**，不是宿主默认daemon。直接`docker pull swebench/...`拉到默认daemon对这些任务没有帮助。检查socket的独立命令为：

```bash
docker --host "unix://$LAB_ROOT/run/docker.sock" info
```

若选择从HF重新生成输入，在**空的**`results/swe-data`中执行：

```bash
docker exec -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/prepare_swe_cases.py \
  --output /lab/results/swe-data
```

该脚本调用官方preprocessor并注入固定HF revision，产出verified-all和六题子集。若已拷贝原parquet，不要再次覆盖；历史已做过逐row重建等价检查，证据是`results/swe-data-replay-equivalence.json`。重新打包的parquet字节可能受序列化版本影响，不能只凭存在性代替row内容验证。

在新lab尚无相应结果目录时做控制：

```bash
python3 scripts/replay_swe.py baseline --output results/swe-baseline-six
python3 scripts/replay_swe.py oracle --output results/swe-oracle-six
python3 scripts/lab_services.py start coder30b --timeout 600
python3 scripts/run_large_swe.py --suite six --name replay-coder30b-six --concurrency 2
```

轻量kit不带旧oracle结果时，**上面baseline和oracle两条命令就是生成它们的完整入口**。`replay_swe.py oracle --output results/swe-oracle-six`会用已下载的六题镜像执行gold控制，并生成黑盒driver所需的`results/swe-oracle-six/<instance>/result.json`。该目录应是此次新机实际判题的结果，不需要也不应拿一份空summary占位。

baseline应6题全部完成判题且resolved0；gold oracle应6题完成且resolved6。这是环境验收条件。模型复跑不保证恰好重现历史4/6，温度0.2、top_p0.9和服务数值差异都会影响轨迹。历史ReAct30B六题为resolved4、finished5、两者交集4，无outer error；用它说明已有可执行闭环，不把它作为新机器必须达到的确定性分数。

预算固定在`configs/swe-react-64k.yaml`：40步、每轮输出2048、原生55000请求停止阈值、service硬窗口65536、tool command90秒、verifier300秒、外层任务900秒。55000不是整段agent累计API token配额，不能拿它估算总推理费用或总生成量。

结果在`results/large-swe/<新名字>/`：`provenance.json`、`runner.log`、`summary.json`及`run/<instance>/`中的candidate patch、完整AgentResult和verifier输出。判定成功至少同时看`reward==1`、`eval_completed==true`以及`finished`；脚本正常exit0不等于模型修复成功。

另需读取`case-audit`而非仅看diff文件数：pytest7432的基础镜像带有大量mode-only差异，且普通git diff可能漏掉untracked文件。原六题Flask的ReAct/Claude都改了已有测试，违反prompt约束；这条记录保留，另有剥离测试hunk后的独立source-only verifier通过，见`results/flask-source-only-audit-20260912-v2/`。功能判题通过、任务约束满足和修改归属是不同验收列，不能互相代替。

### 同六题的真实Claude Code与Mini

这两条路径运行的是实际CLI，模型推理通过Uni-Agent Gateway送往本地Coder30B。Claude请求中的兼容model名称可能为`claude-sonnet-4-5`，**这不代表调用了Anthropic Claude权重**；后端是已记录的Qwen模型。CLI显示的cost字段不代表本次产生了Anthropic API账单。

优先迁移并校验已保留的CLI二进制。若要重新下载，使用固定版本的官方release路径，并检查上述SHA：

```bash
mkdir -p "$LAB_ROOT/cache"
curl --fail --location \
  https://downloads.claude.ai/claude-code-releases/2.1.236/linux-x64/claude \
  --output "$LAB_ROOT/cache/blackbox-claude-2.1.236"
printf '%s  %s\n' \
  6c8818fa22187aa555c242be4abbacc44d6b71a32ac9631ee7b2b5d12f51f752 \
  "$LAB_ROOT/cache/blackbox-claude-2.1.236" | sha256sum --check
chmod +x "$LAB_ROOT/cache/blackbox-claude-2.1.236"
```

这段下载只用于目标文件尚不存在的新lab；已有文件应直接校验。`blackbox-claude-install-upstream.sh`是保存的官方installer，它即使带版本TARGET也先获取latest installer binary；因此不能把裸运行该脚本当作固定二进制重现步骤。官方自动npm/native安装fallback也会引入新CLI版本，正式重现使用上述已校验binary绑定。

Mini重建使用以下脚本；须先复制精确freeze锁。它会下载并校验CPython3.12.13/20260602压缩包，解压到固定portable路径，然后复制当前固定pin的`examples/mini_swe_agent/run_agent.py`：

```bash
docker exec ua-lab-cpu bash /lab/scripts/blackbox-build-portable-mini.sh
```

旧SWE镜像可能没有兼容的Python。harness把portable目录只读绑定为`/opt/mini-swe-agent`，使用其中的Python执行脚本；不要用任务仓库的老Python去import新Mini。`blackbox.Dockerfile`仅用于早期toy案例，不是SWE benchmark实际任务镜像的替代。

两种30B正式入口：

```bash
python3 scripts/run_large_blackbox.py --agent claude \
  --name replay-claude-coder30b-six --concurrency 2
python3 scripts/run_large_blackbox.py --agent mini \
  --name replay-mini-coder30b-six --concurrency 2
```

wrapper主动传递宿主真实lab root和本地模型/解析器；黑盒driver读取固定`/lab/results/swe-oracle-six`。如果把控制结果放到其他名字，直接下层`blackbox-swe-cases.py`并显式给`--oracle-dir`，因为上层30B wrapper没有这个参数。Mini入口固定启用`--guided-mini`的明确工具提交提示，不能称作原始未引导prompt。

非标准oracle目录的完整示例（将`custom-oracle-six`换成已通过的六题控制输出；该命令写独立新结果）：

```bash
docker exec -e DOCKER_HOST=unix:///lab/run/docker.sock \
  -e UNI_AGENT_HOST_LAB_ROOT="$LAB_ROOT" \
  -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl \
  -w /lab/src/uni-agent ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/blackbox-swe-cases.py \
  --data /lab/results/swe-data/verified-six.parquet \
  --oracle-dir /lab/results/custom-oracle-six \
  --agent claude --model Qwen3-Coder-30B-A3B-Instruct \
  --base-url http://127.0.0.1:18082/v1 \
  --tokenizer /lab/models/Qwen3-Coder-30B-A3B-Instruct \
  --tool-parser qwen3_coder --tag replay-claude-custom-oracle \
  --output /lab/results/large-blackbox/replay-claude-custom-oracle/run \
  --concurrency 2 --steps 40 --context-limit 55000 --max-tokens 2048 \
  --agent-timeout 600 --eval-timeout 300
```

Mini将`--agent claude`改成`--agent mini`并增加`--guided-mini`，其余模型/预算相同；另用新tag/output。此下层命令不会自动写上层30B wrapper的额外summary，逐题与Gateway原始输出仍保存。

预算是40轮、55000上下文阈值、每轮2048输出、agent600秒、verifier300秒；Claude/Mini每个任务sandbox为2CPU/6GiB。历史30B Claude为resolved3/6、finished5/6；Mini为resolved2/6、finished1/6、交集1/6。所有轨迹仍必须验证response IDs、mask、logprobs同长度、mask二值、logprobs有限；成功patch和agent正常结束分别报告。

blackbox结果包含`run/run-config.json`、每题`result.json`、`agent-process.stdout`、`verifier.result.json`，以及`sessions/*/trajectories.jsonl`与debug snapshot。恢复任务时不要只复制summary，原始Gateway token/mask/logprob记录才是可用于RL结构验收的材料。这里的case均为推理/工具执行，不包含optimizer update。

手动Gateway调试入口的原始trajectory中reward/finished可能仍为null；真实值来自TaskResult，现有`reward-finished-join.json`按session_id关联，并没有执行trainer的reward attachment或unfinished masking。response段还包含mask=0的工具观察，不能把其总长度写成模型生成token数。

<a id="可选扩展29题与terminal-bench"></a>

### 可选：扩展 29 题

扩展SWE是固定repo分层、镜像和判题条件筛出的诊断集：Django8、Sphinx8、SymPy8、xarray5。`final-manifest.json`记录了32个baseline/gold控制及3个排除项；已有29题模型结果为resolved8、finished28、交集8。每题使用100步/外层1800秒，不能与六题40步预算混成公平单一排名。

迁移时最直接是复制已冻结parquet、selection、final-manifest与image-preparation，再按`final-manifest.json.usable_ids`从`image-preparation.json.images[id].registry.repo_digest`拉取镜像，并恢复`image`对应的`:latest`标签，核对`registry.image_id`。历史新增unique image层约34.45GB，原150GB预算记录不包含容器可写层；新daemon的计账起点不同，不能照搬原`baseline_daemon_layer_bytes`当作新盘占用。

轻量kit只含最终parquet和`final-manifest.json`时也足够恢复所需29个镜像：该manifest的`records`本身含image、repo_digest、image_id以及usable标记。以下只处理这29个声明的镜像，不重新选题、不需要旧oracle文件：

```bash
python3 - <<'PY'
import json, subprocess
from pathlib import Path
lab = Path.cwd().resolve()
manifest = json.loads((lab / 'results/swe-expanded-v1/final-manifest.json').read_text())
docker = ['docker', '--host', f'unix://{lab}/run/docker.sock']
for row in manifest['records']:
    if not row['usable']:
        continue
    tag = row['image'] if ':' in row['image'].rsplit('/', 1)[-1] else row['image'] + ':latest'
    existing = subprocess.run(docker + ['image', 'inspect', tag, '--format', '{{.Id}}'], text=True, capture_output=True)
    if existing.returncode or existing.stdout.strip() != row['image_id']:
        subprocess.run(docker + ['pull', row['repo_digest']], check=True)
        subprocess.run(docker + ['tag', row['repo_digest'], tag], check=True)
    actual = subprocess.check_output(docker + ['image', 'inspect', tag, '--format', '{{.Id}}'], text=True).strip()
    assert actual == row['image_id'], row['instance_id']
PY
```

需要新机独立重做扩展控制时，下层runner可以直接读取这29题，不依赖原candidate pool：

```bash
docker exec -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl \
  -e DOCKER_HOST=unix:///lab/run/docker.sock -e PATH=/lab/tools:/usr/local/bin:/usr/bin:/bin \
  ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_swe.py \
  --data /lab/results/swe-expanded-v1/validated-final.parquet \
  --config /lab/configs/swe-oracle.yaml --baseline \
  --output /lab/results/replay-expanded-baseline --concurrency 2
docker exec -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl \
  -e DOCKER_HOST=unix:///lab/run/docker.sock -e PATH=/lab/tools:/usr/local/bin:/usr/bin:/bin \
  ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/audit_swe.py \
  --data /lab/results/swe-expanded-v1/validated-final.parquet \
  --config /lab/configs/swe-oracle.yaml \
  --output /lab/results/replay-expanded-oracle --concurrency 2
```

```bash
python3 scripts/run_large_swe.py --suite expanded \
  --name replay-coder30b-expanded --concurrency 4
```

若重新选取/验证，使用`cpu-swe-expanded-prepare.py`的`select/prepare`、`cpu-swe-expanded-validate.py`和`cpu-swe-expanded-finalize.py`。它们主要是本次固定路径的实验工具；finalizer硬编码`/lab/results/swe-expanded-v1`，`prepare`会更新准备状态和`prepared.parquet`。为保持原集合/排除证据，正式重现应优先迁移已冻结清单，不要在保留原实验的目录里重新选择或混入不同可用性条件的病例。
