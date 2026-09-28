#!/usr/bin/env python3
"""Eight bounded owned E2B integration cases; no model or GPU work."""
import asyncio,hashlib,importlib.metadata,json,os,sys,time,uuid
from pathlib import Path
import ray
from e2b import AsyncSandbox
from admission import Admission
from e2b_provider import E2BSandbox
from runtime_common import ROOT,RUN_ID,CURRENT,api_options,list_owned,save
from uni_agent.tools import Toolbox


async def run():
    cohort='provider-smoke-'+uuid.uuid4().hex[:12];phase='r2-provider-smoke'
    before=await list_owned()
    if before:raise RuntimeError('R2 already has owned instances; smoke refuses to overlap or clean other phases')
    gate=Admission.remote(creates=3,execs=16)
    await gate.enable_observation.remote(str(ROOT/'results/provider-smoke-admission.json'),RUN_ID,cohort,1)
    result={'schema_version':1,'run_id':RUN_ID,'phase':phase,'cohort':cohort,'target':8,
            'sdk_version':importlib.metadata.version('e2b'),'started_unix':time.time(),'cases':[],'cleanup':[],
            'scope':'Real SDK2.50/provider/ticket actor plus file and persistent-shell state; no LLM/GPU/reward controls',
            'source_sha256':{name:hashlib.sha256((ROOT/'code'/name).read_bytes()).hexdigest() for name in ('e2b_provider.py','admission.py')}}
    templates=[('n5bqkdf66o7jkwsbuv4e','jpadilla__pyjwt-623'),('6g5ge4qlc9og21tzdwfz','pallets__flask-5014')]*4
    async def one(index,template,instance):
        token=CURRENT.set({'phase':phase,'cohort':cohort,'session_id':f'{cohort}-{index}',
                           'instance_id':instance,'supervisor_attempt_id':cohort})
        started=time.monotonic();stage='startup'
        row={'index':index,'template_id':template,'instance_id':instance,'success':False}
        try:
            sandbox=E2BSandbox(template,900);sandbox.gate=gate
            async with sandbox.entered(retry=1):
                row['sandbox_id']=sandbox.client.sandbox_id;row['startup_s']=time.monotonic()-started
                stage='exec'
                command=await sandbox.exec(['bash','-c',"test -d /testbed/.git && printf 'smoke-ok'"],workdir='/testbed',timeout=30)
                assert command.exit_code==0 and command.stdout=='smoke-ok';row['exec_ok']=True
                stage='file_roundtrip';payload=f'{cohort}-payload-{index}'.encode();path=f'/tmp/{cohort}-{index}.txt'
                await sandbox.write_file(path,payload);observed=await sandbox.read_file(path)
                assert observed==payload;row['file_roundtrip_ok']=True;row['payload_sha256']=hashlib.sha256(payload).hexdigest()
                stage='persistent_shell';tools=Toolbox.from_specs([{'name':'stateful_shell','command_timeout':30}],sandbox=sandbox)
                async with tools.entered(retry=1,timeout=30):
                    marker=f'{cohort}-state-{index}'
                    first=await tools.call('shell',{'command':f'cd /tmp; export UA_GATE_SMOKE={marker}'},timeout=45)
                    second=await tools.call('shell',{'command':'printf "%s\\n" "$UA_GATE_SMOKE"; pwd'},timeout=45)
                    assert first.status=='ok' and second.status=='ok' and marker in second.text and '/tmp' in second.text
                    row['persistent_shell_ok']=True
            row['success']=True
        except BaseException as exc:
            row.update(error_type=type(exc).__name__,failed_stage=stage)
            if isinstance(exc,asyncio.CancelledError):raise
        finally:
            row['elapsed_s']=time.monotonic()-started;result['cases'].append(row);CURRENT.reset(token)
    try:
        await asyncio.gather(*(one(index,*entry) for index,entry in enumerate(templates)))
    finally:
        for attempt in range(5):
            boxes=[b for b in await list_owned(phase) if (b.metadata or {}).get('cohort')==cohort]
            if not boxes:break
            options=api_options();options.pop('sandbox_url',None)
            for box in boxes:
                try:
                    await AsyncSandbox.kill(box.sandbox_id,request_timeout=45,**options)
                    result['cleanup'].append({'sandbox_id':box.sandbox_id,'success':True,'attempt':attempt+1})
                except Exception as exc:result['cleanup'].append({'sandbox_id':box.sandbox_id,'success':False,'error_type':type(exc).__name__})
            await asyncio.sleep(1)
        result['remaining_phase_ids']=[b.sandbox_id for b in await list_owned(phase) if (b.metadata or {}).get('cohort')==cohort]
        result['remaining_run_ids']=[b.sandbox_id for b in await list_owned()]
        result['admission_after']=await gate.snapshot.remote()
        await gate.stop_observation.remote()
        ledger=ROOT/'results/gate-ticket-events.jsonl';tickets={}
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                event=json.loads(line)
                if event.get('phase')==phase and event.get('session_id','').startswith(cohort+'-'):
                    tickets.setdefault(event['ticket'],[]).append(event['event'])
        result['ticket_audit']={'tickets':len(tickets),'requested':sum('requested' in events for events in tickets.values()),
            'retired':sum('retired' in events for events in tickets.values()),
            'unretired_tickets':[ticket for ticket,events in tickets.items() if 'retired' not in events]}
        result['success']=(len(result['cases'])==8 and all(row['success'] for row in result['cases']) and
            not result['remaining_run_ids'] and not result['remaining_phase_ids'] and
            not result['ticket_audit']['unretired_tickets'] and all(row['consistent'] and row['active']==0 and row['queued']==0 and
                row['available']==row['capacity'] and row['records']==0 for row in result['admission_after'].values()))
        result.update(ended_unix=time.time(),torch_imported='torch' in sys.modules)
        save(ROOT/'results/provider-smoke.json',result)
        print(json.dumps({'success':result['success'],'cases':len(result['cases']),
            'passed':sum(row['success'] for row in result['cases']),'remaining_run_ids':result['remaining_run_ids'],
            'ticket_audit':result['ticket_audit'],'torch_imported':result['torch_imported']}),flush=True)
    return 0 if result['success'] else 1


if __name__=='__main__':
    ray.init(address='local',namespace='r2-provider-smoke-'+uuid.uuid4().hex,num_cpus=2,num_gpus=0,
             include_dashboard=False,object_store_memory=268435456,_node_ip_address='127.0.0.1',
             _temp_dir='/tmp/r2-provider-smoke-'+uuid.uuid4().hex[:10],log_to_driver=False)
    try:code=asyncio.run(run())
    finally:ray.shutdown()
    raise SystemExit(code)
