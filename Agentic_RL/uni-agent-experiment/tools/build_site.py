"""Build a local, multi-page HTML reader from the Markdown documentation."""
import html,json,os,re
from pathlib import Path
from urllib.parse import unquote,urlsplit
import markdown
ROOT=Path(__file__).resolve().parents[1];SITE=ROOT/'site';SITE.mkdir(exist_ok=True)
groups=[('', '阅读入口'),('architecture','架构与流程'),('experiments','实验'),('results','结果与结论'),('reproduce','复现'),('references','参考与证据'),('tools','文档工具')]
docs=[]
for folder,label in groups:
 base=ROOT/folder
 paths=sorted(base.glob('*.md'),key=lambda p:(p.name!='README.md',p.name))
 if not folder:paths=sorted(paths,key=lambda p:(p.name!='README.md',p.name))
 docs.extend((p,label) for p in paths)
extra=ROOT/'reproduce/scripts/README.md'
if extra.exists():docs.append((extra,'复现'))
def output(p):
 rel=p.relative_to(ROOT);return SITE/rel.parent/('index.html' if rel.name=='README.md' else rel.with_suffix('.html').name)
def title(p):
 m=re.search(r'^# (.+)$',p.read_text(),re.M);return m.group(1) if m else p.stem
CSS='''*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:#f5f7fb;color:#18304a;font:16px/1.78 -apple-system,BlinkMacSystemFont,"Noto Sans CJK SC","Microsoft YaHei",sans-serif}a{color:#1c62ba}header{background:#122b48;color:white;padding:22px 30px}header a{color:white;text-decoration:none}header small{display:block;color:#bdd0e5;margin-top:4px}.layout{display:grid;grid-template-columns:280px minmax(0,1fr);max-width:1580px;margin:auto}nav{padding:24px 20px;border-right:1px solid #dbe4ee;background:#edf2f8;position:sticky;top:0;height:100vh;overflow:auto}nav h3{font-size:13px;color:#536d87;margin:22px 0 8px}nav a{display:block;padding:7px 9px;text-decoration:none;font-size:14px;line-height:1.45;border-radius:5px;color:#2d4866}nav a.active{background:#d9e8fc;color:#174d91;font-weight:650}nav input{width:100%;padding:9px;border:1px solid #afc1d5;border-radius:5px}main{padding:30px 44px 70px;max-width:1280px;min-width:0}h1,h2,h3{line-height:1.4;color:#142f4c}h1{font-size:30px;margin:10px 0 20px}h2{font-size:23px;border-top:1px solid #dce5ee;padding-top:24px;margin-top:34px}h3{font-size:18px;margin-top:26px}p{margin:13px 0}table{border-collapse:collapse;width:100%;background:#fff;margin:20px 0;font-size:14px}th,td{padding:10px 12px;border:1px solid #dce5ee;vertical-align:top;overflow-wrap:anywhere}th{background:#e7eff9;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eaf0f7;padding:18px;border-radius:6px;font-size:13px;line-height:1.6}code{font-family:ui-monospace,monospace;font-size:.9em}img{max-width:100%;height:auto;display:block;background:white;border:1px solid #e1e7ee;margin:20px auto}blockquote{margin:18px 0;border-left:4px solid #3779c6;padding:8px 18px;background:#eaf2fc;color:#385776}.scope{font-size:13px;padding:8px 12px;background:#e3f1ec;border-radius:5px;color:#28634f}.toc{font-size:14px;background:#fff;padding:12px 20px;border:1px solid #dce5ee}.toc ul{margin:4px 0}footer{margin-top:45px;padding-top:18px;border-top:1px solid #dce5ee;font-size:13px;color:#62788d}.prevnext{display:flex;justify-content:space-between;gap:20px;margin-top:30px;font-size:14px}.source{font-size:13px;color:#62788d}li{margin:6px 0}@media(max-width:900px){.layout{display:block}nav{position:static;height:auto;max-height:260px;border-bottom:1px solid #ccc}main{padding:22px 18px}h1{font-size:25px}table{display:block;overflow-x:auto}th,td{min-width:90px}}@media print{header,nav,.toc,.scope,.source,.prevnext,footer{display:none}.layout{display:block}body{background:white;font-size:10pt}main{max-width:none;padding:0}h1{font-size:21pt}h2{font-size:16pt;break-after:avoid}h3{font-size:12pt;break-after:avoid}table{font-size:8.5pt}th,td{padding:6px}img{max-height:235mm;object-fit:contain;break-inside:avoid}a{color:inherit;text-decoration:none}pre{font-size:8pt}@page{size:A4;margin:15mm}}'''
CSS += '\nmain{overflow-wrap:anywhere}\n'
(SITE/'style.css').write_text(CSS)
index=[]
for n,(p,group) in enumerate(docs):
 out=output(p);out.parent.mkdir(parents=True,exist_ok=True);md=markdown.Markdown(extensions=['tables','fenced_code','toc'],extension_configs={'toc':{'toc_depth':'2-3'}});body=md.convert(p.read_text())
 def rewrite(m):
  attr,value=m.group(1),html.unescape(m.group(2));u=urlsplit(value)
  if u.scheme or value.startswith(('#','//')):return m.group(0)
  target=(p.parent/unquote(u.path)).resolve()
  if target.suffix=='.md' and target.is_relative_to(ROOT) and any(target==q for q,_ in docs):target=output(target)
  rel=os.path.relpath(target,out.parent).replace(os.sep,'/')
  if u.fragment:rel+='#'+u.fragment
  return f'{attr}="{html.escape(rel,quote=True)}"'
 body=re.sub(r'(href|src)="([^"]*)"',rewrite,body)
 toc=md.toc if len(re.findall(r'<h[23]',body))>=3 else ''
 nav='<input id="nav-search" placeholder="筛选章节"><div id="nav-links">'
 for folder,label in groups:
  candidates=[q for q,g in docs if g==label]
  if not candidates:continue
  nav+='<h3>'+label+'</h3>'
  for q in candidates:nav+=f'<a class="{"active" if q==p else ""}" href="{os.path.relpath(output(q),out.parent)}">{html.escape(title(q))}</a>'
 nav+='</div>'
 prev='<span></span>' if n==0 else f'<a href="{os.path.relpath(output(docs[n-1][0]),out.parent)}">← 上一篇</a>'
 nxt='<span></span>' if n==len(docs)-1 else f'<a href="{os.path.relpath(output(docs[n+1][0]),out.parent)}">下一篇 →</a>'
 style=os.path.relpath(SITE/'style.css',out.parent);home=os.path.relpath(SITE/'index.html',out.parent);source=os.path.relpath(p,out.parent)
 page=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title(p))}</title><link rel="stylesheet" href="{style}"></head><body><header><a href="{home}"><strong>Uni-Agent × AMD ROCm · 实验记录</strong></a><small>实验 2026-09-12 · 整理 2026-09-14 · 架构、实验结果与可追溯证据</small></header><div class="layout"><nav>{nav}</nav><main><div class="scope">范围：固定30B模型的agent推理、sandbox、评测和轨迹验证；没有执行后训练。</div><p class="source"><a href="{source}">查看Markdown源文件</a></p>{body}<details><summary>本页目录</summary>{toc}</details><div class="prevnext">{prev}{nxt}</div><footer>原始实验资产保留在 /home/yuhanya/uni-agent-lab；本页面来自已完成的实验记录。</footer></main></div><script>document.getElementById('nav-search').addEventListener('input',e=>{{const s=e.target.value.toLowerCase();document.querySelectorAll('#nav-links a').forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(s));}});</script></body></html>'''
 out.write_text(page);index.append({'source':str(p.relative_to(ROOT)),'html':str(out.relative_to(ROOT)),'title':title(p),'group':group})
(SITE/'pages.json').write_text(json.dumps(index,ensure_ascii=False,indent=2));print('Built',len(index),'HTML pages')
