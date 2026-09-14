# Gateway session 示例

## Gateway session

`gateway_session.py` 是可导入的异步 helper，依据 Uni-Agent commit `472c875a97f9a2764c81a6ec7581167632bd8bcc`。需要完整工程已启动 Ray、rollout backend，并创建 `GatewayManager`；依赖和启动顺序见复现手册。

在完整工程已有的异步上下文中调用：

```python
from gateway_session import run_task_in_gateway

# 由完整工程准备 gateway_manager、task_config 和 messages。
task_result, trajectories = await run_task_in_gateway(
    gateway_manager,
    task_config,
    messages,
    model_name="Qwen3-8B",
    sampling_params={"temperature": 0.0, "max_tokens": 1024},
)
```

`task_config` 应包含 Task 所需的 metadata、Agent 和 Sandbox 信息。`run_task` 把 session 的 `base_url` 注入 Agent；Agent 通过 HTTP 请求模型。`create_session`、`finalize_session` 和 `abort_session` 则是 Python/Ray 调用，不是 REST 接口。

`TaskResult` 提供任务分数，finalize 返回 token IDs、mask、logprobs 等轨迹并移除 session。正式训练还需要关联 reward、写入 TransferQueue、计算 loss 和更新参数。调用方负责自己创建的 `GatewayManager` 生命周期。
