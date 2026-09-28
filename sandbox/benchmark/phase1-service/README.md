# 阶段 1：E2B 完整服务的协议层压测

该阶段通过正式形态的 `http://10.145.64.67:8088` 网关调用服务。测量客户端为 HTTPX 0.28.1：创建/删除使用 REST，命令使用 Connect JSON 流，文件使用 envd 的 HTTP 接口。数据面测试的实例准备使用 SDK，但准备、模板核对和预热不计入正式窗口。

代码入口：[runner.py](code/runner.py)。协议客户端复用已创建实例的认证/路由头，测量请求不经过 SDK 的 commands/files 高层方法。它也不是直接调用 Factory 的 runtime 微基准；本阶段未运行需要独立网络/NBD资源的原生 Factory benchmark。

## 场景与配置

- 模板：`base-4k-4g`，2 vCPU、4096 MiB，普通 4 KiB guest backing。每个单元先核对实际模板内存和 envd 版本。
- 命令：`/bin/bash -l -c true`，root、cwd `/tmp`，完整消费进程结束与 Connect EndStream。
- 文件：1 KiB 读/写、1 MiB 读，固定输入并核对内容。SDK对照使用相同文件/命令语义。
- 命令并发：1、8、32、64；文件并发：1、8、32。每主要点三个独立重复。
- 驻留锚点：64/256实例，8个活跃 worker，测短命令。额外驻留实例主要空闲，不是所有实例同时满内存/满CPU。
- 控制面：create/delete churn，并发1/2/3/8；固定到达率2/8/20次循环每秒，客户端在途上限16，服务端启动准入保持3。
- 补充：pause/resume+内存/文件状态验证、多发生器及记录开销实验单独归档，不混入上述普通曲线。

准确单元见 [matrix.json](configs/matrix.json)。SDK对照在总矩阵中交错，减少不同时间段的环境漂移。

## 单项运行

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python code/runner.py \
  --adapter service --phase phase1-service --scenario exec_true \
  --concurrency 8 --residents 8 --duration 20 --repeat 1 \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase1-service/new-run.jsonl
```

这条命令单独运行不自动启动资源采样。完整有采样的执行使用根目录 `run_stages.py` 与对应矩阵；完整环境步骤和统计边界见 [总入口](../README.md)。

## 正确性与计时

Connect 解码器处理任意 HTTP 分块，要求合法帧顺序、完整进程结束及流终结，校验 base64 与输出上限。短命令核对退出码/stdout/stderr；文件读取核对内容；写入用独占slot和逐操作序号，最后做状态核对。

开放到达率模式记录计划发送时间、实际开始、发生器在途上限丢弃和服务拒绝。它不是无限排队的发生器。创建失败响应不明时，按精确 run metadata 对账，并保留迟到观察窗与连续空列表确认；不以重试同一个未知结果的POST清理不确定性。

正式报告会把成功延迟与失败/拒绝率放在一起，并对照发生器CPU与VM资源。HTTPX客户端吞吐下降不自动证明E2B服务资源耗尽。现有原生OTel未开启，精确内部锁/缺页/调度阶段仍需后续诊断，不能从外部曲线虚构内部归因。
