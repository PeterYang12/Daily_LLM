# Uni-Agent ROCm agentic RL 实验记录

## 范围与模型

- 使用 Uni-Agent 仓库固定的 verl 子模块提交 `a9f2985159536a607211dcac730d3f5d55028950`；独立工作树 `/lab/src/verl-rl`，不覆盖主线 checkout。
- 首个模型 `Qwen/Qwen3-0.6B`，596.05M 参数，标准 dense 架构。目的为验证 agentic RL 基础设施与真实参数更新，并非代表 SWE benchmark 能力。
- 使用上游 `Task` / `ReActAgent` / `GatewayAgentFramework` / `AgentFrameworkRolloutAdapter` / V1 PPO trainer。实际完成sync、colocate_async、separate_async三种布局。算法 GRPO，FSDP2，BF16 forward、FP32参数，SDPA，无 FlashAttention / Megatron 编译需求。
- 自定义任务是隐藏表记录的工具读取 + 算术：prompt 只给记录ID和表达式，`lookup_record` 实际通过 Sandbox 读取记录文件。v2按原生ReAct语义接受合法finish字符串或plain final整数。每轮最多4步。
- 这是实际 Uni-Agent agent/tool/gateway/token-mask/rollout-logprob/TransferQueue/optimizer 闭环，不是把普通单轮 GSM8K 当作 agentic RL。

## Docker 与资源

- Docker `ua-lab-rl`，基于已有 `vllm/vllm-openai-rocm:nightly-9ea8f3ffc354901b740f0b31988900897b7221d7`。
- 已固定镜像digest `vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa`；本机image ID `sha256:33b992ce0f367784daf23c535ed63af3703e55ade7f5016a08e9c41a24a7a3b9`。
- 实测镜像：PyTorch `2.12.0+git6bbd260`，ROCm/HIP `7.2.53211`，vLLM `0.28.1rc1.dev516+g9ea8f3ffc`，Python3.12.13。
- Python venv `/lab/envs/rl` 使用 `uv venv --system-site-packages`；训练轻量依赖放 venv，保留基础镜像ROCｍ torch/triton。
- `/lab`、HOME、cache、容器 `/tmp` 均落在 `/path/to/uni-agent-lab`。前期GPU使用物理2,3,4,5；32B pilot使用2–7。`RL_GPU_IDS`显式指定物理卡，未修改主机驱动/sysctl。
- 本任务的 `sandbox.provider=local` 指运行在训练Docker内的文件工具；各episode有独立文件目录，没有执行模型生成的任意shell。它不是每条episode一个嵌套Docker容器。

## 数据与reward（包含已保留的失败pilot）

- `scripts/rl_prepare_data.py` 固定seed生成192条train、48条heldout、8条独立smoke validation。
- 三类表达式：`a+b+c`、`a-b+c`、`a*b+c`。训练与heldout ID/随机种子均不同，隐藏记录不在prompt。
- 第一个pilot的strict accuracy要求成功读取记录后必须调用finish给出整数。原生ReAct也允许无tool call的plain final reply；0.6B几乎总是在lookup后直接回复，导致这个人为协议条件完全堵住正奖励。
- v2按原生ReAct终止语义验奖：必须成功读取记录；最后plain reply里的最终整数或合法finish字符串与隐藏记录计算值完全一致，才accuracy=1。末尾小数/分数不作为整数，未lookup猜对仍是0。
- shaping reward：准确为1；错误答案但已读取记录0.15；额外可解析整数final answer格式0.05。reward和accuracy分开汇报，不把过程分当成功。
- `runs/rl/reward-validation.json` 保存正确答案、错误答案、未观察猜测、错误格式、观察前提交这5种独立验证。
- `scripts/rl_validate_reward.py` / `runs/rl/reward-validation-v2.json`覆盖11项positive/negative控制，包括合法plain、合法finish、错误数值、未观察猜对、观察前提交、小数/分数、finish传number违反schema。

