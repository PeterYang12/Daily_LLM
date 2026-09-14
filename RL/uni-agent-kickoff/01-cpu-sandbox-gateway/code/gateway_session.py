"""可导入的session示例；调用方需已有完整Uni-Agent/verl运行环境与GatewayManager。

依据Uni-Agent commit 472c875a97f9a2764c81a6ec7581167632bd8bcc。
不创建模型服务，不提供REST session接口，也不执行TQ写入或参数更新。
"""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from uni_agent.gateway.manager import GatewayManager
    from uni_agent.gateway.session.types import Trajectory
    from uni_agent.tasks.base import TaskResult


async def run_task_in_gateway(
    gateway_manager: GatewayManager,
    task_config: dict[str, Any],
    raw_prompt: list[dict[str, Any]],
    *,
    model_name: str,
    sampling_params: dict[str, Any] | None = None,
    task_config_path: str | None = None,
) -> tuple[TaskResult, list[Trajectory]]:
    """运行一个Task并取回token轨迹；不关闭调用方拥有的GatewayManager。"""
    from uni_agent.framework.task_runner import run_task

    session_id = f"example-{uuid4().hex}"
    session = await gateway_manager.create_session(
        session_id, sampling_params=dict(sampling_params or {}),
    )
    try:
        # run_task将session.base_url注入agent.model，实际HTTP模型请求由Agent发出。
        # task_config应来自已验证的数据/config解析流程，包含Task所需metadata。
        result = await run_task(
            session=session,
            tools_kwargs={"task": task_config},
            raw_prompt=raw_prompt,
            task_config_path=task_config_path,
            model_name=model_name,
            api_key="EMPTY",  # 本地Gateway占位值，不是Verdal或外部模型凭据。
        )
        trajectories = await gateway_manager.finalize_session(session_id)
        # TaskResult提供任务分数；此处取回的是Gateway的token轨迹。
        # 正式Framework还需关联reward/finished、评分、写TQ，之后才可能训练。
        return result, trajectories
    except BaseException as error:
        # 与正式Framework一致：异常或取消时清理已创建的会话。
        try:
            await asyncio.shield(gateway_manager.abort_session(session_id))
        except Exception as cleanup_error:
            error.add_note(f"Gateway会话清理失败: {type(cleanup_error).__name__}")
        raise


if __name__ == "__main__":
    raise SystemExit("这是可导入的async helper；请在已有GatewayManager的完整工程中调用。")
