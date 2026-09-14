import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path('/docs');pages=json.loads((ROOT/'site/pages.json').read_text());errors=[];results=[];api_results=[]
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage']);page=browser.new_page(viewport={'width':1500,'height':1050},device_scale_factor=1);page.on('pageerror',lambda e:errors.append(str(e)))
 for record in pages:
  page.goto((ROOT/record['html']).as_uri(),wait_until='load');page.evaluate('document.fonts.ready')
  broken=page.locator('img').evaluate_all('(images)=>images.filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.getAttribute("src"))')
  assert not broken,(record['html'],broken)
  assert page.locator('main h1').count()==1,record['html']
  if record['source'].endswith(('03-uni-agent-api-reference.md','04-sandbox-and-verdal-api-reference.md')):
   layout=page.evaluate('''() => ({width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,tables:document.querySelectorAll('main table').length,malformedTables:Array.from(document.querySelectorAll('main table')).flatMap((t,i)=>{const n=t.querySelector('tr').children.length;return Array.from(t.querySelectorAll('tr')).filter(r=>r.children.length!==n).map(()=>i)})})''')
   assert not layout['malformedTables'],(record['html'],layout)
   assert layout['scrollWidth']<=layout['width']+1,(record['html'],layout)
   page.screenshot(path=str(ROOT/'assets'/(Path(record['source']).stem+'-page.png')))
   page.set_viewport_size({'width':390,'height':844})
   mobile=page.evaluate('({width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth})')
   assert mobile['scrollWidth']<=mobile['width']+1,(record['html'],mobile)
   page.set_viewport_size({'width':1500,'height':1050})
   api_results.append({'page':record['html'],'desktop':layout,'mobile':mobile})
  results.append({'page':record['html'],'images':page.locator('img').count(),'broken_images':broken})
 page.goto((ROOT/'site/index.html').as_uri(),wait_until='load');n=page.locator('#nav-links a:visible').count();page.fill('#nav-search','E2B');filtered=page.locator('#nav-links a:visible').count();assert 0<filtered<n;page.fill('#nav-search','');assert page.locator('#nav-links a:visible').count()==n
 page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(ROOT/'assets/site-overview.png'),full_page=False)
 page.goto((ROOT/'site/architecture/01-deployment.html').as_uri(),wait_until='load');page.screenshot(path=str(ROOT/'assets/architecture-page.png'),full_page=False)
 page.goto((ROOT/'site/00-executive-summary.html').as_uri(),wait_until='load');page.pdf(path=str(ROOT/'00-executive-summary.pdf'),print_background=True,prefer_css_page_size=True)
 browser.close()
assert not errors
report={'pages_checked':len(results),'page_errors':errors,'navigation_search':{'all':n,'E2B_filtered':filtered,'restored':n},'pages':results}
(ROOT/'evidence/site-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='pages'},ensure_ascii=False))
(ROOT/'evidence/api-page-audit.json').write_text(json.dumps(api_results,ensure_ascii=False,indent=2)+'\n');print(json.dumps(api_results,ensure_ascii=False))