## 已遇到的问题

1. 普通 `uv pip install` 即使在system-site-packages venv内也会解析/安装最新CUDA torch，初次解析下载并装入torch2.14和CUDA依赖。已完整卸载该venv的torch/triton/nvidia-/cuda-*以及额外numpy，恢复继承镜像ROCm包。之后安装使用 `--no-deps` 和准确的轻量依赖快照。GPU tensor与import探针已确认实际torch.version.hip非空。该问题没有改动基础镜像。
2. 同设 `ROCR_VISIBLE_DEVICES=2,3,4,5` 和同值HIP会产生二次过滤；`torch.cuda.device_count()`甚至仍返回4，但第2、3逻辑卡无效。实际逐卡tensor验证必须做。
3. ROCR物理选择 + HIP0,1,2,3在单进程可工作，但verl Worker显式拒绝同时设ROCR和HIP/CUDA。最终启动脚本只设 `HIP_VISIBLE_DEVICES=2,3,4,5`，unset ROCR；Ray识别HIP，verl worker自身转换成CUDA选择。物理PCI bus ID：102、22、246、134。
4. 缺失Torchtitan/VeOmni/Megatron等的启动warnings来自未选用的可选backend，本路线FSDP2已成功初始化，不需为消除warning安装CUDA训练栈。
5. vLLM 0.28严格要求HIP/CUDA两个可见卡变量一致，verl vLLMHttpServer仅缩窄CUDA而继承旧HIP list会失败。在独立verl工作树加AMD下同步HIP的4行兼容变更，patch在 `scripts/rl_verl_rocm.patch`；GPU训练模型未做算法改动。
6. vLLM 0.28每次 `load_weights` 都检查tied embedding。verl默认2048MB桶将FP32的0.6B模型分成多桶，若lm_head与embed_tokens在不同桶会误报embedding未初始化。本实验把 `rollout.checkpoint_engine.update_weights_bucket_megabytes=4096`，让小模型全部参数在同一次加载。对大模型应采用上游支持分桶的兼容loader，而不能无限增大桶。

## 入口与证据

```bash
docker exec ua-lab-rl /lab/envs/rl/bin/python /lab/scripts/rl_prepare_data.py
docker exec ua-lab-rl bash /lab/scripts/rl_run.sh --check-config
docker exec ua-lab-rl bash /lab/scripts/rl_run.sh --run-dir /lab/runs/rl/smoke
```

- `configs/rl_grpo.yaml`：覆盖精确pin的原生ppo_trainer配置。
- 每次run保存 `resolved_config.yaml`、`episodes/*/episode.json`（完整对话/工具观察/严格判分）、`agent_logs/step_*/.../trajectory.json`和`trajectory.npz`（原生token IDs/mask/logprob）。
- 原生TensorBoard scalars、rollout JSON、validation JSON和训练checkpoint保存于run目录。
- `runs/rl/environment-freeze.txt` 是额外venv包快照；模型与ROCm包继承基础镜像。
- `configs/rl_requirements.lock` + `scripts/rl_setup_env.sh` 用 `uv pip install --no-deps` 复现额外环境，不重新解析CUDA torch。

## Pilot 结果：闭环通，但学习没有发生

`runs/rl/smoke` / `runs/rl/smoke-06.log` 实际完成2步GRPO、64条训练trajectories，以及训练前后各8条validation，进程exit0。两步 `actor/grad_norm=0`、全部奖励0.15、GRPO advantage全0；保存checkpoint后与初始模型抽查9个参数tensor，12,585,984个数值无任何变化。`runs/rl/smoke/analysis.json` 保存完整证据。不能把这次报告成有效RL训练。

旧数据保留 `/lab/data/rl_record_protocol_pilot`，旧reward协议以 `answer_protocol=finish`（默认）仍可复现。v2数据显式设置 `answer_protocol=final_integer`，使用独立run目录，不覆盖pilot轨迹。

