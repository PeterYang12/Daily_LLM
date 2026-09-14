# Docker 大文件写入兼容修复

2026-09-11 的 Coder30B 扩展 SWE 运行在编辑 `/testbed/xarray/core/dataset.py` 时遇到 `OSError: [Errno 7] Argument list too long: 'docker'`。证据保存在 `results/large-swe/coder30b-expanded/runner.log`。这属于 sandbox 传输限制，不能解释为模型不会修复该题。该次运行后来中断，没有完成逐题结果汇总；它的日志单独保留。

固定上游版本是 Uni-Agent `472c875a97f9a2764c81a6ec7581167632bd8bcc`。原 `Sandbox.write_file` 把整个文件编码成 base64，拼入一个 `bash -c` 参数，再经 Docker CLI 执行。Linux 单个 argv 字符串有限长，因此即使模型只替换一行，`edit_file` 回写整个大文件也可能先触发宿主的 E2BIG。

实验目录中的 `DockerSandbox` 现在单独覆盖 `write_file`：文件 bytes 通过 `docker exec -i` 的 stdin 写入，字符串按 UTF-8 编码；shell 脚本固定，目标路径通过独立位置参数传入，不执行路径内容。父目录创建、现有文件截断写入、已有权限与符号链接行为沿用原语义。错误继续向调用方抛出。基类与其他 sandbox provider、agent prompt、reward 和题目内容都没有修改。

完整补丁：`patches/uni-agent-docker-stdin.patch`。原始源码归档仍保持未修改版本。将补丁用于新的复现目录时显式执行：

```bash
git -C /path/to/uni-agent-lab-replay/src/uni-agent apply \
  /path/to/uni-agent-lab/patches/uni-agent-docker-stdin.patch
```

`scripts/audit_docker_write_file.py` 是无 GPU 的实际 Docker 验证入口，使用独立 sandbox daemon 和固定 Python slim 镜像。它在同一个新建 sandbox 中先调用未修改的基类，验证 1 MiB 内容仍触发 E2BIG；然后验证修复后的空文件、1 MiB 和 16 MiB 任意二进制、UTF-8/NUL 与特殊字符路径的完整 SHA256 round trip，检查路径内容没有执行、权限/符号链接保持、不可写目的地抛错。只使用合成内容，不读取 benchmark gold patch。

```bash
docker exec -e DOCKER_HOST=unix:///lab/run/docker.sock \
  -e PYTHONPATH=/lab/src/uni-agent:/lab/src/verl ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/audit_docker_write_file.py \
  --output /lab/results/docker-write-file-regression.json
```

入口拒绝覆盖已有结果；复跑请换输出文件名。2026-09-12 01:06 UTC 的真实回归已全部通过：未修改基类的 1 MiB 对照复现 E2BIG；修复后的四组内容 SHA256 完全相同，特殊路径未执行、权限/符号链接行为保持、不可写目的地正常报错；临时容器已清理。完整结果为 `results/docker-write-file-regression.json` 与 `logs/docker-write-file-regression.log`。原有 Docker sandbox 与 exec error-policy 两个测试文件另外执行 **45 passed**，见 `results/docker-write-file-unit.xml`、`logs/docker-write-file-unit.log`。

容器恢复入口 `scripts/start_cpu_container.sh` 复用 `/lab/envs/cpu`，检查记录的镜像 digest/image ID 与挂载路径，不重新解析依赖、不映射 GPU。该入口已成功恢复 `ua-lab-cpu`，核心导入成功、GPU 不可见，且 81 个锁定覆盖层包版本全部匹配，见 `logs/cpu-restoration-20260912.log` 和 `logs/cpu-restoration-overlay-20260912.log`。新环境完整重建仍使用 `scripts/cpu-reproduce.sh`，两者用途不同。
