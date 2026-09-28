# Phase 2 补充对照结果草稿

冻结矩阵按记录phase归属包含27个SDK run，全部完成并清理成功。这里将create路径、文件内容/编码、预热过程分别比较，未改动主矩阵或混入多发生器、idle-pool。

**公开SDK create与raw create。** 各点3轮；吞吐包含create/delete循环，但删除统一由raw HTTPX执行，不能称为完整SDK create+kill吞吐。

| 循环C | raw cycles/s，中位数 [min,max] | SDK cycles/s，中位数 [min,max] | raw / SDK create P95 ms |
|---:|---:|---:|---:|
| 1 | 3.64 [3.56, 3.75] | 3.55 [3.54, 3.70] | 455.28 / 431.89 |
| 2 | 5.51 [5.41, 5.74] | 5.13 [5.04, 5.45] | 395.40 / 424.16 |
| 3 | 5.88 [5.73, 6.23] | 5.83 [5.74, 5.95] | 537.11 / 551.43 |
| 8 | 5.47 [4.70, 5.80] | 5.16 [5.08, 5.46] | 2914.65 / 2754.79 |

[create路径对照图](results/supplemental/plots/create-sdk-vs-raw.png)。本地配置下两条路径的曲线形态接近；测试按顺序执行，差值包含时段、客户端和启动路径差异，不能直接命名为SDK额外成本。N不适用于没有预建驻留池的churn。

**以C8遍历64个slot后，C64第一条测量调用仍有错误。** 新过程在继承的slot0预热后，额外对全部64个slot各执行1条命令，预热并发8，再切到测量并发64。原始scenario名称保留在CSV，但它不能证明测量所需transport连接均已建立。

| 准备过程 | 成功 ops/s，中位数 [min,max] | 测量失败/请求 | 首条失败/192 | 后续失败/请求 |
|---|---:|---:|---:|---:|
| Slot 0 warmup | 1956.60 [1794.77, 1958.63] | 19/171,510 | 18/192 (9.3750%) | 1/171,318 |
| 64 slots exercised at C8 | 2225.60 [1748.20, 2283.13] | 9/187,909 | 9/192 (4.6875%) | 0/187,717 |

[预热过程比较](results/supplemental/plots/warmup-c8-to-c64.png)与[新过程逐轮first/later图](results/supplemental/plots/first-call-cohorts-b8cc0bff3bac.png)保留全部错误。新过程三轮首条错误分别为4、3、2个，均为ConnectError/RPC unavailable；其余187717条后续调用未记录错误。此分类按每worker最早measurement时间确定，setup可能更早调用过SDK。运行间变动较大且过程未随机化，不能宣称预热已修复问题或确定网络根因。

随机identity文件读取的SDK中位数为210.40 reads/s，原重复内容/default-gzip为128.60；两者同时改变内容与编码。[跨客户端完整表/图](../phase1-service/SUPPLEMENTAL-RESULTS.md)保留差异与范围，数值代表逻辑payload处理，不能换称网络带宽。

官方20轮脚本和内存暂停/恢复见[应用专报](APPLICATION-RESULTS.md)、[生命周期专报](../phase1-service/LIFECYCLE-RESULTS.md)。这些专报使用合并60观测的描述性分位数；本套件通用CSV使用各run分位数的中位数，不能直接混用。缓冲记录对照见[Phase 0报告](../phase0-environment/REPORT.md)：其SDK r1有1个成功请求在声明窗结束后1.798μs发出，原始计数保留，精确窗口指标排除它；这不是记录缺失。

统计与复现：图中吞吐/时延点为3轮统计的中位数，带或须为观察到的min–max，不是置信区间。首条失败表为合并计数；有限样本不支持稳定SLO承诺。host/VM资源分开，只采用完整落在窗口内的采样区间，详见资源覆盖表。

时钟补充：VM UTC与host/client UTC的历史对齐未经校准；窗口资源统计保留未平移选择，不作精确跨层归因。客户端单调计时和host/发生器同机CPU保持原口径，见[时钟对齐限制与主矩阵敏感性](../phase0-environment/CLOCK-ALIGNMENT.md)。主矩阵中的假设平移不直接套用于这些补充实验。

[补充输入冻结清单](results/supplemental/INPUTS-FROZEN.json) · [主矩阵对照输入清单](results/supplemental/COMPARISON-INPUTS-FROZEN.json) · [对照CSV](results/supplemental/comparisons.csv) · [逐run CSV](results/supplemental/per_run.csv) · [错误样本](results/supplemental/errors.json) · [资源报告](results/supplemental/resources/RESOURCES.md)。
