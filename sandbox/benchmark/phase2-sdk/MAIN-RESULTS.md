# Phase 2 主矩阵结果

主矩阵包含 **39 个有效 run、13 个配置，每配置 3 轮**；共 1,016,866 个测量请求、19 个失败，全部归属清理成功。19 个失败均发生在 `exec_true` C=N=64。本页保留这些失败，不把高吞吐等同于满足可靠性或业务 SLO。

| C=N | 有效轮数 | 成功吞吐 ops/s，中位数 [最小, 最大] | P95 / P99 ms | 失败/请求 |
|---:|---:|---:|---:|---:|
| 1 | 3 | 119.60 [112.80, 119.75] | 14.14 / 20.70 | 0/7,046 |
| 8 | 3 | 918.15 [916.20, 922.65] | 14.04 / 23.70 | 0/55,164 |
| 32 | 3 | 2025.25 [1991.25, 2112.80] | 27.97 / 97.94 | 0/122,682 |
| 64 | 3 | 1956.60 [1794.77, 1958.63] | 70.03 / 230.28 | 19/171,510 |

[命令吞吐、长尾及失效率](results/main-matrix/plots/coupled-population-concurrency-phase2-sdk-exec-true-exec-true-4c9c25ec3c.png)显示：C=N=32 到 64 时，吞吐中位数从 2,025.25 变为 1,956.60 ops/s，P95 从 27.97 增至 70.03 ms。C64 合并失效率为 19/171,510 = 0.01108%；图中则使用逐轮失效率的中位数与最小—最大范围。

**错误集中于每个 worker 的第一条测量调用。** 按 start_offset_s 排序识别首条，不按文件行顺序；setup 调用可以更早发生，不能把此分类叫作第一次 SDK 操作。原始 warmup 只覆盖 slot 0。

| 重复 | 每 worker 首条测量调用：失败/请求 | 后续测量调用：失败/请求 |
|---:|---:|---:|
| 1 | 2/64 (3.125%) | 0/53,844 (0.000000%) |
| 2 | 4/64 (6.250%) | 1/58,703 (0.001703%) |
| 3 | 12/64 (18.750%) | 0/58,771 (0.000000%) |

合计首条为 **18/192 = 9.375%**，后续为 **1/171,318 = 0.000584%**。[分组图](results/main-matrix/plots/first-call-cohorts-a90beb8723a5.png)与[分组原表](results/main-matrix/first_call_per_run.csv)保留该差异；这描述了位置关联，尚不能判定网络或服务根因。

错误分类为 18 个 ConnectError、1 个 TimeoutException；RPC code 为 16 个 unavailable、3 个未记录。TimeoutException 在原始记录中属于 error，耗时约 7.8 ms；其名称不能据此解释为 1,800 秒 sandbox TTL 到期。完整错误样本及所有分母均保留。

C=N=32 时发生器 CPU 逐轮均值的中位数为 1.33 核，VM 为 57.97/192 核；C=N=64 时分别为 1.17 和 82.35/192 核。资源总量有余量不排除局部串行瓶颈；完整预热、多个发生器和记录开销对照独立归档。

| 文件场景 | C=N=1 ops/s | C=N=8 ops/s | C=N=32 ops/s |
|---|---:|---:|---:|
| file_read_1k | 354.25 | 2703.00 | 2843.90 |
| file_read_1m | 39.20 | 128.60 | 134.80 |
| file_write_1k | 359.00 | 2225.70 | 2239.55 |

[1 MiB 文件曲线](results/main-matrix/plots/coupled-population-concurrency-phase2-sdk-file-read-1m-file-read-1m-6caef05686.png)需按下述压缩口径解读。SDK 资源测量覆盖率中位数为 90.0%，范围 89.2–93.4%；未发现采样缺口或时钟跳变。

口径：吞吐按显式测量窗内成功完成数计算；时延按窗内发出的成功请求计算，保留排空期间完成的样本。图表为逐轮统计的中位数与实际最小—最大范围，不是置信区间；失败计数另列。C=N 主曲线同时改变并发与驻留量。数据面窗口为 20–30 秒，尚未冻结业务 SLO，也未完成长稳验证。

时钟补充：事后核验发现VM UTC与host/client UTC存在约1秒级对齐不确定性。本文VM窗口资源均值保留未平移选择，只能作近似归属；客户端单调计时和host/发生器同机CPU不受这一跨机问题影响。[事后敏感性检查](../phase0-environment/CLOCK-ALIGNMENT.md)只是假设性窗口平移，不构成历史时钟校准或精确跨层归因。

文件读取使用重复字节。独立[传输诊断](/data2/yuhanya/e2b-benchmark/session-20260920/phase0/file-transport-probe.json)已确认：1 MiB 逻辑内容对应默认 gzip HTTP 实体仅 1,055 字节；SDK 可自动解压并移除相关响应头。因此大文件结果代表逻辑 payload/解压工作负载，不能作为网络 MiB/s。HTTPX 与 SDK/pyqwest 的客户端传输成本也不同。文件写入仅在窗口结束后核对每个 worker 的最终内容，9 个写入 run 的此项检查均通过；这不等于逐次写入的独立内容校验。

资源指标按实际采样间隔加权，host 与 VM 分开；边界采样不按比例切分，缺失值不补零。原始文件保存在外部数据目录，报告只读取 [INPUTS-FROZEN.json](results/main-matrix/INPUTS-FROZEN.json) 列出的主矩阵文件。补充对照独立归档，其结果不进入本页主矩阵统计。

完整数据：[逐轮统计](results/main-matrix/per_run.csv)、[聚合统计](results/main-matrix/summary.csv)、[run/失败 setup](results/main-matrix/runs.csv)、[错误样本](results/main-matrix/errors.json)、[资源按 run 对齐](results/main-matrix/resource_by_run.csv)、[资源报告](results/main-matrix/resources/RESOURCES.md)、[质量检查](results/main-matrix/data_quality.json)。
