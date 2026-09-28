# 全连接预热的 SDK 补充诊断

主矩阵 SDK `exec_true` C64/N64 三轮共 171,510 次计时操作，19 次失败；其中18次是每个worker的第一条计时命令。后续诊断记录到连接被对端重置，以及一次流提前关闭。第一轮只保存异常类，具体网络层根因仍未确定。

`warmed_sdk_probe.py` 不修改主 runner。它只接受 SDK、`exec_true`、closed-loop、64 并发、64 resident、30 秒及正数 repeat。先执行原 prepare，再以最多8个并行请求预热全部64个slot，使用互不重复的负数warmup index；避免在准备阶段直接重复待研究的64路首次请求突发。只有所有warmup校验通过才进入C64测量。记录 `warmup_concurrency=8` 和独立的 `batch8-v2` comparison key。

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B warmed_sdk_probe.py \
  --adapter sdk --phase phase2-sdk --scenario exec_true \
  --concurrency 64 --residents 64 --duration 30 --repeat 1 \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase2-sdk/warmed-sdk-r1.jsonl
```

这是总调度器随后排队执行的诊断；编写和离线测试本身不调用服务。计划做 3 个 repeat，逐次核对错误、清理和资源窗口。诊断行标记 `scenario=exec_true_all_connections_warm`、独立 comparison key、`instrumentation=diagnostic-warmed`，不能并入原始 C64 样本。

仅异常路径增加 `operation_error_detail`：记录关联 operation ID、slot、时间 offset、RPC code 和最长 2,048 字符的脱敏 message，然后把原异常交回父 runner 计数。脱敏从内存中的 API key、所有 resident 的 envd/traffic token、敏感 envd headers 收集已知值，也处理 URL/JSON/base64 形式及常见认证字段。不会导出 error details 二进制、对象 repr、请求 headers、完整配置或 traceback；脱敏失败时只记录不含原文的占位信息。

即使预热后的三个 repeat 没有错误，也只说明此次条件下未复现；它不能单独证明原故障由连接预热、服务端负载或网络引起。异常路径的额外日志也使这组数据属于诊断样本。原始失败 cell 保留在正式结果中。

离线检查使用假句柄、假 token 和 mock 工具调用，不读取认证文件、不创建 HTTP client：

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B test_warmed_sdk_probe.py
```
