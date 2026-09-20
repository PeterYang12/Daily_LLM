# Use and manage the local E2B service

**Current service:** use [experiment 11](../11_local_e2b_capacity/STEPS.md). This file preserves the original small-VM procedure; that VM is now stopped.

These commands target the prepared local deployment. The E2B service is inside a dedicated VM; the host's existing ROCm kernel remains unchanged.

## 1. Check service status from the host

```bash
cd /home/yuhanya/uni-agent-lab/local-e2b-20260916
bash code/status.sh
```

The health endpoint should respond successfully and the E2B Pod should show `8/8 Running`.

## 2. Create, use, and delete a sandbox

Use the existing CPU coordinator container and its E2B SDK 2.49.1:

```bash
docker exec -i ua-lab-cpu /lab/envs/cpu/bin/python - <<'PY'
import sys
sys.path.insert(0, '/lab/scripts')
from run_e2b_e2e import load_environment
from e2b import Sandbox

load_environment('/lab/secrets/local-e2b/container.env')
sb = Sandbox.create('base', timeout=300)
try:
    print('Sandbox:', sb.sandbox_id)
    sb.files.write('/tmp/hello.txt', 'hello local E2B\n')
    result = sb.commands.run('cat /tmp/hello.txt')
    print('Exit code:', result.exit_code)
    print(result.stdout)
finally:
    sb.kill()
PY
```

The SDK control URL is `http://172.30.90.1:18090`; its sandbox traffic URL is `http://172.30.90.1:18091`. They are different endpoints. Host-side clients use the settings from `secrets/local-e2b/host.env`, with both URLs on `127.0.0.1`.

Use an explicit timeout at or below 3,600 seconds. Template names and IDs belong to this installation; earlier remote E2B template IDs do not apply here.

## 3. Repeat the SDK acceptance checks

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-e2b-20260916/code/smoke.py \
  --output /lab/local-e2b-20260916/results/sdk-smoke-manual.json
```

The test creates temporary sandboxes and removes them. It checks files, commands, stateful Bash, nonzero exits, and four concurrent sandbox operations.

## 4. Inspect Kubernetes and service logs

```bash
cd /home/yuhanya/uni-agent-lab/local-e2b-20260916
python3 code/vmctl.py sudo k3s kubectl -n e2b get pods -o wide
python3 code/vmctl.py sudo k3s kubectl -n e2b logs e2b-0 -c api --tail=30
python3 code/vmctl.py sudo k3s kubectl -n e2b logs e2b-0 -c orchestrator --tail=30
```

`vmctl.py` uses the deployment SSH configuration and redacts E2B keys in command output. The host also forwards port 18092 to the VM Kubernetes API; the guest kubeconfig remains root-readable inside the VM. Do not print Kubernetes Secrets when collecting logs.

## 5. Repeat the Uni-Agent Flask task

Start the temporary test model and wait for the model endpoint to become ready:

```bash
cd /home/yuhanya/uni-agent-lab/local-e2b-20260916
python3 code/start_test_model.py
curl --fail http://127.0.0.1:18088/v1/models
```

Create a new result directory so the driver does not reuse completed results:

```bash
LOCAL_E2B_RUN="manual-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "results/$LOCAL_E2B_RUN/assets"
cp results/uni-agent-flask/manifest.json "results/$LOCAL_E2B_RUN/manifest.json"
cp results/uni-agent-flask/templates.json "results/$LOCAL_E2B_RUN/templates.json"
cp results/uni-agent-flask/assets/tmux.tar.gz "results/$LOCAL_E2B_RUN/assets/tmux.tar.gz"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-e2b-20260916/code/run_local_uni_agent.py \
  --root "/lab/local-e2b-20260916/results/$LOCAL_E2B_RUN" --mode controls

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-e2b-20260916/code/run_local_uni_agent.py \
  --root "/lab/local-e2b-20260916/results/$LOCAL_E2B_RUN" --mode react
```

Inspect `controls-v2/e2b/pallets__flask-5014/{baseline,oracle}/result.json`, then `react-local/e2b/pallets__flask-5014/result.json`. Raw token trajectories are under its `sessions/` directory. After your test, release the temporary inference replica with `docker stop ua-e2b-test-model`; this does not stop E2B.

The wrapper reuses the existing `ProfileE2B`, Uni-Agent task runner, ReAct, Gateway, and SWE reward implementation. It selects local credentials and a 3,600-second lifetime. No training update is performed.

## 6. Stop and resume the deployment

After client tasks have finished, stop the outer VM cleanly:

```bash
sudo systemctl stop ua-e2b-local-tunnel.service
sudo virsh shutdown ua-e2b-local-20260916
```

This ends running sandboxes while preserving the VM disk, templates, and database. Resume with:

```bash
sudo virsh start ua-e2b-local-20260916
sudo systemctl start ua-e2b-local-tunnel.service
```

Wait for Kubernetes and E2B readiness, then run `code/status.sh`. The VM has libvirt autostart and the tunnel unit is enabled. To disable automatic startup without deleting data, use `sudo virsh autostart --disable ua-e2b-local-20260916` and `sudo systemctl disable ua-e2b-local-tunnel.service`.

## 7. Rebuild notes

The installed assets are retained in the runtime directory. Their roles are:

| File | Purpose |
|---|---|
| `downloads/cloud-image-provenance.json` | Ubuntu cloud image URL and verified SHA256 |
| `code/create_vm.py` | Cloud-init seed and libvirt VM creation; refuses an existing domain |
| `code/bootstrap_guest.sh` | Guest packages, 32 GiB hugepage reservation, and pinned K3s installation |
| `configs/deploy/upstream/` | Pinned official E2B Kubernetes resources |
| `configs/deploy/overlay/` | Resource limits, init ordering, 1 GiB base template, and credential-log handling |
| `code/build_overlay.py` | Generates JSON patches against the pinned init-container positions, preserving startup order |
| `code/deploy_e2b.sh` | Kubernetes Secret creation, site-specific guest network rules, and deployment |
| `code/setup_access.py` | Owned VM DHCP reservation, autostart, and local SSH tunnels |
| `code/fetch_credentials.py` | Stores generated local credentials in protected files |

For a fresh machine, review VM names, paths, IP addresses, available resources, and DNS routing. Obtain the pinned E2B source commit from [the official repository](https://github.com/e2b-dev/runtime/tree/e7b22501988c826563bf1b3cf0d22a1183e7affe). The VM creator requires `pycdlib==1.16.0` and the overlay generator requires `PyYAML==6.0.2`; this deployment keeps both in the runtime's `.tools` virtual environment. New credentials and SSH keys should be generated for a new installation.
