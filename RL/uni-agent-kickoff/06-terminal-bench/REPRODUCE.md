# Terminal-Bench：三项任务运行步骤

[本实验总览](README.md) · [全部实验](../README.md)

先完成[共用环境准备](../00-overview/SETUP.md)。以下命令在完整实验工作区执行，所引用的模型、数据和实验脚本需另行准备。

Terminal-Bench2.1使用Harbor0.22.0只下载任务，执行仍由Uni-Agent完成。固定任务集引用为：

```text
terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a
```

三项固定case是rstan-to-pystan、multi-source-data-merger、qemu-alpine-ssh。从空下载目录准备：

```bash
docker exec ua-lab-cpu bash /lab/scripts/tbench_setup.sh
docker exec ua-lab-cpu /lab/envs/tbench/bin/harbor download \
  terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a \
  --output-dir /lab/data/tbench-v1
```

已迁移固定`selection.json/selection-policy.json`时直接使用它们的三份repo digest；不重新按当前registry选择。`prepare_tbench.py pull-and-pack`依赖89题解压目录`data/tbench-v1/terminal-bench-2-1`、固定selection与policy，并拒绝覆盖已有`selected-three.parquet`。Harbor依赖装在独立tbench env，constraints禁止装torch；其完整包版本在`results/tbench-v1/harbor-environment.txt`，当前setup仍按Harbor版本解析依赖，不是完整离线wheel锁。

为轻量kit补了两份逐字相同的小metadata副本：`configs/tbench_reproduction_policy.json`约2KB、`configs/tbench_reproduction_selection.json`约60KB，不包含任务镜像或模型。下载89题后，在新lab安装规范路径并打包三题：

```bash
python3 - <<'PY'
from pathlib import Path
base = Path.cwd()
target = base / 'results/tbench-v1'
target.mkdir(parents=True, exist_ok=True)
for source, name in [('tbench_reproduction_policy.json', 'selection-policy.json'),
                     ('tbench_reproduction_selection.json', 'selection.json')]:
    data = (base / 'configs' / source).read_bytes()
    path = target / name
    if path.exists():
        assert path.read_bytes() == data, f'Preserve existing different selection: {path}'
    else:
        with path.open('xb') as handle:
            handle.write(data)
PY
docker exec -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl \
  -e DOCKER_HOST=unix:///lab/run/docker.sock \
  ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/prepare_tbench.py pull-and-pack
```

新lab的控制顺序如下；即使baseline/oracle不调用模型，上层wrapper也先检查18082模型服务，所以Coder服务需已ready：

```bash
python3 scripts/run_tbench_cases.py baseline
python3 scripts/run_tbench_cases.py oracle
python3 scripts/run_tbench_cases.py baseline --name qemu-baseline-snapshot \
  --task qemu-alpine-ssh --qemu-snapshot-env
python3 scripts/run_tbench_cases.py oracle --name qemu-oracle-snapshot \
  --task qemu-alpine-ssh --qemu-snapshot-env
python3 scripts/run_tbench_cases.py model --name replay-tbench-model --qemu-snapshot-env
```

QEMU的原Bullseye security仓库已过期，兼容处理仅在一次性任务容器把它指向`https://snapshot.debian.org/archive/debian-security/20260901T000000Z`；baseline、gold和model均需同一显式flag。没有改宿主APT、solution或tests。历史有效控制是三题baseline0/gold1，模型resolved1/3、finished2/3。`run_tbench_cases.py --name`只改变输出，不改变其对固定`baseline/`、`oracle/`路径的前置检查；在已有lab中用新控制名字不会自动成为新的前置control，需要直接下层runner或在独立新lab维持规范目录。
