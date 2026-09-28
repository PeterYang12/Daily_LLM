# 阶段1：服务协议压测与诊断总览

阶段1通过现有网关进行真实REST/Connect请求。主矩阵65个有效性能run，另保留3次准备失败；成功测量部分共539,931个操作，未记录请求失败。失败准备与后续诊断必须一起阅读，不能由此认定服务没有可靠性问题。

## 结果入口

- [主矩阵与资源](MAIN-RESULTS.md)：并发/驻留、创建到达率、发生器未发出请求、文件与命令曲线。
- [暂停/恢复状态验证](LIFECYCLE-RESULTS.md)：原始HTTP与SDK控制路径、内存和文件保持。
- [多发生器](MULTIGENERATOR-RESULTS.md)：global C64/N64下单进程与4进程，含所有边界失败。
- [准备失败日志](results/setup-failure-log-20260920.md)与[资源分析](results/setup-failure-20260920.md)：一次FC socket连接拒绝、两次envd就绪超时。
- [补充对照](SUPPLEMENTAL-RESULTS.md)：客户端在途上限、SDK创建、文件identity编码和预热等条件。
- [慢速空闲池诊断](IDLE-POOL-RESULTS.md)：约1次/秒建池仍复现就绪失败，含存活guest事后检查及解释边界。

## 可据此作出的判断

HTTPX单进程`exec_true`在C=N=8约841次/秒，在C=N=64约313次/秒；4进程保持global C64/N64后中位数约1947次/秒。因此原曲线的回落不能被当成E2B整机容量。

当前create→delete循环由C3升至C8，吞吐没有提高而等待增加。固定到达率实验还包含本地在途上限造成的未发出请求。报告同时给出计划、实发、完成和失败；SDK配对的create仅计公开SDK创建，删除由双方共用HTTPX清理路径完成，循环吞吐不是纯创建吞吐。

256驻留的3次准备失败发生在165、161、216个resident已创建之后；另有2次完整成功测量。因此不存在可从本轮推出的“165个硬上限”。失败中CPU状态不同，见分别记录的日志与资源；没有删除失败或把无测量窗口当作0性能。

本轮没有修改E2B服务二进制、启动准入或内核配置，也未运行直接调用Factory的原生runtime专项。代码/配置入口见[README](README.md)，实际源码版本差异见[执行版本](code/EXECUTED-VERSIONS.md)。
