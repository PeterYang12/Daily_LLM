# OpenEnv API Reference

Notes from reading `huggingface/OpenEnv` @ main (`src/openenv/core/`), 2026-09-14.
2.6k stars, BSD-3-Clause, formerly under `meta-pytorch`. Officially marked experimental — the API will change.

Related: [LLM Sandbox landscape](./llm-sandbox-landscape-2026.md)

## Is it a de facto standard?

Not yet. It's the strongest candidate.

**For:** governance committee spans Meta-PyTorch, HF, NVIDIA, Microsoft, Modal, Prime Intellect, Mercor, Unsloth, Reflection, Fleet AI, RadixArk. Integrated with TRL, torchforge, SkyRL, ART, Oumi, Unsloth, Lightning AI. Ships 39 environments (Atari, Chess, BrowserGym, CARLA, OpenSpiel, SUMO-RL, Terminal-Bench 2, Jupyter, git, coding, websearch…). API deliberately mirrors Gymnasium.

**Against:** 2.6k stars vs verl's 23.4k and Harbor's 5.2k. README says expect bugs and breaking changes. **verl has no first-party integration** — the largest RL training framework is absent, and verl users tend to go through Harbor's `RemoteAgentLoop` instead.

Two parallel tracks today: OpenEnv owns the *environment interface*, Harbor owns *rollout orchestration*. No winner yet. Writing new environments against OpenEnv is cheap (Gymnasium + Pydantic + FastAPI), so it's a reasonable bet — just don't assume trainer-side support.

## Architecture

```
Trainer (TRL / SkyRL / ART / torchforge)
     │  EnvClient.reset() / .step(action) / .state
     │      ↕  HTTP (POST /reset, /step)  or  WebSocket (/ws)
     ▼
FastAPI server — create_app(EnvCls, ActionT, ObsT)
     │  Environment.reset() / .step() / .state
     ▼
Your Environment implementation (in a container)
```

Both sides mirror each other, bridged by Pydantic JSON schemas.

| | Server | Client |
|---|---|---|
| Base class | `Environment[ActT, ObsT, StateT]` (ABC) | `EnvClient[ActT, ObsT, StateT]` (ABC) |
| Module | `openenv.core.env_server.interfaces` | `openenv.core.env_client` |
| Sync variant | — | `SyncEnvClient`, via `.sync()` |

## Data models

`core/env_server/types.py`. All Pydantic `BaseModel` — validation and auto-generated JSON Schema are the foundation of the whole design.

```python
class Action(BaseModel):
    model_config = ConfigDict(
        extra="forbid",                # reject unknown fields
        validate_assignment=True,
        arbitrary_types_allowed=True,  # numpy arrays, torch tensors
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True,
                              arbitrary_types_allowed=True)
    done: bool = False
    reward: bool | int | float | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class State(BaseModel):
    model_config = ConfigDict(extra="allow", ...)   # allow, not forbid
    episode_id: Optional[str] = None
    step_count: int = Field(default=0, ge=0)
```

**`reward` and `done` live on the Observation**, not as extra return values. Server-side `step()` returns a single `ObsT`. This is the biggest shape difference from Gymnasium's 5-tuple.

`State` is internal state, kept separate from observations — the agent sees `Observation`, the training framework reads `State`.

Client side gets a flattened dataclass (`core/client_types.py`):

```python
@dataclass
class StepResult(Generic[ObsT]):
    observation: ObsT
    reward: Optional[float] = None
    done: bool = False
    metadata: Optional[Dict[str, Any]] = None
```

Wire format:

```python
ResetRequest   {seed?: int, episode_id?: str, ...}       # extra="allow"
ResetResponse  {observation: dict, reward?, done, metadata?}
StepRequest    {action: dict, timeout_s?: float, request_id?: str, ...}
StepResponse   {observation: dict, reward?, done, metadata?}
```

Both request models are `extra="allow"`, so custom parameters (e.g. `split` / `index` for task selection) pass straight through.

## Server: `Environment`

