#!/usr/bin/env python3
"""Bounded, owned idle-pool diagnostic. No daemon settings or workload jobs.

Create sequentially at no more than one request per second. At each 32-instance
milestone, inspect CPU counters in three owned guests. A failed create is never
retried; retained guests are inspected before owned-only reconciliation.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import time
import uuid
from urllib.parse import quote, quote_plus

from e2b import AsyncSandbox, SandboxQuery, SandboxState

CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')
VM_SSH = '/data2/yuhanya/e2b-local-20260916/code/vm-ssh'
PURPOSE = 'e2b-benchmark-20260920'

GUEST_SCRIPT = r'''
import json, os, time
from pathlib import Path
collector = os.getpid()
hz = os.sysconf('SC_CLK_TCK')
def snapshot():
    cpu = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    rows = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == collector:
            continue
        try:
            s = (p/'stat').read_text().rsplit(')', 1)[1].split()
            rows[int(p.name)] = {'pid':int(p.name), 'comm':(p/'comm').read_text().strip(),
                'state':s[0], 'start_ticks':int(s[19]), 'user_ticks':int(s[11]), 'system_ticks':int(s[12])}
        except (OSError, ValueError, IndexError):
            pass
    return {'monotonic':time.monotonic(), 'cpu_ticks':cpu, 'load_average':list(os.getloadavg()), 'rows':rows}
a = snapshot()
time.sleep(1)
b = snapshot()
dt = b['monotonic']-a['monotonic']
rows = []
for pid, v in b['rows'].items():
    old = a['rows'].get(pid)
    if old is None or old['start_ticks'] != v['start_ticks']:
        continue
    row = dict(v)
    row.update(user_ticks_before=old['user_ticks'], system_ticks_before=old['system_ticks'],
        user_ticks_delta=max(0,v['user_ticks']-old['user_ticks']),
        system_ticks_delta=max(0,v['system_ticks']-old['system_ticks']))
    row['cpu_cores'] = (row['user_ticks_delta']+row['system_ticks_delta'])/hz/dt
    rows.append(row)
rows.sort(key=lambda x:(-x['cpu_cores'],x['pid']))
d = [max(0,y-x) for x,y in zip(a['cpu_ticks'],b['cpu_ticks'])]
total = sum(d)
busy = sum(d[:3])+d[5]+d[6]
print(json.dumps({'schema_version':1, 'collector_pid_excluded':collector, 'clock_ticks_per_second':hz,
    'interval_seconds':dt, 'guest_logical_cpus':os.cpu_count(),
    'guest_cpu_ticks_before':a['cpu_ticks'], 'guest_cpu_ticks_after':b['cpu_ticks'],
    'guest_busy_cpu_cores':busy/hz/dt,
    'guest_busy_percent':100*busy/total if total else 0,
    'load_average_before':a['load_average'], 'load_average_after':b['load_average'],
    'continuous_processes':len(rows), 'returned_processes':min(len(rows),256),
    'new_pids':sorted(set(b['rows'])-set(a['rows'])), 'exited_pids':sorted(set(a['rows'])-set(b['rows'])),
    'processes':rows[:256], 'process_rows_truncated':len(rows)>256}))
'''


def utc():
    return datetime.now(timezone.utc).isoformat()


def select_guests(handles):
    ordered = list(handles.values())
    if not ordered:
        return []
    choices = [('oldest', 0), ('middle', (len(ordered)-1)//2), ('newest', len(ordered)-1)]
    seen = set(); result = []
    for role, index in choices:
        if index not in seen:
            result.append((role, index, ordered[index])); seen.add(index)
    return result


def validate_guest_result(data):
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('invalid guest counter schema')
    if not 0 < data.get('interval_seconds', 0) <= 15:
        raise ValueError('invalid guest counter interval')
    top_allowed={'schema_version','collector_pid_excluded','clock_ticks_per_second','interval_seconds',
        'guest_logical_cpus','guest_cpu_ticks_before','guest_cpu_ticks_after','guest_busy_cpu_cores','guest_busy_percent',
        'load_average_before','load_average_after','continuous_processes','returned_processes','new_pids','exited_pids',
        'processes','process_rows_truncated'}
    if set(data)!=top_allowed or not isinstance(data['processes'],list):
        raise ValueError('guest top-level whitelist mismatch')
    if len(data['guest_cpu_ticks_before'])!=8 or len(data['guest_cpu_ticks_after'])!=8:
        raise ValueError('invalid aggregate CPU counters')
    excluded = data.get('collector_pid_excluded')
    allowed = {'pid', 'comm', 'state', 'start_ticks', 'user_ticks', 'system_ticks',
               'user_ticks_before', 'system_ticks_before', 'user_ticks_delta', 'system_ticks_delta', 'cpu_cores'}
    for row in data.get('processes', []):
        if row.get('pid') == excluded or set(row) - allowed:
            raise ValueError('guest process schema or collector exclusion failed')
    return data


class IdlePool:
    def __init__(self, args):
        self.args = args
        self.cfg = json.loads(CONFIG.read_text())
        self.opts = {k:self.cfg[k] for k in ('api_url','sandbox_url','api_key')}
        self.opts.update(request_timeout=15, retries=0)
        self.run_id = uuid.uuid4().hex
        self.metadata = {'purpose':PURPOSE,'run_id':self.run_id,'diagnostic':'idle-pool-slow-ramp'}
        self.started = time.monotonic(); self.started_utc = utc()
        self.deadline = self.started + args.max_runtime
        # Leave time for observations plus 150 seconds after an unknown create.
        self.work_deadline = self.deadline - 180
        self.stop = asyncio.Event(); self.handles = {}; self.attempts = 0
        self.uncertain_until = 0; self.last_guard = 0
        self.creation_failure = None; self.inspections = []; self.milestones = []
        os.umask(0o077)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        self.summary_file = args.output.open('x')
        self.observation_path = args.output.with_suffix('.observations.jsonl')
        self.events = self.observation_path.open('x', buffering=1)
        self.summary_file.write(json.dumps({'status':'running','run_id':self.run_id,'started_utc':self.started_utc})+'\n')
        self.summary_file.flush()
        self.write('metadata', parameters=vars(args), source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   guest_script_sha256=hashlib.sha256(GUEST_SCRIPT.encode()).hexdigest(), pid=os.getpid())

    def write(self, kind, **values):
        self.events.write(json.dumps({'record_type':kind,'run_id':self.run_id,'utc':utc(),
            'offset_seconds':time.monotonic()-self.started, **values}, default=str)+'\n')

    def safe_error(self, error):
        secrets = {self.cfg.get('api_key','')}
        for sb in self.handles.values():
            for attr in ('_envd_access_token','traffic_access_token'):
                value = getattr(sb, attr, None)
                if isinstance(value,str): secrets.add(value)
            for k,v in getattr(getattr(sb,'_envd_api',None),'headers',{}).items():
                if re.search(r'token|authorization|api.?key',k,re.I) and isinstance(v,str):secrets.add(v)
        message = str(getattr(error,'message',None) or error)
        variants=set()
        for secret in secrets:
            if secret:variants.update((secret,quote(secret,safe=''),quote_plus(secret),
                json.dumps(secret)[1:-1],base64.b64encode(secret.encode()).decode()))
        for secret in sorted(variants,key=len,reverse=True):
            if secret:message=message.replace(secret,'<redacted>')
        message=re.sub(r'\b(?:e2b_|sk-)[A-Za-z0-9_-]{12,}\b','<redacted-token>',message)
        message=re.sub(r'(?i)(?:authorization|api[_-]?key|access[_-]?token)\s*[:=][^,;\n]+','<credential-field>',message)
        code=getattr(error,'code',None)
        return {'error_class':type(error).__name__, 'rpc_code':code.name if isinstance(code,Enum) else None,
                'redacted_message':message[:1500]}

    async def list_all(self):
        pager=AsyncSandbox.list(query=SandboxQuery(state=[SandboxState.RUNNING,SandboxState.PAUSED]),limit=100,**self.opts)
        rows=[]
        while pager.has_next:
            rows.extend(await pager.next_items())
            if len(rows)>10000:raise RuntimeError('sandbox list exceeded diagnostic bound')
        return rows

    async def guard(self, initial=False):
        rows=await asyncio.wait_for(self.list_all(),15)
        foreign=[x for x in rows if initial or (x.metadata or {}).get('purpose')!=PURPOSE
                 or (x.metadata or {}).get('run_id')!=self.run_id]
        if foreign:raise RuntimeError('foreign workload present; stopping diagnostic')
        proc=await asyncio.create_subprocess_exec(VM_SSH,"awk '/MemAvailable/ {print $2}' /proc/meminfo",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            out,_=await asyncio.wait_for(proc.communicate(),10)
            if proc.returncode:raise RuntimeError('VM memory check failed')
            available_gib=int(out.strip())/1024/1024
            if available_gib<self.args.memory_floor_gib:raise RuntimeError('VM memory floor reached')
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()
        self.last_guard=time.monotonic()
        self.write('guard', foreign_count=0, vm_available_gib=available_gib)

    async def create_one(self):
        self.attempts+=1
        started=time.monotonic()
        try:
            sb=await asyncio.wait_for(AsyncSandbox.create('base-4k-4g',timeout=1800,metadata=self.metadata,**self.opts),20)
        except Exception as error:
            # Stop after every failed POST. No retry even if response is unknown.
            self.uncertain_until=max(self.uncertain_until,time.monotonic()+150)
            self.creation_failure={'attempt':self.attempts,'existing_handles':len(self.handles),
                'utc':utc(),'duration_seconds':time.monotonic()-started,**self.safe_error(error)}
            self.write('creation_failure',**self.creation_failure)
            return None
        self.handles[sb.sandbox_id]=sb
        self.write('created', attempt=self.attempts, resident_count=len(self.handles),sandbox_id=sb.sandbox_id,
                   duration_seconds=time.monotonic()-started)
        return sb

    async def inspect_pool(self, reason):
        selected=select_guests(self.handles)
        if not selected or time.monotonic()+14>=self.deadline:
            return
        command="exec python3 - <<'_E2B_IDLE_CPU_'\n"+GUEST_SCRIPT+"\n_E2B_IDLE_CPU_"
        async def inspect(role,index,sb):
            began=time.monotonic()
            record={'reason':reason,'resident_count':len(self.handles),'selection':role,
                    'creation_index':index,'sandbox_id':sb.sandbox_id}
            try:
                result=await asyncio.wait_for(sb.commands.run(command,user='root',timeout=8,request_timeout=10),12)
                if result.exit_code!=0 or len(result.stdout)>1024*1024:
                    raise ValueError('guest counter process failed or exceeded output bound')
                record.update(status='success',guest=validate_guest_result(json.loads(result.stdout)))
            except Exception as error:
                # No command text, stderr, args or environment in failed probes.
                code=getattr(error,'code',None)
                record.update(status='error',error_class=type(error).__name__,
                              rpc_code=code.name if isinstance(code,Enum) else None)
            record['duration_seconds']=time.monotonic()-began
            self.write('guest_observation',**record);return record
        records=await asyncio.gather(*(inspect(*x) for x in selected))
        self.inspections.extend(records)

    async def cleanup(self):
        if not self.attempts:
            return {'status':'not_needed','verified_zero':True,'remaining_owned_ids':[]}
        empty=0; errors=0; remaining=[]
        deadline=min(self.deadline,time.monotonic()+160)
        while time.monotonic()<deadline:
            try:
                rows=await asyncio.wait_for(self.list_all(),max(.1,min(15,deadline-time.monotonic())))
                owned=[x for x in rows if (x.metadata or {}).get('purpose')==PURPOSE
                       and (x.metadata or {}).get('run_id')==self.run_id]
                remaining=[x.sandbox_id for x in owned]
                if not owned:
                    empty+=1
                    if empty>=2 and time.monotonic()>=self.uncertain_until:
                        return {'status':'verified_zero','verified_zero':True,'remaining_owned_ids':[],
                                'empty_checks':empty,'read_or_delete_errors':errors}
                else:
                    empty=0
                    semaphore=asyncio.Semaphore(8)
                    async def kill(info):
                        async with semaphore:
                            if time.monotonic()>=deadline:return
                            try:
                                await asyncio.wait_for(AsyncSandbox.kill(info.sandbox_id,**self.opts),
                                                       max(.1,min(10,deadline-time.monotonic())))
                            except Exception:
                                nonlocal errors
                                errors+=1
                    await asyncio.gather(*(kill(x) for x in owned))
                self.write('cleanup_observation',owned_count=len(owned),empty_checks=empty)
            except Exception as error:
                errors+=1;self.write('cleanup_error',error_class=type(error).__name__)
            await asyncio.sleep(max(0,min(1,deadline-time.monotonic())))
        return {'status':'unconfirmed','verified_zero':False,'remaining_owned_ids':remaining,
                'read_or_delete_errors':errors,'note':'Bounded deadline reached; preserve run metadata for owned-only follow-up.'}

    async def run(self):
        status='complete';problem=None;next_create=time.monotonic()
        try:
            await self.guard(initial=True)
            while len(self.handles)<self.args.max_instances:
                if self.stop.is_set():status='stopped';break
                if time.monotonic()>=self.work_deadline:status='work_deadline';break
                if time.monotonic()-self.last_guard>=10:await self.guard()
                if time.monotonic()>=self.work_deadline:status='work_deadline';break
                try:await asyncio.wait_for(self.stop.wait(),max(.001,next_create-time.monotonic()))
                except asyncio.TimeoutError:pass
                if self.stop.is_set():status='stopped';break
                if time.monotonic()>=self.work_deadline:status='work_deadline';break
                began=time.monotonic();sb=await self.create_one();next_create=began+1
                if sb is None:
                    status='creation_failed'
                    await self.inspect_pool('first_creation_failure')
                    break
                if len(self.handles)==1:
                    info=await asyncio.wait_for(AsyncSandbox.get_info(sb.sandbox_id,**self.opts),15)
                    if info.memory_mb!=4096:raise RuntimeError('template is not the intended 4 GiB environment')
                    self.write('template_check',memory_mib=info.memory_mb,cpu_count=info.cpu_count,envd_version=info.envd_version)
                if len(self.handles)%32==0:
                    mark={'resident_count':len(self.handles),'utc':utc(),'elapsed_seconds':time.monotonic()-self.started}
                    self.milestones.append(mark);self.write('milestone',**mark)
                    await self.guard();await self.inspect_pool('milestone')
            if status=='complete' and len(self.handles)%32:
                await self.inspect_pool('final_partial_milestone')
        except Exception as error:
            status='aborted';problem=self.safe_error(error);self.write('diagnostic_error',**problem)
        finally:
            cleanup=await self.cleanup()
            summary={'schema_version':1,'run_id':self.run_id,'ownership_metadata':self.metadata,
                'status':status,'started_utc':self.started_utc,'finished_utc':utc(),
                'elapsed_seconds':time.monotonic()-self.started,'max_runtime_seconds':self.args.max_runtime,
                'template':'base-4k-4g','maximum_requested_instances':self.args.max_instances,
                'create_rate_limit_per_second':1,'create_attempts':self.attempts,'created_instances':len(self.handles),
                'creation_failure':self.creation_failure,'problem':problem,'milestones':self.milestones,
                'observations':self.inspections,'cleanup':cleanup,'observation_file':str(self.observation_path),
                'scope':'idle-pool slow-ramp diagnostic with brief CPU counter commands; does not replace failed benchmark repeats',
                'limits':['The one-second guest aggregate includes observer overhead; collector PID is excluded from process rows.',
                          'Only oldest/middle/newest owned guests are sampled; other guests may behave differently.',
                          'No same-ramp control without counter probes was run by this script.']}
            self.write('completion',status=status,cleanup=cleanup)
            self.summary_file.seek(0);self.summary_file.truncate();json.dump(summary,self.summary_file,indent=2);self.summary_file.write('\n')
            self.summary_file.close();self.events.close()
        print(json.dumps({'status':status,'run_id':self.run_id,'created':len(self.handles),
                          'cleanup_ok':cleanup['verified_zero'],'output':str(self.args.output)}))
        return 0 if status=='complete' and cleanup['verified_zero'] else 1


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-instances',type=int,default=256)
    p.add_argument('--max-runtime',type=float,default=600)
    p.add_argument('--memory-floor-gib',type=float,default=128)
    a=p.parse_args()
    if not 1<=a.max_instances<=256:p.error('max instances must be 1..256')
    if not 240<=a.max_runtime<=600:p.error('runtime must be 240..600 seconds, including cleanup reserve')
    if a.memory_floor_gib<128:p.error('memory floor must be at least 128 GiB')
    return a


async def main():
    diag=IdlePool(arguments())
    for sig in (signal.SIGINT,signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig,diag.stop.set)
    return await diag.run()


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
