"""Render source-declaration indexes; hand-written API explanations remain above markers."""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
inventory = json.loads((ROOT / 'evidence/api-inventory.json').read_text())
source_root = f"https://github.com/verl-project/uni-agent/blob/{inventory['uni_agent_commit']}/"


def cell(text):
    return str(text).replace('|', r'\|').replace('\n', ' ')


def summary(item, parent=None):
    paragraph = item.get('doc', '').split('\n\n')[0]
    if paragraph:
        paragraph = re.sub(r':(?:class|meth|func|attr|mod):`([^`]+)`', r'`\1`', paragraph)
        return ' '.join(paragraph.split())
    name = item['name']
    meanings = {
        '__init__': '构造对象；只列源码显式声明的构造器。',
        '__enter__': '进入同步上下文。',
        '__exit__': '退出同步上下文并清理。',
        '__aenter__': '进入异步上下文；生命周期由该类实现。',
        '__aexit__': '退出异步上下文并清理。',
        '__call__': '实现可调用协议；完整参数见签名。',
        'from_config': '按配置构造该类。',
        'run': '执行该对象的运行逻辑；具体返回类型见签名。',
        'start': '启动该对象负责的资源/执行环境。',
        'stop': '停止并释放该对象负责的资源。',
        'is_alive': '查询执行环境是否可用。',
        'get_port_url': '取得已配置的端口访问地址。',
        'get_tunnel_url': '取得隧道访问地址。',
        'try_map': '尝试匹配并映射镜像名称。',
        'read_file': '读取执行环境内文件。',
        'write_file': '写入执行环境内文件。',
        'upload_file': '上传单个文件。',
        'download_file': '下载单个文件。',
        'expose_port': '端口访问扩展点；Modal实现是抛异常的占位，详见正文。',
        'main': '模块命令行入口。',
        'is_valid_ipv6_address': '检查是否为合法IPv6地址。',
        'get_event_loop': '获取或创建事件循环。',
        'remove_boxed': '剥离答案的boxed外层标记。',
        'last_boxed_only_string': '提取最后一个boxed答案片段。',
        'parse_json_view_range': '将JSON文本形式的查看行范围转成参数值。',
    }
    if name in meanings:
        return meanings[name]
    if name.endswith('_error_body'):
        return '构造对应协议的错误响应体。'
    if name.endswith('_build_response'):
        return '将生成结果封装成对应协议的JSON响应。'
    if name.startswith('build_swe_'):
        return '构建该模块对应的SWE评测数据集。'
    if name.endswith('Config'):
        return '配置数据模型；字段与继承关系见下方及机器清单。'
    if name.endswith('Arguments'):
        return '模型工具调用的参数schema。'
    if name.endswith('Task'):
        return '该任务家族的运行与评测实现。'
    if name.endswith('Tool'):
        return '该工具的执行实现；模型可见参数见正文。'
    if parent:
        return '该类声明的方法；参数和返回类型如下，具体分支见源码。'
    return '实现层的数据/辅助声明；字段、类型与用途结合所属模块查阅。'


def render_symbols(symbols):
    modules = defaultdict(list)
    for symbol in symbols:
        modules[symbol['module']].append(symbol)
    out = []
    for module, records in modules.items():
        out += [f'#### `{module}`', '']
        for record in records:
            link = source_root + record['source'] + '#L' + str(record['line'])
            out += [f"**`{record['name']}`** — {summary(record)} [源码]({link})", '']
            if record['kind'] == 'function':
                out += ['```python', record['signature'], '```', '']
                continue
            if record.get('bases'):
                out += ['继承：' + '、'.join('`' + base + '`' for base in record['bases']) + '。', '']
            if record.get('fields'):
                out += ['直接声明字段：' + '、'.join('`' + f['name'] + '`' for f in record['fields']) + '。完整类型/默认值见机器清单；继承字段见基类。', '']
            methods = record.get('methods', [])
            if methods:
                out += ['| 方法签名 / 属性getter | 功能（优先保留源码docstring） |', '|---|---|']
                for method in methods:
                    signature = method['signature']
                    if 'property' in method.get('decorators', []):
                        signature = '@property ' + signature
                    elif 'classmethod' in method.get('decorators', []):
                        signature = '@classmethod ' + signature
                    out += [f"| `{cell(signature)}` | {cell(summary(method, record))} |"]
                out += ['']
    return '\n'.join(out)