```python
class Environment(ABC, Generic[ActT, ObsT, StateT]):
    SUPPORTS_CONCURRENT_SESSIONS: bool = False
    REQUIRES_SINGLE_THREAD_EXECUTOR: bool = False
    rubric: Optional["Rubric"]

    def __init__(self, transform: Optional[Transform[ObsT]] = None,
                       rubric: Optional["Rubric"] = None): ...

    @abstractmethod
    def reset(self, seed=None, episode_id=None, **kwargs) -> ObsT: ...

    @abstractmethod
    def step(self, action: ActT, timeout_s=None, **kwargs) -> ObsT: ...

    @property
    @abstractmethod
    def state(self) -> StateT: ...

    # default to the sync versions; override for true async
    async def reset_async(self, seed=None, episode_id=None, **kwargs) -> ObsT: ...
    async def step_async(self, action, timeout_s=None, **kwargs) -> ObsT: ...

    def get_metadata(self) -> EnvironmentMetadata: ...
    def close(self) -> None: ...
```

Three required members: `reset()`, `step()`, `state`.

### `SUPPORTS_CONCURRENT_SESSIONS`

Defaults to `False`. Set it to `True` and each WebSocket connection gets its own env instance, up to `max_concurrent_envs`. Requires real session isolation: unique working dirs, no shared mutable state, external resources that tolerate concurrent access.

**Required for RL rollouts.** Leave it `False` and one container runs one episode at a time.

### Transform

TorchRL-style observation rewriting, typically for reward computation or observation augmentation. Base class provides `self._apply_transform(obs)`.

```python
class Transform(ABC, Generic[ObsT]):
    @abstractmethod
    def __call__(self, observation: ObsT) -> ObsT: ...
```

### Rubric

