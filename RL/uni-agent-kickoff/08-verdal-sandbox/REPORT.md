# Verdal / E2B兼容sandbox：实际烟测记录

[本实验总览](README.md) · [全部实验](../README.md)

2026-09-13 UTC，在独立CPU Docker容器中，用兼容 E2B 的 API 与 sandbox 网关进行真实调用。**使用 `e2b==2.49.1` 和预装 Python / Node 的模板，11 项检查全部通过。** 没有运行模型或RL，也没有修改之前32B/8B实验的环境、源码或复现包。

## 运行条件

| 项目 | 实测条件 / 复现要求 |
| --- | --- |
| SDK | `e2b==2.49.1` |
| 服务 | 兼容 E2B 的控制面与 sandbox 网关；地址由环境变量指定 |
| 模板 | 预装 Python 的可用模板；原测试模板也包含 Node，2 CPU / 1024 MB |
| 本地运行环境 | CPU Docker，不需要 GPU；原测试容器上限为 4 CPU / 8 GiB |
| 实例 TTL | 原完整测试创建时 180 秒，另验证改成 90 秒；测试后主动删除 |

服务地址、模板 ID 和凭据由自己的服务配置提供。本仓库不保存这些部署值。这里没有修改或构建远端模板。

## 通过的11项检查

| 检查 | 实际证据 |
| --- | --- |
| 创建 | 主实例一次创建约0.49秒；只表示本次观测，不是延迟benchmark |
| Shell命令 | 正确返回stdout、exit0；环境为Linux/x86_64，命令用户UID0 |
| UTF-8文件 | 中文文本通过files.write/read往返，49 bytes完全一致 |
| 二进制文件 | 4096 bytes往返，完整SHA256一致 |
| 文件与进程共享状态 | 用files API写入的文件可被命令中的cat完整读取 |
| Python执行 | 实际执行1²+…+10²，返回385 |
| 非零退出码/stderr | 故意exit7，准确收到退出码7及指定stderr |
| 后台进程 | background=True后wait，正确得到完成状态与stdout |
| 重连 | Sandbox.connect同一ID后仍可读取先前文件 |
| 两实例文件隔离 | 两个不同ID的测试文件双向互不可见 |
| TTL更新/信息 | set_timeout(90)与get_info成功，运行状态正确 |

两个实例均在finally中kill，随后GET对应sandbox返回404。此前第一轮创建的两个实例也已删除；没有遗留本次远端资源。这里的隔离测试仅验证两个实例的测试文件互不可见，不是完整安全隔离审计。

成功原始记录：report.json（原始文件：`results/verdal-sandbox-20260913/uni-agent-verdal-57be2652bf9d/report.json`）。

## 第一轮发现的问题

最初用Harbor声明支持的最低版本`e2b==2.25.0`和`ubuntu`模板，基础命令、文件、后台执行、退出码、双实例隔离和清理正常，但有两项失败：

1. `ubuntu`模板没有`python3`，命令返回127。这是所选模板的软件内容问题；换成现成的预装 Python / Node 的模板后通过，没有在远端临时安装依赖。
2. 旧SDK重连后的文件读取返回`unauthorized`。源码检查发现2.25.0的create会添加`E2b-Sandbox-Id`与`E2b-Sandbox-Port`，connect却没有；这对固定sandbox网关URL很关键。2.49.1的connect已添加这两个路由头，新组合的重连实测通过。本次没有修改SDK源码或绕过鉴权。

第一轮失败原样保留：report.json（原始文件：`results/verdal-sandbox-20260913/uni-agent-verdal-43352db6d361/report.json`）。两轮同时改变了SDK与模板，因此不将整套结果当作单变量性能对照；具体SDK路由差异和模板中缺Python分别记录。

## 在其他环境运行基础示例

见 [示例说明](REPRODUCE.md) 与 [verdal_basic.py](code/verdal_basic.py)。配置 `E2B_API_URL`、`E2B_SANDBOX_URL` 和 `E2B_TEMPLATE_ID`，API key 使用环境变量或交互隐藏输入；示例支持在 CPU Docker 中运行。

这个短示例演示创建 sandbox、执行命令、UTF-8 文件回读和 `finally` 清理，使用 60 秒 TTL。上表 11 项是此前完整烟测的结果，短示例不声称重新覆盖后台进程、重连、双实例隔离及全部错误路径。

原完整烟测与短示例分开记录；没有为了整理仓库而再次调用远端服务。

## 与Uni-Agent的连接范围

Uni-Agent当前没有原生`e2b` sandbox provider；已有路径是`name: harbor`与`harbor_env: e2b`，由Harbor管理环境。E2B变量应传给实际Harbor/Ray worker进程，不能写成不支持的`sandbox.provider: e2b`或Harbor YAML里的`api_url`字段。

这次完成的是平台SDK与远端执行/文件生命周期烟测。尚未执行Harbor完整Oracle/benchmark或RL；Harbor还涉及模板alias查找/构建、固定24小时TTL及verifier，需要单独验收。源码接入说明见[verdal-integration-review.md](details/verdal-integration-review.md)。
