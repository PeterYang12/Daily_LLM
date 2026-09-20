"""Paired 128K eager/AITER serving benchmark after all agent runs finish."""
import argparse,asyncio,hashlib,json,statistics,time
from pathlib import Path
import aiohttp
from transformers import AutoTokenizer

MODEL='Qwen3-Coder-30B-A3B-Instruct'
ENDPOINTS={'eager':'http://172.30.90.5:8000/v1','aiter_graph':'http://172.30.90.9:8000/v1'}

async def request(session,url,ids):
    payload={'model':MODEL,'prompt':ids,'max_tokens':256,'ignore_eos':True,'temperature':0,
             'stream':True,'stream_options':{'include_usage':True},'logprobs':1,'return_token_ids':True}
    start=time.perf_counter();first=None;usage={}
    async with session.post(url+'/completions',json=payload) as response:
        if response.status!=200:raise RuntimeError(await response.text())
        async for raw in response.content:
            line=raw.decode().strip()
            if not line.startswith('data: ') or line=='data: [DONE]':continue
            value=json.loads(line[6:])
            if value.get('usage'):usage=value['usage']
            if first is None and value.get('choices'):
                choice=value['choices'][0]
                if choice.get('text') or choice.get('token_ids') or (choice.get('logprobs') or {}).get('tokens'):
                    first=time.perf_counter()-start
    if usage.get('completion_tokens')!=256:raise RuntimeError('Incomplete fixed-output request: '+str(usage))
    return {'elapsed_s':time.perf_counter()-start,'ttft_s':first,'usage':usage}

