import time
from runtime_common import CURRENT, ROOT, event, save
from uni_agent.framework.task_runner import score_from_runner_result
from uni_agent.framework.task_runner import run_task as upstream_run_task


async def run_task(*, session, tools_kwargs=None, **kwargs):
    task = (tools_kwargs or {}).get('task', {})
    iid = task.get('metadata', {}).get('instance_id', '?')
    session_id = session.base_url.split('/sessions/', 1)[-1].split('/', 1)[0]
    identity = {'instance_id': iid, 'session_id': session_id}
    token = CURRENT.set(identity)
    start = time.time()
    try:
        result = await upstream_run_task(session=session, tools_kwargs=tools_kwargs, **kwargs)
        save(ROOT / 'results/sessions' / session_id / 'result.json', {
            **identity, 'started_unix': start, 'elapsed_s': time.time()-start,
            'reward': result.reward, 'finished': result.finished, 'extra_info': result.extra_info})
        event('task_completed', phase='train', **identity, reward=result.reward,
              finished=result.finished, elapsed_s=time.time()-start)
        return result
    except BaseException as exc:
        event('task_error', phase='train', **identity, error_type=type(exc).__name__, elapsed_s=time.time()-start)
        raise
    finally:
        CURRENT.reset(token)
