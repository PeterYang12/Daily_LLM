"""Uni-Agent Task + ReAct + Gateway + E2B rollout, scoring, and trajectory export.

Uses the upstream framework.task_runner.run_task entry and Gateway implementation.
The interval-repair Task and E2B provider are local extensions, not upstream demos.
No trainer, optimizer, or TransferQueue worker is started by this entry.
"""
import argparse
import asyncio
import dataclasses
import json
import math
import re
import time
import uuid
from pathlib import Path

from uni_agent.tasks import Task, TaskConfig, TaskResult
from uni_agent.tasks.registry import register_task

import e2b_provider  # Register the sandbox provider; no remote instance is created here.
from run_e2b_e2e import BUG, TESTS, PROMPT, dump, load_environment, serialized

MODEL = 'Qwen3-Coder-30B-A3B-Instruct'
TASK_NAME = 'e2b_merge_intervals'


def test_counts(result):
    text = result.stdout + '\n' + result.stderr
    match = re.search(r'Ran (\d+) tests?', text)
    total = int(match.group(1)) if match else 0
    failed = sum(int(n) for n in re.findall(r'(?:failures|errors)=(\d+)', text))
    passed = total - failed if total else 0
    resolved = total == 10 and result.exit_code == 0
    return {'tests_run': total, 'tests_passed': passed, 'resolved': resolved}


