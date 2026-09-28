# 在另一台机器手动搭建 E2B Sandbox

这份 README 从新建 VM 开始，到客户端能创建 sandbox 为止。

```text
新宿主机 → Ubuntu 24.04 VM → K3s → E2B → Firecracker sandbox
客户端   → 新宿主机的内网 IP:8088
```

宿主机使用 Ubuntu 24.04、x86-64 CPU，需要 sudo 权限和 KVM 嵌套虚拟化。
宿主机和 VM 都要能下载 GitHub、Go、K3s、容器镜像和 E2B 制品。不需要 E2B 云账号。

## 0. 把材料放到新宿主机

把随附的 `e2b-manual-setup-20260924.tar.gz` 复制到新宿主机，然后执行：

```bash
set -euo pipefail
test ! -e "$HOME/e2b-setup" || { echo 'e2b-setup 已存在，请先换一个新目录'; exit 1; }
tar -xzf e2b-manual-setup-20260924.tar.gz -C "$HOME"
```

解压后应有 `~/e2b-setup/README.md`、`code/`、`configs/`、`patches/`。
包里没有旧 VM 磁盘、模型、日志或 API key。

## 1. 宿主机：设置参数

以下示例用 **32 vCPU、128 GiB 内存**，先把服务跑通。运行 RL 时按实际资源调大。
保留本包配置时，VM 至少需要 **16 vCPU、32 GiB 内存**。
本机曾用 192 vCPU、2253 GiB；新机器不必照搬，也要给其他任务留内存。

```bash
set -euo pipefail
read -rp '新宿主机的内网 IP: ' HOST_IP
: "${HOST_IP:?必须填写 IP}"
export HOST_IP
export VM_CPUS=32
export VM_RAM=128G
export VM_DISK=1T
export SETUP="$HOME/e2b-setup"
export VM_DIR="$HOME/e2b-vm-new"   # 大文件目录，可改到自己的数据盘

sudo apt-get update
sudo apt-get install -y qemu-system-x86 qemu-utils cloud-image-utils \
  openssh-client curl python3 python3-venv kmod

case "$(awk '/vendor_id/{print $3; exit}' /proc/cpuinfo)" in
  AuthenticAMD) export KVM_MODULE=kvm_amd CPU_FEATURE=svm=on ;;
  GenuineIntel) export KVM_MODULE=kvm_intel CPU_FEATURE=vmx=on ;;
  *) echo '本指南只适用于 AMD/Intel x86-64'; exit 1 ;;
esac
sudo modprobe "$KVM_MODULE"
test -c /dev/kvm
cat "/sys/module/$KVM_MODULE/parameters/nested"

declare -p HOST_IP VM_CPUS VM_RAM VM_DISK SETUP VM_DIR KVM_MODULE CPU_FEATURE \
  > "$SETUP/host-settings.sh"
```

最后的 `nested` 应显示 `Y` 或 `1`。若没有开启，在**新宿主机**执行下面两条，再重启宿主机：

```bash
printf 'options %s nested=1\n' "$KVM_MODULE" | sudo tee /etc/modprobe.d/e2b-kvm.conf
sudo reboot
```

重启或换终端后，先执行 `source ~/e2b-setup/host-settings.sh` 恢复变量。

## 2. 宿主机：创建并启动 VM

VM 目录必须是新的，不能覆盖已有磁盘。

```bash
source "$HOME/e2b-setup/host-settings.sh"
test ! -e "$VM_DIR" || { echo 'VM_DIR 已存在，请换一个新目录'; exit 1; }
mkdir -p "$VM_DIR"
cd "$VM_DIR"
ssh-keygen -t ed25519 -N '' -f vm_key
curl -fL https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img -o ubuntu.img
cp ubuntu.img root.qcow2
qemu-img resize root.qcow2 "$VM_DISK"

cat > user-data <<EOF
#cloud-config
hostname: e2b-vm
manage_etc_hosts: true
users:
  - name: e2badmin
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - $(cat vm_key.pub)
ssh_pwauth: false
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
EOF
cloud-localds seed.iso user-data
```

创建独立的 VM 服务，支持开机自启：

