# Uni-Agent Gateway接口清单：HTTP与Python/Ray的边界

核对日期：2026-09-14。源码版本`472c875a97f9a2764c81a6ec7581167632bd8bcc`。本次只读源码，没有启动模型、Gateway或远端sandbox。

## HTTP业务路由

`_GatewayActor._register_routes()`仅注册两个业务路由：

| 方法和路径 | 用途 |
| --- | --- |
| `POST /sessions/{session_id}/v1/chat/completions` | OpenAI Chat Completions兼容请求 |
| `POST /sessions/{session_id}/v1/messages` | Anthropic Messages兼容请求 |

session必须已由框架创建，未知ID返回404。这里不是完整OpenAI/Anthropic所有产品API的实现。FastAPI默认文档页面如`/docs`、`/openapi.json`属于接口文档，不是新增的任务或训练管理能力。`run_uvicorn`只启动HTTP服务，没有补注册管理路由。

当前没有HTTP形式的创建session、finalize、轨迹下载或reward提交业务路由；也没有Gateway里的sandbox执行或训练启动端点。

## Python/Ray管理方法

| 方法 | 调用位置 | 实际行为 |
| --- | --- | --- |
| `create_session(session_id, metadata=..., sampling_params=...)` | `GatewayManager`或GatewayActor | 选择/创建actor内的session，返回`SessionHandle` |
| `finalize_session(session_id)` | `GatewayManager`或GatewayActor | 将session中的chain整理为`list[Trajectory]`返回，并移除session |
| `abort_session(session_id)` | `GatewayManager`或GatewayActor | 标记中止、丢弃会话状态并移除session；不应当作已验证的底层生成强制取消API |
| `get_session_state(session_id)` | 仅GatewayActor | 返回phase、时间、chain数量、rollback等内存状态快照 |
| `start()` / `shutdown()` | GatewayActor | 启停该actor的HTTP服务 |
| `shutdown()` | `GatewayManager` | 调用各actor的shutdown并清空manager路由 |

Manager内部用`actor.method.remote(...)`进行Ray调用。Manager没有`get_session_state`代理方法；需持有相应actor句柄。`shutdown`没有逐session执行abort，不能将它解释成完整任务取消服务。

SessionHandle的base_url形如`http://host:port/sessions/<id>/v1`。框架先创建session，把会话URL交给Agent；Agent继续使用熟悉的模型API。任务结束后框架通过Ray finalize取回轨迹，Agent不需要用HTTP下载训练数据。

```python
# 示意：manager已由框架初始化，省略Ray/backend/tokenizer配置。
handle = await manager.create_session(
    session_id="task-001",
    metadata={"purpose": "example"},
    sampling_params={"temperature": 0.7},
)
# Agent通过handle.base_url发送模型请求。
trajectories = await manager.finalize_session("task-001")
```

## 返回的轨迹与reward

finalize返回的token轨迹包含`prompt_ids`、`response_ids`、`response_mask`、可选`response_logprobs`以及chain/权重版本等元数据。模型生成位置mask=1，工具观察等续接内容mask=0。

Reward由Task/Runner或RewardLoopWorker产生。Gateway构造的Trajectory默认`reward_score=None`；Framework收到Runner的TaskResult后，再补充finished/reward/metrics，必要时调用scorer。不要从旧文档中的“reward POST”措辞推断出存在HTTP reward端点。

处理后的轨迹进入TransferQueue，并可写本地`trajectory.json`及`trajectory.npz`。`get_session_state`不是完整轨迹或reward的历史查询；session保存在actor内存中，finalize/abort后即移除。

## 源码入口

- [HTTP路由和actor方法](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/gateway.py)
- [GatewayManager](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/manager.py)
- [Session实现](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/session.py)
- [SessionHandle与Trajectory类型](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/gateway/session/types.py)
- [官方Gateway与轨迹说明](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/docs/source/concepts/gateway-and-trajectories.md)
