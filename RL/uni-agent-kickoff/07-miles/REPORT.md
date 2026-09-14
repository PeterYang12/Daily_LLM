# Miles：MI35x 单节点 FSDP2 实验

[本实验总览](README.md) · [全部实验](../README.md)

记录日期：2026-09-11 UTC。Miles 工作树固定为 `50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9`。GSM8K RL 底座、checkpoint 恢复、0.6B 的真实 Python 工具 RL 均已通过。4B 原版工具流程运行完成但任务全部失败，原因已定位到代码输入契约；单独保留了失败证据。

容器状态更新：2026-09-12 的中断证据（原始文件：`results/environment/interruption-20260912.json`）表明原 Miles CPU/GPU 容器和宿主镜像已消失；data2 中的源码、venv、checkpoint、日志仍保留。下表和成功实验描述是第一阶段实测事实，不代表恢复后又执行了 Miles 验收。再次运行需按下文环境步骤重新创建 CPU/GPU 容器；GPU 6、7 是历史配置，不能假定当前空闲。

## 已完成结果

| 验收项 | 本机证据 |
| --- | --- |
| 真实生成 | 4轮×16=64条Qwen3-0.6B SGLang生成，官方GSM8K math reward；无mock输出或随机reward。 |
| FSDP2训练 | 两个rank、Adam step=1/2/3/4；后三轮grad norm=2.458796/2.113171/3.072448。 |
| 参数实际变化 | 第一个checkpoint与base相同；checkpoint2→3→4连续变化，选定q_proj全为有限值。 |
| checkpoint恢复 | 两rank恢复model、optimizer、LR scheduler，继续rollout2/3并保存checkpoint3/4。 |
| 权重回传 | 每次训练后begin/update/end/continue端点完成，下一批使用新的SGLang权重版本。 |
| agentic 工具 RL | Qwen3-0.6B：32 个真实 samples、50 次工具执行、7 个正确且有 Python stdout 的答案；两个非零梯度和参数变化。 |
| 4B 原工具对照 | 32 个 samples、125 次调用、全部 reward=-1；Adam 两步但 grad=0、权重不变，不计作有效 RL 更新。 |
| 4B 加强提示 | 16 个 samples、61 次调用；仍全部错误、grad=0、权重不变。仅改提示未解决接口问题。 |
| 4B 工具适配 | 独立 rollout-only：16/16 正确，每条一次 Python 执行；不做参数更新，不计作 RL 提升。 |

总审计：logs/miles/analysis.json（原始文件：`logs/miles/analysis.json`）。本case验证训练闭环，不是能力提升评测，也不等于agentic工具RL。四轮使用不同题目，reward均值1.0/0.4375/0.0625/0.625不能直接解释为随训练能力升降。

## 目标与官方起点

先用本机已有 Qwen3-0.6B 跑官方 GSM8K/math reward 的真实 rollout → GRPO → FSDP2 update → checkpoint，再接入官方 retool_v2 Python 工具和多轮生成器。两个层次分别验收：数学任务验证训练底座；受控算术工具任务验证 agentic 轨迹与训练。没有运行 SWE benchmark。

