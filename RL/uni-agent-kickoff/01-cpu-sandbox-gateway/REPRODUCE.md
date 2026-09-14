# CPU、Sandbox 与 Gateway：运行顺序

[本实验总览](README.md) · [全部实验](../README.md)

详细原始命令已保留在 [REPORT.md 的复现命令](REPORT.md#复现命令)。本页给出它们的依赖顺序。

1. 完成 [公共准备](../00-overview/SETUP.md)，固定 Uni-Agent 与 CPU verl 版本，建立无 GPU 映射的 CPU driver 容器。
2. 运行 CPU level0 测试，显式排除已过期的 deployment 测试文件；记录 passed、deselected 及原始日志。
3. 依次运行 Local Sandbox 与 Docker Sandbox demo，验证退出码、工作目录、文件编辑及上传下载。
4. 使用 fake backend 验证真实 Claude Code → Gateway 的请求和轨迹。
5. 启动 Qwen/vLLM 后端并核对 tokenizer / context，运行实际模型请求；同时设置 `prompt_length=12000` 和 `response_length=64`。
6. finalize session，检查 token IDs、mask、logprobs 的长度与返回轨迹，再进入更复杂的代码任务。

[code/gateway_session.py](code/gateway_session.py) 是在已有 GatewayManager 中使用的 helper，调用方式见 [code/README.md](code/README.md)。它不启动模型服务，也不执行 RL 更新。

Docker 文件写入兼容修改在 [patches/uni-agent-docker-stdin.patch](patches/uni-agent-docker-stdin.patch)。将补丁用于与报告一致的源码副本，完整环境和启动顺序见 [复现审计](details/reproduction-audit.md)。
