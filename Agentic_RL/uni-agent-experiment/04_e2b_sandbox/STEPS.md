# Replay: E2B sandbox

Prerequisites: complete lab, running CPU services, local `tp1` model, E2B SDK, and an existing compatible template (`testlab-python-node` in the recorded experiment).

## 1. Configure the endpoint

Use [e2b.env.example](configs/e2b.env.example) as a format reference. The lab reads:

```text
/lab/secrets/e2b.env
E2B_API_KEY=...
E2B_API_URL=...
E2B_SANDBOX_URL=...
```

Keep the real file outside this repository with mode 600. Management and execution URLs can share a proxy prefix; the SDK handles their different requests.

## 2. Run the official sandbox demo

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_e2b_demo.py
```

This imports the provider before running upstream's demo. Check persistent state and file operations against the [saved log](results/e2b-official-demo.txt).

The [SDK smoke source](code/e2b_smoke.py) uses a fixed historical output filename. Change that destination in a replay copy before running it.

## 3. Run the paired Docker/E2B repair

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py model --which tp1

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_sandbox_agent.py \
  --output /lab/results/replay-sandbox-agent
```

Use a new output directory. This is the same paired command as [experiment 3](../03_docker_sandbox/STEPS.md); run it once for both results. Shared source: [run_sandbox_agent.py](../03_docker_sandbox/code/run_sandbox_agent.py).

Inspect E2B baseline/verifier files and confirm cleanup. The recorded task passed 10/10 tests and destroyed its instance. This ReAct arrangement makes model requests locally, so the remote instance does not need access to the local model endpoint.

