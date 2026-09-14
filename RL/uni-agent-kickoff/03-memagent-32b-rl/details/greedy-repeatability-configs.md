# ROCm Qwen3-32B greedy 推理重复性：限定配置对照

2026-09-12，专用HIP1、`ua-lab-infer32-stable`、端口18084。这个实验调查同一base、同一greedy请求为什么会给出不同文本，不使用QA正确率或奖励选择配置。原18083服务、`serve_qwen3_32b.sh`、模型文件和训练配置均未修改。

结果：原服务配方克隆下，62次请求产生 **5种** assistant.content；只增加 **`VLLM_BATCH_INVARIANT=1`** 后，同样62次请求产生 **1种** assistant.content。两组均0错误、62次正常stop，没有256-token截断。这个结果只覆盖本次固定输入和调度方式，不是全模型或全部评测的确定性保证。

| 项目 | default克隆 | batch-invariant |
| --- | ---: | ---: |
| 完全相同请求体的调用数 | 62 | 62 |
| 逐字不同content数 | **5** | **1** |
| 第一轮3次串行的content数 | 2 | 1 |
| 第一轮6并发的content数 | 2 | 1 |
| 第一轮16并发的content数 | 4 | 1 |
| 错误 / 非正常stop | 0 / 0 | 0 / 0 |
| 实际attention后端 | ROCM_ATTN | TRITON_ATTN |
| max_num_seqs / prefix cache | 16 / 开启 | 16 / 开启 |
| 全部client profile墙钟时间 | 38.45秒 | 137.04秒 |

墙钟时间混合冷启动后的首次请求、暖缓存、串行与并行，节点上同时有RL及其他实验；输出文本也不同，因此不把这两个时间当模型吞吐benchmark。batch-invariant的唯一文本**并非**default的五种之一：该模式还改变了数值与生成路径，不能与冻结primary评测当作同一协议混用，也不能从重复性推断回答更正确。

## 固定输入与操作顺序

请求逐字复制自原12次probe请求（原始文件：`results/large-memagent/greedy-fixed-prompt-probe/request.json`）：固定external首题的首个5000-token chunk，唯一user message，`temperature=0`、`top_p=1`、`max_tokens=256`。未添加seed、修改prompt、读取gold答案或调用reward函数。

每个服务配置只启动一次，执行两轮相同profile。每轮为串行3次、6并发、串行3次、16并发、串行3次，共31次。并发组使用线程barrier同时提交。比较完整assistant.content，保留raw HTTP响应、请求hash、时间、finish reason及usage；不把响应ID或时间戳差异算内容变化。

服务为固定Qwen3-32B BF16 base，revision `9216db5781bf21249d130ec9da846c4624c16137`，TP1、eager、16384窗口、GPU利用率0.55、hermes工具parser、qwen3 reasoning parser、默认非thinking。default只将primary配方的GPU和端口移到独立资源；batch-invariant仅增加环境变量，vLLM内部自动切换attention并应用对应算子路径。

事先冻结的协议（原始文件：`results/large-memagent/greedy-stability-configs/protocol.json`）最多允许3配置、约45分钟。第三个“关闭prefix/cascade并限制max_num_seqs=1”的边界对照只在batch-invariant失败或仍不一致时执行；本次未触发，因此没有执行第三配置。整个结果没有按QA成绩筛选。

## 当前镜像的支持证据

镜像为固定ROCm digest，torch2.12.0+git6bbd260、vLLM0.28.1rc1.dev516+g9ea8f3ffc.rocm723、Transformers5.16.1，宿主amdgpu7.1.0.31500000。未安装或更换包。

只读源码检查确认环境变量存在，dense linear在CUDA-like平台有batch-invariant路径，TRITON_ATTN声明支持batch invariance；启用时cascade会被禁用。ROCm没有对应NVIDIA架构的tuned GEMM表，不能仅凭源码存在就认为可用。本机进一步完成了实际启动和62次生成，日志确认后端由ROCM_ATTN切成TRITON_ATTN。

关闭prefix、disable_cascade_attn和max_num_seqs参数均能在当前arg_utils源码找到。最初在不挂GPU的CPU容器运行`vllm serve --help=...`时因无法推断device type失败，原日志保留；这是CLI帮助初始化失败，不是某个服务配置失败，也没有删掉它伪装成全部检查通过。

## 原始记录与复现边界

- 完整配置对照（原始文件：`results/large-memagent/greedy-stability-configs/comparison.json`）
- default逐组统计（原始文件：`results/large-memagent/greedy-stability-configs/default/summary.json`）及batch-invariant逐组统计（原始文件：`results/large-memagent/greedy-stability-configs/batch_invariant/summary.json`）
- 每请求CSV（原始文件：`results/large-memagent/greedy-stability-configs/requests.csv`）、不同content及频数（原始文件：`results/large-memagent/greedy-stability-configs/content-variants.json`）
- 完整源码支持扫描（原始文件：`results/large-memagent/greedy-stability-configs/vllm-support-scan.json`）、支持审阅（原始文件：`results/large-memagent/greedy-stability-configs/support-review.json`）、runtime（原始文件：`results/large-memagent/greedy-stability-configs/runtime.json`）
- 限定实验控制脚本（原始文件：`scripts/probe_greedy_stability.py`），两组`service.json`保留精确argv和环境变量，各目录`server.log`与`responses/`保留全部原始输出。

本次执行入口依次为`probe_greedy_stability.py start default`、`probe default`、`start batch_invariant`、`probe batch_invariant`。它拒绝覆盖既有配置目录和响应记录；这里记录的是已完成操作，不应重复执行来覆盖证据。

该比较没有测试不同prompt混合batch、完整64题多轮轨迹、跨服务重启一致性、logits/张量逐bit一致性或训练后模型；也没有隔离到某一个内核原因。原primary协议保留，若进一步评测64题，应使用独立输出名和明确的batch-invariant辅助协议。

当前服务保留在18084供root决定后续辅助实验。原`run_large_memagent.py`写死18083及primary服务容器，不能直接用它给18084辅助结果写provenance；辅助入口必须同时记录真实endpoint、服务容器和模式，不覆盖冻结primary。
