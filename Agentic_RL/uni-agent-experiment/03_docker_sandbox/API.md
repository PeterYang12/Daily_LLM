# Local sandbox API

## Uni-Agent Python interface

| Method | Purpose |
|---|---|
| `build_sandbox(config)` | Construct a provider object; does not start it |
| `await start()` / `await stop()` | Create / destroy the environment |
| `await is_alive()` | Check availability |
| `await exec(argv, ...)` / `exec_shell(script, ...)` | Run a command; return exit_code, stdout, stderr |
| `await read_file(path)` / `write_file(path, content)` | Read / write sandbox files |
| `await upload(local, remote)` / `download(remote, local)` | Transfer files or directories between caller and sandbox |

`async with sandbox` manages startup and cleanup. Local paths belong to the CPU caller's filesystem. One-shot exec does not preserve cwd/env; the persistent tool used tmux. Native shell and port exposure are optional provider capabilities.

## Docker Engine interface

The CPU container's Docker CLI sends these through `/lab/run/docker.sock` to the dedicated daemon.

| Engine API | Purpose |
|---|---|
| `POST /containers/create` + `POST /containers/{id}/start` | Create and start a task sandbox |
| `POST /containers/{id}/exec` + `POST /exec/{id}/start` | Execute a command |
| `GET /exec/{id}/json` | Read command exit state |
| `GET /containers/{id}/json` | Inspect container state |
| `GET /containers/{id}/archive` / `PUT /containers/{id}/archive` | Download / upload files |
| `DELETE /containers/{id}?force=1` | Terminate and delete the container |

The provider does not enforce a Docker TTL just because `runtime_timeout` is set; this experiment adds a janitor.

[Remote E2B interface](../04_e2b_sandbox/API.md) · [Source inventory](../01_model_and_examples/results/api-inventory.json)

