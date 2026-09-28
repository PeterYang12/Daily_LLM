#!/usr/bin/env python3
"""Isolated real-Ray cancellation regression; SDK calls are replaced by in-memory fakes."""
import argparse,asyncio,hashlib,importlib.util,json,os,sys,time,types,uuid
from pathlib import Path
import ray


@ray.remote(num_cpus=0,max_concurrency=64,max_restarts=0,max_task_retries=0)
class Legacy:
    def __init__(self):self.sem=asyncio.Semaphore(1);self.grants=0;self.releases=0;self.waiters=0
    async def acquire(self):
        self.waiters+=1
        await self.sem.acquire();self.waiters-=1;self.grants+=1
        return True
    async def release(self):self.releases+=1;self.sem.release()
    async def snapshot(self):return {'available':self.sem._value,'grants':self.grants,'releases':self.releases,'waiters':self.waiters}


async def await_ref(reference):return await reference


async def until(check,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=await check()
        if result:return result
        await asyncio.sleep(.02)
    raise AssertionError('Condition did not become true')


async def cancelled(task):
    try:await asyncio.wait_for(asyncio.shield(task),5)
    except asyncio.CancelledError:return
    raise AssertionError('Expected caller cancellation')


async def legacy_case():
    gate=Legacy.remote()
    try:
        await gate.acquire.remote()
        waiting=asyncio.create_task(await_ref(gate.acquire.remote()))
        async def queued():return (await gate.snapshot.remote())['waiters']==1
        await until(queued);waiting.cancel();await cancelled(waiting)
        await gate.release.remote()
        async def orphan_granted():return (await gate.snapshot.remote())['grants']==2
        await until(orphan_granted)
        fresh=asyncio.create_task(await_ref(gate.acquire.remote()))
        timed_out=False
        try:await asyncio.wait_for(fresh,.3)
        except asyncio.TimeoutError:timed_out=True
        state=await gate.snapshot.remote()
        assert timed_out and state['available']==0 and state['grants']-state['releases']==1,state
        return {'case':'legacy_cancelled_waiter_loses_permit','passed':True,'fresh_waiter_timed_out':timed_out,'snapshot':state}
    finally:ray.kill(gate,no_restart=True)


def load_candidate(directory):
    sys.path.insert(0,str(directory))
    from admission import Admission,TicketAdmission
    path=directory/'e2b_provider.py';spec=importlib.util.spec_from_file_location('candidate_provider',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.load_secrets=lambda:None;module.api_options=lambda:{}
    class Exit(Exception):
        def __init__(self):self.exit_code=7;self.stdout='expected';self.stderr='nonzero'
    module.CommandExitException=Exit
    class Client:
        sandbox_id='FAKE-NO-E2B'
        def __init__(self):self.commands=self;self.calls=[];self.started=asyncio.Event();self.killed=False
        async def run(self,command,**kwargs):
            self.calls.append(command);self.started.set()
            if command=='raise':raise ValueError('injected SDK failure')
            if command=='nonzero':raise Exit()
            if command=='block':await asyncio.Event().wait()
            if command=='delay':await asyncio.sleep(.01)
            if command=='/bin/true':await asyncio.sleep(.01)
            return types.SimpleNamespace(exit_code=0,stdout='tmux 3.3a',stderr='')
        async def kill(self,**kwargs):self.killed=True
    class Service:
        calls=0;fail=False
        @classmethod
        async def create(cls,**kwargs):
            cls.calls+=1
            if cls.fail:raise ValueError('injected create failure')
            return Client()
    module.AsyncSandbox=Service
    return module,Admission,TicketAdmission,Client,Service


async def fixed_cases(directory):
    module,Admission,TicketAdmission,Client,Service=load_candidate(directory)
    @ray.remote(num_cpus=0,max_concurrency=2048,max_restarts=0,max_task_retries=0)
    class Delayed(TicketAdmission):
        def __init__(self,cancel_delay=0,release_delay=0):
            super().__init__(creates=1,execs=1);self.cancel_delay=cancel_delay;self.release_delay=release_delay
            self.cancel_entered=False;self.release_entered=False
        async def cancel_exec(self,ticket):
            self.cancel_entered=True;await asyncio.sleep(self.cancel_delay)
            return await super().cancel_exec(ticket)
        async def release_exec(self,ticket):
            self.release_entered=True;await asyncio.sleep(self.release_delay)
            return await super().release_exec(ticket)
        async def flags(self):return {'cancel':self.cancel_entered,'release':self.release_entered}
    @ray.remote(num_cpus=0,max_concurrency=64,max_restarts=0,max_task_retries=0)
    class Broken(TicketAdmission):
        async def acquire_exec(self,ticket):raise ValueError('injected acquire RPC failure')
    def provider(gate,client=True):
        token=module.CURRENT.set({'phase':'isolated-cpu-regression','session_id':'test-'+uuid.uuid4().hex})
        try:p=module.E2BSandbox('FAKE-NO-E2B',60)
        finally:module.CURRENT.reset(token)
        p.gate=gate
        if client:p.client=Client()
        return p
    async def clean(gate,kind='exec',records=0):
        state=(await gate.snapshot.remote())[kind]
        assert state['consistent'] and state['active']==0 and state['available']==state['capacity'] and state['queued']==0,state
        assert state['records']==records,state
        return state
    results=[]
    # Cancellation while queued must cancel and settle the remote acquisition before retirement.
    for kind in ('create','exec'):
        gate=Admission.remote(creates=1,execs=1)
        try:
            held='held-'+uuid.uuid4().hex;await getattr(gate,'acquire_'+kind).remote(held)
            p=provider(gate,client=kind=='exec');before=Service.calls
            task=asyncio.create_task(p.start() if kind=='create' else p._exec(['ok']))
            async def queued():return (await gate.snapshot.remote())[kind]['queued']==1
            await until(queued);task.cancel();await cancelled(task)
            assert (Service.calls==before if kind=='create' else not p.client.calls)
            await getattr(gate,'release_'+kind).remote(held);await getattr(gate,'retire_'+kind).remote(held)
            state=await clean(gate,kind)
            fresh=provider(gate,client=kind=='exec')
            await asyncio.wait_for(fresh.start() if kind=='create' else fresh._exec(['ok']),5)
            if kind=='create':await fresh.stop()
            results.append({'case':kind+'_queued_cancellation','passed':True,'snapshot':await clean(gate,kind)})
        finally:ray.kill(gate,no_restart=True)
    # Grant the ticket during a deliberately delayed cancel, then cancel the local cleanup twice.
    gate=Delayed.remote(cancel_delay=.2)
    try:
        held='held-'+uuid.uuid4().hex;await gate.acquire_exec.remote(held)
        p=provider(gate);task=asyncio.create_task(p._exec(['ok']))
        async def queued():return (await gate.snapshot.remote())['exec']['queued']==1
        await until(queued);task.cancel()
        async def cancel_entered():return (await gate.flags.remote())['cancel']
        await until(cancel_entered);await gate.release_exec.remote(held);await gate.retire_exec.remote(held)
        task.cancel();await cancelled(task)
        assert not p.client.calls
        state=await clean(gate);assert state['counters']['cancelled_grants']==1,state
        await asyncio.wait_for(provider(gate)._exec(['ok']),5)
        results.append({'case':'grant_during_cancel_and_repeated_local_cancel','passed':True,'snapshot':await clean(gate)})
    finally:ray.kill(gate,no_restart=True)
    gate=Delayed.remote(release_delay=.2)
    try:
        p=provider(gate);task=asyncio.create_task(p._exec(['ok']))
        async def release_entered():return (await gate.flags.remote())['release']
        await until(release_entered);task.cancel();await asyncio.sleep(.03);task.cancel();await cancelled(task)
        assert p.client.calls==['ok']
        results.append({'case':'cancel_during_release_ack','passed':True,'snapshot':await clean(gate)})
    finally:ray.kill(gate,no_restart=True)
    gate=Admission.remote(creates=1,execs=1)
    try:
        ticket='early-'+uuid.uuid4().hex
        await gate.cancel_exec.remote(ticket);refused=await gate.retire_exec.remote(ticket)
        assert refused['retired'] is False
        assert (await gate.acquire_exec.remote(ticket))['state']=='cancelled'
        assert (await gate.retire_exec.remote(ticket))['retired'] is True
        ticket='double-'+uuid.uuid4().hex;await gate.acquire_exec.remote(ticket)
        await gate.release_exec.remote(ticket);await gate.release_exec.remote(ticket);await gate.cancel_exec.remote(ticket)
        await gate.retire_exec.remote(ticket);await gate.release_exec.remote(ticket)
        results.append({'case':'early_cancel_and_duplicate_unknown_release','passed':True,'snapshot':await clean(gate)})
    finally:ray.kill(gate,no_restart=True)
    gate=Admission.remote(creates=1,execs=1)
    try:
        p=provider(gate)
        try:await p._exec(['raise'])
        except ValueError:pass
        else:raise AssertionError('SDK failure did not propagate')
        assert (await p._exec(['nonzero'])).exit_code==7
        p=provider(gate);task=asyncio.create_task(p._exec(['block']));await p.client.started.wait();task.cancel();await cancelled(task)
        Service.fail=True
        try:
            try:await provider(gate,client=False).start()
            except ValueError:pass
            else:raise AssertionError('Create failure did not propagate')
        finally:Service.fail=False
        results.append({'case':'sdk_failure_nonzero_inflight_cancel_and_create_failure','passed':True,
                        'exec_snapshot':await clean(gate),'create_snapshot':await clean(gate,'create')})
    finally:ray.kill(gate,no_restart=True)
    gate=Broken.remote(creates=1,execs=1)
    try:
        try:await provider(gate)._exec(['ok'])
        except Exception:pass
        else:raise AssertionError('Acquire failure did not propagate')
        state=await clean(gate,records=1)
        assert state['states']['cancelled']==1
        results.append({'case':'failed_acquire_keeps_protective_tombstone','passed':True,'snapshot':state})
    finally:ray.kill(gate,no_restart=True)
    for kind,capacity in (('create',3),('exec',16)):
        gate=Admission.remote(creates=3,execs=16)
        try:
            held=['storm-'+uuid.uuid4().hex for _ in range(capacity)]
            for ticket in held:await getattr(gate,'acquire_'+kind).remote(ticket)
            providers=[provider(gate,client=kind=='exec') for _ in range(256)]
            tasks=[asyncio.create_task(p.start() if kind=='create' else p._exec(['delay'])) for p in providers]
            async def all_queued():return (await gate.snapshot.remote())[kind]['queued']==256
            await until(all_queued)
            for task in tasks[::2]:task.cancel()
            for ticket in held:await getattr(gate,'release_'+kind).remote(ticket)
            for ticket in held:await getattr(gate,'retire_'+kind).remote(ticket)
            await asyncio.sleep(.02)
            for task in tasks[1::4]:task.cancel()
            values=await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),30)
            def successful(value):return value is None if kind=='create' else getattr(value,'exit_code',None)==0
            assert all(isinstance(value,asyncio.CancelledError) or successful(value) for value in values)
            if kind=='exec':assert all(len(p.client.calls)<=1 for p in providers)
            else:await asyncio.gather(*(p.stop() for p in providers if p.client is not None))
            fresh=provider(gate,client=kind=='exec')
            await asyncio.wait_for(fresh.start() if kind=='create' else fresh._exec(['ok']),5)
            if kind=='create':await fresh.stop()
            results.append({'case':kind+'_mixed256_waiter_cancellation_storm','passed':True,
                            'logical_waiters':256,'cancelled_callers':sum(isinstance(v,asyncio.CancelledError) for v in values),
                            'completed_callers':sum(successful(v) for v in values),
                            'create_snapshot':await clean(gate,'create'),'exec_snapshot':await clean(gate,'exec')})
        finally:ray.kill(gate,no_restart=True)
    gate=Admission.remote(creates=3,execs=16)
    try:
        path=directory.parent/('observation-'+uuid.uuid4().hex+'.json')
        await gate.enable_observation.remote(str(path),'probe-run','probe-attempt',1)
        initial=json.loads(path.read_text())
        assert initial['run_id']=='probe-run' and initial['attempt_id']=='probe-attempt'
        ticket='observed-'+uuid.uuid4().hex;await gate.acquire_exec.remote(ticket)
        async def observed_active():
            row=json.loads(path.read_text())
            return row if row['exec']['active']==1 and row['exec']['min_active_age_s']>0 else False
        observed=await until(observed_active)
        assert observed['create']['consistent'] and observed['exec']['consistent']
        await gate.stop_observation.remote();stopped_bytes=path.read_bytes()
        await asyncio.sleep(1.2);assert path.read_bytes()==stopped_bytes
        await gate.release_exec.remote(ticket);await gate.retire_exec.remote(ticket)
        results.append({'case':'observation_owner_freshness_lease_age_and_stop','passed':True,
                        'observed_active_age_s':observed['exec']['min_active_age_s'],
                        'writes_stopped':True,'create_snapshot':await clean(gate,'create'),'exec_snapshot':await clean(gate,'exec')})
    finally:ray.kill(gate,no_restart=True)
    return results


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--candidate-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    record={'started_unix':time.time(),'scope':'Isolated real-Ray CPU-only regression; no E2B service or GPU calls',
            'ray_version':ray.__version__,'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (args.candidate_dir/'admission.py',args.candidate_dir/'e2b_provider.py',Path(__file__))},'results':[]}
    ray.init(address='local',namespace='lease-regression-'+uuid.uuid4().hex,num_cpus=2,num_gpus=0,
             include_dashboard=False,object_store_memory=268435456,_node_ip_address='127.0.0.1',
             _temp_dir='/tmp/lease-regression-'+uuid.uuid4().hex[:10],log_to_driver=False)
    try:
        record['results'].append(asyncio.run(legacy_case()))
        (args.output/'progress.json').write_text(json.dumps(record,indent=2)+'\n')
        record['results'].extend(asyncio.run(fixed_cases(args.candidate_dir)))
        record['success']=all(row['passed'] for row in record['results'])
    except BaseException as exc:
        record.update(success=False,error_type=type(exc).__name__,error=str(exc));raise
    finally:
        ray.shutdown();record.update(ended_unix=time.time(),torch_imported='torch' in sys.modules)
        (args.output/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps({'success':record.get('success'),'tests':len(record['results']),
                          'torch_imported':record['torch_imported'],'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
