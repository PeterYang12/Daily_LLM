# Manual end-to-end replay: E2B

This runs **Uni-Agent ReAct + local Qwen30B + a remote E2B sandbox**. The task is the recorded `merge_intervals` repair. Model calls go directly to vLLM; this replay does not use Gateway or train the model.

Use the existing complete lab. The new [E2B-only entry](code/run_e2b_e2e.py) reuses the original task, tests, prompt, and ReAct settings. The historical paired Docker/E2B script is preserved separately. The helper has passed offline orchestration checks; these instructions are for your real manual run.

## 1. Start the coordinator and check dependencies

Run all commands in the same host terminal:

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py up

docker exec ua-lab-cpu /lab/envs/cpu/bin/python -c \
  "import uni_agent, e2b; print('Uni-Agent and E2B imports OK')"
```

The E2B settings file already exists at `/home/yuhanya/uni-agent-lab/secrets/e2b.env` (container path `/lab/secrets/e2b.env`). The runner loads it automatically. The configured fields are E2B_API_KEY, E2B_API_URL, and E2B_SANDBOX_URL; do not paste credentials into logs.

## 2. Start the original model service

```bash
python3 scripts/labctl.py model --which tp1
curl --fail http://127.0.0.1:18082/v1/models
```

This uses GPU 0, fixed Qwen3-Coder-30B-A3B-Instruct, and the 64K eager configuration. Wait until curl succeeds and shows the model before continuing. If it is still loading, inspect startup output and repeat curl:

```bash
docker logs --tail 60 ua-lab-model-tp1
```

The CPU container reaches this service at `http://172.30.90.3:8000/v1`.

## 3. Run the complete E2B repair

```bash
E2B_RUN="e2b-manual-$(date +%Y%m%d-%H%M%S)"

docker exec -it ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/run_e2b_e2e.py \
  --output "/lab/results/$E2B_RUN" \
  --template testlab-python-node
```

The entry prints five stages:

1. Create an E2B instance.
2. Upload the broken function and run the baseline tests; remove verifier and cache before the agent starts.
3. Run ReAct: inspect code, edit files, execute checks, observe results, and submit.
4. Restore and execute independent tests.
5. Destroy the remote instance and save the result.

The recorded task reached 10/10 tests. A new sampled run may differ; use the verifier to determine success. The agent has a 600-second limit, 30-step limit, and a sandbox lifetime request of 900 seconds. Reusing the same result subdirectory is rejected.

## 4. Inspect the result and agent actions

```bash
cat "results/$E2B_RUN/e2b/result.json"

python3 - "$E2B_RUN" <<'CHECK'
import json, sys
from pathlib import Path
root = Path('results') / sys.argv[1] / 'e2b'
verification = json.loads((root / 'verifier.json').read_text())
print(verification['stdout'])
print(verification['stderr'])
print('Verifier exit code:', verification['exit_code'])
agent = json.loads((root / 'agent-result.json').read_text())
for i, call in enumerate((call for message in agent['transcript']
                         for call in message.get('tool_calls', [])), 1):
    print(i, call['function']['name'])
CHECK
```

Expected successful result: `resolved: true`, normally `finished: true`, and `cleanup_completed: true`; verifier output says `Ran 10 tests` and `OK` with exit code 0. Completion and verifier success are separate fields.

| Artifact under results/<run>/e2b/ | Meaning |
|---|---|
| `result.json` | Overall result, completion, sandbox ID, and cleanup status |
| `baseline.json` | Before repair: the recorded bug passes 1/10 tests |
| `verifier.json` | Independent tests after the repair |
| `interval_utils.py` | Repaired function |
| `agent-result.json` | Message/tool transcript and agent statistics |
| `task.log` | Detailed execution log |
| `sandbox.json` | Created instance ID, saved before agent execution |

This is a message/tool transcript, not a Gateway token/logprob trajectory. Keep E2B_RUN set to the run name when switching terminals.

## 5. Cleanup

A normal run calls the provider's stop/kill when leaving the sandbox context. If the process was interrupted and cleanup is uncertain, this command kills only the instance saved by this run:

```bash
docker exec -i -w /lab/scripts \
  -e E2B_REPLAY_DIR="/lab/results/$E2B_RUN/e2b" \
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

When you no longer need the model started in step 2:

```bash
docker stop ua-lab-model-tp1
```

## Optional: upstream tool demo

This checks sandbox tools without running the model-driven repair above:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_e2b_demo.py
```

[Experiment overview](README.md) · [E2B API](API.md) · [Historical paired runner](../03_docker_sandbox/code/run_sandbox_agent.py)
