# Uni-Agent API quick reference

Pinned commit: `10743439dd0a19da44a94cccad069b135d957bf1`. Most orchestration is Python; this Gateway defines two business HTTP routes.

## Model requests

| HTTP route | Purpose / caller |
|---|---|
| `POST /sessions/{session_id}/v1/chat/completions` | OpenAI-compatible chat/tool calls; ReAct and Mini |
| `POST /sessions/{session_id}/v1/messages` | Anthropic-compatible messages; Claude Code |

Create the session first through Python/Ray. There is no HTTP `POST /sessions` management endpoint. The backend binding selects the real model. This version formats SSE after a completed generation and does not implement API-key validation in its route handlers.

## Python orchestration

| Entry point | Purpose |
|---|---|
| `TaskConfigResolver.resolve(...)` | Merge task, agent, sandbox, and runtime model configuration |
| `get_task(config)` / `await task.run()` | Construct and execute a complete task |
| `build_agent(config)` / `task.build_agent()` | Construct the selected harness adapter |
| `await agent.run(sandbox=..., messages=...)` | Solve a task using an already started sandbox |
| `await Toolbox.call(name, args)` | Invoke ReAct tools such as shell and file editing |
| `compute_reward(...)` | Run the task verifier and return its score |

## Gateway lifecycle

`start()` → `create_session(...)` → model requests → `finalize_session(...)` → `shutdown()`.

`get_session_state(...)` inspects a session; `abort_session(...)` cancels it. Finalization returns token trajectories. The experiment directly instantiated `_GatewayActor`; exported `GatewayActor` is its Ray wrapper. Training adaptation through `generate_sequences(...)` exists in source, but no training update was run here.

[Complete source inventory](results/api-inventory.json) · [Pinned source](https://github.com/verl-project/uni-agent/tree/10743439dd0a19da44a94cccad069b135d957bf1/uni_agent)

