# Example: build one E2B template step by step

## Step 0: set the environment and start Python

```bash
pip install e2b==2.64.0

export E2B_API_URL=http://<e2b-server-address>
export E2B_SANDBOX_URL=http://<e2b-server-address>
export E2B_API_KEY=<e2b-api-key>

# Optional: pull images with a Docker Hub account instead of anonymously (100 pulls per hour).
export DOCKERHUB_USERNAME=<docker-hub-user>
export DOCKERHUB_TOKEN=<docker-hub-access-token>

python
```

Paste the following steps into this Python session in order.

```python
import os

import e2b
from e2b import Sandbox

IMAGE = "swerebench/sweb.eval.x86_64.12rambau_1776_sepal_ui-574"  # the task's Docker image
NAME = "example-tmux"                                             # template name
```

## Step 1: describe the template

```python
# Base image. With a Docker Hub account the registry must be spelled out.
user, token = os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
if user and token:
    template = e2b.Template().from_image("docker.io/" + IMAGE, username=user, password=token)
else:
    template = e2b.Template().from_image(IMAGE)  # anonymous pull, 100 per hour

# Command the server runs inside the VM after it boots.
template = template.run_cmd("apt-get update -qq; apt-get install -y -qq tmux", user="root")

# The snapshot is taken only after this command succeeds.
template = template.set_ready_cmd("tmux -V")
```

## Step 2: build

```python
info = e2b.Template.build(
    template,
    NAME,
    cpu_count=2,
    memory_mb=4096,
    on_build_logs=lambda entry: print(entry),
)
print("template id:", info.template_id)
```

## Step 3: check the status

```python
print(e2b.Template.get_build_status(info).status.value)
```

Expected: `ready`.

## Step 4: start a sandbox from the template

```python
sandbox = Sandbox.create(NAME, timeout=300)  # destroyed automatically after 300 s
print("sandbox id:", sandbox.sandbox_id)
```

## Step 5: look inside the sandbox

```python
result = sandbox.commands.run("tmux -V", user="root")
print(result.stdout)
```

Expected: the tmux version, e.g. `tmux 3.3a`.

## Step 6: clean up

```python
sandbox.kill()
e2b.Template.delete(NAME)
```