def replace_tail(relative, marker, appendix):
    path = ROOT / relative
    text = path.read_text()
    assert marker in text
    path.write_text(text.split(marker)[0] + marker + '\n\n' + appendix.rstrip() + '\n')


all_symbols = inventory['symbols']
groups = [
    ('gateway', 'Gateway、adapter、session与轨迹'),
    ('agents', 'Agent、模型客户端与注册'),
    ('tasks', 'Task、预处理与reward'),
    ('tools', 'Tool、编辑器与持久shell'),
    ('framework', '训练框架适配与后处理'),
    ('agent_aware_router', '路由、collector、store和策略'),
    ('logging', '日志上下文'),
    ('rl_insight', '可选观测接口'),
    ('utils', '通用工具'),
]
parts = [
    '## 12. 完整公开名称索引（源码附录）', '',
    f"静态索引共记录 **{len(all_symbols)}个类/函数声明、{sum(len(s.get('methods', [])) for s in all_symbols)}个直接声明的方法（含构造器和选定生命周期方法）**。这些数字包括Sandbox；其详细索引在独立Sandbox文档。属性getter计入声明数，不是额外网络接口。", '',
    '正文解释常用接入面；附录保留精确方法签名及源码首段docstring，包含实现层对象。名称公开不等于已承诺稳定SDK。签名中的self/cls不由调用方显式传入；异步方法需await；property按属性读取。', '',
]
for number, (prefix, label) in enumerate(groups, 1):
    subset = [s for s in all_symbols if s['module'].split('.')[1] == prefix]
    parts += [f'### 12.{number} {label}', '', render_symbols(subset)]
parts += ['### 12.10 包导出、别名与常量', '',
          '`__all__`记录公开导入入口。这里也覆盖非函数声明的导出，例如GatewayActor的Ray别名、ToolStatus类型别名、emitter单例、指标规格常量与版本号。它们不增加HTTP路由数。', '',
          '| 模块 | 显式导出名 |', '|---|---|']
for module, exports in inventory['exports'].items():
    parts += [f"| `{module.removesuffix('.__init__')}` | {', '.join('`'+name+'`' for name in exports)} |"]
parts += ['', '## 13. 版本、来源与使用边界', '',
          f"- [固定版本源码](https://github.com/verl-project/uni-agent/tree/{inventory['uni_agent_commit']}/uni_agent)。每个附录声明带源码行号。", 
          '- [api-inventory.json](../evidence/api-inventory.json)保存签名、字段、导出、路由、源文件SHA256及E2B契约索引。',
          '- [extract_api_inventory.py](../tools/extract_api_inventory.py)只解析源码AST与生成客户端；[render_api_appendices.py](../tools/render_api_appendices.py)生成附录，不会导入/启动训练、模型或sandbox。',
          '- `examples/`脚本参数、私有函数、第三方继承API与未来版本新增API不在“Uni-Agent自有公开声明”计数中。配置属性详见正文或JSON字段记录。',
          '- 本次新增的是源码核对文档；实际使用范围仍以[实验索引](../experiments/README.md)为准，没有据此新增训练或远端服务兼容结论。', '']
replace_tail('references/03-uni-agent-api-reference.md', '<!-- API_INVENTORY_START -->', '\n'.join(parts))
sandbox_symbols = [s for s in all_symbols if s['module'].split('.')[1] == 'sandbox']
replace_tail('references/04-sandbox-and-verdal-api-reference.md', '<!-- SANDBOX_INVENTORY_START -->',
             '## 11. Uni-Agent Sandbox源码接口索引\n\n' + render_symbols(sandbox_symbols))
print('Rendered', len(all_symbols), 'declarations across two API documents')