## Smoke v2：已验证真实参数更新

`runs/rl/smoke_v2` / `runs/rl/smoke_v2.log` 从原始base重新跑2步，exit0：

- clip前 `actor/grad_norm` 分别 `9.344083786`、`2.600953102`，GRPO advantage有正有负。
- 训练平均reward分别 `0.292187512`、`0.46875`。这两步样本不同，不能把均值上升直接当作泛化提升。
- 8条独立smoke greedy validation，训练前/后 accuracy 都为50%。
- `analysis.json` 将step2 HF checkpoint与原始base数值比较，确认真实参数变化；例如最后层q_proj最大绝对变化约`2.0012e-5`。
- 原生Gateway轨迹均保留每token的response_mask、rollout logprob，分析脚本检查长度对齐与有限值；观察内容进入上下文但loss mask为0。

宽松的事后数值诊断还识别裸JSON/boxed整数；8条smoke该诊断从87.5%到50%。这个诊断不影响训练reward，也不能把“strict没掉”当成能力没有退化。

## 64步工具任务：真实更新后退化

`runs/rl/grpo_64` 完成64步、exit0，训练段约8分30秒。配置 `configs/rl_grpo_64.yaml`，从base独立开始，LR1e-5，无KL。消费2048个训练session，step0/16/32/48/64各评估同一48题，共240个验证session。

- 自身step0→step64的strict accuracy为12.5%→0%，宽松数值诊断33.33%→0%。
- step16虽然得到0.2过程reward，但答案通常只复述一个记录字段，数值全错；step32以后工具JSON也破坏了，reward和梯度逐渐归零。
- checkpoint仍证明有真实参数改变：抽查9个tensor，12,585,870/12,585,984个值变化，最大绝对变化约2.339e-4。
- 学习率、缺少KL、稀疏小数据和过程reward局部最优都是可以继续检验的解释，本次未隔离因果。

## colocate_async：布局跑通，协议指标不代表数值能力

`runs/rl/colocate_async_8` 完成8步、exit0，约76秒训练段。从base独立开始，LR1e-6，KL coefficient 0.01，FSDP2 reference policy。

- 同一run的48题strict accuracy为8.33%→31.25%；事后数值诊断为39.58%→31.25%。因此协议指标提高，但没有建立数值能力提高的证据。
- step8 clip前grad_norm约8.433、KL loss约0.01383、训练reward约0.69844；各batch内容不同，不能把训练均值变化当作验证提升。
- 9个抽查tensor中12,584,959/12,585,984个值变化，最大绝对变化约8.077e-6。
- 各run的同一base greedy基线不完全一致，具体原因未隔离；比较以各run自身step0为准，不把跨run基线差异当作训练效果。

两条固定heldout曲线在 `plots/rl_record_validation.{png,svg,pdf}`，数据在各run的 `validation_curve.csv` / `validation_comparison.json`。图中的宽松诊断仅供判别格式变化。

## 官方4B MemAgent separate_async smoke

`runs/rl/memagent_separate_4` 完成4步、exit0，训练段约3分58秒，另有模型/engine初始化。直接使用官方 `HotpotQAMemAgentDataset`、`MemAgent`、`HotpotQATask` 和原始boxed token-LCS奖励，未新增任务奖励。

