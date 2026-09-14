# Verdal / E2B 接口与 Uni-Agent Harbor 集成的只读审阅

记录日期：2026-09-13 UTC。本页检查现有源码和公开SDK源码，提供最小实测路线；本页作者没有读取API key、调用Verdal端点、创建或删除远端sandbox，也没有修改RL源码/环境。实际联网结果由主执行流程另行记录。

**最小真实检查应先走E2B SDK：创建一个已有template的短时sandbox，执行一条命令，做文件写入/回读，再结束这个sandbox。** Uni-Agent的现成E2B入口是Harbor Task；Harbor还涉及template查询/构建、较长lifetime及verifier协议，适合放在SDK基础能力通过之后。

## 本机已存在的接口

| 层 | 源码核对结果 |
| --- | --- |
| Uni-Agent | 固定commit `472c875a97f9a2764c81a6ec7581167632bd8bcc`；内建sandbox registry只有local/docker/modal/vefaas/openyuanrong，没有e2b或verdal |
| Uni-Agent Harbor | `name: harbor`、`harbor_env: e2b`；内部调用`harbor trial start`，不是把E2B当作Uni-Agent原生Sandbox provider |
| Harbor | 本机`envs/tbench`安装0.22.0；历史Terminal-Bench实验只用它下载任务，没有验过Harbor的E2B执行 |
| Harbor E2B extra | 0.22.0元数据要求`e2b>=2.25.0`、`dockerfile-parse>=2.0.1`；审阅开始时该env未安装e2b |
| E2B SDK来源 | 公开PyPI的`e2b==2.25.0` wheel，在内存中读取源码，没有安装到现有环境 |

源码参考：

