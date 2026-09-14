"""Static inventory of the pinned Uni-Agent source and installed E2B SDK; no services invoked."""
import argparse,ast,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('/home/yuhanya/uni-agent-lab/src/uni-agent'));p.add_argument('--sdk',type=Path,default=Path('/home/yuhanya/uni-agent-lab/envs/e2b/lib/python3.12/site-packages/e2b'));a=p.parse_args();ROOT=Path(__file__).resolve().parents[1]
def doc(n):return ast.get_docstring(n) or ''
def sig(n):
 if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
  return ('async ' if isinstance(n,ast.AsyncFunctionDef) else '')+n.name+'('+ast.unparse(n.args)+')'+(' -> '+ast.unparse(n.returns) if n.returns else '')
 return n.name
symbols=[];exports={};routes=[];hashes={}
for path in sorted((a.source/'uni_agent').rglob('*.py')):
 text=path.read_text();tree=ast.parse(text);rel=str(path.relative_to(a.source));hashes[rel]=hashlib.sha256(text.encode()).hexdigest();module=rel.removesuffix('.py').replace('/','.')
 for node in tree.body:
  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__all__' for t in node.targets):
   try:exports[module]=ast.literal_eval(node.value)
   except Exception:pass
  if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and (not node.name.startswith('_') or node.name=='_GatewayActor'):
   item={'module':module,'name':node.name,'kind':'class' if isinstance(node,ast.ClassDef) else 'function','signature':sig(node),'doc':doc(node),'line':node.lineno,'source':rel}
   if isinstance(node,ast.ClassDef):
    item['bases']=[ast.unparse(b) for b in node.bases];item['fields']=[];item['methods']=[]
    for n in node.body:
     if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and not n.target.id.startswith('_'):item['fields'].append({'name':n.target.id,'annotation':ast.unparse(n.annotation),'default':ast.unparse(n.value) if n.value else None})
     if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and (not n.name.startswith('_') or n.name in {'__init__','__call__','__enter__','__exit__','__aenter__','__aexit__'}):item['methods'].append({'name':n.name,'signature':sig(n),'doc':doc(n),'line':n.lineno,'decorators':[ast.unparse(d) for d in n.decorator_list]})
   symbols.append(item)
 for n in ast.walk(tree):
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
   for d in n.decorator_list:
    if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr in {'get','post','put','delete','patch','websocket','api_route'} and d.args and isinstance(d.args[0],ast.Constant) and isinstance(d.args[0].value,str):routes.append({'method':d.func.attr.upper(),'path':d.args[0].value,'source':rel,'line':n.lineno})
e2b=[];sdk_hashes={}
for path in sorted((a.sdk/'api/client/api').rglob('*.py')):
 if path.name=='__init__.py':continue
 sdk_hashes[str(path.relative_to(a.sdk))]=hashlib.sha256(path.read_bytes()).hexdigest()
 tree=ast.parse(path.read_text());method=url=None;desc='';body=None
 for n in ast.walk(tree):
  if isinstance(n,ast.Dict):
   for key,val in zip(n.keys,n.values):
    if isinstance(key,ast.Constant) and key.value=='method' and isinstance(val,ast.Constant):method=str(val.value).upper()
    if isinstance(key,ast.Constant) and key.value=='url':
     if isinstance(val,ast.Constant):url=val.value
     elif isinstance(val,ast.JoinedStr):url=''.join(v.value if isinstance(v,ast.Constant) else '{'+ast.unparse(v.value)+'}' for v in val.values)
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'sync_detailed','sync','asyncio_detailed'}:
   if not desc:desc=doc(n).split('\n\n')[0].strip()
   for arg in n.args.kwonlyargs:
    if arg.arg=='body':body=ast.unparse(arg.annotation) if arg.annotation else None
 if method and url:e2b.append({'method':method,'path':url,'description':desc,'body_model':body,'source':str(path.relative_to(a.sdk))})
rpc=[]
for path in sorted((a.sdk/'envd').rglob('*_connect.py')):
 sdk_hashes[str(path.relative_to(a.sdk))]=hashlib.sha256(path.read_bytes()).hexdigest()
 text=path.read_text()
 for route in sorted(set(re.findall(r'"(/(?:process\.Process|filesystem\.Filesystem)/[^\"]+)"',text))):rpc.append({'path':route,'source':str(path.relative_to(a.sdk))})
out={'uni_agent_commit':'10743439dd0a19da44a94cccad069b135d957bf1','e2b_sdk_version':'2.49.1','scope':'Named public declarations and public methods (plus constructor/context methods) under uni_agent; _GatewayActor included because exported as GatewayActor. Inherited third-party APIs and private helpers are not a stable public API inventory.','exports':exports,'symbols':symbols,'source_owned_http_routes':routes,'source_sha256':hashes,'e2b_rest_contracts':e2b,'e2b_envd_rpc_contracts':rpc,'e2b_source_sha256':sdk_hashes}
p=ROOT/'evidence/api-inventory.json';p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print('symbols',len(symbols),'methods',sum(len(s.get('methods',[])) for s in symbols),'gateway routes',routes,'E2B REST',len(e2b),'envd RPC',len(rpc))
