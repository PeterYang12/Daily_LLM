# VM / host 时钟对齐：事后敏感性检查

事后检查发现 VM UTC 与 host/client UTC 不能假定完全同步。客户端的延迟与吞吐使用本进程单调时钟，未受此次跨机对齐问题影响；host/发生器 CPU 也使用同机时间域。原报告的 VM 平均 CPU 按未平移 UTC 选择采样窗口，应作为近似归属，不能据此做精确的逐请求跨层归因。

[13:03 UTC 的三次 SSH 括界检查](results/clock-check-final.json)中，guest−host 中点估计分别约 −835.5、−962.8、−964.2 ms，RTT 为300–557 ms。若额外假定这三次之间偏移恒定，区间交集约为 [-1114.21, -814.17] ms；中点不是已知真实偏移。这次事后探测不能证明此前数小时一直具有相同偏移。sampler的received_utc−guest.utc还包含采集/传递时间，不能直接拿来校准。

本检查保留所有原始UTC、单调时钟、计数器速率和原报告数值，只在窗口选择时假设guest UTC标签加0、0.8、1.0或1.2秒。每种假设仍只选完整落在client测量窗内的guest采样区间，按原interval_seconds加权；不切分边界，不插值，不把缺失值置零。正向平移用于检验guest时间落后host时可能改变的样本归属。

覆盖主矩阵全部 **104个有效run × 4种对齐假设 = 416条结果**。零平移对原报告CPU均值的最大复现差为 0 核；没有重算客户端延迟/吞吐，也没有重跑工作负载或调整系统时钟。三个失败setup不进入测量窗敏感性；同一VM内部的计数器峰值和整段capture观察不依赖跨机平移。

| exec_true 路径 | C=N | 有效轮数 | 未平移 VM平均核中位数 | +0.8s | +1.0s | +1.2s |
|---|---:|---:|---:|---:|---:|---:|
| HTTPX | 8 | 3 | 16.67 | 16.81 | 16.81 | 16.81 |
| HTTPX | 32 | 3 | 22.36 | 22.75 | 22.75 | 22.75 |
| HTTPX | 64 | 3 | 34.50 | 34.73 | 34.73 | 34.73 |
| SDK | 8 | 3 | 18.18 | 18.43 | 18.43 | 18.43 |
| SDK | 32 | 3 | 57.97 | 57.97 | 57.97 | 57.16 |
| SDK | 64 | 3 | 82.35 | 83.11 | 83.11 | 83.11 |

全部场景中，最大的窗口均值改变量为 +6.28 核：phase1-service / exec_true / C=64 / repeat=1 / shift=+1.2s。各假设下最大的VM平均CPU为 92.93/192 核（48.4%）。无完整区间的结果共有 0 条。较短批次和时间变化较快的负载，其窗口均值更敏感；这些差异不用于宣称精确历史CPU值。

在这组假设范围内，主矩阵不存在“整台192-vCPU VM的平均CPU已耗尽”的证据，这一总量判断保持成立。局部热核、串行路径或瞬时尖峰仍可能限制性能。客户端/发生器方向的证据主要来自同机发生器CPU以及保持global C/N后的多进程对照，并不依赖把某个VM采样点精确对齐到某条RPC；其量化因果解释仍有实验设计限制。

本敏感性不等于历史校准，不把同样偏移强行套到多发生器或idle实验。相关报告保留未校准UTC选择的VM数值并明确限制；精确跨层时序需要在实验期间记录双向时钟界限或可靠的同步状态。

[逐run CSV](results/clock-sensitivity/per_run.csv) · [逐run JSON](results/clock-sensitivity/per_run.json) · [按配置汇总](results/clock-sensitivity/by_configuration.csv) · [总览](results/clock-sensitivity/summary.json) · [输入SHA](results/clock-sensitivity/INPUTS.json) · [分析源码](../analysis/clock_sensitivity.py)。

复现仅做本地读取：

```bash
taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \
  /home/yuhanya/Daily_LLM/sandbox/benchmark/analysis/clock_sensitivity.py
```