- [Uni-Agent Harbor说明](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/docs/source/quickstart/harbor-integration.md)
- [Uni-Agent Harbor Task与CLI构造](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/tasks/harbor/task.py)
- [Uni-Agent Sandbox注册表](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/sandbox/registry.py)
- 本地Harbor：`envs/tbench/lib/python3.12/site-packages/harbor/environments/e2b.py`
- [E2B 2.25.0公开元数据](https://pypi.org/pypi/e2b/2.25.0/json)，wheel为`e2b-2.25.0-py3-none-any.whl`，308,999 bytes；发布元数据SHA256为`1021eebb74ab06166f6f8a551d9667af141711bdedfbb682197d670409e38fd5`。

## 两个自定义URL的准确语义

E2B 2.25.0的`e2b/connection_config.py`明确支持以下环境变量，不需要修改Harbor provider：

| 变量 | 实际用途 |
| --- | --- |
| `E2B_API_KEY` | 控制面API认证；Harbor preflight要求其存在 |
| `E2B_API_URL` | 控制面API的完整base URL，用于create、kill、template等API；不应默认追加OpenAI式`/v1` |
| `E2B_SANDBOX_URL` | envd数据面的完整base URL，SDK原样使用；不是自动替换sandbox ID的URL模板 |
| `E2B_DOMAIN` | 未覆盖URL时的默认域名及port-host计算；默认`e2b.app` |
| `E2B_DEBUG` | 开发用行为开关，不是自定义endpoint必需项；不要为接Verdal而误开 |

`ConnectionConfig.get_sandbox_url(sandbox_id, sandbox_domain)`在设置`E2B_SANDBOX_URL`时直接返回该字符串。若未设置，通常生成`https://49983-<sandbox_id>.<sandbox_domain>`。因此自定义网关URL必须包含正确scheme/host/必要path；SDK不会替你把`{sandbox_id}`或端口占位符展开。

SDK创建sandbox后会给envd请求增加`E2b-Sandbox-Id`、`E2b-Sandbox-Port: 49983`，并使用创建响应返回的envd `X-Access-Token`。一个统一的Verdal sandbox网关可以据这些字段路由；这仍需真实服务验证，单纯设置变量不证明该网关实现了相同协议。

`sandbox.get_host(other_port)`是另一条路径，仍按domain/port/sandbox ID计算地址；它不会自动变成`E2B_SANDBOX_URL`下的自定义转发URL。因此最小烟测先检查commands/files，不把其他端口HTTP转发成功作为已证明能力。

## 最小E2B SDK实测建议

在单独的CPU Docker环境使用明确版本的`e2b`；不需要GPU或LLM，也不需要`e2b-code-interpreter`。已有凭据与URL由主执行流程注入，不放进脚本、YAML或stdout。

下面是待执行的SDK接口示意，本审阅没有运行：

```python
from e2b import Sandbox

# 换成服务已存在且允许使用的template；省略时SDK默认为"base"。
known_template = "base"
sandbox = None
try:
    sandbox = Sandbox.create(
        template=known_template,
        timeout=60,
        request_timeout=20,
        metadata={"purpose": "uni-agent-verdal-smoke"},
    )
    result = sandbox.commands.run(
        "printf 'verdal-smoke\\n'; uname -s; pwd",
        timeout=10,
    )
    assert result.exit_code == 0
    assert "verdal-smoke" in result.stdout

    path = "/tmp/uni-agent-verdal-smoke.txt"
    payload = "sandbox file round trip\n"
    sandbox.files.write(path, payload)
    assert sandbox.files.read(path) == payload
    print({"command_exit": result.exit_code, "file_round_trip": True})
finally:
    if sandbox is not None:
        sandbox.kill()
```

`timeout=60`是sandbox lifetime，`request_timeout=20`是控制面请求等待预算，`commands.run(..., timeout=10)`是命令执行预算，三者不同。SDK默认template为`base`不代表Verdal一定有这个名字；若该服务要求特定template ID，应使用其已有模板，避免在简单烟测阶段引入镜像构建。

两次`commands.run`通常是不同进程。文件可持久存在于同一个sandbox，前一次shell里的`cd`或临时shell变量不应当作下一次调用会自动继承的状态；明确传`cwd`或在同一命令里完成相关动作。

## Harbor为何不是最短入口

Harbor 0.22.0的`E2BEnvironment.start()`依次调用：

1. `AsyncTemplate.alias_exists(<environment_name>__<content_hash>)`。
2. 若alias不存在或force_build，使用`Template.from_image`或`Template.from_dockerfile`，再`AsyncTemplate.build`。
3. `AsyncSandbox.create(template=<该alias>, timeout=86400, metadata=..., envs=..., allow_internet_access=..., network=...)`。
4. 创建目录、上传任务相关文件，后续执行agent/verifier并下载日志。

这要求Verdal兼容模板查询/构建，而不仅是已有template的sandbox create；设置task的`docker_image`仍会进入E2B template构建，并不绕过该层。当前provider没有显式的“使用任意现成template ID”构造参数。

另一个具体边界是`timeout=86400`写在provider代码中。Uni-Agent的`agent.timeout_sec`、`timeout_multiplier`和Harbor task中的`build_timeout_sec`不会把这个sandbox lifetime改成60秒。如果Verdal限制短lifetime，可能出现SDK烟测通过而Harbor创建失败；应按实际响应定位，不把它误报成E2B_URL未生效。

provider的`stop(delete=False)`仍会删除临时sandbox；它不提供通过该flag保留sandbox的行为。正常Harbor完成会走清理，但模板alias本身可能作为缓存保留。SDK烟测避免了模板创建这一额外资源。

Harbor的commands路径实际使用background dispatch + wait，还需要批量文件写入、文件列表/info、download以及可选network update。SDK一次前台命令与文本读写成功，只证明其最基本子集，不能据此声称整个Harbor后端通过。

## 最小Harbor / Uni-Agent集成路线

若SDK和Verdal模板能力均通过，可准备一个独立toy task：`task.toml`、`instruction.md`、`environment/Dockerfile`、`solution/solve.sh`、`tests/test.sh`。Oracle只执行reference solution，不调用任何模型。

建议solution只写一份固定输出，verifier检查该文件，写`/logs/verifier/reward.txt`为1或0；不要跑SWE/Terminal-Bench大任务来初测远端sandbox。一个可用的task配置骨架是：

```toml
schema_version = "1.4"

[agent]
timeout_sec = 30.0

[verifier]
timeout_sec = 30.0

[environment]
build_timeout_sec = 180.0
```

先用Harbor原CLI排除Uni-Agent编排层：

```bash
harbor trial start \
  --path /lab/data/verdal-toy-task \
  --agent oracle --env e2b \
  --trial-name verdal-toy-first \
  --trials-dir /lab/results/verdal-harbor-smoke
```

这个入口会创建远端环境，只有主执行流程在能力/资源条件明确后才执行。保留唯一trial名称和原始`result.json`；同时看verifier reward与exception，CLI返回码或“创建成功”不能替代完整任务通过。

Uni-Agent配置随后使用：

```yaml
- name: harbor
  agent:
    name: oracle
    timeout_sec: 30
  harbor_env: e2b
  timeout_multiplier: 1.0
```

不要加入`sandbox:`、`environment_kwargs:`、`api_url:`之类未经支持的Task字段：其Pydantic配置是`extra="forbid"`。当前Uni-Agent只把`agent.kwargs`转成Harbor的`--agent-kwarg`，没有转发`--environment-kwarg`的字段。Harbor provider也没有把任意环境kwarg自动传给`AsyncSandbox.create`；自定义URL应通过进程环境传入。

`HarborTask`的CLI子进程继承`os.environ`。Oracle的`build_harbor_process_env()`返回None，仍会继承E2B的三个环境变量；非Oracle只在此基础上附加模型endpoint别名，不会删除E2B设置。必须让实际Ray worker/Harbor进程能看到这些变量；只在连接既有Ray集群的driver shell里临时设置，并不自动证明每个worker收到。

最小Uni-Agent层也可以直接构造一次`HarborTaskConfig`并`await HarborTask(config).run()`，metadata包含非空`instance_id`和真实绝对`task_path`；不必启动Ray或模型。使用`uni_agent.logging.LogContext`及`sample_logging.from_context()`保留本次日志，否则任务finally会清理临时trial目录。走官方`parallel_infer_api.py`时使用`NUM_WORKERS=1`、`GLOBAL_CONCURRENCY=1`、`--limit 1`，Oracle仍需传入口要求的`--base-url http://unused.invalid/v1`占位。

若以后换成模型agent，sandbox内必须能访问模型API/Gateway；远端sandbox的`127.0.0.1`不是本机GPU节点。上游Harbor文档明确将Harbor+Gateway rollout标为尚未端到端验证。本次SDK或Oracle试验不代表完成token轨迹回传、RL对齐或策略参数更新。
