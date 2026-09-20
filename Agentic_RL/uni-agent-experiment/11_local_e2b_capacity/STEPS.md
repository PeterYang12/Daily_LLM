# Use and manage the larger local E2B

Commands below run on the host unless they explicitly use `docker exec` or SSH. The existing CPU container has E2B SDK 2.49.1 and the Uni-Agent environment. The service already runs; normal use does not require recreating the VM.

## 1. Check current state

```bash
cd /home/yuhanya/uni-agent-lab/local-e2b-96c512g-20260916
bash code/status.sh
```

Expected: 96 CPUs, 512 GiB configured memory, `8/8 Running`, and a successful health response. `ua-e2b-local-tunnel.service` now points to the large VM despite retaining its original service name.

To inspect Kubernetes directly:

```bash
ssh -F configs/ssh_config ua-e2b-large 'sudo k3s kubectl get nodes -o wide; sudo k3s kubectl -n e2b get pods'
```

## 2. Reuse the original E2B client configuration

No API key rotation or provider change is needed. The CPU container reads `/lab/secrets/local-e2b/container.env`; host clients read `/home/yuhanya/uni-agent-lab/secrets/local-e2b/host.env`. Do not substitute the separate remote-provider credential file.

Run lifecycle, command, file, persistent-shell, and cleanup checks, saving a new result:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/local-e2b-96c512g-20260916/code/smoke.py \
  --output "/lab/local-e2b-96c512g-20260916/results/manual-smoke-$(date -u +%Y%m%dT%H%M%SZ).json"
```

The existing Uni-Agent E2B provider can use the preserved `base` and Flask template IDs. The API URL remains `http://172.30.90.1:18090` in `ua-lab-cpu`; sandbox execution uses port `18091`. Model serving and Uni-Agent Gateway sessions are separate from this sandbox service.

## 3. Replay the 24-environment acceptance

Run one capacity measurement at a time. It uses 24 instances of the real Flask SWE-bench template and creates at most three at once. It does not start a model service.

```bash
cd /home/yuhanya/uni-agent-lab/local-e2b-96c512g-20260916
python3 code/run_concurrency.py \
  --name "manual-capacity-$(date -u +%Y%m%dT%H%M%SZ)" --n 24
```

The driver samples guest resources, waits until all sandboxes coexist, records a guest snapshot, releases parallel verification, and destroys the sandboxes. The new `results/manual-capacity-*/summary.json` should contain `simultaneously_live: 24`, `passed: 24`, `all_cleaned: true`, and `success: true`. Each case includes verifier output and timing spans. Twelve baseline outcomes should be unresolved, and twelve gold outcomes resolved.

The existing template metadata and SWE task are read from `local-e2b-20260916/results/uni-agent-flask`. That directory is preserved even though its VM has been retired.

## 4. Start and stop the platform

Stop after owned tasks have completed and their sandboxes have been destroyed:

```bash
sudo systemctl stop ua-e2b-local-tunnel.service
sudo virsh shutdown ua-e2b-96c512g-20260916
```

Start again:

```bash
sudo virsh start ua-e2b-96c512g-20260916
sudo systemctl start ua-e2b-local-tunnel.service
bash /home/yuhanya/uni-agent-lab/local-e2b-96c512g-20260916/code/status.sh
```

VM autostart and the tunnel service are enabled. K3s, E2B, hugepages, the internal-port guard, and sandbox DNS forwarding persist across reboot. The full reboot check recovered in about 149 seconds. A host power-cycle was not tested. The recorded reboot occurred with zero active sandboxes; it does not establish survival of running sandboxes across a VM reboot.

## 5. Deployment recipe and rollback assets

The recorded build order was:

1. Define a fresh domain with `code/create_vm.py`, using the retained Ubuntu cloud image and a new SSH key.
2. Copy the verified K3s binary into the guest; run `code/bootstrap_guest.sh` to configure the guest and hugepages before kubelet starts.
3. Stop the old E2B application, take its cold backup, and shut down the old VM with `code/backup_and_stop_old.py`.
4. Run `code/build_overlay.py`, then `code/restore_and_deploy.py` to restore data and the old API Secret before starting the new service.
5. Run `code/setup_access.py` to reserve its address, enable autostart, and replace the stable host tunnel.
6. Run `code/configure_limits.py`, SDK smoke, capacity acceptance, `code/reboot_check.py`, and SDK smoke again.

These migration scripts are a record of a one-time migration, not a blind rerun sequence against the running service. Creating a second deployment requires a new domain name, state file, IP/MAC, paths, and ports. `create_vm.py` requires Python `pycdlib`; `build_overlay.py` requires PyYAML. Private state is intentionally outside the report/export.

The old domain `ua-e2b-local-20260916` remains defined with autostart disabled. Its disk remains under `/var/lib/libvirt/images/ua-e2b-local-20260916`. The cold archive and original API Secret are protected under `/home/yuhanya/uni-agent-lab/secrets/local-e2b-large`. The archive represents state at migration time, so later changes made on the new service are separate.

To deliberately return to that old snapshot: first stop the new service and VM, start the old domain, then restore its E2B replica and tunnel:

```bash
sudo virsh start ua-e2b-local-20260916
ssh -F /home/yuhanya/uni-agent-lab/local-e2b-20260916/configs/ssh_config ua-e2b \
  'sudo k3s kubectl -n e2b scale statefulset/e2b --replicas=1'
sudo install -m 644 \
  /home/yuhanya/uni-agent-lab/local-e2b-96c512g-20260916/configs/previous/ua-e2b-local-tunnel.service \
  /etc/systemd/system/ua-e2b-local-tunnel.service
sudo systemctl daemon-reload
sudo systemctl restart ua-e2b-local-tunnel.service
```

Allow the old VM to finish booting before the SSH command. If choosing the old deployment permanently, disable autostart for the new VM and enable it for the old one. This rollback procedure is documented but was not executed after acceptance. OpenSandbox is independent and is not involved in these operations.
