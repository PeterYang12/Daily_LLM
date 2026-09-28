"""Cancellation-safe admission tickets for one isolated Ray run.

Tickets are unique UUIDs and never reused. A client retires a terminal ticket
only after its acquire RPC and release/cancel acknowledgement have settled.
No lease is reclaimed by elapsed time, inferred owner death, or an unknown release.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

import ray


@dataclass
class _Ticket:
    state: str
    acquire_seen: bool = False
    inflight_acquires: int = 0
    future: asyncio.Future | None = None
    granted_monotonic: float | None = None


class _TicketPool:
    def __init__(self, capacity: int, max_records: int = 65536):
        if capacity < 1 or max_records < capacity:
            raise ValueError('Admission capacity must be positive and fit the record bound')
        self.capacity = capacity; self.max_records = max_records
        self.records: dict[str, _Ticket] = {}
        self.last_capacity_progress = time.monotonic()
        self.active: set[str] = set()
        self.queued: OrderedDict[str, _Ticket] = OrderedDict()
        self.counters = {name: 0 for name in ('acquire_calls', 'grants', 'released_grants',
            'cancelled_grants', 'cancel_calls', 'release_calls', 'retired', 'unknown_releases')}

    @staticmethod
    def validate(ticket):
        if not isinstance(ticket, str) or not 1 <= len(ticket) <= 128:
            raise ValueError('Ticket must be a nonempty unique string of at most128 characters')

    def _new(self, ticket, state):
        if len(self.records) >= self.max_records:
            raise RuntimeError('Admission ticket record bound reached; inspect incomplete cleanup acknowledgements')
        record = _Ticket(state=state)
        self.records[ticket] = record
        return record

    def _grant(self, ticket, record):
        if record.state != 'queued' or ticket in self.active:
            raise RuntimeError('Invalid ticket grant transition')
        if len(self.active) >= self.capacity: raise RuntimeError('Admission capacity invariant violated')
        record.state = 'granted'; self.active.add(ticket); self.counters['grants'] += 1
        record.granted_monotonic = time.monotonic(); self.last_capacity_progress = record.granted_monotonic
        if record.future is not None and not record.future.done(): record.future.set_result(None)

    def _drain(self):
        while self.queued and len(self.active) < self.capacity:
            ticket, record = self.queued.popitem(last=False)
            if record.state == 'queued': self._grant(ticket, record)

    def reply(self, ticket, record):
        return {'ticket': ticket, 'state': record.state}

    async def acquire(self, ticket):
        self.validate(ticket); self.counters['acquire_calls'] += 1
        record = self.records.get(ticket)
        if record is None: record = self._new(ticket, 'queued')
        record.acquire_seen = True; record.inflight_acquires += 1
        try:
            if record.state == 'queued':
                if record.future is None:
                    record.future = asyncio.get_running_loop().create_future()
                    self.queued[ticket] = record
                    self._drain()
                if record.state == 'queued': await asyncio.shield(record.future)
            return self.reply(ticket, record)
        except asyncio.CancelledError:
            self.cancel(ticket)
            raise
        finally:
            record.inflight_acquires -= 1

    def cancel(self, ticket):
        self.validate(ticket); self.counters['cancel_calls'] += 1
        record = self.records.get(ticket)
        # Tombstone before acknowledging a cancel that arrives before acquire.
        if record is None: record = self._new(ticket, 'cancelled')
        elif record.state == 'granted':
            self.active.remove(ticket); self.counters['cancelled_grants'] += 1
            self.last_capacity_progress = time.monotonic()
            record.state = 'cancelled'
        elif record.state == 'queued':
            self.queued.pop(ticket, None); record.state = 'cancelled'
        if record.future is not None and not record.future.done(): record.future.set_result(None)
        self._drain()
        return self.reply(ticket, record)

    def release(self, ticket):
        self.validate(ticket); self.counters['release_calls'] += 1
        record = self.records.get(ticket)
        if record is None:
            self.counters['unknown_releases'] += 1
            return {'ticket': ticket, 'state': 'absent'}
        if record.state == 'granted':
            self.active.remove(ticket); self.counters['released_grants'] += 1
            self.last_capacity_progress = time.monotonic()
            record.state = 'released'
        elif record.state == 'queued':
            # Defensive handling; clients normally release only an acknowledged grant.
            self.queued.pop(ticket, None); record.state = 'released'
        if record.future is not None and not record.future.done(): record.future.set_result(None)
        self._drain()
        return self.reply(ticket, record)

    def retire(self, ticket):
        self.validate(ticket); record = self.records.get(ticket)
        if record is None: return {'ticket': ticket, 'state': 'absent', 'retired': True}
        if record.state not in ('cancelled', 'released'):
            return {'ticket': ticket, 'state': record.state, 'retired': False, 'reason': 'ticket_not_terminal'}
        if not record.acquire_seen or record.inflight_acquires:
            return {'ticket': ticket, 'state': record.state, 'retired': False, 'reason': 'acquire_not_settled'}
        del self.records[ticket]; self.counters['retired'] += 1
        return {'ticket': ticket, 'state': 'retired', 'retired': True}

    def snapshot(self):
        states = {name: sum(record.state == name for record in self.records.values())
                  for name in ('queued', 'granted', 'cancelled', 'released')}
        now = time.monotonic()
        ages = [now - self.records[ticket].granted_monotonic for ticket in self.active]
        consistent = ((not self.queued or len(self.active) == self.capacity) and len(self.active) <= self.capacity and states['granted'] == len(self.active)
            and states['queued'] == len(self.queued)
            and self.counters['grants'] == self.counters['released_grants'] + self.counters['cancelled_grants'] + len(self.active))
        return {'capacity': self.capacity, 'active': len(self.active), 'available': self.capacity - len(self.active),
                'queued': len(self.queued), 'records': len(self.records), 'states': states,
                'counters': dict(self.counters), 'consistent': consistent,
                'capacity_progress_counter': sum(self.counters[key] for key in ('grants', 'released_grants', 'cancelled_grants')),
                'capacity_progress_age_s': now - self.last_capacity_progress,
                'min_active_age_s': min(ages) if ages else None, 'max_active_age_s': max(ages) if ages else None}


class TicketAdmission:
    def __init__(self, creates=3, execs=16, max_records=65536):
        self._creates = _TicketPool(creates, max_records)
        self._execs = _TicketPool(execs, max_records)
        self._observer_task = None

    async def acquire_create(self, ticket): return await self._creates.acquire(ticket)
    async def cancel_create(self, ticket): return self._creates.cancel(ticket)
    async def release_create(self, ticket): return self._creates.release(ticket)
    async def retire_create(self, ticket): return self._creates.retire(ticket)
    async def acquire_exec(self, ticket): return await self._execs.acquire(ticket)
    async def cancel_exec(self, ticket): return self._execs.cancel(ticket)
    async def release_exec(self, ticket): return self._execs.release(ticket)
    async def retire_exec(self, ticket): return self._execs.retire(ticket)
    async def snapshot(self): return {'create': self._creates.snapshot(), 'exec': self._execs.snapshot()}
    async def ping(self): return True

    async def enable_observation(self, path, run_id, attempt_id, interval=5):
        if not 1 <= interval <= 60: raise ValueError('Observation interval must be1..60 seconds')
        if self._observer_task is not None: return {'enabled': True, 'already_enabled': True}
        output = Path(path); output.parent.mkdir(parents=True, exist_ok=True)
        async def publish():
            value = {'schema_version': 1, 'run_id': run_id, 'attempt_id': attempt_id,
                     'observed_unix': time.time(), 'observer_pid': os.getpid(), **(await self.snapshot())}
            temporary = output.with_name('.' + output.name + '.' + uuid.uuid4().hex + '.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(value, stream); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, output)
        await publish()
        async def observe():
            while True:
                await asyncio.sleep(interval)
                await publish()
        self._observer_task = asyncio.create_task(observe())
        return {'enabled': True, 'interval_seconds': interval}

    async def stop_observation(self):
        if self._observer_task is not None:
            self._observer_task.cancel()
            try: await self._observer_task
            except asyncio.CancelledError: pass
            self._observer_task = None
        return {'stopped': True}


Admission = ray.remote(num_cpus=0, max_concurrency=2048, max_restarts=0, max_task_retries=0)(TicketAdmission)
