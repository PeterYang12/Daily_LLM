"""Offline ownership, failure, inspection-order and guest-schema contracts."""
import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from connectrpc.code import Code
from connectrpc.errors import ConnectError

spec=importlib.util.spec_from_file_location('diagnose_idle_pool',Path(__file__).with_name('diagnose_idle_pool.py'))
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


def fake():
    d=probe.IdlePool.__new__(probe.IdlePool)
    d.args=SimpleNamespace(max_instances=256,max_runtime=600,memory_floor_gib=128,output=Path('/tmp/offline-unused.json'))
    d.cfg={'api_key':'fake/api+key'};d.opts={};d.run_id='offline';d.metadata={'purpose':probe.PURPOSE,'run_id':'offline'}
    d.started=time.monotonic();d.started_utc='2026-09-20T00:00:00Z';d.deadline=d.started+600;d.work_deadline=d.started+420
    d.stop=asyncio.Event();d.handles={};d.attempts=0;d.uncertain_until=0;d.last_guard=d.started
    d.creation_failure=None;d.inspections=[];d.milestones=[];d.observation_path=Path('/tmp/offline-observations-unused.jsonl')
    d.summary_file=io.StringIO();d.events=io.StringIO();d.recorded=[]
    d.write=lambda kind,**kw:d.recorded.append((kind,kw))
    return d


def guest():
    return {'schema_version':1,'collector_pid_excluded':999,'clock_ticks_per_second':100,'interval_seconds':1,
        'guest_logical_cpus':2,'guest_cpu_ticks_before':[0]*8,'guest_cpu_ticks_after':[1]*8,
        'guest_busy_cpu_cores':.05,'guest_busy_percent':50.,'load_average_before':[0,0,0],'load_average_after':[0,0,0],
        'continuous_processes':1,'returned_processes':1,'new_pids':[],'exited_pids':[],
        'processes':[{'pid':1,'comm':'init','state':'S','start_ticks':1,'user_ticks':1,'system_ticks':1,
                      'user_ticks_before':0,'system_ticks_before':0,'user_ticks_delta':1,'system_ticks_delta':1,'cpu_cores':.02}],
        'process_rows_truncated':False}


class Contracts(unittest.IsolatedAsyncioTestCase):
    def test_oldest_middle_newest_are_unique_and_ordered(self):
        handles={str(i):i for i in range(32)}
        self.assertEqual(probe.select_guests(handles),[('oldest',0,0),('middle',15,15),('newest',31,31)])
        self.assertEqual(probe.select_guests({'one':1}),[('oldest',0,1)])

    def test_no_process_args_environment_or_collector_can_escape(self):
        probe.validate_guest_result(guest())
        d=guest();d['processes'][0]['args']='forbidden'
        with self.assertRaises(ValueError):probe.validate_guest_result(d)
        d=guest();d['processes'][0]['pid']=999
        with self.assertRaises(ValueError):probe.validate_guest_result(d)
        d=guest();d['environment']={'secret':'forbidden'}
        with self.assertRaises(ValueError):probe.validate_guest_result(d)

    async def test_failed_create_is_not_retried_and_reserves_150_seconds(self):
        d=fake();error=ConnectError(Code.UNAVAILABLE,'lost response fake/api+key')
        with patch.object(probe.AsyncSandbox,'create',new=AsyncMock(side_effect=error)) as create:
            self.assertIsNone(await d.create_one())
        create.assert_awaited_once();self.assertEqual(d.attempts,1)
        self.assertGreater(d.uncertain_until-time.monotonic(),149)
        self.assertEqual(d.creation_failure['rpc_code'],'UNAVAILABLE')
        self.assertNotIn('fake/api+key',str(d.recorded))

    async def test_first_creation_failure_inspects_before_cleanup(self):
        d=fake();order=[]
        d.guard=AsyncMock()
        async def fail():
            d.attempts+=1;order.append('create');return None
        async def inspect(reason):order.append('inspect:'+reason)
        async def cleanup():order.append('cleanup');return {'verified_zero':True,'status':'verified_zero'}
        d.create_one=fail;d.inspect_pool=inspect;d.cleanup=cleanup
        with redirect_stdout(io.StringIO()):result=await d.run()
        self.assertEqual(result,1)
        self.assertEqual(order,['create','inspect:first_creation_failure','cleanup'])

    async def test_cleanup_deletes_only_exact_owned_metadata(self):
        d=fake();d.attempts=1
        own=SimpleNamespace(sandbox_id='owned',metadata={'purpose':probe.PURPOSE,'run_id':'offline'})
        other=SimpleNamespace(sandbox_id='other',metadata={'purpose':probe.PURPOSE,'run_id':'different'})
        d.list_all=AsyncMock(side_effect=[[own,other],[other],[other]])
        with patch.object(probe.AsyncSandbox,'kill',new=AsyncMock()) as kill,patch.object(probe.asyncio,'sleep',new=AsyncMock()):
            result=await d.cleanup()
        self.assertTrue(result['verified_zero']);kill.assert_awaited_once_with('owned')

    async def test_initial_foreign_guard_stops_before_memory_or_create(self):
        d=fake();foreign=SimpleNamespace(sandbox_id='foreign',metadata={})
        d.list_all=AsyncMock(return_value=[foreign])
        with patch.object(probe.asyncio,'create_subprocess_exec',new=AsyncMock()) as proc:
            with self.assertRaises(RuntimeError):await d.guard(initial=True)
        proc.assert_not_awaited();self.assertEqual(d.attempts,0)


if __name__=='__main__':
    unittest.main()
