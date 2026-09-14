"""Check documented evidence consistency, copied hashes, local links and credential exclusion."""
import ast,csv,hashlib,json,math,re
from pathlib import Path
from urllib.parse import unquote,urlsplit
ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads((ROOT/p).read_text())
s=load('evidence/summary/final-summary.json');assert s['status']=='complete';assert len(s['rows'])==84
for a,n in [('react',17),('claude',11),('mini',12)]:
 d=s['agents'][a];assert (d['n'],d['resolved'],d['eval_completed'],d['errors'])==(28,n,28,0)
 cases=list((ROOT/'evidence/cases'/a).glob('*/result.json'));assert len(cases)==28;assert sum(json.loads(p.read_text())['resolved'] for p in cases)==n
assert len(list(csv.DictReader((ROOT/'results/per-case-results.csv').open())))==84
valid=load('evidence/summary/validated-manifest.json');b=load('evidence/summary/thirty-baseline-dns.json');g=load('evidence/summary/thirty-oracle-dns.json');bm={x['instance_id']:x for x in b['per_case']};gm={x['instance_id']:x for x in g['per_case']}
computed={i for i in bm if bm[i]['eval_completed'] and not bm[i]['resolved'] and gm[i]['eval_completed'] and gm[i]['resolved']};assert computed==set(valid['instances']);assert len(computed)==28
for folder in ['serving-benchmark','serving-aiter-benchmark','serving-replicas-benchmark-v2']:
 summary=load('evidence/performance/'+folder+'/summary.json')['results'];raw=load('evidence/performance/'+folder+'/raw.json');assert len(raw)==18
 for row in summary:
  key='replicas' if 'replicas' in row else 'backend';group=[q for q in raw if q[key]==row[key] and q['concurrency']==row['concurrency']];assert len(group)==3
  assert math.isclose(sum(q['aggregate_output_tok_s'] for q in group)/3,row['aggregate_output_tok_s'],rel_tol=1e-10)
  assert all(q['output_tokens']==q['concurrency']*256 for q in group)
for record in load('evidence/source-manifest.json')['copied_files']:
 p=ROOT/record['destination'];assert hashlib.sha256(p.read_bytes()).hexdigest()==record['destination_sha256'],str(p)
missing=[]
for p in ROOT.rglob('*.md'):
 if 'site' in p.parts:continue
 for v in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  u=urlsplit(v)
  if u.scheme or v.startswith('#'):continue
  if not (p.parent/unquote(u.path)).exists():missing.append((str(p.relative_to(ROOT)),v))
for p in (ROOT/'site').rglob('*.html'):
 for v in re.findall(r'(?:href|src)="([^"]+)"',p.read_text()):
  u=urlsplit(v)
  if u.scheme or v.startswith('#'):continue
  if not (p.parent/unquote(u.path)).exists():missing.append((str(p.relative_to(ROOT)),v))
secret_pattern=re.compile(rb'(?:e2b_[A-Za-z0-9]{40,}|sk-ant-[A-Za-z0-9_-]{30,}|hf_[A-Za-z0-9]{30,})')
secrets=[]
for p in ROOT.rglob('*'):
 if p.is_file() and '__pycache__' not in p.parts:
  if secret_pattern.search(p.read_bytes()):secrets.append(str(p.relative_to(ROOT)))
assert not missing,missing;assert not secrets,secrets
inventory=load('evidence/api-inventory.json')
api_doc=(ROOT/'references/03-uni-agent-api-reference.md').read_text()
sandbox_doc=(ROOT/'references/04-sandbox-and-verdal-api-reference.md').read_text()
method_count=0
for symbol in inventory['symbols']:
 doc=sandbox_doc if symbol['module'].split('.')[1]=='sandbox' else api_doc
 assert '**`'+symbol['name']+'`**' in doc,(symbol['module'],symbol['name'])
 assert symbol['source']+'#L'+str(symbol['line']) in doc,symbol['source']
 if symbol['kind']=='function':assert symbol['signature'] in doc,symbol['signature']
 for method in symbol.get('methods',[]):
  assert method['signature'].replace('|',r'\|') in doc,(symbol['name'],method['name'])
  method_count+=1
for route in inventory['source_owned_http_routes']:
 assert route['method']+' '+route['path'] in api_doc,route
for route in inventory['e2b_rest_contracts']:
 assert '`'+route['method']+' '+route['path']+'`' in sandbox_doc,route
for route in inventory['e2b_envd_rpc_contracts']:
 assert '`'+route['path']+'`' in sandbox_doc,route
snippet_count=0
for doc in [api_doc,sandbox_doc]:
 for snippet in re.findall(r'```python\n(.*?)\n```',doc,re.S):
  # Appendix signatures are reference notation, not executable statements.
  if not ('import ' in snippet or '\n' in snippet):continue
  ast.parse(snippet);snippet_count+=1
api_coverage={'public_declarations':len(inventory['symbols']),'declared_methods':method_count,'gateway_business_routes':len(inventory['source_owned_http_routes']),'e2b_rest_contracts':len(inventory['e2b_rest_contracts']),'envd_rpc_contracts':len(inventory['e2b_envd_rpc_contracts']),'python_examples_syntax_checked':snippet_count,'scope':'Static source/documentation coverage, not remote compatibility tests'}
report={'main_cases':28,'agent_results':84,'all_final_verifiers_completed':True,'dataset_gate_recomputed':True,'performance_batches_recomputed':54,'copied_files_sha_verified':len(load('evidence/source-manifest.json')['copied_files']),'markdown_pages':len([p for p in ROOT.rglob('*.md') if 'site' not in p.parts]),'missing_links':missing,'credential_pattern_matches':secrets,'api_reference':api_coverage}
(ROOT/'evidence/documentation-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
