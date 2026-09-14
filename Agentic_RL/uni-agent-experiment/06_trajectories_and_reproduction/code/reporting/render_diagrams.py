"""Render local Mermaid assets in a CPU-only Playwright container; no external page requests."""
import argparse,http.server,json,threading
from functools import partial
from pathlib import Path
from playwright.sync_api import sync_playwright
p=argparse.ArgumentParser();p.add_argument('--docs',type=Path,default=Path('/docs'));p.add_argument('--vendor',type=Path,default=Path('/vendor/package/dist'));a=p.parse_args()
class Handler(http.server.SimpleHTTPRequestHandler):
 def translate_path(self,path):
  path=path.split('?',1)[0]
  if path.startswith('/vendor/'):return str(a.vendor/path.removeprefix('/vendor/'))
  return str(a.docs/path.lstrip('/'))
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
html='''<!doctype html><meta charset="utf-8"><style>body{margin:0;background:white}svg{max-width:none!important}</style><script type="module">import mermaid from '/vendor/mermaid.esm.min.mjs';mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',fontFamily:'Arial, sans-serif',htmlLabels:false,themeVariables:{fontSize:'17px',primaryColor:'#edf3fc',primaryBorderColor:'#7291b6',lineColor:'#5a708b',primaryTextColor:'#132d47',secondaryColor:'#f1f6fc',tertiaryColor:'#ffffff'},flowchart:{htmlLabels:false,curve:'linear',nodeSpacing:28,rankSpacing:40},sequence:{useMaxWidth:false,wrap:true,actorFontSize:16,messageFontSize:15,noteFontSize:15}});window.mermaid=mermaid;</script>'''
boot=a.docs/'_diagram_boot.html';boot.write_text(html);results=[]
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1500,'height':1000},device_scale_factor=1)
  page.goto(f'http://127.0.0.1:{server.server_port}/_diagram_boot.html');page.wait_for_function('window.mermaid !== undefined')
  for i,src in enumerate(sorted(a.docs.glob('*/figures/*.mmd'))):
   rendered=page.evaluate('async ({id,code}) => (await mermaid.render(id,code)).svg',{'id':'chart'+str(i),'code':src.read_text()})
   target=src.with_suffix('.svg');target.write_text(rendered)
   page.evaluate('(svg)=>{document.body.innerHTML=svg}',rendered);page.evaluate('document.fonts.ready')
   results.append({'source':str(src.relative_to(a.docs)),'svg':str(target.relative_to(a.docs)),'bytes':target.stat().st_size})
  browser.close()
finally:boot.unlink(missing_ok=True);server.shutdown()
(a.docs/'06_trajectories_and_reproduction/results/provenance/diagram-rendering.json').write_text(json.dumps({'mermaid':'11.12.0','diagrams':results},indent=2));print(json.dumps(results))