```bash
cat <<EOF | sudo tee /etc/systemd/system/e2b-repro-vm.service >/dev/null
[Unit]
Description=Local E2B VM
Wants=network-online.target
After=network-online.target
RequiresMountsFor=$VM_DIR

[Service]
User=$(id -un)
Group=$(id -gn)
SupplementaryGroups=kvm
ExecStart=/usr/bin/qemu-system-x86_64 \
  -name e2b-vm -machine q35,accel=kvm,mem-merge=off \
  -cpu host,migratable=off,$CPU_FEATURE -smp $VM_CPUS -m $VM_RAM \
  -drive "file=$VM_DIR/root.qcow2,format=qcow2,if=virtio,cache=none,aio=threads,discard=unmap" \
  -drive "file=$VM_DIR/seed.iso,format=raw,if=virtio,readonly=on" \
  -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:12222-:22,hostfwd=tcp:$HOST_IP:8088-:8088" \
  -display none -serial "file:$VM_DIR/console.log" \
  -qmp "unix:$VM_DIR/qmp.sock,server=on,wait=off"
ExecStop=/usr/bin/python3 "$SETUP/code/shutdown_vm.py" "$VM_DIR/qmp.sock"
Restart=on-failure
RestartSec=10
TimeoutStopSec=130
KillMode=control-group
LimitNOFILE=1048576
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now e2b-repro-vm.service
```

等一两分钟，再登录并复制材料：

```bash
ssh -i "$VM_DIR/vm_key" -p 12222 e2badmin@127.0.0.1 'sudo cloud-init status --wait'
ssh -i "$VM_DIR/vm_key" -p 12222 e2badmin@127.0.0.1 \
  'sudo mkdir -p /opt/e2b-local && sudo chown e2badmin:e2badmin /opt/e2b-local'

sed -i "s/10\.145\.64\.67/$HOST_IP/g" "$SETUP/configs/kubernetes/ready-private.yaml"
scp -i "$VM_DIR/vm_key" -P 12222 -r \
  "$SETUP/code" "$SETUP/configs" "$SETUP/patches" \
  e2badmin@127.0.0.1:/opt/e2b-local/

ssh -i "$VM_DIR/vm_key" -p 12222 e2badmin@127.0.0.1
```

**现在进入了 VM。第 3–5 步都在 VM 里执行。**

## 3. VM 内：编译 E2B

```bash
set -euo pipefail
sudo apt-get update
sudo apt-get install -y git curl ca-certificates build-essential python3-yaml \
  python3-venv iptables rsync e2fsprogs iproute2 kmod
case "$(awk '/vendor_id/{print $3; exit}' /proc/cpuinfo)" in
  AuthenticAMD) sudo modprobe kvm_amd ;;
  GenuineIntel) sudo modprobe kvm_intel ;;
esac
test -c /dev/kvm

cd /opt/e2b-local
export E2B_LOCAL_ROOT=/opt/e2b-local
export E2B_RUNTIME_SOURCE_DIR="$E2B_LOCAL_ROOT/build/runtime"
export E2B_RUNTIME_OUTPUT_DIR="$E2B_LOCAL_ROOT/custom-runtime"
export E2B_RUNTIME_VERSION=local-4k-512-start1024-20260921

mkdir -p build toolchain/go1.26.8
curl -fL https://go.dev/dl/go1.26.8.linux-amd64.tar.gz -o /tmp/e2b-go.tar.gz
tar -xzf /tmp/e2b-go.tar.gz -C toolchain/go1.26.8

git clone https://github.com/e2b-dev/runtime.git "$E2B_RUNTIME_SOURCE_DIR"
git -C "$E2B_RUNTIME_SOURCE_DIR" checkout --detach e7b22501988c826563bf1b3cf0d22a1183e7affe
git -C "$E2B_RUNTIME_SOURCE_DIR" apply "$E2B_LOCAL_ROOT/patches/runtime-4k-512.patch"
git -C "$E2B_RUNTIME_SOURCE_DIR" apply "$E2B_LOCAL_ROOT/patches/runtime-starting-concurrency.patch"

bash code/build_runtime_4k.sh api
bash code/build_runtime_4k.sh orchestrator
bash code/build_runtime_4k.sh migrator

cp -a "$E2B_RUNTIME_SOURCE_DIR/packages/db/migrations" custom-runtime/
cp "$E2B_RUNTIME_SOURCE_DIR/embed/compose/scripts/host-setup.sh" custom-runtime/
mkdir -p vendor/e2b/embed
cp -a "$E2B_RUNTIME_SOURCE_DIR/embed/kubernetes" vendor/e2b/embed/
curl -fsSL https://get.k3s.io -o vendor/install-k3s.sh
```