固定源码已有 [2 GPU colocated FSDP 测试](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/tests/e2e/short/test_qwen3_0.6B_fsdp_colocated_2xGPU.py)，同时注册 CUDA 与 `nightly-stage-c-2-gpu-mi350` ROCm CI。另有 [AMD Triton Qwen3-0.6B recipe](https://github.com/radixark/miles/blob/50ad28b47c8b7cd1f6bfed7767bb505a9fc8d9a9/examples/infra_features/true_on_policy/run_simple_amd_triton.py)。这是比从大模型 Megatron recipe 缩小更直接的起点。

Miles 将模型、优化器与梯度更新封装在 `FSDPTrainRayActor` 中，使用 Hugging Face 模型实现 + PyTorch FSDP2；SGLang 负责 rollout，Ray worker manager 启动 trainer、engine 和 router。`train.py` 的循环先生成数据、训练、保存，再把新权重传回 SGLang。分离资源上的 `train_async.py` 才是 fully-async driver；本次先使用同步的短 colocated case。

## 隔离与环境

| 项目 | 实际配置 |
| --- | --- |
| 镜像 | 已存在的 `lmsysorg/sglang-rocm:v0.5.18-rocm720-mi35x-20260826`，未另拉 Miles 大镜像 |
| 镜像 ID | `sha256:334898a4b0cfbecbf18d788a0687011cfbb2d817e8e9b5ea694717308cd1de67` |
| CPU 容器 | `ua-lab-miles`，不挂 GPU |
| GPU 容器 | `ua-lab-miles-gpu`，独立 PID namespace，访问 `/dev/kfd`、`/dev/dri` |
| GPU 限制 | 只设置 `HIP_VISIBLE_DEVICES=6,7`，未同时设置 ROCR mask |
| Python overlay | `/lab/envs/miles`，宿主对应 `/path/to/uni-agent-lab/envs/miles` |
| Python / torch | Python 3.10.12；PyTorch runtime `2.9.1+rocm7.2.0.git7e1940d4` |
| ROCm runtime | `7.2.26015-fc0010cf6a` |
| Ray / Transformers | Ray 2.56.0、Transformers 5.12.1 |
| Ray 端口 | head 16879、dashboard 18665，其他 worker/agent 端口在 launcher 中明确保留 |
| SGLang router | 18766 |
| 文件和缓存 | 全部新增内容在 data2 的 `/lab` 挂载中 |

设备探测实际返回两个 `AMD Instinct MI350X` 名称，两卡 tensor 矩阵计算通过。此处保留 runtime 返回值；用户称本机为 MI355，二者均走 gfx950 支持路径，不依据名称差异擅自改动硬件或驱动。

宿主默认 Docker bridge 缺少 `docker0`，第一次 bridge 模式创建失败，已删除本实验那个未启动的容器并改用 host network。Miles 的上游 `command_utils.execute_train()` 默认执行 `pkill` 和 `ray stop`、使用默认 Ray 端口；因此本次只在独立容器 PID namespace 内调用，并预建专用端口的 Ray，设置 `MILES_SCRIPT_EXTERNAL_RAY=1`。没有停止或修改 Uni-Agent 的 Ray/推理进程。

## 已处理的依赖问题

1. 原镜像有 torch、SGLang、Transformers，但缺 Ray 和若干 Miles 控制面依赖。独立 venv 通过 `site.addsitedir()` 复用镜像包，在 overlay 补 Ray 2.56、backports.strenum、OmegaConf、polars、blake3 等；未安装 Megatron/TE/Apex。
2. 容器 root 读取宿主用户所有的仓库触发 Git dubious ownership，给该容器配置精确的 `/lab/src/miles` safe.directory，editable Miles 安装通过。
3. 原镜像 SGLang `937af8538b` 缺 `/begin_weight_update`、`/end_weight_update`；Miles 当前 FSDP weight sync 会调用它们。只导入 Miles 包成功不能排除这种协议缺口。
4. 按官方 ROCm Dockerfile 使用 `sglang-miles` 分支，另取源码到 `/lab/src/sglang-miles`，固定 `32839114c4ada2ae237581405a8ff39dc0db9e25`。该版本包含上述端点。venv 的 `00_sglang_miles.pth` 将此 Python 源码优先加入路径，native ROCm 库仍使用原镜像。
5. 新 SGLang 的 `pyproject.toml` 构建依赖要求 `torch==2.13.0`，直接 `pip install --no-deps -e` 仍会创建隔离构建环境安装它。已中止这个构建；没有用它覆盖 ROCm torch。源码路径覆盖是本次明确选择的最小适配，完整版本组合仍需实际训练验证。
6. 前两次 job 又暴露运行时问题：tokenizer 默认 Rayon 并行触发线程资源不足，设置 `TOKENIZERS_PARALLELISM=false` 和 Rayon/OpenMP/BLAS 线程上限后真实 rollout 完成；首个训练 forward 发现 Miles 的 Triton bridge 仍导入 SGLang 旧路径。已在隔离 Miles 工作树改为新 kernel 路径，并为新增的 BF16 K/V scale 参数传入 1.0，差异保存在 [miles-sglang-triton-compat.patch](patches/miles-sglang-triton-compat.patch)。
7. 在实际 GPU 上对修复后的 bridge 做 causal grouped-query attention 前向/反向对比，参考为 FP32 PyTorch SDPA。形状 B=2、S=128、Q heads=16、KV heads=8、head dim=128；前向 RMS 相对误差 0.001886，dq/dk/dv 分别 0.002560/0.003125/0.002908，全部有限并通过 atol=0.05、rtol=0.05 检查。见 attention-probe.json（原始文件：`logs/miles/attention-probe.json`）。这只验证所测形状，不证明所有模型、精度或严格 on-policy 条件。
8. 第三次 job 的模型 forward 已通过，但共享 loss 路径仍无条件导入 `megatron.core.fusions.fused_cross_entropy`。为 FSDP 的非分片 vocabulary（`process_group=None` 或显式单rank group）增加 PyTorch 原生交叉熵路径，保留 tensor-parallel 分支原样。补丁为 [miles-fsdp-no-megatron.patch](patches/miles-fsdp-no-megatron.patch)。对19×257 logits及显式 sampling mask 检查 log-prob 和解析梯度，最大误差分别 8.9e-16/1.4e-17，被屏蔽 logits 梯度为0；见 logprob-probe.json（原始文件：`logs/miles/logprob-probe.json`）。
9. CPU 容器能通过 Miles 参数模块/FSDP 模块 import 和配置解析。完整 `train.py` import 会由 AITER/SGLang 在导入时查询 GPU，在无设备的 CPU 容器失败；GPU 容器的完整 `train.py --help` 已通过。这不是 CPU preflight 可以证明的 GPU kernel 正确性。

原始探测和命令在 logs/miles/（原始文件：`logs/miles`），包清单在 miles_environment.lock（原始文件：`configs/miles_environment.lock`）。SGLang Python 源码覆盖须结合上述 commit 一起记录，不能只看 pip 的旧包版本字段。

## 本次短配置

配置文件：miles_smoke.json（原始文件：`configs/miles_smoke.json`）。数据采用 `zhuzilin/gsm8k` revision `0cbd9f31d91ac21a7613dcbc7fef992adac459ae` 的 train.parquet 前 64 行，保留原始 messages/label，未改写答案。官方 math grader 已验证 `boxed{72}` 对标签 72 返回真，`boxed{71}` 返回假。

初次配置两轮 rollout，每轮 4 prompts × 4 samples = 16 samples；恢复实验继续两轮，总计 64 条。response 上限 256、context 2048、关闭 Qwen thinking，使用 GRPO、lr=1e-6、weight decay=0。训练侧和推理侧使用 triton attention；SGLang 每个 engine 一卡、静态显存占比 0.15、最多 4 个 running requests、关闭 CUDA graph 与 custom all-reduce。

相比官方长训练，该配置显式 `--no-offload-train --no-offload-rollout`，让很小的模型在两张卡上同时驻留，先验证梯度和权重同步，避开 ROCm7.2 memory-saver/offload 的额外原生依赖。它仍是 colocated 放置，但不验证原版 offload/sleep/wake 性能。未开启 true-on-policy 的严格确定性断言；选择相同 triton backend 也不能直接宣布位级一致。

成功判据是：真实 SGLang 生成与合法 math reward、两个训练 step 的非零梯度、每轮 checkpoint、选定权重前后确有变化。所有 reward 相同可能导致 GRPO advantage 为零；这种情况下只出现 step/checkpoint 还不够，需要调整真实任务采样后再核验。

## 可复现命令

创建容器和环境：

```bash
cd /path/to/uni-agent-lab
python3 scripts/miles_env.py start
docker exec ua-lab-miles python3 /lab/scripts/miles_setup.py
docker exec ua-lab-miles /lab/envs/miles/bin/python /lab/scripts/miles_prepare_data.py
python3 scripts/miles_env.py start --name ua-lab-miles-gpu --gpu-ids 6,7
```

`sglang-miles` 源码应先按上述 commit 准备，`miles_setup.py` 检测其目录存在后会配置路径覆盖。当前目录已经完成该步骤，不要重新在原 RL venv 中安装。

检查配置、启动本实验私有 Ray 和训练：

```bash
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_plan.py --validate
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py
```

若本实验 Ray 已在运行，使用 `miles_run.py --submit-only` 复用它。首次复现请先在干净的上述 Miles commit 上依次应用 `patches/miles-sglang-triton-compat.patch` 和 `patches/miles-fsdp-no-megatron.patch`；当前工作树已应用，不应重复应用。

| 尝试 | 结果与证据 |
| --- | --- |
| run-01 / `raysubmit_8nGByjXiBh4fcJ4f` | 两个 SGLang engine、两 rank FSDP2、初始权重同步通过；首次 tokenization 因 Rayon thread pool 资源错误退出。 |
| run-02 / `raysubmit_dyBnfQ5JBQdJ8dpw` | 16 条真实 GSM8K 生成、平均 reward=0.9375、2228 response tokens、4 个组中 1 个 reward 不同、token/logprob/mask 长度对齐；首个 train forward 因 SGLang kernel 旧路径退出。尚无训练更新。 |
| run-03 / `raysubmit_4sr8kQSL6QzR9hzX` | Attention forward 已通过，因共享 log-prob 路径无条件导入 Megatron 退出，尚未 backward/update。 |
| run-04 | FSDP 实际创建显式 size=1 TP group，首次 fallback 仅处理 None，仍导入 Megatron。已扩大条件到明确的单rank group，并补充真实 Gloo singleton 检查。 |
| run-05 / `raysubmit_Jzt25w6jtm2jdSJh` | 完整成功（16:20:39–16:23:38 UTC）。2轮×16真实样本、2个checkpoint、权重版本1→2→3。第0轮reward全1导致grad=0；第1轮reward均值0.4375、grad norm=2.458796，发生1次实际参数更新。 |
| run-06-resume / `raysubmit_aVbuckfmgdH9drLX` | 16:25:36–16:28:26 UTC成功。恢复checkpoint2的model/optimizer/LR，连续执行rollout2/3，grad=2.113171/3.072448，生成并保存checkpoint3/4。 |

Run-02 的生成审计见 analysis-run02.json（原始文件：`logs/miles/analysis-run02.json`），原始 rollout 另归档在 `logs/miles/attempts/run02/`，避免被重跑覆盖。

## 与 Uni-Agent 的关系

Uni-Agent 将 Task/Agent/Tool/Sandbox 和 Gateway trajectory 作为主要交互抽象，训练交给 verl；Miles 同时拥有训练/rollout 控制流程及自己的 agentic 接入层，FSDP2 可以直接加载 HF checkpoint。两者都必须把生成 token、logprobs、reward 和训练侧 token loss 对齐，都不能通过“服务能回复”证明 RL 成功。

Miles 官方专用 ROCm 镜像和 0.6B ROCm CI 提供了更直接的硬件起点，但它同样要求 patched SGLang 等版本匹配。本次随后已完成下面的 Python 工具 RL。更复杂的终端/SWE 任务还需要参考 `examples/swe-agent-harbor-docker` 和 agentic rollout 文档，单独验证 sandbox、任务数据与 reward；不能由 GSM8K 或算术工具成功外推。

## 工具调用 case

配置 miles_retool.json（原始文件：`configs/miles_retool.json`） 使用官方 `examples.retool_v2.tool_sandbox` 的工具定义、Python 执行器与 reward 函数，生成循环使用内置 `miles.rollout.generate_hub.multi_turn.generate`。把默认大模型 Megatron 启动配置替换为已验证的0.6B FSDP2底座。数据同时写入官方tools schema，并设置`--tool-key tools`，使Dataset在提前应用chat template时注入工具说明；否则`sample.prompt`已成为字符串，生成函数不会再次注入tools。

输入是8个本地构造的确定性整数表达式，由 miles_prepare_retool.py（原始文件：`scripts/miles_prepare_retool.py`） 同时生成任务和精确答案。它是工具集成 smoke，不是 DAPO/AIME benchmark。设置最多4个模型轮次、每次生成最多512 token、总context上限1800、每prompt采样4次。角色轨迹、工具内容和token loss mask均保存。

参数解析和固定工具调用 fixture 检查：Qwen工具解析器识别 `code_interpreter`，真实Python执行返回9615；追加18个工具/模板token，loss mask均为0，logprob占位均为0。见 protocol-probe.json（原始文件：`logs/miles-retool/protocol-probe.json`）。这项 preflight 没有模型生成；下面的结果才来自真实模型 rollout。

上游这个示例的Python sandbox基于正则检查和子进程，并非独立容器或内核沙箱；本次执行仅在专用Docker内完成，适合受控算术示例。它不能作为接收不可信代码的强隔离证明。reward中的tool bonus读取`metadata.tool_call_count`，当前内置multi_turn生成器并未维护该字段；本例保留官方reward逻辑，单独从保存的role轨迹统计实际调用数，避免把缺省0误报为没调用工具。

### 0.6B：两个实际 agentic RL 更新

Job `raysubmit_LM4gKCJgeMZgpqnq` 于 16:29:28–16:33:16 UTC 完成。审计见 logs/miles-retool/analysis.json（原始文件：`logs/miles-retool/analysis.json`）。

| rollout | samples | 有工具消息的 samples | 实际执行次数 | 正确且有 Python 输出 | 平均 reward | grad norm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 16 | 15 | 27 | 1 | -0.875 | 3.072438 |
| 1 | 16 | 14 | 23 | 6 | -0.250 | 3.495048 |

共 7/32 正确，全部 7 个都有真实 Python stdout 和后续 assistant 答案。其余样本并非都遵守工具协议：50 次执行中 28 次没有 stdout，13 次工具报错，常见原因是遗漏 `print`、代码格式或内容错误。这个很小的模型能演示完整链路，但任务成功率有限。

两个 checkpoint 的 Adam step 分别为 1/2；q_proj 有 2,096,946 / 2,095,762 个元素相对前一版本变化，最大绝对变化约 1.013e-6。两个 `changed_updates` 均为真。所有样本的 response token、rollout logprob 和 loss mask 长度对齐，observation/template masked token 对应 logprob 为 0。例如 rollout0/sample4 实际执行 sum of squares 得到 9455，最后输出 boxed 9455；其 18 个工具/模板 token 不计入 policy loss。

初始 prompt 禁用了 thinking，但官方多轮生成器追加工具消息时没有传递相同 chat-template kwargs，Qwen0.6 在部分后续轮次重新输出 `<think>`。本次保留上游行为，没有把这些轨迹描述为全程非 thinking。实际例子见 [miles-trajectories.md](details/miles-trajectories.md)。

`trajectory-N.jsonl` 在 reward 计算前保存，所以其中 `reward=null`；reward 应从同一 sample 的 `rollout-N.pt` 读取。反过来，`.pt` 会移除 `metadata.messages`，工具次数和返回内容必须从 JSONL 读取。审计脚本按 `sample_index` 和 occurrence 将二者配对，避免将缺字段误报为零工具调用。

### 4B：训练流程成功，工具任务失败

利用本机已有 `Qwen3-4B-Instruct-2507`，保持原工具 schema、执行器、reward 和 8 个算术任务，运行 miles_retool_4b.json（原始文件：`configs/miles_retool_4b.json`）。Job `raysubmit_Pz3vRdXW5tuCcyXe` 于 16:35:13–16:41:47 UTC 完成。

32 个 samples 全部调用工具，共 125 次调用，但全部 reward=-1。两个 optimizer step 的 grad norm 均为 0；两个 checkpoint 选定 q_proj（4096×2560）与 base 完全相同，`changed_updates=0`。见 analysis.json（原始文件：`logs/miles-retool-4b/analysis.json`）。Job 完成、写 checkpoint 和增加 Adam step 本身不能证明有学习。

轨迹直接显示模型把完整的 Markdown ` ```py ... ``` ` 围栏放进 `arguments.code`，并常把最后一个裸表达式当作 notebook 的显示结果。官方执行器把 code 直接插入 Python 脚本，因此围栏触发 SyntaxError；即使删掉围栏，裸表达式也不产生 stdout。模型看到错误后常重复同样格式，或最终给出错误算术答案。这是可定位的工具契约不匹配，不能由本实验推论“4B 数学能力弱于 0.6B”或 ROCm 计算错误。

进一步仅加强 system prompt：明确原始 Python、禁止围栏、必须 `print`。配置 miles_retool_4b_contract.json（原始文件：`configs/miles_retool_4b_contract.json`），数据 `retool-arithmetic-8-raw-python.jsonl`；8 prompts×2 samples 一批。Job `raysubmit_rgmMwWmLwji1pG1Q` 在 16:50:35 UTC 成功结束，但 16 samples / 61 次调用仍全部错误、grad=0、权重不变。60 次工具错误，1 次空 stdout；具体空输出后编造答案的例子也保存在轨迹摘录中。见 该次审计（原始文件：`logs/miles-retool-4b-contract/analysis.json`）。

### 独立的工具契约适配实验

miles_retool_adapter.py（原始文件：`scripts/miles_retool_adapter.py`） 是 lab 插件，没有改官方执行器或 reward。它只接受完整外层 Python Markdown 围栏，并用 AST 将最后的裸表达式改为 `print(expression)`；已经是 `print` 的代码保持原样，然后交给原 Python executor 真实执行。适配器看不到任务 label，不插入计算答案。

这改变了工具输入语义，因此结果必须和上面的原版失败单列。配置 miles_retool_4b_adapter.json（原始文件：`configs/miles_retool_4b_adapter.json`） 使用原始 8 题 prompt、每题 2 次采样、`--debug-rollout-only`；不做训练、不保存优化器 checkpoint。目标是验证接口原因，不继续制造零梯度的 4B checkpoint。

真实执行 fixture 已通过：围栏代码原执行器 SyntaxError、适配后 stdout=9615；裸表达式原先 stdout 为空、适配后为9455；显式 print 保持输出311；原执行器对 `import os` 的检查仍触发。证据为 adapter protocol-probe.json（原始文件：`logs/miles-retool-4b-adapter/protocol-probe.json`）。原有 regex 检查的能力边界没有因此增强。模型 rollout 结果单独保存于 `logs/miles-retool-4b-adapter`。

实际模型 job `raysubmit_QjpDs5Xa3MKBrtrA` 于 17:00:28–17:02:21 UTC 完成，**16/16 samples 正确，16 次真实 Python 执行、16 个 stdout、0 个工具错误**。16 次输入均有外层代码围栏，15 次同时需要显示末尾表达式；每条轨迹有两次 assistant 消息和一次 tool 消息，随后给出正确 boxed 答案。输入代码、实际执行代码、变换标记与输出逐条保存在 tool-audit（原始文件：`logs/miles-retool-4b-adapter/tool-audit`），汇总（原始文件：`logs/miles-retool-4b-adapter/adapter-audit-summary.json`） 和 rollout 审计（原始文件：`logs/miles-retool-4b-adapter/analysis.json`） 对应。

这验证了原失败的主要接口原因。样本仍是同一小组受控算术题；没有 optimizer checkpoint、没有训练 metric，不能写成“训练后从 0% 提升到 100%”。`debug-rollout-only` 虽然会构建 Ray trainer 控制对象，其 init/train/update_weights 的 FSDP 分支立即返回，实际不加载训练参数或执行梯度更新。

## 首次完成后的参数审计

analysis-first-two-rollouts.json（原始文件：`logs/miles/analysis-first-two-rollouts.json`） 对HF原始权重与两个DCP checkpoint做CPU读取比较，选定 `model.layers.0.self_attn.q_proj.weight`（2048×1024 FP32 master）：checkpoint1与base完全相同；checkpoint2有2,096,977/2,097,152个元素变化，最大绝对变化7.45e-7。这与第一轮zero-variance GRPO、第二轮非零梯度一致。

上游FSDP actor的`global_step`/`micro_step`只初始化为0，在当前训练循环未递增，导致两个`meta.json`中的这些字段均为0。`iteration`/`rollout_id`/`next_rollout_id`分别正常递增；审计进一步读取Adam状态的`step`并结合训练日志和权重差异，不能拿这个陈旧global_step字段作为更新次数。当前未修改此计数行为。

训练与rollout token logprob的平均绝对差约0.0109/0.0118，并非严格位级on-policy；本次没有使用相等断言。GRPO的batch平均标量loss接近0也不意味着没有梯度：组内中心化的正负advantage会相抵，第二轮grad norm仍为2.458796。

连续参数审计的具体值（每次均对比前一个checkpoint）：

| checkpoint | Adam step | q_proj变化元素 / 2,097,152 | 最大绝对变化 |
| --- | ---: | ---: | ---: |
| 1 | 1 | 0 | 0 |
| 2 | 2 | 2,096,977 | 7.45e-7 |
| 3 | 3 | 2,095,737 | 8.64e-7 |
| 4 | 4 | 2,095,163 | 9.09e-7 |

`changed_updates=3`、`two_consecutive_changed_updates=true`。这同时证明零variance首轮没有靠weight decay假造权重变化（本次weight decay=0）。第2轮62.5%样本达到256-token上限，属于本smoke的短预算限制，不能作为完整数学任务基准。

恢复与工具case的启动命令：

```bash
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_resume.json
docker exec ua-lab-miles /lab/envs/miles/bin/python /lab/scripts/miles_prepare_retool.py
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_retool.json
docker exec ua-lab-miles /lab/envs/miles/bin/python /lab/scripts/miles_analyze.py
```

`miles_resume.json`中的`--num-rollout 4`是总终止轮数，并非额外4轮。当前checkpoint已经到4，重复恢复命令不会再执行rollout2/3。若需要复现实验序列，应给新实验单独的save与log路径，不覆盖已有证据。

其他工具配置和分析命令：

```bash
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_retool_4b.json
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_retool_4b_contract.json
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_retool_4b_adapter.json
docker exec -e OMP_NUM_THREADS=4 ua-lab-miles /lab/envs/miles/bin/python /lab/scripts/miles_analyze.py --checkpoint-dir /lab/checkpoints/miles-retool --logs /lab/logs/miles-retool --model-dir /lab/models/Qwen3-0.6B
python3 scripts/miles_extract_trajectories.py
```

4B 分析时把 model-dir 改为 `/lab/models/Qwen3-4B-Instruct-2507`，checkpoint-dir/logs 改为对应实验名。rollout-only adapter 没有 checkpoint，指定一个不存在的对应 checkpoint 目录即可，分析输出会明确保留 `checkpoints=[]`。GPU 训练命令应依次运行，不要在这个专用 2-GPU Ray 中并发启动这些 colocated jobs。

源码 commit、工作树差异和补丁 SHA256 统一在 miles-sources.json（原始文件：`configs/miles-sources.json`）。两处 Miles 核心补丁、SGLang 源码覆盖、lab 工具适配器分别记录；不能将本环境描述为官方镜像原封不动即可运行。

## 已有结果与复跑保护

以上固定配置是本次实验记录。现在直接启动已完成的 smoke/retool 配置会在启动 Ray、导入 Miles 和调用其清理逻辑之前拒绝覆盖已存在的 rollout、trajectory、train dump 或 checkpoint。已完成的 resume（当前总终止轮数 4、latest checkpoint 4）只报告 `already_complete` 并退出。resume 缺 tracker 或 model/optimizer/LR 元数据时也拒绝继续，避免上游静默退回 base 模型。

使用 `--run-name` 会把日志、debug dump 和 checkpoint 改到新的同名目录；同根目录的 resume 路径也一起改名。下面是可直接查看的复跑方式，训练顺序仍是先 smoke 再 resume：

```bash
# 纯只读：在 CPU 容器中检查新输出，不占 GPU、不启动 Ray
docker exec ua-lab-miles /lab/envs/miles/bin/python /lab/scripts/miles_run.py --config /lab/configs/miles_smoke.json --run-name replay-miles-smoke --check-outputs

# 中断后容器需重新创建；先完成上方 CPU/GPU 环境重建，6、7 为历史 GPU 配置
python3 scripts/miles_env.py start --name ua-lab-miles-gpu --gpu-ids 6,7
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --config /lab/configs/miles_smoke.json --run-name replay-miles-smoke
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_resume.json --run-name replay-miles-smoke

# 另一项独立工具实验，使用自己的新名字
docker exec ua-lab-miles-gpu /lab/envs/miles/bin/python /lab/scripts/miles_run.py --submit-only --config /lab/configs/miles_retool.json --run-name replay-miles-retool
```

如果这些 replay 名也已存在，应选另一个名字；没有默认覆盖开关。wrapper 会保存本次实际启动的 `launch-config-<start>-<end>.json`。数据准备脚本复用完全相同的已有输入，发现内容不同会拒绝覆盖。分析、绘图和 probe 输出属于可重建报告，可按相同原始证据重新生成；不要用 shell 的 `>` 覆盖已有原始 run log。

replay-guard-audit.json（原始文件：`logs/miles/replay-guard-audit.json`） 实际在 CPU 容器验证了上述 9 种检查：既有结果拒绝覆盖、完成的 resume 无操作退出、新名字可用、缺 checkpoint 拒绝、未来 step4→5 可追加但没有提交训练。审计前后 25 个原始 rollout/trajectory/checkpoint metadata 文件 SHA256 完全相同。数据准备脚本也使用缓存重新检查通过，没有重写已有数据。两个核心补丁通过 `git apply --reverse --check`，新脚本通过内存中的 Python 语法检查。

第一阶段完成后曾停止 `ua-lab-miles-gpu`，其私有 Ray、SGLang、trainer 一并退出；后来宿主中断使它和 `ua-lab-miles` CPU 容器均不再存在，保留的是 data2 中的文件。停止前的 final-ray-jobs.json（原始文件：`logs/miles/final-ray-jobs.json`） 仍记录 10 个历史 job 的终态（6 成功、4 失败），当时没有 running/pending job。miles-artifacts-manifest.json（原始文件：`results/miles-artifacts-manifest.json`） 是那一阶段的文件、容器状态与 checkpoint 快照，不是恢复后的实时容器清单。
