# Phase 1 补充对照结果草稿

输入来自冻结的42项补充矩阵；按记录phase归属，本页数据表包含15个run，全部完成并清理成功。主矩阵保持原样，对照仅引用另行列出的27份主矩阵原始文件。多发生器和idle-pool不进入此集合。

**提高客户端在途上限减少丢弃，同时增加排队。** 下表中服务端启动准入配置未变；两档客户端cap比较的都是100次计划到达的有限批次，吞吐分母含排空。

| λ /s | 客户端cap | 实发/计划（三轮合计） | 未发出 | 成功 cycles/s，中位数 [min,max] | create P95 ms |
|---:|---:|---:|---:|---:|---:|
| 8 | 16 | 236/300 | 64 | 5.05 [5.05, 5.64] | 6341.42 |
| 8 | 64 | 300/300 | 0 | 5.25 [4.78, 5.36] | 9837.44 |
| 20 | 16 | 123/300 | 177 | 5.51 [5.22, 5.65] | 5664.70 |
| 20 | 64 | 264/300 | 36 | 5.20 [4.83, 5.36] | 13800.90 |

[cap16/cap64曲线](results/supplemental/plots/client-cap16-vs64.png)显示更多计划请求进入系统后，成功吞吐仍约5 cycles/s，P95显著延长。不能把较少的发生器丢弃解释为服务容量同倍增加；这些有限批次也不是稳态上限认证。图按逐轮中位数显示比例，表中计划/实发为合并计数。

**1 MiB随机内容 + identity编码属于另一个工作负载。** 每个条件有3轮，C=N=8。

| 客户端 | 内容 / 编码 | 成功 reads/s，中位数 [min,max] | P95 ms |
|---|---|---:|---:|
| HTTPX | Repeated bytes / default gzip | 301.90 [301.70, 303.10] | 39.37 |
| HTTPX | Random bytes / identity | 280.05 [193.25, 293.75] | 31.48 |
| SDK | Repeated bytes / default gzip | 128.60 [123.05, 130.45] | 81.67 |
| SDK | Random bytes / identity | 210.40 [195.60, 212.35] | 42.97 |

[内容与编码对照图](results/supplemental/plots/payload-encoding-c8.png)。重复字节的默认gzip实体仅1055字节；随机identity场景使用固定SHA的1048576字节内容。内容熵和编码同时改变，不能把全部差异归因于压缩或宣称网络带宽。新场景6轮均通过内容校验。

SDK/raw create对照见[Phase 2补充报告](../phase2-sdk/SUPPLEMENTAL-RESULTS.md)；两者删除均走相同的raw HTTPX路径。暂停/恢复见[生命周期专报](LIFECYCLE-RESULTS.md)。记录开销见[Phase 0报告](../phase0-environment/REPORT.md)：本页保留其3个service run，并保留其中1个InvalidResult；详细RPC解释以专报为准。

统计与复现：图中吞吐/时延点为3轮统计的中位数，带或须为观察到的min–max，不是置信区间。首条失败表为合并计数；有限样本不支持稳定SLO承诺。host/VM资源分开，只采用完整落在窗口内的采样区间，详见资源覆盖表。

时钟补充：VM UTC与host/client UTC的历史对齐未经校准；窗口资源统计保留未平移选择，不作精确跨层归因。客户端单调计时和host/发生器同机CPU保持原口径，见[时钟对齐限制与主矩阵敏感性](../phase0-environment/CLOCK-ALIGNMENT.md)。主矩阵中的假设平移不直接套用于这些补充实验。

[补充输入冻结清单](results/supplemental/INPUTS-FROZEN.json) · [主矩阵对照输入清单](results/supplemental/COMPARISON-INPUTS-FROZEN.json) · [对照CSV](results/supplemental/comparisons.csv) · [逐run CSV](results/supplemental/per_run.csv) · [错误样本](results/supplemental/errors.json) · [资源报告](results/supplemental/resources/RESOURCES.md)。
