# 复现审计：CPU、推理服务、Sandbox 与黑盒 Agent

审计目录 `/path/to/uni-agent-lab`。本记录把“当前机器已经跑通”与“换一个空目录能否重新准备环境”分开。模型输出有采样随机性，即使固定输入也不承诺重现完全相同的 patch 或分数。

## 已实际复现与审计范围

- CPU 源码归档、81 项 Python 覆盖层 lock、固定 ROCm 镜像，已实际在 `/path/to/uni-agent-lab-replay` 重建。Gateway、Framework、Agent、Sandbox、SWE-bench、Ray 导入通过，并确认 GPU 不可见。没有重跑原环境的 685 个测试。
- 后续只更新该 replay 容器的 Docker CLI 绑定和 `DOCKER_HOST` / `UNI_AGENT_HOST_LAB_ROOT`，保留已安装 venv，再验证 Docker CLI、核心导入和 Claude Code 2.1.236。证据已复制到本 lab 的 mounts-smoke.log（原始文件：`results/cpu-replay/mounts-smoke.log`），原文件来自 replay lab。
- CPU/blackbox 修改通过 shell 语法、Python AST 与 SHA256 检查；先前的 25 条正式黑盒 trajectory 结构检查全部通过。此次审计没有重新采样这 25 个 episode，也没有重新执行基准测试。
- RL、Miles 启动脚本只做阅读审计，没有在本审计中修改或启动。它们的训练验证以各自记录为准。

## 固定输入清单

机器可读清单：`results/cpu-reproduction-inputs.json`，包括模型 revision、dataset revision、基础镜像 digest、六个 SWE 镜像 digest、源码归档和数据文件 SHA256。基础镜像原 tag 便于辨认，真正固定下载应使用 digest。

| 输入 | 固定版本 / 位置 |
|---|---|
| Uni-Agent CPU 源码 | `472c875a97f9a2764c81a6ec7581167632bd8bcc`，`results/cpu-uni-agent-source.tar.gz` |
| CPU 环境用 verl | `10db40d0da4d59150bb389960b77585f81a89b8d`，`results/cpu-verl-source.tar.gz`；与 RL worktree 不同 |
| CPU / vLLM 镜像 | `vllm/vllm-openai-rocm@sha256:67d4317ba8aa9e60171c4eaa74eda3d1e877011c809aa186687b524ab4472aaa` |
| Docker-in-Docker 镜像 | `docker@sha256:aa3df78ecf320f5fafdce71c659f1629e96e9de0968305fe1de670e0ca9176ce` |
| Qwen/Qwen3-0.6B | `c1899de289a04d12100db370d81485cdf75e47ca` |
| Qwen/Qwen3-4B-Instruct-2507 | `cdbee75f17c01a7cc42f958dc650907174af0554` |
| Qwen/Qwen3.5-9B | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| princeton-nlp/SWE-bench_Verified | 本地 HF cache revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`；正式输入是已保存的 `results/swe-data/verified-six.parquet` |
| BytedTsinghua-SIA/hotpotqa | `27275ff4fee67ac0acb6478e405e7ac07efbdc1a`，`data/hotpotqa/` |
| Claude Code | 官方 native binary 2.1.236，`cache/blackbox-claude-2.1.236`；SHA256 在清单中 |
| Portable mini runtime | python-build-standalone 3.12.13 / 20260602；`results/blackbox-mini-portable-freeze.txt` 锁定完整 Python 包版本 |

这里的 SWE revision 是本次缓存来源记录。上游 `build_swe_bench_verified()` 本身没有传 `revision=`；收尾的 `scripts/prepare_swe_cases.py` 在调用上游预处理器时绑定这个 revision，并拒绝写入已有非空目录。正式六例的原始 parquet 和 SHA256 仍保留，重新预处理请写入新目录再比较内容。

## 正确启动顺序

主 README 的新入口负责整合这些步骤；下面列出每步实际依赖，便于定位失败。

本次审计发现的主流程缺口已经促成三个新增入口：`download_assets.py` 固定模型/Hotpot revision 并选择下载内容；`lab_services.py` 管理服务启动及健康等待；`replay_swe.sh` / `replay_swe.py` 检查或按 digest 准备六例镜像，并用正式配置写入新的输出目录。对现有环境执行 `python3 scripts/lab_services.py status`，4B、9B、sandbox 均为 running / ready。这是现有服务健康检查，不是新节点完整重建的证明。

```bash
# 在已建立的 CPU Docker 中下载或验证选定模型；按需求选择大小。
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/download_assets.py \
  --models 4b 9b --hotpotqa eval