Pluggable reward computation ([RFC-004](https://github.com/huggingface/OpenEnv/blob/main/rfcs/004-rubrics.md)). The base class gives you four helpers, which you call explicitly:

```python
def step(self, action, ...) -> MyObservation:
    obs = ...
    obs.reward = self._apply_rubric(action, obs)   # or await self._apply_rubric_async(...)
    return obs

def reset(self, ...) -> MyObservation:
    self._reset_rubric()                           # clear trajectory state
    ...
```

Introspectable from the training side:

```python
for name, r in env.rubric.named_rubrics():
    print(f"{name}: {r.last_score}")
```

Implementations in `core/rubrics/`: `llm_judge.py`, `trajectory.py`, `containers.py`.

### TaskProvider

A **structural Protocol** — declare the methods on your `Environment` subclass, no inheritance needed. When present, `HTTPEnvServer` exposes them as routes; when absent those routes return `501 Not Implemented`. Sync or async both work.

```python
env.list_splits()          # ["train", "test"]
env.list_tasks("test")
env.num_tasks("test")      # 7595
env.get_task("test", 12)   # {"id": "test-12", "index": 12}
env.get_task_range("test", start=0, stop=100)
```

Two constraints:

- These are **discovery only and must be side-effect-free**, and must work on a freshly constructed instance — HTTP compatibility routes may spin up a short-lived instance purely for task discovery.
- **Task selection is not part of this protocol.** Pass split and index to `reset()`: `env.reset(split="test", index=12)`.

## Client: `EnvClient`

Async-first; sync is a wrapper.

```python
class EnvClient(ABC, Generic[ActT, ObsT, StateT]):
    # three required serialization hooks
    @abstractmethod
    def _step_payload(self, action: ActT) -> Dict[str, Any]: ...
    @abstractmethod
    def _parse_result(self, payload: Dict[str, Any]) -> StepResult[ObsT]: ...
    @abstractmethod
    def _parse_state(self, payload: Dict[str, Any]) -> StateT: ...

    # public surface
    def reset(self, **kwargs) -> StepResult[ObsT] | Awaitable[...]
    def step(self, action: ActT, **kwargs) -> StepResult[ObsT] | Awaitable[...]
    def state(self) -> StateT | Awaitable[StateT]
    def close(self)
    def connect(self) / def disconnect(self)
    async def new_session(self) -> "EnvClient"

    @property
    def base_url(self) -> Optional[str]

    def sync(self) -> "SyncEnvClient"

    async def __aenter__ / __aexit__
    def __enter__ / __exit__
```

`reset()` / `step()` / `state()` dual-dispatch through `_dispatch()`: awaitable in async context, concrete result in sync context. The client **locks its execution mode on first use** (`_claim_execution_mode`) — you can't mix afterwards.

### Construction

```python
# 1. connect to a running server
EnvClient(base_url="http://localhost:8000")

# 2. start a Docker container
@classmethod
def from_docker_image(cls, image: str,
                      provider: Optional[ContainerProvider] = None,
                      **kwargs) -> _BootstrapResult

# 3. pull from an HF Space
@classmethod
def from_env(cls, repo_id: str, *,
             use_docker: bool = True,
             provider: Optional[ContainerProvider | RuntimeProvider] = None,
             **provider_kwargs) -> _BootstrapResult
```

`from_env(use_docker=False)` runs locally via `UVProvider`; `project_path` overrides the default git URL (`git+https://huggingface.co/spaces/{repo_id}`).

The last two return a lazy `_BootstrapResult` handle — the container starts when the handle resolves:

```python
env = await MyEnv.from_docker_image("coding-env:latest")    # async
env = MyEnv.from_docker_image("coding-env:latest").sync()   # sync, for TRL GRPO loops that can't await
```

That dual form exists specifically so synchronous training loops can use the same call.

### Usage

```python
# async (preferred)
async with EchoEnv(base_url="https://openenv-echo-env.hf.space") as client:
    result = await client.reset()
    result = await client.step(
        CallToolAction(tool_name="echo_message", arguments={"message": "Hello"})
    )
    print(result.observation.result, result.reward, result.done)

# sync
with EchoEnv(base_url="...").sync() as client:
    result = client.reset()
    result = client.step(CallToolAction(...))
```

## HTTP / WebSocket protocol

`create_app(EnvCls, ActionT, ObservationT, env_name=..., max_concurrent_envs=...)` exposes:

| Route | Method | Purpose |
|---|---|---|
| `/reset` | POST | `ResetRequest` → `ResetResponse` |
| `/step` | POST | `StepRequest` → `StepResponse` |
| `/state` | GET | current `State` |
| `/schema` | GET | JSON Schema for action, observation, state |
| `/metadata` | GET | name, description, readme, version, author |
| `/health` | GET | `healthy` / `unhealthy` / `degraded` |
| `/ws` | WS | main channel, concurrent sessions |
| `/mcp` | POST + WS | MCP endpoint |
| `/list_environments` | GET | Task API |
| `/{env_name}/splits` | GET | Task API |
| `/{env_name}/tasks` | POST | Task API |
| `/{env_name}/num_tasks` | POST | Task API |
| `/{env_name}/task` | POST | Task API |
| `/{env_name}/task_range` | POST | Task API |
| `/docs` `/redoc` `/openapi.json` | GET | FastAPI built-ins |

`/schema` is the load-bearing one: trainers discover the environment's action/observation shape at runtime instead of hardcoding types. That's what makes "implement once, consume anywhere" work.

`create_app` takes the **class, not an instance** — that's how per-session env instances get constructed.

### WebSocket messages

Discriminated union on `type`:

```python
{"type": "reset", "data": {...}}
{"type": "step",  "data": {...}}   # data is the action
{"type": "state"}
{"type": "close"}
```

Structured error codes (`WSErrorCode`): `INVALID_JSON`, `UNKNOWN_TYPE`, `VALIDATION_ERROR`, `EXECUTION_ERROR`, `CAPACITY_REACHED`, `FACTORY_ERROR`, `SESSION_ERROR`.

**`CAPACITY_REACHED`** matters for RL — it's returned when concurrent sessions hit `max_concurrent_envs`. A rollout scheduler should back off on it rather than treat it as a hard failure.

## Writing an environment

```
my_env/
├── models.py                   # Action / Observation / State
├── client.py                   # MyEnv(EnvClient)
├── server/
│   ├── my_environment.py       # MyEnvironment(Environment)
│   ├── app.py                  # create_app(...)
│   ├── requirements.txt
│   └── Dockerfile
├── openenv.yaml
└── pyproject.toml
```

### Manifest

From `envs/echo_env/openenv.yaml`:

```yaml
spec_version: 1
name: echo_env
version: 0.1.0
type: space
runtime: fastapi
app: server.app:app
port: 8000
validation:
  reward:
    range: [0.0, 1.0]
    oracle_tolerance: 0.0
    floor_margin: 0.5
  resources:
    cpu: 1.0
    memory_mb: 1024
    disk_mb: 512
    episode_timeout_s: 60.0
  capabilities:
    verifier:
      kind: reward_channel
    declared_tools: [echo_message, echo_with_length]
  types:
    tags: [demo]
```

The `validation` block is what raw Gymnasium doesn't have: declared reward range, resource quota, episode timeout, verifier kind. A training scheduler needs exactly this — reward range to normalize correctly, resource quota to compute concurrency density.

### Server entrypoint

```python
from openenv.core.env_server.http_server import create_app
from .my_environment import MyEnvironment
from ..models import MyAction, MyObservation

app = create_app(
    MyEnvironment,              # the class, not an instance
    MyAction,
    MyObservation,
    env_name="my_env",
    max_concurrent_envs=int(os.getenv("MAX_CONCURRENT_ENVS", "8")),
)
```

### MCP environments — the shorter path

If the environment is essentially a set of tools, subclass `MCPEnvironment` with inline FastMCP tools. The client can then be empty.

```python
# server/my_environment.py
from openenv.core.env_server.mcp_environment import MCPEnvironment
from fastmcp import FastMCP

class MyEnvironment(MCPEnvironment):
    SUPPORTS_CONCURRENT_SESSIONS = True

    def __init__(self):
        mcp = FastMCP("my_env")

        @mcp.tool
        def echo_message(message: str) -> str:
            """Echo back the provided message."""
            return message

        super().__init__(mcp)
        self._state = State(episode_id=str(uuid4()), step_count=0)

    def reset(self, seed=None, episode_id=None, **kwargs) -> Observation:
        self._state = State(episode_id=episode_id or str(uuid4()), step_count=0)
        return Observation(done=False, reward=0.0, metadata={"status": "ready"})

    def step(self, action, timeout_s=None, **kwargs) -> Observation:
        self._state.step_count += 1
        return super().step(action, timeout_s=timeout_s, **kwargs)   # base routes MCP actions

    async def step_async(self, action, timeout_s=None, **kwargs) -> Observation:
        self._state.step_count += 1
        return await super().step_async(action, timeout_s=timeout_s, **kwargs)

    @property
    def state(self) -> State:
        return self._state
```

```python
# client.py — this is the whole file
from openenv.core.mcp_client import MCPToolClient

class MyEnv(MCPToolClient):
    pass
```

`MCPToolClient` provides `list_tools()`, `call_tool(name, **kwargs)`, `reset()`, `step(action)`:

```python
with MyEnv(base_url="http://localhost:8000") as env:
    env.reset()
    tools = env.list_tools()                              # [Tool(name='echo_message', ...)]
    result = env.call_tool("echo_message", message="Hi")  # "Hi"
```

MCP action types in `core/env_server/mcp_types.py`: `ListToolsAction`, `CallToolAction`, `CallToolObservation`.

The point: **the agent's tool definitions and the RL action space are the same artifact.** No double authoring.

## CLI

```bash
openenv init <name>                          # scaffold
openenv serve <name>                         # local server with auto-reload
openenv build                                # build Docker image
openenv validate                             # check openenv.yaml
openenv push [--repo-id ...] [--private]     # publish to HF Space
openenv import <source> --name <name>        # wrap a third-party environment
```

## Container providers

`core/containers/runtime/`. Where the environment runs is decoupled from what it does.

| Provider | File | Notes |
|---|---|---|
| `LocalDockerProvider` | `providers.py` | default |
| `DockerSwarmProvider` | `providers.py` | multi-host |
| `UVProvider` | `uv_provider.py` | no Docker, local uv |
| `DaytonaProvider` | `daytona_provider.py` | Daytona cloud sandbox |
| `ModalProvider` | `modal_provider.py` | Modal |
| `ACASandboxProvider` | `aca_provider.py` | Azure Container Apps |
| `HFSandboxProvider` | `hf_sandbox_provider.py` | HF sandbox |
| `KubernetesProvider` | `providers.py` | placeholder class only — doesn't implement the abstract methods, can't be instantiated |

This layer is the seam between OpenEnv and the [sandbox ecosystem](./llm-sandbox-landscape-2026.md): one environment definition, swap the provider to move from local Docker to Modal or Daytona.

## Other modules

| Module | Contents |
|---|---|
| `core/rubrics/` | `base.py`, `llm_judge.py`, `trajectory.py`, `containers.py` |
| `core/evals/` | `base.py`, `inspect_harness.py`, `types.py` — includes Inspect integration |
| `core/harness/` | `collect.py` — trajectory collection |
| `core/llm_client.py` | `OpenAIClient`, `AnthropicClient`, `create_llm_client`, `LLMResponse`, `ToolCall` |
| `core/tools/` | `local_python_executor.py`, `git_server_client.py` |
| `core/generic_client.py` | `GenericEnvClient`, `GenericAction` — untyped fallback |
| `core/env_server/gradio_ui.py` | auto-generated debug UI |

`ModelTokenizer` (Protocol in `interfaces.py`) is compatible with HF transformers tokenizers — requires `apply_chat_template(conversation, tokenize, return_tensors, **kwargs)` and `decode(token_ids, skip_special_tokens, **kwargs)`. `Message` is `TypedDict{role, content}`, matching HF chat template format.

## Differences from Gymnasium

| | Gymnasium | OpenEnv |
|---|---|---|
| `step()` returns | `(obs, reward, terminated, truncated, info)` | server: single `ObsT` with reward/done on it; client: `StepResult` |
| Types | numpy / `Space` objects | Pydantic models |
| Process model | in-process | HTTP / WebSocket, cross-container |
| Async | none | async-first, sync is a wrapper |
| State | mixed into obs/info | separate `state` property |
| Packaging | pip package | Docker + manifest + HF Space |
| Task sets | none | `TaskProvider` protocol |
| Reward | hardcoded in env | pluggable `Rubric`, incl. LLM judge |

## Getting started

1. Run `envs/echo_env` first — understand reset/step/state and the WebSocket channel.
2. Tool-shaped environment → `MCPEnvironment`, zero client code.
3. Traditional RL environment with state transitions → subclass `Environment` directly with your own Action/Observation/State.
4. Before any RL use: set `SUPPORTS_CONCURRENT_SESSIONS = True`, and fill in the `validation` block honestly.
5. Put reward logic in a `Rubric`, not inline in `step()` — the training side can then introspect and swap it.

## References

- [huggingface/OpenEnv](https://github.com/huggingface/OpenEnv) · [docs](https://huggingface.co/docs/openenv/index) · [API reference](https://huggingface.github.io/OpenEnv/)
- [HF blog: The Open Source Community is backing OpenEnv for Agentic RL](https://huggingface.co/blog/openenv-agentic-rl)
- [TRL OpenEnv integration](https://huggingface.co/docs/trl/en/openenv)
- [SkyRL + OpenEnv example](https://skyrl.readthedocs.io/en/latest/examples/openenv.html)
- [RFC-004: Rubrics](https://github.com/huggingface/OpenEnv/blob/main/rfcs/004-rubrics.md) · [Task API guide](https://huggingface.co/docs/openenv/guides/task-api)
- Source entry points: `src/openenv/core/env_server/interfaces.py`, `src/openenv/core/env_client.py`, `src/openenv/core/env_server/types.py`, `envs/echo_env/`
