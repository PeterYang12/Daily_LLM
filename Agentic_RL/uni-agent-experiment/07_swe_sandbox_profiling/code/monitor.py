"""Read-only 1 Hz GPU/cgroup/vLLM sampling on the host; no GPU profiler injection."""
import argparse,json,os,subprocess,time,urllib.request
from pathlib import Path

METRICS={
 'vllm:num_requests_running','vllm:num_requests_waiting','vllm:kv_cache_usage_perc',
 'vllm:request_success_total','vllm:request_prompt_tokens_sum','vllm:request_prompt_tokens_count',
 'vllm:request_generation_tokens_sum','vllm:request_generation_tokens_count',
 'vllm:request_queue_time_seconds_sum','vllm:request_inference_time_seconds_sum',
 'vllm:request_prefill_time_seconds_sum','vllm:request_decode_time_seconds_sum',
 'vllm:time_to_first_token_seconds_sum','vllm:time_to_first_token_seconds_count',
 'vllm:e2e_request_latency_seconds_sum','vllm:e2e_request_latency_seconds_count',
 'vllm:prefix_cache_queries_total','vllm:prefix_cache_hits_total',
 'vllm:request_prefill_kv_computed_tokens_sum','vllm:request_prefill_kv_cached_tokens_sum'}

def number(path):
 try:return int(path.read_text().strip())
 except (OSError,ValueError):return None

def parse_metrics(text):
 values={}
 for line in text.splitlines():
  if not line or line.startswith('#'):continue
  name=line.split('{',1)[0].split()[0]
  if name not in METRICS:continue
  try:value=float(line.split()[-1])
  except ValueError:continue
  values[name]=values.get(name,0)+value
 return values

def cgroups():
 result={}
 for name in ['ua-lab-cpu','ua-lab-model-128k','ua-profile-model-aiter-128k','ua-lab-sandbox-daemon']:
  try:
   data=json.loads(subprocess.check_output(['docker','inspect',name],stderr=subprocess.DEVNULL))[0]
   pid=data['State']['Pid']
   relative=next(line.split(':',2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0:'))
   result[name]=Path('/sys/fs/cgroup')/relative.lstrip('/')
  except Exception:pass
 return result

def main(args):
 root=args.root;root.mkdir(parents=True,exist_ok=True)
 devices=[p for p in Path('/sys/class/drm').glob('card[0-9]*') if (p/'device/mem_info_vram_used').exists()]
 groups=cgroups();count=0
 with (root/args.output_name).open('a',buffering=1) as output:
  while not (root/args.stop_file).exists():
   started=time.monotonic();record={'time_ns':time.time_ns(),'gpus':[],'containers':{}}
   for device in devices:
    path=device/'device'
    record['gpus'].append({'card':device.name,'pci':path.resolve().name,
      'gfx_busy_percent':number(path/'gpu_busy_percent'),'mem_busy_percent':number(path/'mem_busy_percent'),
      'vram_used_bytes':number(path/'mem_info_vram_used')})
   for name,path in groups.items():
    try:
     stats={line.split()[0]:int(line.split()[1]) for line in (path/'cpu.stat').read_text().splitlines()}
     record['containers'][name]={'cpu':stats,'memory_bytes':number(path/'memory.current')}
    except OSError:pass
   try:
    with urllib.request.urlopen(args.metrics_url,timeout=3) as r:record['vllm']=parse_metrics(r.read().decode())
   except Exception as exc:record['metrics_error']=type(exc).__name__
   record['sample_cost_ms']=(time.monotonic()-started)*1000
   output.write(json.dumps(record)+'\n');count+=1
   if count%60==0:groups=cgroups();print('telemetry samples',count,flush=True)
   time.sleep(max(0,args.interval-(time.monotonic()-started)))
 print('monitor stopped, samples',count,flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--interval',type=float,default=1)
 p.add_argument('--metrics-url',default='http://127.0.0.1:18083/metrics')
 p.add_argument('--output-name',default='telemetry.jsonl');p.add_argument('--stop-file',default='STOP_MONITOR');main(p.parse_args())