# 宿主命令只编排 Docker；模型和任务程序仍在容器内。
python3 scripts/lab_services.py start sandbox 4b 9b
bash scripts/replay_swe.sh prepare
bash scripts/replay_swe.sh check
# oracle / baseline / react4b / react9b 每次默认创建新输出目录。
bash scripts/replay_swe.sh oracle
```

1. 宿主 Docker 可用；GPU 推理还要求宿主 ROCm 驱动、`/dev/kfd`、`/dev/dri` 已可用。驱动不由应用容器安装。
2. 准备固定基础镜像和独立 CPU driver。CPU driver 可先于 GPU 服务启动，以便在 Docker 内下载资产。
3. 下载固定 revision 的模型到 `/lab/models/<模型名>`；下载任务所需数据。模型目录只含 config 而缺权重并不算完成。
4. 启动独立 sandbox daemon，经 `/usr/local/bin/dind dockerd` 进入正确的 cgroup nesting。等待其 socket **且 `docker info` 成功**。
5. 在独立 daemon 中准备任务镜像：六例 SWE 镜像按清单 digest 拉取，再 tag 为数据中的名字。CPU 主机 Docker 中有同名镜像，不等于独立 daemon 中也有。
6. 创建推理容器，再启动 vLLM 进程；等待 `/health` 和 `/v1/models`，确认实际模型名、端口及 context 设置。
7. baseline / gold oracle 验证六例环境；然后运行 ReAct 或黑盒 Agent，输出使用新目录。黑盒 driver 在运行前校验已有的六例 oracle 结果。

当前约定：GPU0 / 18080 对应 Qwen3-4B-Instruct-2507，GPU1 / 18081 对应 Qwen3.5-9B。CPU driver 不映射 GPU。所有任务 sandbox 使用 host network，因为本机没有 docker0，独立 daemon 明确禁用了 bridge 和 iptables。

### CPU 环境重建入口

```bash
CPU_CONTAINER=ua-lab-cpu-fresh bash \
  /path/to/uni-agent-lab/scripts/cpu-reproduce.sh \
  /path/to/uni-agent-lab-fresh
```

这里的容器名和目标目录必须是新的。原 `ua-lab-cpu-replay` 容器随宿主中断（原始文件：`results/environment/interruption-20260912.json`）丢失，已验证的 replay 目录和日志保留；脚本默认目录仍有源码，所以会拒绝覆盖，应选择新目录。主 CPU 容器另已恢复验收（原始文件：`results/cpu-restoration-20260912.json`）。脚本固定基础镜像 digest，缺镜像时拉取，验证 image ID，校验 `results/cpu-reproduce-inputs.sha256`，再用 `--no-deps` 安装精确覆盖层，避免替换镜像中的 ROCm torch/vLLM。

此入口只重建 CPU 环境，不是完整实验室的一键复制。它复制两个源码归档、Python lock，以及存在时的 Claude binary。若在新 lab 中复现 blackbox，需要另外准备 `scripts/`、`configs/`、模型、`results/swe-data/verified-six.parquet`、对应 oracle 输出和 portable mini runtime，还要在这个 lab 路径启动自己的 sandbox daemon。单纯拷贝脚本而漏掉这些输入会失败。

CPU 容器中的 `/lab` 对应宿主的实际 lab root。黑盒挂载路径已经支持 `UNI_AGENT_HOST_LAB_ROOT`；`blackbox-run-swe.sh` 也接受 `CPU_CONTAINER`。修改 lab 路径时，两者须与容器挂载一致。新的 `start_*` / `lab_services.py` / `replay_swe.py` 也支持按脚本目录定位 lab，且复用容器前检查 mount；`run_memagent.sh` 已接受 lab/container 参数并拒绝已有输出。旧 `run_swe_*` 仍有原 lab 路径常量。服务管理器目前使用固定容器名和端口，所以另一个 lab 可以单独重建 CPU driver，但不能在同一节点用相同服务名并行启动整套副本。

### vLLM 的两阶段启动

旧 `start_infer_container.sh` / `start_infer9_container.sh` 只创建 `sleep infinity` 容器，不启动模型服务。对**新建且尚未启动 vLLM**的容器，完整流程还需要：

```bash
docker exec -d ua-lab-infer bash -lc \
  'exec bash /lab/scripts/serve_qwen3_4b.sh > /lab/logs/infer-startup-replay.log 2>&1'
