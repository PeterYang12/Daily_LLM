# 1 次/秒的空闲实例池诊断

快速建立 256 resident 的部分轮次在准备阶段失败；另外的采样窗口显示，名义上空闲的实例池曾占用大量 CPU，且主要计入 sandbox cgroup。`diagnose_idle_pool.py` 用一次较慢的建立过程，观察少量 microVM 内部进程是否有对应活动。它是单独的诊断，不替换正式矩阵中的失败 repeat，也不修改 v2 runner 或服务配置。

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B diagnose_idle_pool.py \
  --max-instances 256 --max-runtime 600 \
  --output /data2/yuhanya/e2b-benchmark/session-20260920/phase1-service/idle-pool-slow-ramp.json
```

执行保持前台，创建请求顺序发送、相邻请求开始至少间隔 1 秒；milestone 检查造成的延迟不会被后续突发请求补回。只使用 `base-4k-4g`，第一实例核对 4096 MiB 配置。初始必须没有其他 sandbox，随后至少每 10 秒或 milestone 检查外来实例和 VM MemAvailable；内存底线为 128 GiB。

每达到 32 个实例，记录 milestone，再从当前池选择 oldest/middle/newest 三个 owned 句柄。以 `user='root'` 执行短 Python 采集器，通过两次 `/proc` 快照和 1 秒间隔，记录 PID、comm、state、user/system ticks、CPU 增量、guest aggregate 和 load average。不会记录进程参数或环境；采集器自身 PID 被排除；最多返回 CPU 排序后的 256 个连续存活进程，并报告截断和新增/退出 PID。SDK command timeout 为 8 秒、request timeout 为 10 秒，外层截止为 12 秒。没有文件上传或需要普通用户写入 `/tmp` 的步骤。

第一次创建失败后停止发 POST，不进行创建重试。先对已经创建的池做同样的三点检查，再执行清理。原始观察按事件立即写入 `*.observations.jsonl`，最终 summary 写入指定 JSON，包含 run_id、精确 ownership metadata、UTC、创建数、错误、milestone、guest observations 和清理结果。

默认总预算 600 秒，工作阶段预留 180 秒用于检查和清理；未知创建结果自失败时起观察至少 150 秒，清理只作用于同时匹配本次 purpose 与随机 run_id 的实例。每次删除最多 8 个并发，最后至少两次空列表才确认；总截止内不能确认时写 `cleanup.verified_zero=false` 和剩余 ID，供主调度器继续按本次 metadata 核对。脚本不会重启组件、重配内存/CPU/网络、清理别人的实例或启动长期监控。

总调度器应在运行期间附加已有资源 sampler，以相同 UTC 关联 VM/cgroup 与 guest 进程。guest aggregate 仍包含短采集命令的开销；只抽样三个 guest，也会遗漏瞬时任务。若这次慢速建立没有出现故障或高 CPU，也不能单独证明问题由创建速率引起；milestone 停顿与检查本身同样改变了时间分布。

离线检查（无网络请求、使用假句柄与假 token）：

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python -B test_diagnose_idle_pool.py
```
