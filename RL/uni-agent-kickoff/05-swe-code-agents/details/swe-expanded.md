# SWE 诊断扩展集：独立于原六题的仓库分层子集

目标是为更大模型准备约 24–32 个可信的代码修复诊断，先验证环境和判题，再运行模型。本步骤不调用模型，不更新模型参数。

正式材料目录：`results/swe-expanded-v1/`。全部 32 题的 baseline/gold 验证已经完成，`validation-summary.json` 的 `final_for_requested_batch=true`。模型评测固定读取 **`validated-final.parquet`，共 29 题**；`validated-current.parquet` 只是验证过程中的中间产物。

## 最终结果

32 个原始代码 baseline 均 reward=0，32 个 gold patch 均 reward=1，所有 verifier 都完成且成功解析测试状态。按开始验证前声明的严格条件保留 29 题，未使用备用候选补足到 32。没有调用模型。

| 仓库 | 已验证候选 | 严格可用 | 排除 |
|---|---:|---:|---:|
| django/django | 8 | 8 | 0 |
| sphinx-doc/sphinx | 8 | 8 | 0 |
| sympy/sympy | 8 | 8 | 0 |
| pydata/xarray | 8 | 5 | 3 |
| 合计 | 32 | 29 | 3 |

三个排除样本的 gold 都通过，排除原因是 baseline 存在额外 PASS_TO_PASS 失败；这不是 gold oracle 无效的证据：

| 样本 | Baseline FAIL_TO_PASS 失败 | Baseline PASS_TO_PASS 失败 | Gold 结果 |
|---|---:|---:|---|
| pydata__xarray-4356 | 8 | 8：`test_min_count_nd[sum/prod-True-float/int/float32/bool_]` | 8 FTP + 604 PTP 全部通过 |
| pydata__xarray-6599 | 1 | 1：`test_polyval[timedelta-True]` | 1 FTP + 264 PTP 全部通过 |
| pydata__xarray-3993 | 2 | 4：`TestDataArray::test_computation[float64/int64-method_integrate-data/coords]` | 2 FTP + 2398 PTP 全部通过 |

已完成 final parquet 的 ID/顺序、与原六题不相交、与候选池原始行完全相等、无 oracle/agent override 泄漏检查。固定输入和逐题控制证据的 SHA256 见 `final-manifest.json` / `checksums.sha256`；便于浏览的完整 32 行表为 `final-cases.csv`。验证容器全部自行清理，未停止其他任务容器。

## 固定选样规则