docker exec -d ua-lab-infer9 bash -lc \
  'exec bash /lab/scripts/serve_qwen3p5_9b.sh > /lab/logs/infer9-startup-replay.log 2>&1'
curl --fail http://127.0.0.1:18080/health
curl --fail http://127.0.0.1:18080/v1/models
curl --fail http://127.0.0.1:18081/health
curl --fail http://127.0.0.1:18081/v1/models
```

启动需要加载权重，首次 `curl` 不通时应先读日志、等待服务，而不是再启动一个 vLLM。新整合入口应负责幂等检查与健康等待。正式六例需要 65536 max_model_len；早期 16K smoke 服务不足以复现 55K 上下文实验。4B 使用 hermes；9B 使用 qwen3_coder、language-model-only、enable_thinking=false。

### Sandbox 与 oracle

审计初始的 `start_sandbox_daemon.sh` 用可变 `docker:27-dind` tag、固定 gid 110，且最多等 socket 30 秒后没有最后的失败检查。收尾已经改成固定 image digest 和当前宿主组；整合入口 `lab_services.py` 会继续等待 daemon 的 `docker info` 成功。直接运行底层 shell 仍应以以下检查确认 daemon 已可用：

```bash
docker -H unix:///path/to/uni-agent-lab/run/docker.sock info
```

六例镜像 tag 和 digest 均在 `results/cpu-reproduction-inputs.json` 的 `swe_images` 列表。因为配置使用 `pull_policy: never`，缺任何镜像必须在运行前报错或完成下载。按 digest 拉取后还须恢复 `tag`，因为 parquet 中保存的是标签名。

`run_swe_oracle.sh` 默认跑最初四例子集；六例要显式传 `verified-six`。该官方并行示例的聚合输出布局与黑盒 driver 所需的逐例 `swe-oracle-six/<instance>/result.json` 不完全相同。为黑盒准备新的 oracle 时，应使用 `audit_swe.py` 与 `configs/swe-oracle.yaml` 输出逐例记录，或显式给 `--oracle-dir` 指向同样布局的新目录。

旧 `run_swe_react.sh` 固定使用 15K budget 的 `configs/swe-react-4b.yaml`，不能用于重现正式六例 40-step 实验。正式配置是 `configs/swe-react-64k.yaml`，数据是 `verified-six.parquet`。新的主入口应显式使用这两个文件，并避免覆盖原结果。

## 本次直接修复的 CPU / blackbox 缺口

- `cpu-reproduce.sh`：不可变 image digest；源码/lock SHA256；CPU 容器名校验同时接受主名称与派生名称；绑定 Docker CLI；设置 driver 的 socket 与实际宿主 lab root。
- `blackbox-swe-cases.py`、`blackbox-toy-case.py`：宿主挂载路径读取 `UNI_AGENT_HOST_LAB_ROOT`，不再只能用原 lab。
- `blackbox-run-swe.sh`：可选择 CPU container 和 lab root。
- 黑盒 runner 默认生成带时间戳的新 tag；拒绝复用已有输出，直接 Python driver 只有显式 `--resume` 才接受非空目录，并核对关键实验设置没有变化。
- Toy driver 拒绝已存在的 session 输出目录；文档重跑命令使用新的 session 和 JUnit 输出名，保护原始实验记录。
- `blackbox-build-portable-mini.sh`：优先使用完整 freeze，并通过 `--no-deps` 恢复；使用已下载的固定 Python archive，验证 SHA256 后解压。
- 刷新 `results/blackbox-artifact-hashes.json`。其中脚本 hash 对应审计后的版本；原始实验输出未改写。
- `notes/cpu-cases.md` 的 loss mask 描述改成条件式：开启 `mask_unfinished_episode=True` 才清零未完成轨迹。当前 mini-SWE 训练脚本默认开启；框架本身及通用 quickstart 默认关闭。

## 仍需保留的边界

- CPU 依赖锁只锁版本，未打包 wheelhouse 或逐 wheel hash；新机器仍需要相应软件包能下载。系统依赖主要由固定基础镜像提供。
- `/lab/envs/report` 是独立的绘图环境，不包含在 CPU bootstrap 内。迁移后先在 CPU 容器执行 `bash /lab/scripts/report-setup.sh`，固定安装 matplotlib 3.10.8 到 report overlay；它不修改 CPU lock 或 CPU venv。绘图只重建派生的 PNG/SVG。
- Toy Dockerfile 固定 Python 基础镜像、mini-SWE 与 litellm 两个主要版本，但其 apt 与传递 Python 包没有完整快照。正式 SWE 黑盒的 portable runtime 已有完整 Python freeze，且固定 Python archive。
- 原始 `results/swe-data/manifest.json` 描述最初四例，未改写历史文件。更新后的 `prepare_swe_cases.py` 会在新目录生成明确的 `manifest-six.json`；现有正式六例以 parquet 或本审计清单为准。
- `eval_12800.json` 含整数 ID context，被现有 MemAgent wrapper 拒绝。不能通过“让脚本成功退出”把它包装成有效长文评测；应下载并使用已验证的文本档。
- `rl_reproduce.sh` 要求另一个 `src/verl-rl` checkout 为 `a9f2985159536a607211dcac730d3f5d55028950`，且 `rl_verl_rocm.patch` 已应用。CPU 的 verl 归档不满足该要求；它也不包含 RL 的专用 venv。RL 入口已经注明“already downloaded lab checkouts and model artifacts”。
- Miles 另有 SGLang overlay、兼容补丁和基础镜像记录，见 `configs/miles-sources.json`。`miles_env.py` 对已有同名容器直接复用，未验证 `/lab` mount；换目录时应使用新名字，并由其独立记录验证来源。

上述审计说明准备条件，不改变任何已记录的 benchmark 分数，也不把 CPU import smoke 当作 GPU/RL 训练验收。

## 总报告的完整性检查

`python3 scripts/summarize_lab.py --require-complete` 会检查固定的 CPU、blackbox、MemAgent、Uni-Agent RL、Miles 底座及 retool 主报告。缺失文件、空的必要清单、坏 JSON 或不完整的六例记录会列入 `pending` 并返回非零退出码。`completion.complete` 表示报告材料齐全，**不是所有实验成功或训练有效**；例如 Miles 4B 零梯度、SWE 未解决、MemAgent 答错仍应完整记录。

原 `swe`、`uni_agent_rl`、`miles` 等绘图字段保留；新增 `miles_retool` 单列官方 0.6B、官方 4B、加强提示 4B 与 adapter 4B。最后一种明确是 rollout-only，optimizer update 不被推断为真。另新增 `protocol_probe`、`blackbox_diagnostics`、`cpu_rebuild`，并保留每项 source。

CPU 重建日志已经从 replay lab 复制到 `results/cpu-replay/{imports,mounts-smoke}.log`，来源与 SHA256 记录在 `results/cpu-replay-validation.json`，避免总报告只引用外部目录。utility 的缺文件 / 空 inventory / 坏 JSON 控制结果在 `results/summary-utility-audit.json`；新汇总通过绘图兼容检查，审阅图放在 `cache/report-review/results/figures/`，没有覆写正式图表。
