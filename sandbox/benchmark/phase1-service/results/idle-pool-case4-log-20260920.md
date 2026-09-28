# 诊断 Case 4：慢速建立空闲池时的 envd 初始化超时

这是独立的 `idle-pool-slow-ramp` 诊断，run
`115e158aaf514244be692e2d56f3d384`，不是第四个正式矩阵失败重复。正式N256
结果仍是2个有效测量重复、3个setup失败尝试。本次仅读取12:50:25–12:50:42
UTC目标日志，没有新增/proc采样、SDK/benchmark请求或daemon改动。

客户端在116个resident后，第117次创建失败。目标sandbox为
`iqg9absq4klnsbwvsz7wn`。VM日志显示12:50:27.121 FC API server启动，
12:50:27.163完成snapshot load（31.517 ms），12:50:37.165记录envd初始化
58次尝试后超时，单次请求timeout_ms为50；最后的原因是
`context canceled with cause: syncing took too long`。

API于12:50:39.578返回HTTP500，handler耗时12.559882841秒。因此直接失败
阶段与正式Case 2/3一致：FC API和snapshot load已完成，后续envd初始化超时；
它与Case 1的首次FC metrics socket连接拒绝不同。更慢的建立节奏下再次出现
同类错误，但这尚不足以唯一归因，需结合独立的资源/guest探针报告。

客户端错误UTC为12:50:40.691422，请求耗时12.569857181秒。client与server
耗时只差约10 ms，而绝对UTC相差约1.113秒，提示需核对host/VM时钟域。
不得把绝对时间戳差直接说成网络延迟或SDK额外开销；本报告分别记录两个时钟
域，依照因果顺序解释，并将精确时间对齐留给采样器偏移核验。

完整摘录、源日志路径、诊断JSON的SHA见
[idle-pool-case4-log-20260920.json](idle-pool-case4-log-20260920.json)。
