"""Incremental passive API/build/host-stat collection; bounded queries and log rotation."""
from datetime import datetime, timezone, timedelta
import fcntl
import gzip
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT=Path('/data2/yuhanya/e2b-local-20260916')
OUT=ROOT/'monitoring/events'
ANSI=re.compile(r'\x1b\[[0-9;]*m')
FACTORS={'ns':1e-6,'µs':.001,'μs':.001,'us':.001,'ms':1,'s':1000,'m':60000,'h':3600000}


def utc():return datetime.now(timezone.utc).isoformat()


def logger(name):
    out=logging.getLogger('e2b-events-'+name);out.setLevel(logging.INFO)
    h=RotatingFileHandler(OUT/(name+'.jsonl'),maxBytes=32*2**20,backupCount=48)
    h.namer=lambda p:p+'.gz'
    def rotate(a,b):
        with open(a,'rb') as x,gzip.open(b,'wb',compresslevel=3) as y:shutil.copyfileobj(x,y)
        os.remove(a)
    h.rotator=rotate;h.setFormatter(logging.Formatter('%(message)s'));out.addHandler(h)
    return out,h


def ssh(command,timeout=30):
    r=subprocess.run([str(ROOT/'code/vm-ssh'),command],capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError('Remote read failed with exit '+str(r.returncode))
    return r.stdout


def duration(value):
    pieces=re.findall(r'(\d+(?:\.\d+)?)(ns|µs|μs|us|ms|s|m|h)',value)
    return round(sum(float(n)*FACTORS[u] for n,u in pieces),4) if pieces else None


def route(path):
    path=re.sub(r'^(/v\d+)?/sandboxes/[^/]+',lambda m:(m[1] or '')+'/sandboxes/{id}',path)
    path=re.sub(r'^(/v\d+)?/templates/[^/]+',lambda m:(m[1] or '')+'/templates/{id}',path)
    return re.sub(r'/builds/[^/]+','/builds/{id}',path)


def parse_log(source,line):
    line=ANSI.sub('',line)
    if ' ' not in line:return None,None
    stamp,body=line.split(' ',1)
    try:when=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    except ValueError:return None,None
    record={'utc':stamp,'source':source}
    if source=='gateway':
        fields=body.split()
        if len(fields)!=5:return when,None
        try:code=int(fields[3]);elapsed=float(fields[4])*1000
        except ValueError:return when,None
        record.update(event='http_proxy_request',client_ip=fields[0],method=fields[1],path=fields[2],
                      route=route(fields[2]),status=code,request_ms=elapsed,
                      note='Route headers are absent in existing log format; /health may target a sandbox')
        return when,record
    data={}
    if '{' in body:
        try:data=json.loads(body[body.index('{'):])
        except ValueError:pass
    if source=='api' and 'method' in data and 'latency' in data:
        record.update(event='api_request',method=data['method'],path=data.get('path'),
                      route=route(data.get('path','')),status=data.get('status'),
                      handler_ms=duration(data['latency']),user_agent=data.get('user-agent'))
        return when,record
    if 'failed to upload metrics' in body:
        record.update(event='metrics_export_failure',reason='endpoint_missing' if 'missing address' in body else 'other')
        return when,record
    if 'failed to reset memory.peak' in body:
        record.update(event='memory_peak_reset_unsupported',note='Do not interpret native memory peak as a reset interval peak')
        return when,record
    keep=['sandbox.id','template.id','build.id','sandbox_id','template_id','build_id','state_action']
    fields={k:data[k] for k in keep if k in data}
    vm_time=re.search(r"'([^']+)' (VMM action|API request) took ([\d.]+) (us|ms|s)",body)
    if vm_time:
        record.update(event='firecracker_action',action=vm_time[1],layer=vm_time[2],
                      duration_ms=float(vm_time[3])*FACTORS[vm_time[4]],identifiers=fields)
        return when,record
    for text,event in [('Sandbox created details','sandbox_created'),('Removing sandbox','sandbox_removal')]:
        if text in body:
            record.update(event=event,identifiers=fields);return when,record
    return when,None


def main():
    os.umask(0o077);OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        path=OUT/'cursor.json'
        state=json.loads(path.read_text()) if path.exists() else {}
        seen_list=state.get('recent_ids',[]);seen=set(seen_list)
        stats={'utc':utc(),'sources':{},'errors':[]}
        writers={}
        def write(source,record):
            encoded=json.dumps(record,sort_keys=True,separators=(',',':'))
            ident=hashlib.sha256(encoded.encode()).hexdigest()
            if ident in seen:return False
            seen.add(ident);seen_list.append(ident)
            if source not in writers:writers[source]=logger(source)
            writers[source][0].info(json.dumps({'event_id':ident,**record},separators=(',',':')))
            return True
        try:
            for source in ['api','gateway','orchestrator']:
                byte_limit=16*2**20 if source=='orchestrator' else 4*2**20
                previous=datetime.fromisoformat(state[source]) if source in state else datetime.now(timezone.utc)-timedelta(minutes=30)
                since=(previous-timedelta(seconds=2)).isoformat().replace('+00:00','Z')
                target='deployment/e2b-gateway' if source=='gateway' else 'pod/e2b-0 -c '+source
                try:
                    raw=ssh(f'sudo k3s kubectl -n e2b logs {target} --since-time={since} --timestamps --limit-bytes={byte_limit}')
                    latest=previous;count=0
                    for line in raw.splitlines():
                        when,record=parse_log(source,line)
                        if when and when>latest:latest=when
                        if record:count+=write(source,record)
                    state[source]=latest.isoformat();stats['sources'][source]={'written':count,'read_bytes':len(raw.encode()),'cursor':state[source]}
                    if len(raw.encode())>=byte_limit:stats['errors'].append(source+': log byte limit reached; coverage may be incomplete')
                except Exception as e:stats['errors'].append(source+': '+type(e).__name__)
            for table in ['sandbox_host_stats','build_events']:
                previous=int(state.get(table,int((time.time()-1800)*1e9)))
                start=max(0,previous-60_000_000_000)
                end=max(previous,int(time.time()*1e9)-10_000_000_000)
                if table=='sandbox_host_stats':
                    query=f'SELECT toUnixTimestamp64Nano(timestamp) AS ts_ns, * FROM sandbox_host_stats WHERE timestamp > fromUnixTimestamp64Nano({start}) AND timestamp <= fromUnixTimestamp64Nano({end}) ORDER BY timestamp LIMIT 30000 SETTINGS max_execution_time=5,max_memory_usage=268435456 FORMAT JSONEachRow'
                else:
                    query=f'''SELECT toUnixTimestamp64Nano(timestamp) AS ts_ns,timestamp,sandbox_id,template_id,build_id,service,category,message FROM sandbox_logs WHERE timestamp > fromUnixTimestamp64Nano({start}) AND timestamp <= fromUnixTimestamp64Nano({end}) AND service IN ('template-manager','orchestrator_template-manager') AND (positionCaseInsensitive(message,'download')>0 OR positionCaseInsensitive(message,'pull')>0 OR positionCaseInsensitive(message,'extract')>0 OR positionCaseInsensitive(message,'unpack')>0 OR positionCaseInsensitive(message,'cache')>0 OR positionCaseInsensitive(message,'build finished')>0 OR positionCaseInsensitive(message,'optimiz')>0 OR positionCaseInsensitive(message,'uploading layer')>0 OR positionCaseInsensitive(message,'loading layer')>0) ORDER BY timestamp LIMIT 10000 SETTINGS max_execution_time=5,max_memory_usage=268435456 FORMAT JSONEachRow'''
                # SQL is generated only from fixed identifiers and integer cursors.
                import shlex
                try:
                    raw=ssh('sudo k3s kubectl -n e2b exec e2b-0 -c clickhouse -- clickhouse-client --user clickhouse --password clickhouse --query '+shlex.quote(query))
                    count=0;rows=0;latest=previous
                    for line in raw.splitlines():
                        item=json.loads(line);rows+=1;latest=max(latest,int(item['ts_ns']))
                        if table=='build_events':
                            # Preserve performance text, never credential-looking substrings.
                            item['message']=re.sub(r'(?i)(?:e2b_|sk-)[A-Za-z0-9_-]{12,}','<redacted>',item['message'])
                            item['message']=re.sub(r'(?i)(bearer\s+)[^\s\"\']+',r'\1<redacted>',item['message'])
                        count+=write(table,item)
                    if rows<(30000 if table=='sandbox_host_stats' else 10000):latest=max(latest,end)
                    state[table]=latest;stats['sources'][table]={'read':rows,'written':count,'cursor':latest}
                    if rows>=(30000 if table=='sandbox_host_stats' else 10000):stats['errors'].append(table+': row limit reached; cursor retained for catch-up')
                except Exception as e:stats['errors'].append(table+': '+type(e).__name__)
        finally:
            for log,handler in writers.values():handler.close();log.removeHandler(handler)
            state['recent_ids']=seen_list[-60000:]
            tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(path)
            temporary=OUT/'latest-collection.tmp';temporary.write_text(json.dumps(stats,indent=2));temporary.replace(OUT/'latest-collection.json')
            health_log,health_handler=logger('collection-health')
            health_log.info(json.dumps(stats,separators=(',',':')))
            health_handler.close();health_log.removeHandler(health_handler)
            print(json.dumps(stats),flush=True)
        if stats['errors']:raise SystemExit(2)


if __name__=='__main__':main()
