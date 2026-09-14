# Manual Uni-Agent rollout with E2B

This entry runs the complete **single-task rollout** path:

```text
uni_agent.framework.task_runner.run_task()
  -> Task.run() -> Uni-Agent ReAct
       model calls -> Uni-Agent Gateway -> local vLLM / Qwen30B
       tool calls  -> E2B provider -> remote sandbox
  -> independent verifier -> TaskResult reward
  -> Gateway finalization -> token/mask/logprob trajectories
```

The runner, agent, tools, and Gateway are upstream Uni-Agent components. `E2BIntervalTask` and `e2b_compat` are experiment extensions. The driver uses the in-process `_GatewayActor` implementation, not a Ray Gateway pool. It attaches TaskResult scores to exported trajectories. It does not start a trainer, optimizer, or TransferQueue worker.

The previous [direct-model helper](code/run_e2b_e2e.py) already ran a real Uni-Agent agent, but bypassed Task and Gateway. Neither path uses OpenEnv.

## 1. Start the prepared Docker environment and model

Run these in the same host terminal:

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py up
python3 scripts/labctl.py model --which tp1
curl --fail http://127.0.0.1:18082/v1/models
```

Wait for curl to succeed and show Qwen3-Coder-30B-A3B-Instruct before continuing. The tp1 service uses GPU 0 and the 64K eager configuration. Inspect startup with:

```bash
docker logs --tail 60 ua-lab-model-tp1
```

The CPU container reaches vLLM at `http://172.30.90.3:8000/v1`. The existing E2B credentials are loaded from `/lab/secrets/e2b.env` automatically.

## 2. Run the Uni-Agent task

```bash
UA_RUN="uni-agent-e2b-$(date +%Y%m%d-%H%M%S)"

docker exec -it ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/run_uni_agent_e2b.py \
  --output "/lab/results/$UA_RUN" \
  --template testlab-python-node
```

The entry prints six stages: create Gateway session; create E2B; run the agent; verify and score; export trajectories; close resources. It prints the agent's model endpoint, containing `/sessions/<id>/v1`. The agent receives this Gateway URL rather than the backend vLLM URL.

The coding task is the same merge_intervals repair from the report. The verifier is removed before the agent starts and restored afterward. ReAct is limited to 30 steps and 600 seconds; the sandbox lifetime request is 900 seconds. Use a new output directory for every attempt.

## 3. Check task success and trajectory validity

```bash
cat "results/$UA_RUN/summary.json"
cat "results/$UA_RUN/trajectory-audit.json"
```

For a successful real run, expect:

```json
{
  "gateway": true,
  "offline_injected_backend": false,
  "reward": 1.0,
  "resolved": true,
  "trajectory_valid": true,
  "sandbox_cleanup_completed": true,
  "gateway_shutdown_completed": true,
  "success": true
}
```

`trajectory_count` must be positive. `finished` reports whether the agent finished normally; it is distinct from verifier success. A sampled run can fail the coding task while still yielding a valid trajectory. `success` also requires successful resource cleanup and no recorded infrastructure error.

Read the independent tests and a compact trajectory summary:

```bash
python3 - "$UA_RUN" <<'CHECK'
import json, sys
from pathlib import Path
root = Path('results') / sys.argv[1]
verification = json.loads((root / 'e2b/verifier.json').read_text())
print(verification['stdout'])
print(verification['stderr'])
for path in (root / 'sessions').glob('*/trajectories.jsonl'):
    for line in path.read_text().splitlines():
        record = json.loads(line)
        t = record['trajectory']
        print({
            'session': record['session_id'],
            'response_tokens': len(t['response_ids']),
            'mask_length': len(t['response_mask']),
            'logprob_length': len(t['response_logprobs'] or []),
            'model_tokens': sum(t['response_mask']),
            'reward': t['reward_score'],
            'finished': t['finished'],
        })
CHECK
```

Expect `Ran 10 tests` and `OK` for a correct repair. The three response-array lengths must match; the audit checks mask values and finite logprobs. The score is attached by this demo driver, not by a training update.

## 4. Inspect the artifacts

| Artifact under results/<run>/ | Meaning |
|---|---|
| `summary.json` | Overall rollout, score, trajectory validation, and cleanup |
| `task-result.json` | Upstream TaskResult returned by the framework runner |
| `e2b/task-config.json` | Resolved Uni-Agent Task/Agent/Sandbox configuration, including Gateway URL |
| `e2b/agent-result.json` | Agent messages, tool calls, and observations |
| `e2b/baseline.json`, `e2b/verifier.json` | Independent tests before and after repair |
| `e2b/interval_utils.py` | Repaired function |
| `e2b/sandbox.json` | Created instance ID |
| `task.log` | Task and agent execution log |
| `sessions/<id>/trajectories.jsonl` | Token IDs, masks, logprobs, and attached score |
| `sessions/<id>/debug_snapshot.json` | Gateway messages and chain state |
| `trajectory-audit.json` | Compact checks for the exported trajectories |

Source: [run_uni_agent_e2b.py](code/run_uni_agent_e2b.py). This entry was added after the original experiments. Offline integration exercised the real Task runner, Task, ReAct, editor/submit tools, and HTTP Gateway with a scripted backend and local test sandbox; it did not claim a new live-model/E2B result.

## 5. Cleanup

The driver destroys E2B and closes Gateway on normal completion. If an interrupted run left cleanup uncertain, target only its saved sandbox ID:

```bash
docker exec -i -w /lab/scripts \
  -e E2B_REPLAY_DIR="/lab/results/$UA_RUN/e2b" \
  ua-lab-cpu /lab/envs/cpu/bin/python - <<'CLEANUP'
import asyncio, json, os
from pathlib import Path
from e2b import AsyncSandbox
from run_e2b_e2e import load_environment
load_environment('/lab/secrets/e2b.env')
sandbox_id = json.loads((Path(os.environ['E2B_REPLAY_DIR']) / 'sandbox.json').read_text())['sandbox_id']
deleted = asyncio.run(AsyncSandbox.kill(sandbox_id))
print('Deleted now' if deleted else 'Already absent')
CLEANUP
```

Stop the model started in step 1 when it is no longer needed:

```bash
docker stop ua-lab-model-tp1
```

[Experiment overview](README.md) · [Uni-Agent API](../01_model_and_examples/API.md) · [E2B API](API.md)
