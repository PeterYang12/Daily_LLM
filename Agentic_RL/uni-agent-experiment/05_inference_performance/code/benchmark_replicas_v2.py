import asyncio,json,time,statistics
from pathlib import Path
import aiohttp
from transformers import AutoTokenizer
ROOT=Path('/lab/results/serving-replicas-benchmark-v2');ROOT.mkdir(exist_ok=False)
MODEL='Qwen3-Coder-30B-A3B-Instruct'; tok=AutoTokenizer.from_pretrained('/lab/models/'+MODEL)
async def request(session,url,salt):
 ids=tok.encode('Unique experiment '+salt+'\nReview Python interval merging invariants.\n'+('def merge_intervals(intervals):\n    return sorted(intervals)\n'*500),add_special_tokens=False)[:2048]
 start=time.perf_counter();first=None;usage={}
 async with session.post(url+'/completions',json={'model':MODEL,'prompt':ids,'max_tokens':256,'ignore_eos':True,'temperature':0,'stream':True,'stream_options':{'include_usage':True}}) as r:
  if r.status!=200:raise RuntimeError(await r.text())
  async for raw in r.content:
   line=raw.decode().strip()
   if not line.startswith('data: ') or line=='data: [DONE]':continue
   d=json.loads(line[6:]);usage=d.get('usage') or usage
   if first is None and d.get('choices') and d['choices'][0].get('text'):first=time.perf_counter()-start
 return {'elapsed_s':time.perf_counter()-start,'ttft_s':first,'usage':usage}
async def main():
 urls=['http://172.30.90.7:8000/v1','http://172.30.90.8:8000/v1'];rows=[]
 async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True),timeout=aiohttp.ClientTimeout(total=240)) as s:
  for url in urls:await request(s,url,'warmup-'+url)
  for repeat in range(3):
   for c in [8,16,32]:
    for replicas in ([1,2] if repeat%2==0 else [2,1]):
     start=time.perf_counter();rs=await asyncio.gather(*(request(s,urls[i%replicas],f'{repeat}-{c}-{replicas}-{i}') for i in range(c)));wall=time.perf_counter()-start
     tokens=sum(r['usage']['completion_tokens'] for r in rs)
     row={'replicas':replicas,'concurrency':c,'repeat':repeat,'output_tokens':tokens,'wall_s':wall,'aggregate_output_tok_s':tokens/wall,'mean_ttft_s':statistics.mean(r['ttft_s'] for r in rs),'mean_request_s':statistics.mean(r['elapsed_s'] for r in rs),'gpu_seconds_per_1000_output_tokens':replicas*wall/tokens*1000,'requests':rs}
     rows.append(row);(ROOT/'raw.json').write_text(json.dumps(rows,indent=2));print(json.dumps({k:v for k,v in row.items() if k!='requests'}),flush=True)
 result=[]
 for replicas in [1,2]:
  for c in [8,16,32]:
   sub=[r for r in rows if r['replicas']==replicas and r['concurrency']==c]
   result.append({'replicas':replicas,'concurrency':c,**{k:statistics.mean(r[k] for r in sub) for k in ['aggregate_output_tok_s','mean_ttft_s','mean_request_s','gpu_seconds_per_1000_output_tokens']}})
 (ROOT/'summary.json').write_text(json.dumps({'protocol':'Same pinned BF16 model and AITER/graph TP1 config on GPUs2/3; 2048 input,256 forced output; static round-robin for two replicas; concurrency8/16/32; 3 repeats; prefix cache enabled with unique early salts; GPU1 agent work continues. Not a Uni-Agent AgentAwareRouter benchmark or an 8-replica scale claim.','results':result},indent=2))
asyncio.run(main())
