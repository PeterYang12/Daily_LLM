# Terminal-Bench 2.1：三项原生Task诊断

[本实验总览](README.md) · [全部实验](../README.md)

使用Uni-Agent固定pin内的`terminal_bench` Task，Docker sandbox和本地Qwen3-Coder-30B-A3B-Instruct原生ReAct。Harbor只负责下载任务，不承担agent执行，也没有调用外部模型API。三题单次模型推理已全部完成：**resolved1/3、finished2/3、两者交集1/3**。模型阶段来源位于 model/provenance.json（原始文件：`results/tbench-v1/model/provenance.json`），完整结果与逐项分析见 run/results.json（原始文件：`results/tbench-v1/model/run/results.json`） 和 analysis.json（原始文件：`results/tbench-v1/analysis.json`）。

## 固定选择和资源

Harbor **0.22.0**安装在独立`envs/tbench`；CPU/RL环境未安装或升级依赖，显式constraints禁止Harbor环境引入torch。下载引用严格为：

`terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a`

89/89任务成功下载。先按CPU≤4、memory≤8192MB、GPU=0、公开预构建镜像和可用环境变量筛查，再按`SHA256("uni-agent-tbench-cpu-v1-20260912\n" + task-directory-name)`升序取三项。所选镜像匿名访问成功并锁为linux/amd64 manifest digest，未按oracle或模型表现换题。

| 任务 | CPU | RAM | 任务内容 |
|---|---:|---:|---|
| rstan-to-pystan | 4 | 8GiB | 把RStan脚本迁移为PyStan3.10.0，实际运行后验采样并输出参数均值CSV |
| multi-source-data-merger | 1 | 2GiB | 合并JSON/CSV/Parquet用户记录，按来源优先级处理冲突并生成报告 |
| qemu-alpine-ssh | 1 | 4GiB | 启动真实Alpine虚拟机并配置可登录的SSH服务 |

selection.json（原始文件：`results/tbench-v1/selection.json`）保留完整元数据排序、选中项和镜像manifest。三镜像压缩层约1.58GB；隔离Docker daemon实际新增unique解压image层 **2,458,429,138 bytes**，低于50GB预算。没有清理其他镜像。计账不含临时容器可写层；每个Task结束后由原生sandbox释放容器。

最初增加的“声明verifier timeout≤300s”过滤条件在89项上得到空集；所有声明值均≥360s。该额外条件在任何oracle/模型执行前删除，改为执行时限；初稿与amendment均保留，未利用效果改选样本。

## 模型与时间预算

固定max_steps=100、每次输出最多2048token、temperature=0.2、top_p=0.9；每Task outer timeout1200s，agent≤900s、verifier≤240s，同时至多两项任务。原任务声明的900–1800s等时限被缩短，因此这里只是受限时间下的小型诊断，不是官方排行榜结果。

**55K是原生ReAct的当前请求token停止阈值，不是累计API消费上限。** `agent.py:143`把`info.total_tokens`覆盖为本次`prompt_tokens + completion_tokens`，不是累加，也不读取`generation_info.total_tokens`。下一次生成上限根据前一次保存的用量计算；新工具输出增大prompt时，一次请求可能跨过55K阈值。服务另有65536硬context限制。每次成功响应的usage及累计prompt/completion用量另行保存，见预算解释（原始文件：`results/tbench-v1/budget-interpretation.json`）。

## 先验证任务环境，再评价模型

validated-controls.json（原始文件：`results/tbench-v1/validated-controls.json`）确认同三题的有效控制均为baseline0/oracle1，而且实际pytest已运行，控制阶段模型调用为0。

| 任务 | baseline | official oracle | oracle耗时 |
|---|---:|---:|---:|
| rstan-to-pystan | 0 | 1 | 554.1s |
| multi-source-data-merger | 0 | 1 | 10.6s |
| qemu-alpine-ssh，同一APT snapshot设置 | 0 | 1 | 60.7s |

QEMU最初的baseline与oracle都得到原生reward0，但其Debian Bullseye security索引过期、旧package URL404，curl/uvx缺失，pytest根本没启动。`test.sh`仍写了reward0，原生`eval_completed=true`只说明奖励文件可解析；不能把它当作判题链路有效的充分证据。原始失败保留在`baseline`和`oracle`目录。