- 输入沿用已固定的 `princeton-nlp/SWE-bench_Verified` revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`，共 500 题；原始 parquet 的 SHA256 保存在 `selection.json`。
- 与先前六题完全分开。选择四个此前未覆盖的仓库：`django/django`、`sphinx-doc/sphinx`、`sympy/sympy`、`pydata/xarray`。
- 每仓库对 `SHA256("uni-agent-lab-swe-expanded-v1-20260911:" + instance_id)` 排序，取前 8 个为首批候选，共 32 个；再保留每仓库后 4 个作为有序备用。候选按仓库内 rank 交错排列。
- 排序不读取题面、gold patch、答案或任何模型结果。`selection.json` 固定了全部 48 个候选的 ID、仓库、源行号、rank/hash 与镜像名；`initial-32.parquet` 保存首批候选。
- 如需替补，只沿同仓库的备用顺序，并继续遵守镜像预算。每个坏 oracle 都保留，并从最终模型评测子集排除。

这仍是**按镜像可用性、资源预算和 oracle 可验证性筛选的诊断子集**。它不是对 SWE-bench Verified 的无偏随机抽样，不应把这组通过率包装成全基准分数或公开模型排名。

`notes/research/swe-expansion-manifests.json` 是先行的八个镜像成本/可访问性调研，采用了不同的整数 ID 顺序；它不定义正式候选。正式规则以本目录的 `selection.json` 为准。

## 镜像预算与隔离

`cpu-swe-expanded-prepare.py` 只连接 `/lab/run/docker.sock`，即 data2 中的独立 sandbox daemon。每个镜像先读取 Docker Hub linux/amd64 manifest，按 digest 拉取并校验 image ID，然后恢复 dataset 需要的 tag。token 只在进程内用于公开 registry 请求，不保存到日志。

预算为新增镜像层约 150 GB（十进制字节）。基准与每次拉取后的占用用 Docker API `/system/df` 的 `LayersSize` 精确记录，表示解压后的唯一镜像层，不把共享层重复相加；另保存镜像 virtual size 和压缩 manifest layer size。每次未知镜像拉取前预留 `max(10 GB, 4 × 压缩层总大小)`，这是保守估算，不是解压大小的数学上界。拉取串行执行，不清理任何既有镜像、任务容器或 layer。

32 个镜像全部准备成功；daemon 镜像层从 12,598,638,898 bytes 增至 47,050,685,425 bytes，实际新增 **34,452,046,527 bytes（约 34.452 GB）**。该数不含容器可写层。

`image-preparation.json` 包含所有已尝试镜像的 manifest digest、image ID、压缩 layers、拉取可用性、错误、实际新增层大小和累计预算。原始 Docker pull 输出另存 `pull-logs/`，失败重试会用新的日志文件。

## Baseline / gold 判题

验证 driver 使用原版 `SWEBenchTask` 和 `compute_reward`，只复用 `audit_swe.py` 的观察性 hooks 保存 patch、stdout 和判题报告。每题的 baseline 与 gold 使用两个新 Docker 容器，不共享已经修改过的测试环境；gold 阶段采用上游 `run_oracle_solution=true`。

当时验证并发为 2，每个 sandbox 为 2 CPU / 8 GB，单次 verifier 上限 300 秒、外层 phase 上限 420 秒。容器名使用 `ua-expand-baseline-*` / `ua-expand-oracle-*`，与已有 RL 任务区分；任务结束时仅删除自己创建的容器。镜像拉取和这些判题可以重叠，最高支持并发 4。

最终可用条件在开始评测前写入 `validation-run-*.json`：

1. 两份 verifier 都完成，并找到可解析的测试状态。
2. Baseline reward=0，至少有一个被解析为失败的 FAIL_TO_PASS 目标测试，且没有 PASS_TO_PASS 回归。
3. Gold oracle reward=1。

不符合任一项就保留原始结果、填写排除原因。不存在“先让大模型试做，再挑它能做对的题”的步骤。

每题证据在 `baseline/<instance>/`、`oracle/<instance>/`，包括 `config.json`、`task.log`、`candidate.patch`、`candidate.content.patch`、`verifier.stdout`、`verifier.result.json` 与 `result.json`。两份判题的最终关系在 `pairs/<instance>.json`。`validation-summary.json` 汇总可用与排除 ID；正式可用子集另写 parquet。

## 复现入口

下面的准备命令会复用已经校验相同 image ID 的镜像。验证命令读取已有完整 phase/result，不重跑已完成的判题，也不会覆盖已有但未完成的 phase 目录。

```bash
docker exec -e PATH=/lab/tools:/usr/local/bin:/usr/bin:/bin ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/cpu-swe-expanded-prepare.py select
docker exec -e PATH=/lab/tools:/usr/local/bin:/usr/bin:/bin ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/cpu-swe-expanded-prepare.py prepare
docker exec -e DOCKER_HOST=unix:///lab/run/docker.sock \
  -e PATH=/lab/tools:/usr/local/bin:/usr/bin:/bin \
  -e PYTHONPATH=/lab/scripts:/lab/src/uni-agent:/lab/src/verl ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/cpu-swe-expanded-validate.py --concurrency 2
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/cpu-swe-expanded-finalize.py
```

准备和验证脚本也接受显式 `--instances`，但 ID 必须来自已冻结的候选池。不要在未确认完整性时替换原六题数据或与其汇总混在一起。
