# 执行代码版本

第一批正式主矩阵使用 `runner.py` v1，SHA256：

```text
0eafff00e0fc46902832061b8c5a50f8d052b71f6d1b1e641091c75f73c95075
```

精确源码保留在 [executed/runner-20260920-v1.py](executed/runner-20260920-v1.py)。各单元的 `.manifest.json` 记录执行时源码 hash、完整命令、配置和时间。

第 83 个单元 `exec-resident256-c8-r1` 在准备阶段遇到服务端 500，未进入正式测量。原始失败没有覆盖；完成迟到实例对账后，以独立单元 `exec-resident256-c8-r1-retry2` 重跑。

重跑及后续使用 v2，SHA256：

```text
48bbeab5401c2a9146ea31f455b65877ba01486703f7c6817d66667e941172fc
```

精确源码另保留为 [executed/runner-20260920-v2.py](executed/runner-20260920-v2.py)，便于与将来的修改区分。

v2 仅补充创建准备失败后的迟到实例观察窗，以及脱敏的 HTTP/RPC 错误诊断记录。成功请求的负载、调用方式、计时、并发和日志策略不变；主矩阵的单 slot 预热方式也保留。错误路径额外记录不应被解释为服务端变更。本轮没有修改 E2B daemon 的二进制或配置。

V1 的失败准备单元虽然没有 measurement 操作，仍带有按配置生成的窗口值。分析明确将它标为 setup failure、排除性能点，保留原窗口作来源记录。其资源采样用于失败诊断，不能当作 256 实例稳态结果。

V1/V2 中 warmup 和 measurement 可以重复使用数字 operation ID，解析采用 `(run_id, window, operation_id)`，保留来源值和质量提示，不合并两个窗口。`attempt_count` 是应用层调用次数，未宣称穷尽 SDK 底层网络重试。

补充实验通过独立 wrapper 调用当时的 runner，并记录实际 hash；buffered recording、全连接预热、多发生器、identity 编码随机文件均使用独立场景/观测标签，不混入原始主曲线。