固定源码和两个补丁提供普通 4 KiB 页、存活配额和创建并发配置。
版本名保留构建时的名字；实际创建准入以配置中的 **512** 为准。

## 4. VM 内：安装服务

更新新编译文件的指纹，然后安装：

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib, re
p = Path('configs/kubernetes/runtime-local.yaml')
text = p.read_text()
for name in ['api', 'orchestrator']:
    sha = hashlib.sha256(Path('custom-runtime', name).read_bytes()).hexdigest()
    text = re.sub(r'(local\.e2b/' + name + r'-sha256: )\S+', r'\g<1>' + sha, text)
p.write_text(text)
PY
sudo env E2B_EXPECTED_VCPUS="$(nproc)" bash code/install_guest.sh
sudo k3s kubectl -n e2b get pods
```

脚本会安装 K3s v1.36.4+k3s1，启动数据库、E2B 和网关，自动生成团队、API key 和 `base` 模板。
等 `e2b-0` 为 **8/8 Running**、网关为 **1/1 Running**，再继续。

## 5. VM 内：设置团队配额

```bash
sudo k3s kubectl -n e2b exec e2b-0 -c postgres -- \
  psql -U postgres -d postgres -c 'SELECT id, name, slug FROM public.teams;'
```

确认 `configs/team-quota-512.sql` 中的 UUID 是上面查到的目标团队。不一致就先替换，再执行：

```bash
sudo k3s kubectl -n e2b exec -i e2b-0 -c postgres -- \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres < configs/team-quota-512.sql
```

配额缓存最多需要 5 分钟更新。节点存活上限、团队存活上限、创建准入都设为 **512**。
这是数量上限，不能保证示例 VM 同时承受 512 个重负载任务。

## 6. 宿主机：取 key 并测试

先 `exit` 回宿主机：

```bash
source "$HOME/e2b-setup/host-settings.sh"
umask 077
ssh -i "$VM_DIR/vm_key" -p 12222 e2badmin@127.0.0.1 \
  'sudo cat /var/lib/e2b/data/seed-state/sdk.env' > "$VM_DIR/client.env"
source "$VM_DIR/client.env"

python3 -m venv "$VM_DIR/client"
"$VM_DIR/client/bin/pip" install e2b==2.49.1
"$VM_DIR/client/bin/python" "$SETUP/code/client.py"
```

输出 `sandbox-ok` 和 `Linux` 就跑通了，测试会清理自己的 sandbox。
其他客户端使用同一个 `HOST_IP:8088` 和新生成的 API key；不要使用 `ADMIN_TOKEN`。
需要让客户端能访问宿主机的 TCP 8088 端口。

## 以后怎么进 VM、看状态

在新宿主机执行：

```bash
source "$HOME/e2b-setup/host-settings.sh"
ssh -i "$VM_DIR/vm_key" -p 12222 e2badmin@127.0.0.1
```

从其他机器操作时，先 SSH 到新宿主机，再执行上面的命令。12222 只监听宿主机本地。

| 要看什么 | 命令 |
|---|---|
| VM 状态（宿主机） | `sudo systemctl status e2b-repro-vm` |
| VM 日志（宿主机） | `sudo journalctl -u e2b-repro-vm -n 50` |
| E2B 状态（VM 内） | `sudo k3s kubectl -n e2b get pods` |
| E2B 日志（VM 内） | `sudo k3s kubectl -n e2b logs e2b-0 -c orchestrator --tail=50` |

修改 VM CPU/内存时，修改 `/etc/systemd/system/e2b-repro-vm.service`，在没有任务时执行 `daemon-reload` 和重启。
本指南只建立 `base` 模板；SWE 任务模板需要另行构建。Uni-Agent 的客户端限流也要在训练机器上单独设置。

本次核对了脚本和命令，没有在新机器执行整套重装。
