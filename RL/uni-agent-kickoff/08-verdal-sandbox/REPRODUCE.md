# Verdal：基础示例运行步骤

[本实验总览](README.md) · [全部实验](../README.md)

## Verdal / E2B

依赖 Python 3.12、`e2b==2.49.1` 和兼容 E2B 的服务，不需要 GPU。先安装 SDK，再设置自己的服务地址和模板：

```bash
python -m pip install 'e2b==2.49.1'
export E2B_API_URL='https://sandbox.example.com/e2b'
export E2B_SANDBOX_URL='https://sandbox.example.com/e2b'
export E2B_TEMPLATE_ID='<your-template-id>'
python code/verdal_basic.py
```

上面的地址和模板只是占位符，需替换成服务方提供的值。三个环境变量均必填，脚本没有默认服务地址或模板，也不读取配置文件。`E2B_API_KEY` 由运行环境注入；交互终端未设置时，脚本会使用无回显输入。密钥不接受命令行参数，不写入文件，不输出到日志。

也可在这个目录用 CPU Docker 运行；先设置上述环境变量，密钥可在容器内提示时输入：

```bash
docker run --rm -it \
  --env E2B_API_URL --env E2B_SANDBOX_URL --env E2B_TEMPLATE_ID --env E2B_API_KEY \
  --mount "type=bind,src=$(pwd),dst=/experiment,readonly" --workdir /experiment \
  python:3.12-slim \
  sh -c "python -m pip install 'e2b==2.49.1' && python code/verdal_basic.py"
```

运行会创建一个有效期 60 秒的远端 sandbox，API 请求超时为 20 秒，命令执行等待为 10 秒。成功时输出命令结果和文件回读结果；无论任务是否成功，`finally` 都会尝试删除本次创建的 sandbox。清理失败时脚本非零退出并输出 sandbox ID，便于在服务端处理。这个示例覆盖基本 SDK 能力，不涉及 Harbor、模型调用或 RL。

