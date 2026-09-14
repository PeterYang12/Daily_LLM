"""Small controlled HTTP streaming benchmark; inference only, no agent/tool time."""
import asyncio,json,time,statistics
from pathlib import Path
import aiohttp
from transformers import AutoTokenizer

ROOT=Path('/lab/results/serving-aiter-benchmark'); ROOT.mkdir(exist_ok=False)
MODEL='Qwen3-Coder-30B-A3B-Instruct'; tokenizer=AutoTokenizer.from_pretrained('/lab/models/'+MODEL)
async def request(session,url,salt):
 text=('Benchmark request '+salt+'\nYou are a careful Python software engineer. Review this code and explain invariants.\n'+('def merge_intervals(intervals):\n    return sorted(intervals)\n'*500))
 ids=tokenizer.encode(text,add_special_tokens=False)[:2048]
 payload={'model':MODEL,'prompt':ids,'max_tokens':256,'ignore_eos':True,'temperature':0,'stream':True,'stream_options':{'include_usage':True}}
 start=time.perf_counter(); first=None; usage={}; chunks=0
 async with session.post(url+'/completions',json=payload) as r:
  if r.status!=200:raise RuntimeError(await r.text())
  async for raw in r.content:
   line=raw.decode().strip()
   if not line.startswith('data: ') or line=='data: [DONE]': continue
   d=json.loads(line[6:])
   if d.get('usage'):usage=d['usage']
   if d.get('choices') and d['choices'][0].get('text'):
    chunks+=1
    if first is None:first=time.perf_counter()-start
 total=time.perf_counter()-start
 return {'elapsed_s':total,'ttft_s':first,'usage':usage,'text_chunks':chunks,'salt':salt}
async def main():
 rows=[]
 endpoints={'tp1_eager':('http://172.30.90.3:8000/v1',1),'tp1_aiter_graph':('http://172.30.90.7:8000/v1',1)}
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=240)) as session:
  for name,(url,_) in endpoints.items():await request(session,url,'warmup-'+name)
  for repeat in range(3):
   for concurrency in [1,4,8]:
    # Reverse endpoint order on alternating repeats to reduce order effects.
    names=list(endpoints) if repeat%2==0 else list(reversed(endpoints))
    for name in names:
     url,gpus=endpoints[name]; start=time.perf_counter()
     rs=await asyncio.gather(*(request(session,url,f'{repeat}-{concurrency}-{i}-{name}') for i in range(concurrency)))
     wall=time.perf_counter()-start; tokens=sum(r['usage']['completion_tokens'] for r in rs)
     row={'backend':name,'gpus':gpus,'repeat':repeat,'concurrency':concurrency,'wall_s':wall,'output_tokens':tokens,'aggregate_output_tok_s':tokens/wall,'mean_ttft_s':statistics.mean(r['ttft_s'] for r in rs),'mean_request_s':statistics.mean(r['elapsed_s'] for r in rs),'gpu_seconds_per_1000_output_tokens':gpus*wall/tokens*1000,'requests':rs}
     rows.append(row); (ROOT/'raw.json').write_text(json.dumps(rows,indent=2)); print(json.dumps({k:v for k,v in row.items() if k!='requests'}),flush=True)
 summary=[]
 for n in endpoints:
  for c in [1,4,8]:
   rs=[r for r in rows if r['backend']==n and r['concurrency']==c]
   summary.append({'backend':n,'concurrency':c,**{k:statistics.mean(r[k] for r in rs) for k in ['aggregate_output_tok_s','mean_ttft_s','mean_request_s','gpu_seconds_per_1000_output_tokens']}})
 (ROOT/'summary.json').write_text(json.dumps({'protocol':'2048 input tokens; 256 forced output tokens; greedy; prefix cache enabled but unique early request salts; 3 repeats; TP1 eager GPU0 vs TP1 AITER plus default compilation/graphs GPU2; same pinned BF16 model/image, 64K cap, memory ratio and OMP setting; other agent experiments on GPU1 continued during measurement. Not an optimized throughput claim.','results':summary},indent=2))
asyncio.run(main())
