#!/usr/bin/env python3
"""Read metrics only from vLLM HTTP processes inside the dedicated training cgroup."""
import argparse,json,re,subprocess,time,urllib.request
from pathlib import Path

ALLOW={'process_cpu_seconds_total','process_resident_memory_bytes','process_virtual_memory_bytes',
       'process_open_fds','process_max_fds','process_start_time_seconds','python_gc_collections_total',
       'http_request_duration_highr_seconds_sum','http_request_duration_highr_seconds_count'}
VLLM={'vllm:num_requests_running','vllm:num_requests_waiting','vllm:kv_cache_usage_perc',
      'vllm:gpu_cache_usage_perc','vllm:request_success_total','vllm:request_queue_time_seconds_sum',
      'vllm:request_queue_time_seconds_count','vllm:request_prefill_time_seconds_sum',
      'vllm:request_decode_time_seconds_sum','vllm:request_generation_tokens_sum'}

def discover(group):
 text=subprocess.check_output(['ss','-ltnpH'],text=True,timeout=5);out=[]
 for line in text.splitlines():
  if 'ray::vLLMHttpSe' not in line:continue
  a=line.split()
  if len(a)<6 or a[2]!='2048':continue # uvicorn listener; exclude Ray/gRPC ports.
  found=re.search(r'pid=(\d+)',line)
  if not found:continue
  pid=int(found.group(1))
  try:
   cg=Path(f'/proc/{pid}/cgroup').read_text()
   if not any(x.split(':',2)[-1]==group or x.split(':',2)[-1].startswith(group+'/') for x in cg.splitlines()):continue
   ticks=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])
   host,port=a[3].rsplit(':',1)
   if host not in ['10.145.64.67','127.0.0.1','127.0.1.1']:continue
   out.append({'pid':pid,'start_ticks':ticks,'url':f'http://{host}:{int(port)}/metrics'})
  except (OSError,ValueError,IndexError):continue
 return out

def sample(target):
 row={**target,'values':{},'engine_metrics_available':False}
 try:
  ticks=int(Path(f'/proc/{target["pid"]}/stat').read_text().rsplit(')',1)[1].split()[19])
  if ticks!=target['start_ticks']:return {**row,'status':'process_replaced'}
  with urllib.request.urlopen(target['url'],timeout=2) as r:body=r.read(2000000).decode()
  for line in body.splitlines():
   if not line or line.startswith('#'):continue
   name=line.split('{',1)[0].split()[0]
   if name not in ALLOW|VLLM or 'handler="/metrics"' in line:continue
   try:value=float(line.rsplit(' ',1)[-1])
   except ValueError:continue
   if not -float('inf')<value<float('inf'):continue
   row['values'][name]=row['values'].get(name,0)+value
   if name in VLLM:row['engine_metrics_available']=True
  row['status']='ok'
 except Exception as e:row.update(status='unavailable',error_type=type(e).__name__)
 return row

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--hours',type=float,default=120);a=p.parse_args()
 if not 0<a.hours<=120:p.error('hours must be0..120')
 meta=json.loads((a.root/'results/container.json').read_text());group=meta['cgroup']
 end=time.monotonic()+a.hours*3600;next_discovery=0;targets=[]
 output=a.root/'results/model-http-metrics.jsonl'
 with output.open('x') as f:
  while time.monotonic()<end and not (a.root/'run/STOP_OBSERVER').exists():
   started=time.monotonic();now=time.time_ns()
   if started>=next_discovery:targets=discover(group);next_discovery=started+30
   row={'time_ns':now,'record_type':'model_http_metrics','scope':'dedicated vLLM HTTP processes; absent engine metrics remain unavailable','replicas':[sample(t) for t in targets]}
   row['collection_wall_s']=time.monotonic()-started
   f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush()
   time.sleep(max(0,5-(time.monotonic()-started)))
  f.write(json.dumps({'record_type':'completion','ended_time_ns':time.time_ns()})+'\n')