@register_task(TASK_NAME)
class E2BIntervalTask(Task):
    """Coding task extension using the standard Uni-Agent factories and TaskResult."""
    config_model = TaskConfig

    async def run(self):
        out = Path(self.config.metadata['output_dir'])
        out.mkdir(parents=True, exist_ok=False)
        dump(out / 'task-config.json', self.config.model_dump(mode='json'))
        info = {'task': TASK_NAME, 'resolved': False, 'cleanup_completed': False}
        sandbox = self.build_sandbox()
        agent_result = None
        started = time.monotonic()
        try:
            print('[2/6] Task.run: creating the E2B sandbox...', flush=True)
            await sandbox.__aenter__()
            info['sandbox_id'] = sandbox._sb.sandbox_id
            dump(out / 'sandbox.json', {'sandbox_id': info['sandbox_id']})
            await sandbox.exec(['mkdir', '-p', '/workspace'])
            await sandbox.write_file('/workspace/interval_utils.py', BUG)
            await sandbox.write_file('/workspace/heldout.py', TESTS)
            baseline = await sandbox.exec(['python3', '/workspace/heldout.py'], workdir='/workspace')
            dump(out / 'baseline.json', dataclasses.asdict(baseline))
            before = test_counts(baseline)
            if before['tests_run'] != 10 or baseline.exit_code == 0:
                raise RuntimeError('Invalid baseline; inspect e2b/baseline.json.')
            info['baseline_tests_passed'] = before['tests_passed']
            await sandbox.exec(['rm', '-f', '/workspace/heldout.py'])
            await sandbox.exec(['rm', '-rf', '/workspace/__pycache__'])
            print('[3/6] Task.run: ReAct -> Gateway -> vLLM; tools -> E2B...', flush=True)
            agent_result = await asyncio.wait_for(self.build_agent().run(
                sandbox=sandbox, messages=self.config.prompt, workdir='/workspace'), timeout=600)
            dump(out / 'agent-result.json', dataclasses.asdict(agent_result))
            info['finished'] = agent_result.finished
            info['agent_info'] = agent_result.info
            (out / 'interval_utils.py').write_bytes(await sandbox.read_file('/workspace/interval_utils.py'))
            print('[4/6] Task.run: independent verification and reward...', flush=True)
            await sandbox.write_file('/workspace/heldout.py', TESTS)
            verification = await sandbox.exec(['python3', '/workspace/heldout.py'], workdir='/workspace')
            dump(out / 'verifier.json', dataclasses.asdict(verification))
            info.update(test_counts(verification))
        except BaseException as exc:
            info['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            try:
                await sandbox.stop()
                info['cleanup_completed'] = True
            except Exception as exc:
                info['cleanup_error'] = str(exc)
            info['wall_seconds'] = time.monotonic() - started
            dump(out / 'result.json', info)
        return TaskResult(reward=float(info['resolved']),
                          accuracy=float(info['resolved']), finished=agent_result.finished,
                          extra_info=info)


def sample_config(args, output_dir):
    return {
        'name': TASK_NAME,
        'sandbox': {'provider': 'e2b_compat', 'image': args.template, 'runtime_timeout': 900},
        'agent': {'name': 'react', 'max_steps': 30,
                  'model': {'temperature': 0.2, 'top_p': 0.9,
                            'max_total_tokens': 50000, 'max_tokens_per_turn': args.max_tokens}},
        'metadata': {'output_dir': str(output_dir), 'instance_id': TASK_NAME},
    }


def audit_trajectories(trajectories):
    rows = []
    for index, trajectory in enumerate(trajectories):
        ids, mask, logprobs = trajectory.response_ids, trajectory.response_mask, trajectory.response_logprobs
        errors = []
        if len(ids) != len(mask):
            errors.append('token_mask_length_mismatch')
        if any(x not in (0, 1) for x in mask):
            errors.append('invalid_mask')
        if any(type(x) is not int or x < 0 for x in trajectory.prompt_ids + ids):
            errors.append('invalid_token_id')
        if logprobs is None or len(logprobs) != len(ids):
            errors.append('missing_or_unaligned_logprobs')
        elif not all(isinstance(x, (int, float)) and math.isfinite(x) for x in logprobs):
            errors.append('nonfinite_logprobs')
        if not ids or not sum(mask):
            errors.append('no_generated_tokens')
        rows.append({'index': index, 'generated_tokens': sum(mask),
                     'context_tokens': len(mask) - sum(mask), 'num_turns': trajectory.num_turns,
                     'reward_score': trajectory.reward_score, 'finished': trajectory.finished,
                     'errors': errors})
    return {'valid': bool(rows) and all(not row['errors'] for row in rows),
            'trajectory_count': len(rows), 'rows': rows}


async def run(args, *, backend=None, tokenizer=None):
    """Optional backend/tokenizer injection is for offline integration checks only."""
    from transformers import AutoTokenizer
    from uni_agent.framework.task_runner import run_task
    from uni_agent.gateway.config import GatewayActorConfig
    from uni_agent.gateway.gateway import _GatewayActor
    from uni_agent.logging import sample_logging
    from examples.gateway.debug_launcher import (
        OpenAICompletionsBackend, TemplateResultTokenIdsWrapper,
        capture_debug_snapshot, write_debug_snapshot_json, write_trajectories_jsonl,
    )

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    session_id = 'e2b-' + uuid.uuid4().hex[:12]
    summary = {'task': TASK_NAME, 'model': MODEL, 'session_id': session_id,
               'gateway': True, 'logprobs_requested': True, 'training': False,
               'framework_entry': 'uni_agent.framework.task_runner.run_task',
               'task_extension': 'run_uni_agent_e2b.E2BIntervalTask',
               'gateway_mode': 'in-process _GatewayActor; no Ray actor pool',
               'offline_injected_backend': backend is not None}
    dump(out / 'run-config.json', vars(args))
    if tokenizer is None:
        tokenizer = TemplateResultTokenIdsWrapper(AutoTokenizer.from_pretrained(args.tokenizer))
    if backend is None:
        backend = OpenAICompletionsBackend(backend_base_url=args.base_url, backend_model=MODEL, timeout=240)
    actor = _GatewayActor(GatewayActorConfig(
        tokenizer=tokenizer, tool_parser_name='qwen3_coder', rollout_backend='vllm',
        prompt_length=57344, response_length=8192,
        allowed_request_sampling_param_keys=frozenset({'stop'})), backend)
    task_result = None
    session_open = False
    trajectories = []
    started = time.monotonic()
    try:
        print('[1/6] Starting Uni-Agent Gateway and creating a session...', flush=True)
        await actor.start()
        handle = await actor.create_session(session_id,
            metadata={'task': TASK_NAME},
            sampling_params={'temperature': 0.2, 'top_p': 0.9,
                             'max_tokens': args.max_tokens, 'logprobs': True})
        session_open = True
        summary['gateway_base_url'] = handle.base_url
        print('Agent model endpoint:', handle.base_url, flush=True)
        async with sample_logging(session_id, log_path=str(out / 'task.log')):
            task_result = await run_task(session=handle,
                tools_kwargs={'task': sample_config(args, out / 'e2b')},
                raw_prompt=[{'role': 'user', 'content': PROMPT}],
                sample_index=0, model_name=MODEL)
        dump(out / 'task-result.json', dataclasses.asdict(task_result))
        summary.update({'reward': task_result.reward, 'accuracy': task_result.accuracy,
                        'finished': task_result.finished,
                        'resolved': task_result.extra_info['resolved'],
                        'sandbox_cleanup_completed': task_result.extra_info['cleanup_completed']})
    except Exception as exc:
        summary['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if session_open:
            try:
                print('[5/6] Finalizing Gateway and exporting trajectories...', flush=True)
                snapshot = capture_debug_snapshot(actor, session_id, {})
                write_debug_snapshot_json(output_dir=out / 'sessions', session_id=session_id, snapshot=snapshot)
                trajectories = await actor.finalize_session(session_id)
                session_open = False
                # Explicit demo-level annotation. No optimizer or trainer is called.
                if task_result is not None:
                    for trajectory in trajectories:
                        trajectory.reward_score = task_result.reward
                        trajectory.finished = task_result.finished
                        trajectory.reward_metrics = {'accuracy': task_result.accuracy}
                        trajectory.extra_fields['reward_source'] = 'TaskResult attached by demo driver'
                write_trajectories_jsonl(output_dir=out / 'sessions', session_id=session_id,
                    trajectories=trajectories,
                    metadata={'task': TASK_NAME, 'task_result_path': str(out / 'task-result.json'),
                              'reward_attachment': 'demo driver; no training update'})
            except Exception as exc:
                summary['trajectory_error'] = str(exc)
        if session_open:
            try:
                await actor.abort_session(session_id)
            except Exception as exc:
                summary['abort_error'] = str(exc)
        try:
            await actor.shutdown()
            summary['gateway_shutdown_completed'] = True
        except Exception as exc:
            summary['gateway_shutdown_error'] = str(exc)
        audit = audit_trajectories(trajectories)
        dump(out / 'trajectory-audit.json', audit)
        summary['trajectory_count'] = len(trajectories)
        summary['trajectory_valid'] = audit['valid']
        summary['wall_seconds'] = time.monotonic() - started
        summary['success'] = bool(summary.get('resolved') and audit['valid']
                                  and summary.get('sandbox_cleanup_completed')
                                  and summary.get('gateway_shutdown_completed')
                                  and not any(k.endswith('error') for k in summary))
        dump(out / 'summary.json', summary)
        print('[6/6] Rollout complete; inspect summary.json and trajectory-audit.json.', flush=True)
        print(serialized(summary), flush=True)
    return summary


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New output directory')
    parser.add_argument('--env-file', default='/lab/secrets/e2b.env')
    parser.add_argument('--base-url', default='http://172.30.90.3:8000/v1', help='Backend vLLM URL, not the agent URL')
    parser.add_argument('--tokenizer', default='/lab/models/' + MODEL)
    parser.add_argument('--template', default='testlab-python-node')
    parser.add_argument('--max-tokens', type=int, default=2048)
    return parser.parse_args()


if __name__ == '__main__':
    arguments = parse_args()
    load_environment(arguments.env_file)
    outcome = asyncio.run(run(arguments))
    raise SystemExit(0 if outcome['success'] else 1)
