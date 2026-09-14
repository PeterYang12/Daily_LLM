# E2B API used in this experiment

The experiment registers `e2b_compat` as a Uni-Agent provider, mapping to these E2B SDK operations.

| SDK operation | Underlying interface | Purpose |
|---|---|---|
| `AsyncSandbox.create(...)` | `POST /sandboxes` | Create from an existing template |
| `sb.commands.run(...)` | `/process.Process/Start` (Connect RPC) | Execute and collect output |
| `sb.files.write(...)` | `POST /files` | Write a file |
| `sb.files.read(...)` | `GET /files` | Read a file |
| `sb.is_running()` | `GET /health` | Probe the execution environment |
| `sb.kill()` | `DELETE /sandboxes/{sandbox_id}` | Destroy the instance |

Management uses `E2B_API_URL` and `E2B_API_KEY` (`X-API-Key`). Execution uses `E2B_SANDBOX_URL`; the SDK supplies routing headers and an execution token when returned by the service. Command execution is Connect RPC, not a plain `/commands` REST call.

The adapter maps Uni-Agent `image` to E2B `template`, and `runtime_timeout` to seconds. It implements commands, files, and lifecycle; it does not implement template builds, snapshots, persistent volumes, or native port exposure.

[Adapter](code/e2b_provider.py) · [Unified Sandbox API](../03_docker_sandbox/API.md) · [Full contract inventory](../01_model_and_examples/results/api-inventory.json)

The full inventory contains 46 management REST and 17 envd RPC contracts from SDK 2.49.1. Only the subset above and documented workflows were validated against the supplied service.

