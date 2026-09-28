#!/usr/bin/env python3
"""Six isolated CPU-only E2B transport probes against a local HTTP server.

The canonical script lives outside the frozen training runtime. No E2B API,
credentials, sandbox creation, Ray, torch, or GPU work is used by the probe.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time

WORKER = r'''
import os,sys,time,json,asyncio,threading,collections,hashlib,importlib.metadata
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer

condition=sys.argv[1]; repeat=int(sys.argv[2]); started=time.monotonic()
if condition=='one':os.environ['TOKIO_WORKER_THREADS']='1'
elif condition=='default':os.environ.pop('TOKIO_WORKER_THREADS',None)
else:raise ValueError('Unsupported condition')
# These changes affect only this short-lived child, never the running trainer.
for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
 os.environ.pop(key,None)
os.environ['NO_PROXY']='127.0.0.1,localhost,::1'
requests=[]
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def do_GET(self):
  requests.append({'method':'GET','path':self.path,'peer':self.client_address[0]})
  body=b'ok' if self.path=='/probe' else b'bad path'
  self.send_response(200 if self.path=='/probe' else 404)
  self.send_header('Content-Length',str(len(body)))
  self.send_header('Connection','close');self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.05},daemon=True)
thread.start()
url='http://127.0.0.1:'+str(server.server_port)+'/probe'

def snapshot(label):
 status={}
 for line in Path('/proc/self/status').read_text().splitlines():
  key,_,value=line.partition(':')
  if key in ('Threads','VmRSS','VmSize'):status[key]=int(value.strip().split()[0])
 names=collections.Counter()
 for task in Path('/proc/self/task').iterdir():
  try:names[(task/'comm').read_text().strip()]+=1
  except FileNotFoundError:pass
 return {'stage':label,'elapsed_since_child_start_s':time.monotonic()-started,
         'threads':status['Threads'],'tokio_threads':names.get('tokio-rt-worker',0),
         'rss_kib':status['VmRSS'],'virtual_size_kib':status['VmSize'],
         'thread_name_counts':dict(names)}

result={'schema_version':1,'condition':condition,'repeat':repeat,'pid':os.getpid(),
        'tokio_worker_threads_env':os.environ.get('TOKIO_WORKER_THREADS'),
        'cpu_affinity_count':len(os.sched_getaffinity(0)),'os_cpu_count':os.cpu_count(),
        'http_target':'isolated loopback server','snapshots':[],'timings':{},'success':False}
try:
 result['snapshots'].append(snapshot('before_sdk_import'))
 before=time.monotonic()
 import httpx
 import e2b.api.client_async as sdk_transport
 result['timings']['sdk_import_s']=time.monotonic()-before
 result['snapshots'].append(snapshot('after_sdk_import'))
 before=time.monotonic()
 transport=sdk_transport.get_httpx_transport(proxy=None)
 result['timings']['sdk_transport_factory_s']=time.monotonic()-before
 time.sleep(0.1)
 result['snapshots'].append(snapshot('after_transport_factory'))
 async def request():
  before=time.monotonic()
  async with httpx.AsyncClient(transport=transport,trust_env=False,timeout=5.0) as client:
   response=await client.get(url)
   assert response.status_code==200 and response.content==b'ok'
   result['timings']['first_loopback_get_s']=time.monotonic()-before
   await asyncio.sleep(0.1)
   result['snapshots'].append(snapshot('after_first_get'))
  result['snapshots'].append(snapshot('after_httpx_client_close'))
 asyncio.run(request())
 result['timings']['startup_import_factory_first_get_s']=sum(result['timings'][k] for k in
     ('sdk_import_s','sdk_transport_factory_s','first_loopback_get_s'))
 result['versions']={key:importlib.metadata.version(key) for key in ('e2b','pyqwest','httpx')}
 source=Path(sdk_transport.__file__)
 result['sdk_transport_source']={'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
 result['http_requests_received']=requests
 result['gpu_related_modules_imported']=sorted({name.split('.')[0] for name in sys.modules
                                              if name.split('.')[0] in ('torch','vllm','ray')})
 assert not result['gpu_related_modules_imported']
 assert requests==[{'method':'GET','path':'/probe','peer':'127.0.0.1'}]
 assert result['versions']['e2b']=='2.50.0' and result['versions']['pyqwest']=='0.10.0'
 result['success']=True
except BaseException as exc:
 result['error_type']=type(exc).__name__;result['error']=str(exc)
finally:
 server.shutdown();server.server_close();thread.join(timeout=2)
 result['timings']['child_wall_s']=time.monotonic()-started
 print('SDK_THREAD_PROBE_JSON='+json.dumps(result,sort_keys=True),flush=True)
raise SystemExit(0 if result['success'] else 1)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',default='yuhanya-e2b-rl-pathfinding-20260920')
    parser.add_argument('--output',type=Path,default=Path('/data2/yuhanya/e2b-rl-pathfinding-20260920/results/sdk-thread-probe'))
    args=parser.parse_args()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=args.output/stamp;output.mkdir(parents=True,exist_ok=False)
    rows=[]
    order=[('default',1),('one',1),('one',2),('default',2),('default',3),('one',3)]
    protocol={'scope':'CPU-only transport initialization/local GET; not RL throughput or E2B server performance',
              'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'worker_sha256':hashlib.sha256(WORKER.encode()).hexdigest(),
              'container':args.container,'order':order,'repeats_per_condition':3,
              'frozen_runtime_code_modified':False,'live_training_environment_modified':False,
              'e2b_service_calls':0,'sandboxes_created':0,'per_child_wall_timeout_s':25}
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    for condition,repeat in order:
        command=['docker','exec','-i',args.container,'/usr/bin/timeout','--signal=TERM','--kill-after=2s','25s',
                 '/opt/venv/bin/python','-',condition,str(repeat)]
        started=time.monotonic()
        try:
            completed=subprocess.run(command,input=WORKER,text=True,capture_output=True,timeout=35)
            payload=[line for line in completed.stdout.splitlines() if line.startswith('SDK_THREAD_PROBE_JSON=')]
            result=json.loads(payload[-1].split('=',1)[1]) if payload else {'condition':condition,'repeat':repeat,'success':False,'error_type':'MissingWorkerResult'}
            result.update(docker_exit_code=completed.returncode,parent_process_wall_s=time.monotonic()-started)
            (output/f'{condition}-{repeat}.stderr.txt').write_text(completed.stderr[-65536:])
        except subprocess.TimeoutExpired:
            result={'condition':condition,'repeat':repeat,'success':False,'error_type':'OuterDockerTimeout',
                    'parent_process_wall_s':time.monotonic()-started}
        rows.append(result)
        (output/f'{condition}-{repeat}.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'condition':condition,'repeat':repeat,'success':result['success'],
                          'parent_process_wall_s':result['parent_process_wall_s']}),flush=True)
    summary={'protocol':protocol,'results_directory':str(output),'successful_children':sum(row['success'] for row in rows),'conditions':{}}
    for condition in ('default','one'):
        selected=[row for row in rows if row['condition']==condition and row['success']]
        stats={'successful_repeats':len(selected),'stages':{},'timings':{}}
        if selected:
            for stage in ('before_sdk_import','after_sdk_import','after_transport_factory','after_first_get','after_httpx_client_close'):
                observations=[next(value for value in row['snapshots'] if value['stage']==stage) for row in selected]
                stats['stages'][stage]={key:{'median':statistics.median(value[key] for value in observations),
                      'min':min(value[key] for value in observations),'max':max(value[key] for value in observations)}
                    for key in ('threads','tokio_threads','rss_kib','virtual_size_kib')}
            for key in ('sdk_import_s','sdk_transport_factory_s','first_loopback_get_s','startup_import_factory_first_get_s','child_wall_s'):
                values=[row['timings'][key] for row in selected]
                stats['timings'][key]={'median':statistics.median(values),'min':min(values),'max':max(values)}
        summary['conditions'][condition]=stats
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (args.output/'latest.json').write_text(json.dumps({'directory':str(output),'summary':str(output/'summary.json')},indent=2)+'\n')
    print('SUMMARY='+str(output/'summary.json'))
    return 0 if summary['successful_children']==6 else 1

if __name__=='__main__':raise SystemExit(main())