修复只在一次性QEMU容器内，把security仓库指向`https://snapshot.debian.org/archive/debian-security/20260901T000000Z`，允许读取历史Release；没有改变官方solution、tests、VM命令、任务提示或gold。单独APT探针确认包可安装，再以同设置重跑baseline和oracle，得到真实SSH测试的0/1。QEMU模型case使用相同设置。脚本与hash见 tbench_qemu_snapshot_setup.py（原始文件：`scripts/tbench_qemu_snapshot_setup.py`） 和两次snapshot控制的provenance；节点APT配置没有改变。

QEMU使用host网络以适配隔离daemon；每次任务前检查2222与6665端口未被占用，避免接触其他服务。虚拟机进程随所属sandbox结束而退出，不复制32GB虚拟磁盘作为实验产物。

## 单次Coder30B结果

| 任务 | resolved | finished | 模型响应数 / 实际工具调用数 | 最后一次请求token数 |
|---|---:|---:|---:|---:|
| rstan-to-pystan | 0 | false | 50 / 49 | 63,499 |
| multi-source-data-merger | 1 | true | 25 / 25 | 11,832 |
| qemu-alpine-ssh | 0 | true | 29 / 29 | 6,117 |

data-merger实际生成4个唯一用户、4条冲突报告，输出`user_id`为int64，其余列为string，官方3项pytest全部通过。过程中尝试把二进制Parquet作为UTF-8文本查看，收到一次editor错误后改用程序读取并完成提交。输出 merged_users.parquet（原始文件：`results/tbench-v1/model/run/terminal-bench__multi-source-data-merger/files/app/merged_users.parquet`） 和 conflicts.json（原始文件：`results/tbench-v1/model/run/terminal-bench__multi-source-data-merger/files/app/conflicts.json`）已保留。

RStan的模型在Python依赖安装与环境切换中累积了大量APT输出，第50次请求达到63,499token，触发原生55K停止阈值。该次响应仍提出运行`pystan_analysis.py`，但停止检查发生在工具执行前；最终四份CSV都没有产出，官方测试5失败/1通过。不能由此推断增加预算必然成功；已保存代码和完整轨迹（原始文件：`results/tbench-v1/model/run/terminal-bench__rstan-to-pystan/agent-result.json`），没有重试挑最好一次。

QEMU模型先使用已废弃的`-redir`，之后留下Stopped的后台`-nographic`任务，又尝试不兼容的`-serial stdio -daemonize`组合，多次重启遇到端口转发错误。最终提交的是说明文档，SSH测试失败。原生oracle在同一镜像、资源与APT snapshot设置下成功，所以不能照抄模型自己所说的“环境不支持虚拟化”。完整轨迹（原始文件：`results/tbench-v1/model/run/terminal-bench__qemu-alpine-ssh/agent-result.json`）保留了这些命令与观察。

这也说明`finished=true`仅表示agent调用submit或正常停止；`errors=0`也不表示所有shell命令都返回0。最终仍需看任务verifier。三题累计有104次成功模型响应，API报告的prompt tokens合计 **1,195,910**、completion tokens合计 **13,805**，与“每次当前上下文55K阈值”是不同计数。端到端耗时包含观察hook保存产物的成本，不是独立的模型吞吐测试。

## 运行入口与产物

`prepare_tbench.py`调用官方`build_task_row`封装三题，保留tests/solution archive、固定镜像digest和原始timeout字段。模型只收到官方task prompt；solution只在oracle分支装入新sandbox，tests在agent结束后才由native verifier装入。各阶段使用全新容器。

```bash
cd /path/to/uni-agent-lab
python3 scripts/run_tbench_cases.py baseline
python3 scripts/run_tbench_cases.py oracle
python3 scripts/run_tbench_cases.py baseline --name qemu-baseline-snapshot \
  --task qemu-alpine-ssh --qemu-snapshot-env
python3 scripts/run_tbench_cases.py oracle --name qemu-oracle-snapshot \
  --task qemu-alpine-ssh --qemu-snapshot-env
python3 scripts/run_tbench_cases.py model --qemu-snapshot-env
```

这些是已记录的执行命令；已有目录会被拒绝覆盖，重放必须用新的`--name`。runner保留完整agent transcript、每响应usage、原始verifier stdout/reward、oracle日志，以及要求提交的Python/CSV/Parquet/JSON文件；观察hook不改变评分或模型返回值。只有显式启用的QEMU snapshot设置会改容器APT配置。

resolved、finished、工具错误、verifier实际运行状态和累计API用量需要分别报告。三项case展示框架能力和模型工具行为，不是RL训练后的效果比较，也不能外推到完整Terminal-Bench。