async def main(root):
    out=root/'inference-benchmark';out.mkdir(exist_ok=True)
    tokenizer=AutoTokenizer.from_pretrained('/lab/models/'+MODEL)
    candidates=[]
    for path in (root/'main-optimized').glob('docker/*/sessions/*/trajectories.jsonl'):
        for line in path.read_text().splitlines():
            t=json.loads(line)['trajectory'];ids=t['prompt_ids']+t['response_ids']
            candidates.append((len(ids),path,ids))
    if not candidates:raise RuntimeError('No real task trajectory available for benchmark input')
    _,source,base=max(candidates,key=lambda x:x[0])
    (out/'protocol.json').write_text(json.dumps({'source_trajectory':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'input_lengths':[2048,16384,65536],'concurrency':[1,4,8],'repeats':3,'forced_output_tokens':256,'logprobs':1,'return_token_ids':True,
        'note':'Context sampled/repeated from an actual rollout; unique first-block salts prevent cross-request prefix reuse. Same input IDs sent to both endpoints per paired batch. This is a serving microbenchmark, not another task score.'},indent=2)+'\n')
    rows=json.loads((out/'raw.json').read_text()) if (out/'raw.json').exists() else []
    completed={(r['backend'],r['input_length'],r['concurrency'],r['repeat']) for r in rows}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        for length in [2048,16384,65536]:
            for concurrency in [1,4,8]:
                if all((name,length,concurrency,repeat) in completed for name in ENDPOINTS for repeat in range(3)):continue
                for repeat in [-1,0,1,2]:
                    prompts=[]
                    for index in range(concurrency):
                        salt=hashlib.sha256(f'{length}/{concurrency}/{repeat}/{index}'.encode()).hexdigest()
                        prefix=tokenizer.encode('Benchmark nonce '+salt+'\n',add_special_tokens=False)
                        ids=(prefix+base*((length//len(base))+2))[:length];assert len(ids)==length;prompts.append(ids)
                    names=list(ENDPOINTS) if repeat%2==0 else list(reversed(ENDPOINTS))
                    for name in names:
                        if (name,length,concurrency,repeat) in completed:continue
                        started=time.time();t=time.perf_counter()
                        responses=await asyncio.gather(*(request(session,ENDPOINTS[name],ids) for ids in prompts))
                        elapsed=time.perf_counter()-t
                        if repeat<0:continue  # one warmup batch per configuration
                        tokens=sum(r['usage']['completion_tokens'] for r in responses)
                        ttfts=[r['ttft_s'] for r in responses if r['ttft_s'] is not None]
                        row={'backend':name,'input_length':length,'concurrency':concurrency,'repeat':repeat,
                             'started_unix':started,'ended_unix':time.time(),'wall_s':elapsed,'output_tokens':tokens,
                             'output_tokens_per_s':tokens/elapsed,'mean_ttft_s':statistics.mean(ttfts) if ttfts else None,
                             'mean_request_s':statistics.mean(r['elapsed_s'] for r in responses),'requests':responses}
                        rows.append(row);(out/'raw.json').write_text(json.dumps(rows,indent=2)+'\n')
                        print(json.dumps({k:v for k,v in row.items() if k!='requests'}),flush=True)
    summary=[]
    for name in ENDPOINTS:
        for length in [2048,16384,65536]:
            for concurrency in [1,4,8]:
                group=[r for r in rows if r['backend']==name and r['input_length']==length and r['concurrency']==concurrency]
                assert len(group)==3
                summary.append({'backend':name,'input_length':length,'concurrency':concurrency,
                    **{field:statistics.mean(r[field] for r in group if r[field] is not None) if any(r[field] is not None for r in group) else None for field in ['output_tokens_per_s','mean_ttft_s','mean_request_s']},
                    'throughput_min':min(r['output_tokens_per_s'] for r in group),'throughput_max':max(r['output_tokens_per_s'] for r in group)})
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    # A separate warmed-prefix, non-streaming check isolates logprob capture cost.
    overhead=json.loads((out/'logprob-overhead.json').read_text())['results'] if (out/'logprob-overhead.json').exists() else []
    overhead_done={(r['backend'],r['concurrency'],r['repeat'],r['logprobs']) for r in overhead}
    async def capture_request(session,url,ids,enabled):
        payload={'model':MODEL,'prompt':ids,'max_tokens':256,'ignore_eos':True,'temperature':0,'return_token_ids':True}
        if enabled:payload['logprobs']=1
        started=time.perf_counter()
        async with session.post(url+'/completions',json=payload) as response:
            body=await response.read()
            if response.status!=200:raise RuntimeError(body.decode()[:1000])
        value=json.loads(body)
        assert value['usage']['completion_tokens']==256
        return {'elapsed_s':time.perf_counter()-started,'response_bytes':len(body)}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        for repeat in range(3):
            for concurrency in [1,4]:
                prefix=tokenizer.encode(f'Logprob overhead {repeat} {concurrency}\n',add_special_tokens=False)
                ids=(prefix+base*((16384//len(base))+2))[:16384]
                for backend,url in ENDPOINTS.items():
                    if all((backend,concurrency,repeat,enabled) in overhead_done for enabled in [False,True]):continue
                    await asyncio.gather(*(capture_request(session,url,ids,True) for _ in range(concurrency)))
                    for enabled in ([False,True] if repeat%2==0 else [True,False]):
                        if (backend,concurrency,repeat,enabled) in overhead_done:continue
                        started=time.perf_counter()
                        responses=await asyncio.gather(*(capture_request(session,url,ids,enabled) for _ in range(concurrency)))
                        wall=time.perf_counter()-started
                        row={'backend':backend,'concurrency':concurrency,'repeat':repeat,'logprobs':enabled,
                             'wall_s':wall,'output_tokens_per_s':256*concurrency/wall,
                             'mean_response_bytes':statistics.mean(r['response_bytes'] for r in responses)}
                        overhead.append(row)
                        (out/'logprob-overhead.json').write_text(json.dumps({'protocol':'16K identical warmed prefix, 256 forced output tokens; non-streaming with return_token_ids in both modes. Logprobs toggled only.','results':overhead},indent=2)+'\n')
                        print('logprob-overhead',json.dumps(row),flush=True)
    (out/'COMPLETE').write_text(str(time.time())+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);asyncio.run(main(p.parse_args().root))
