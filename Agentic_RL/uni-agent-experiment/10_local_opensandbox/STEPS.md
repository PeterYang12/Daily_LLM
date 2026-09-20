# Use and replay local OpenSandbox

The prepared deployment runs in the independent `ua-opensandbox-local-20260916` VM. These commands are issued from the AMD host unless otherwise noted.

## 1. Check status

```bash
cd /home/yuhanya/uni-agent-lab/local-opensandbox-20260916
bash code/status.sh
```

The API health endpoint is `http://127.0.0.1:18094/health`. Both Deployments in `opensandbox-system` should be ready.

## 2. Create and use a sandbox through the provider

```bash
docker exec -i ua-lab-cpu /lab/envs/cpu/bin/python - <<'PY'
import asyncio
import sys
sys.path.insert(0, '/lab/local-opensandbox-20260916/code')
from provider import OpenSandboxProvider

async def main():
    sandbox = OpenSandboxProvider('ubuntu:24.04', timeout=300, cpu='1', memory='1Gi')
    async with sandbox:
        print('Sandbox:', sandbox.sb.id)
        await sandbox.write_file('/tmp/hello.txt', 'hello OpenSandbox\n')
        result = await sandbox.exec(['cat', '/tmp/hello.txt'])
        print('Exit code:', result.exit_code)
        print(result.stdout)
        shell = await sandbox.open_shell(cwd='/tmp', env={'DEMO': 'retained'})
        try:
            print((await shell.run('printf "%s:%s" "$PWD" "$DEMO"')).stdout)
        finally:
            await shell.close()

asyncio.run(main())
PY
```

The provider loads the protected container settings and enables SDK server-proxy routing. The same HTTP port carries lifecycle, command, and file requests; direct Pod IPs are not needed by the client.

For SDK-only usage, import `SDK` and `settings` from `provider`, then call `await SDK.create('ubuntu:24.04', connection_config=settings())`. Call `destroy()` afterward; closing only the SDK transport retains the sandbox.

## 3. Inspect Kubernetes resources

```bash
cd /home/yuhanya/uni-agent-lab/local-opensandbox-20260916
python3 code/vmctl.py sudo k3s kubectl -n opensandbox-system get deployments,pods,services
python3 code/vmctl.py sudo k3s kubectl -n opensandbox get batchsandboxes,pods
python3 code/vmctl.py sudo k3s kubectl -n opensandbox-system logs deployment/opensandbox-server --tail=30
```

The second command shows the actual workload resources while a sandbox is running. The helper uses the deployment's SSH key and redacts recognized API keys in output.

## 4. Repeat provider checks

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-opensandbox-20260916/code/smoke.py \
  --output /lab/local-opensandbox-20260916/results/provider-smoke-manual.json
```

It creates temporary sandboxes, exercises output/files/state/timeouts/cancellation/concurrency, and destroys them.

## 5. Repeat the full Uni-Agent example

Start the prepared model replica and wait until `/v1/models` succeeds:

```bash
docker start ua-threeway-model
curl --fail http://127.0.0.1:18087/v1/models
```

For another model service, pass `--base-url` to the driver; it must serve `Qwen3-Coder-30B-A3B-Instruct` with the required context/token/logprob support.

```bash
cd /home/yuhanya/uni-agent-lab/local-opensandbox-20260916
OSB_RUN="manual-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "results/$OSB_RUN/assets"
cp results/uni-agent-flask/manifest.json "results/$OSB_RUN/manifest.json"
cp results/uni-agent-flask/assets/tmux.tar.gz "results/$OSB_RUN/assets/tmux.tar.gz"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-opensandbox-20260916/code/run_e2e.py \
  --root "/lab/local-opensandbox-20260916/results/$OSB_RUN" --mode controls

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-opensandbox-20260916/code/run_e2e.py \
  --root "/lab/local-opensandbox-20260916/results/$OSB_RUN" --mode react
```

Inspect `controls/opensandbox/pallets__flask-5014/{baseline,oracle}/result.json` and `react/opensandbox/pallets__flask-5014/result.json`. Its `sessions/` directory holds the token trajectory. The retained tmux asset is part of the shared task preparation; the agent's shell path uses OpenSandbox native sessions.

If you started the model only for this test, stop it afterward with `docker stop ua-threeway-model`. OpenSandbox remains running.

## 6. Stop and resume the platform

Once client tasks finish:

```bash
sudo systemctl stop ua-opensandbox-local-tunnel.service
sudo virsh shutdown ua-opensandbox-local-20260916
```

Resume with:

```bash
sudo virsh start ua-opensandbox-local-20260916
sudo systemctl start ua-opensandbox-local-tunnel.service
```

The VM and tunnel are configured for automatic startup. API Deployment restart was tested; full host reboot and node-failure recovery were not tested. VM shutdown ends running sandbox processes.

## 7. Deployment sources

The runtime retains the verified Ubuntu image, K3s binary, Helm binary, and released chart in `downloads/`. [configs/values.yaml](configs/values.yaml) sets released component images, the API key Secret reference, a persistent metadata path, and Kubernetes runtime settings. [configs/api-service.yaml](configs/api-service.yaml) exposes the API through NodePort 30808; the chart's internal Service remains ClusterIP.

The VM creator requires `pycdlib==1.16.0`. Helm `v3.19.0` and K3s `v1.36.4+k3s1` were verified against published SHA256 values. The corresponding provenance records are under `results/`.

`create_vm.py` prepares cloud-init and the libvirt domain; `bootstrap_guest.sh` installs K3s in that VM; `deploy.py` installs the chart and supporting resources; `setup_access.py` configures the owned DHCP reservation and SSH tunnels. For a fresh machine, review paths, MAC/IP addresses, resource availability, and generate new SSH/API keys.

The separate SDK directory was installed without changing the existing CPU environment:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python -m pip install --no-deps \
  --target /lab/envs/opensandbox-sdk opensandbox==0.1.16 httpx-sse==0.4.3
```

This assumes the prepared CPU environment already provides the SDK's Pydantic, HTTPX, httpcore, attrs, and dateutil dependencies. Existing installations should be inspected before reinstalling into the same target directory.
