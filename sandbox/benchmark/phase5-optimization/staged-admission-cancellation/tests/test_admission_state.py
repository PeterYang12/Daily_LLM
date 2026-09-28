"""Pure asyncio ticket invariants; real-Ray cancellation tests are separate."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'after/admission.py'
spec = importlib.util.spec_from_file_location('ticket_admission_unit', PATH)
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
with patch.dict(sys.modules, {'ray': SimpleNamespace(remote=lambda **kwargs: lambda cls: cls)}):
    spec.loader.exec_module(module)


class AdmissionStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_cancel_cannot_consume_next_release(self):
        gate = module.TicketAdmission(1, 1)
        self.assertEqual((await gate.acquire_exec('holder'))['state'], 'granted')
        waiter = asyncio.create_task(gate.acquire_exec('waiter')); await asyncio.sleep(0)
        self.assertEqual((await gate.snapshot())['exec']['queued'], 1)
        await gate.cancel_exec('waiter')
        self.assertEqual((await waiter)['state'], 'cancelled')
        await gate.retire_exec('waiter'); await gate.release_exec('holder'); await gate.retire_exec('holder')
        self.assertEqual((await gate.acquire_exec('next'))['state'], 'granted')
        await gate.release_exec('next'); await gate.retire_exec('next')
        state = (await gate.snapshot())['exec']
        self.assertEqual((state['active'], state['queued'], state['records']), (0, 0, 0))
        self.assertTrue(state['consistent'])

    async def test_cancel_before_acquire_tombstones_late_arrival(self):
        gate = module.TicketAdmission(1, 1)
        await gate.cancel_create('late')
        self.assertFalse((await gate.retire_create('late'))['retired'])
        self.assertEqual((await gate.acquire_create('late'))['state'], 'cancelled')
        self.assertTrue((await gate.retire_create('late'))['retired'])
        state = (await gate.snapshot())['create']
        self.assertEqual(state['counters']['grants'], 0); self.assertEqual(state['available'], 1)

    async def test_cancel_after_grant_and_double_release_cannot_add_capacity(self):
        gate = module.TicketAdmission(1, 1)
        await gate.acquire_exec('one'); await gate.cancel_exec('one')
        await gate.release_exec('one'); await gate.release_exec('one'); await gate.release_exec('unknown')
        await gate.retire_exec('one'); await gate.acquire_exec('two')
        waiter = asyncio.create_task(gate.acquire_exec('three')); await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        state = (await gate.snapshot())['exec']; self.assertEqual(state['active'], 1); self.assertEqual(state['available'], 0)
        await gate.cancel_exec('three'); await waiter; await gate.retire_exec('three')
        await gate.release_exec('two'); await gate.retire_exec('two')
        self.assertTrue((await gate.snapshot())['exec']['consistent'])

    async def test_grant_cancel_race_returns_current_terminal_state(self):
        gate = module.TicketAdmission(1, 1)
        await gate.acquire_exec('holder')
        waiter = asyncio.create_task(gate.acquire_exec('race')); await asyncio.sleep(0)
        await gate.release_exec('holder'); await gate.cancel_exec('race')
        self.assertEqual((await waiter)['state'], 'cancelled')
        state = (await gate.snapshot())['exec']
        self.assertEqual(state['available'], 1); self.assertTrue(state['consistent'])

    async def test_duplicate_waits_share_ticket_and_retire_waits_for_settlement(self):
        gate = module.TicketAdmission(1, 1)
        await gate.acquire_create('holder')
        first = asyncio.create_task(gate.acquire_create('same'))
        second = asyncio.create_task(gate.acquire_create('same')); await asyncio.sleep(0)
        await gate.cancel_create('same')
        self.assertFalse((await gate.retire_create('same'))['retired'])
        results = await asyncio.gather(first, second)
        self.assertTrue(all(row['state'] == 'cancelled' for row in results))
        self.assertTrue((await gate.retire_create('same'))['retired'])

    async def test_explicit_server_task_cancel_removes_queued_ticket(self):
        gate = module.TicketAdmission(1, 1)
        await gate.acquire_exec('holder')
        waiter = asyncio.create_task(gate.acquire_exec('cancel-server-call')); await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        state = (await gate.snapshot())['exec']
        self.assertEqual(state['queued'], 0); self.assertEqual(state['states']['cancelled'], 1)
        await gate.retire_exec('cancel-server-call')

    async def test_normal_and_cancelled_retirement_bounds_records_over_many_calls(self):
        gate = module.TicketAdmission(1, 1, max_records=4)
        for index in range(300):
            ticket = str(index)
            if index % 2:
                await gate.cancel_exec(ticket)
                self.assertEqual((await gate.acquire_exec(ticket))['state'], 'cancelled')
            else:
                await gate.acquire_exec(ticket); await gate.release_exec(ticket)
            self.assertTrue((await gate.retire_exec(ticket))['retired'])
        state = (await gate.snapshot())['exec']
        self.assertEqual(state['records'], 0); self.assertTrue(state['consistent'])

    async def test_missing_cancellation_tombstone_is_never_silently_acknowledged_at_record_bound(self):
        gate = module.TicketAdmission(1, 1, max_records=1)
        await gate.cancel_exec('first')
        with self.assertRaises(RuntimeError): await gate.cancel_exec('second')
        self.assertEqual((await gate.snapshot())['exec']['records'], 1)


if __name__ == '__main__': unittest.main()