- 模型为Qwen3-4B-Instruct-2507；数据按官方文件顺序选前32条train、前8条dev，未按reward/答案筛选，所有文档context保留。`data/rl_memagent/manifest.json`记录来源。
- 2 trainer GPU + 2 standalone rollout GPU。原生实现同时初始化trainer侧hybrid replicas用于验证/切换；总计仍是物理2/3/4/5四卡。`backend=nccl` 在ROCm上实际走RCCL。
- rollout n=4，train batch=2，LR1e-6，无KL，memory384/final192为smoke预算。4步梯度分别约1.416、1.944、2.230、0.959，均有非零GRPO advantage。
- 实际消费32个训练episode、216条context trajectory；额外8个episode是异步预取，未进入这4步优化。前后各8题验证分别57个context，不能按57题统计。
- 每步权重同步约1.67–1.83秒。后3步trajectory staleness为1；这是异步运行的实际证据。
- 9个抽查tensor共62,922,240个值，其中62,915,770个改变，最大绝对变化约1.944e-5。保存的398个Adam参数state中step均为28，表明4个trainer global step共做了28次mini-batch更新。
- 固定8题question-weighted LCS为0.65→0.40；满分LCS题比例62.5%→37.5%，2题变差、6题不变。小样本且训练退化，不能报告成能力提升。
- `memagent_validation.json`按episode UID选最后context，重算原始奖励，并核验该reward广播到每段memory。`analysis.json`检查原生token/logprob数组对齐和有限值，全部通过；MemAgent每段观察在prompt中，故response_mask可以全部为1。
- MemAgent的`finished` marker在此路径为null，不代表所有episode失败；模型调用数从task.log的context prompt计数。可读多context文件在 `decoded_examples/`。

4B的FP32参数与tied embedding约需单桶容纳，专用配置设置24576MB桶；没有改变0.6B的4096MB设置。基础镜像已包含ROCm CuPy，RCCL checkpoint engine不需另装CUDA CuPy。

## 预先固定的32步官方对照

配置 `configs/rl_memagent_separate_32.yaml` / `configs/rl_memagent_long_task.yaml`，从原始base重新开始。协议在初次验证前写入 `notes/rl-memagent-32-protocol.json`，含配置与数据sha256。前128train/前32dev，训练32步、每8步评估/保存、只保留最近2份checkpoint；LR1e-7、KL0.01、train batch4、rollout n4，memory/final都恢复1024token。

原始context经官方tokenizer切块审计，train每题5–6块、dev6–7块，max_chunks16足够保留所有文档；没有根据中途成绩修改超参或挑样本。该对照同时改变了数据规模、学习率、KL与memory预算，不是单因素实验。

最终32步完成、exit0，训练段52分57秒。实际消费512个session、3460条真实context和124条synthetic padding；128个GRPO prompt group里51组奖励非均匀，证明存在任务奖励的policy-gradient信号。保存的398个Adam state均step224；9个tensor共62,922,240个值中62,896,252个改变，最大绝对变化8.559e-6。最后step32组内奖励相同，adv与PG均0，当步非零gradient来自KL，不能将每步都描述成有效任务学习。

固定32题dev的question-weighted LCS，step0/8/16/24/32为0.380878、0.420685、0.347768、0.410268、0.389691；最终4题提高、5题变差、23题不变。终点仅+0.008813且中途波动，没有稳定能力提高证据。full-score比例从8/32到9/32。验证与训练的原始LCS均已独立重算；PR183精确匹配score的格式差异另见`memagent-upstream-alignment.md`，未混入训练奖励。checkpoint只保留step24和step32。

## 官方SWE 4B：两次优化已完成，最终验证失败

`runs/rl/swe_separate_2`使用原生SWEBenchTask、ReAct、binary resolved奖励；题目固定为已过正负oracle的Flask-5014和pytest-7982。训练与验证重用这两题，只是engineering replay，不是heldout。模型原始base，LR1e-7、KL0.01、batch2×n4，2 trainer+2 rollout，mask_unfinished_episode=false；每个Docker sandbox经`/lab/run/docker.sock`独立启动，candidate patch与verifier stdout/stderr/exit code/result均保留。

首步消费8个episode，两题GRPO rewards分别为[1,0,1,0]和[0,1,1,0]，adv范围±0.866024，PG loss=-0.121390、KL loss=0、clip前grad_norm=2.450512。这是由任务奖励驱动的真实优化。step2 checkpoint完整写出，398个Adam state均step2，抽查9个tensor有非零变化。随后在最终验证切换的vLLM `wake_up(tags=['kv_cache'])`发生ROCm显存分配OOM，入口exit1。因此不能称为完整两步运行通过，也没有after-validation成绩。基线只是两题replay的1/2成功。

