# 文档生成与校验工具

这些工具只整理已完成实验的记录、生成图和HTML，不启动模型评测或训练。

| 工具 | 用途 |
|---|---|
| `collect_evidence.py` | 从原lab按白名单复制/提取证据，脱敏远端地址；不读取凭据文件 |
| `generate_case_matrix.py` | 从84行最终结果生成可点击矩阵 |
| `generate_figures.py` | 用Matplotlib从原始统计生成SVG/PNG |
| `render_diagrams.py` | 用本地Mermaid资源及Playwright渲染图 |
| `build_site.py` | 将分层Markdown转换为多页离线HTML |
| `extract_api_inventory.py` | 从固定Uni-Agent源码和E2B SDK静态提取公开声明、路由及hash，不连接服务 |
| `render_api_appendices.py` | 根据机器清单更新两份API文档的源码附录，保留人工整理的功能正文 |
| `validate_documentation.py` | 检查分数、环境分母、54个性能batch、复制hash、链接和凭据模式 |
| `audit_site.py` | 用浏览器检查页面、图片与目录过滤，并导出汇报摘要PDF |

图表使用Matplotlib3.10.8；Markdown转换使用3.8.2；流程图使用Mermaid11.12.0；浏览器使用Playwright1.55.0。Mermaid依赖从公开npm包取得并核对完整性字段，渲染时通过容器内回环HTTP访问本地资源，不访问模型或远端sandbox。

原环境中可使用已有CPU报告环境或独立临时Docker容器，绑定本目录后执行工具。示例：

```bash
# 在绑定了 /docs 的报告环境中
python /docs/tools/generate_case_matrix.py
python /docs/tools/generate_figures.py
python /docs/tools/build_site.py
python /docs/tools/validate_documentation.py
```

`collect_evidence.py`默认原lab为`/home/yuhanya/uni-agent-lab`，也可用`--lab-root`显式指定。不要把别的一次实验覆盖到当前文档后仍沿用原来的日期与结论。

API文档的重建入口如下（在绑定`/lab:ro`、`/docs`的报告容器中执行），仅解析文件，不加载模型或创建sandbox：

```bash
python /docs/tools/extract_api_inventory.py --source /lab/src/uni-agent --sdk /lab/envs/e2b/lib/python3.12/site-packages/e2b
python /docs/tools/render_api_appendices.py
python /docs/tools/build_site.py
python /docs/tools/validate_documentation.py
```

校验器还检查两份文档覆盖全部提取声明/方法、两条Gateway路由、46条E2B REST与17条envd RPC。覆盖源码不等于远端服务兼容性测试。

网站入口是 `../site/index.html`；Markdown是主要内容来源，SVG/PNG及Mermaid源码均随文保留。生成结果与浏览器校验记录放在`../evidence/`。