上游`TrainerBase.fit`先优化和保存，再验证，最后才写rollout dump和TensorBoard；故step2优化状态存在，但step2 rollout JSONL和scalars未落盘。`analysis.json`中的8 consumed sessions和2个非均匀group仅覆盖已写出的step1，不能视为全部生成轨迹。19个完成的train task日志包含异步预取，不能用其总数替代优化量。未为提高表面成功率无限重跑，按新优先级切换32B路线。

## 32B升级：固定划分与独立pilot

Qwen3-32B dense固定revision `9216db5781bf21249d130ec9da846c4624c16137`，原始权重61.02GiB，untied embedding。官方train前512、dev0–63监控、dev64–127只做外部base/final，文件位于`data/rl_memagent_512_64_64/`；manifest保存每条源行SHA256和parquet/JSON SHA256。train5–6块、两个dev分区6–7块，均不截context。

`configs/rl_memagent_32b_pilot.yaml`及`notes/rl-memagent-32b-pilot-protocol.json`定义独立1步工程校准。4 trainer +2 rollout TP2，HIP2–7，batch4×n4，LR1e-6、KL0.01、norm_adv_by_std=false，no-thinking、SDPA，8GiB权重桶。正式协议将在pilot性能结果后确定，正式训练从原始base重新开始。

源码审核确认`checkpoint.save_contents=['hf_model','extra']`跳过native model和optimizer shards，只保存HF权重及极小scheduler/RNG state。HF可能仍为FP32 master precision，预计约122GiB/份；不能以初始化save_model的BF16配置推断磁盘tensor精度。scheduler仅在一次train_mini_batch循环的最后iteration更新，因此warmup按trainer global step计数，而不是每个Adam mini-batch。Gateway从data配置将`enable_thinking=false`传给codec和generation suffix；32B tokenizer实测后缀为已闭合的空think块。

2026-09-12续记：原multi_sender=True的首次多桶同步在环境中断前停滞，栈与拓扑证据已保存。single-sender实际通过发生在环境恢复后；旧single_sender入口没有真正启动模型。未在恢复后的相同环境重做multi_sender受控A/B，因此只确认所选single-sender配置全链路可用，不将单个flag视作唯一已证明原因。恢复后的1步pilot完整完成、4rank各707个Adam state均step7、HF约122.059GiB，随后已冻结并启动6卡的128步正式训练。详细协议、实际时序和结果边界见`rl-32b-long-run.md`及`rl-memagent-32b-128-protocol.json`。

## 复现入口与环境分层

在已经下载模型/数据并保留固定源码的本实验目录，宿主可用：

```bash
bash scripts/rl_reproduce.sh smoke_v2
bash scripts/rl_reproduce.sh colocate_async_8
bash scripts/rl_reproduce.sh memagent_separate_32
```

入口检查verl pin和ROCm patch、复用正确挂载的容器、拒绝与现有RL进程并发、使用新的带时间戳run目录，最后自动做轨迹/参数/验证分析。`RL_RUN_NAME`可指定新的唯一名称。`rl_launch.py`也拒绝覆盖含真实实验产物的旧run。执行过shell语法、Python编译和“训练运行中拒绝新任务”检查；完整入口实跑结果将单独注明。

`rl`是固定ROCm训练环境；`rl-swe`通过.pth继承rl并仅添加 `rl_swe_requirements.lock` 的SWE依赖；`rl-plots`仅用于matplotlib图表。全部用uv且安装时不解析依赖，避免替换ROCm torch。三个setup脚本均可重入。图表重绘：

```bash
docker exec ua-lab-rl bash /lab/scripts/rl_setup_plot_env.sh
docker exec ua-lab-rl /lab/envs/rl-plots/bin/python /lab/scripts/rl_plot_results.py
```
