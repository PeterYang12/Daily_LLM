import asyncio
import json
import time

from e2b import AsyncSandbox
from runtime_common import ROOT, RUN_ID, api_options, event, list_owned, load_secrets, save


async def main():
    load_secrets()
    templates = json.loads((ROOT / 'results/initial-templates.json').read_text())
    wanted = '6g5ge4qlc9og21tzdwfz'
    template = next(t for t in templates if t['templateID'] == wanted and t['buildStatus'] == 'ready')
    result = {'target': 128, 'template_id': wanted, 'cpu_per_sandbox': template['cpuCount'],
              'memory_mb_per_sandbox': template['memoryMB'], 'created': [], 'checks': [],
              'started_unix': time.time(), 'errors': [], 'transient_errors': []}
    boxes = []
    create_slots = asyncio.Semaphore(3)
    exec_slots = asyncio.Semaphore(16)

    async def create(i):
        async with create_slots:
            start = time.monotonic()
            try:
                box = await AsyncSandbox.create(template=wanted, timeout=900, request_timeout=120,
                                                metadata={'experiment': RUN_ID, 'phase': 'capacity', 'index': str(i)},
                                                **api_options())
                boxes.append((i, box))
                # Readiness checks are idempotent; never retry arbitrary agent commands this way.
                for attempt in range(3):
                    try:
                        await box.commands.run('/bin/true', user='root', timeout=30)
                        break
                    except Exception as exc:
                        result['transient_errors'].append({'index': i, 'phase': 'readiness',
                                                           'attempt': attempt, 'error': str(exc)})
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.5 * (attempt + 1))
                record = {'index': i, 'sandbox_id': box.sandbox_id, 'seconds': time.monotonic() - start}
                result['created'].append(record)
                event('created', phase='capacity', **record)
                if len(boxes) % 16 == 0:
                    print('created', len(boxes), flush=True)
            except Exception as exc:
                result['errors'].append({'index': i, 'phase': 'create', 'error': str(exc)})
            save(ROOT / 'results/capacity-progress.json', result)

    async def check(i, box):
        async with exec_slots:
            start = time.monotonic()
            marker = f'{RUN_ID}-{i}'
            try:
                command = (f"test -d /testbed/.git && command -v tmux && "
                           f"printf '%s' '{marker}' > /tmp/ua-isolation-check && "
                           f"test \"$(cat /tmp/ua-isolation-check)\" = '{marker}' && "
                           "/opt/miniconda3/envs/testbed/bin/python -c 'import flask; print(flask.__version__)'")
                for attempt in range(3):
                    try:
                        response = await box.commands.run(command, user='root', timeout=120)
                        break
                    except Exception as exc:
                        result['transient_errors'].append({'index': i, 'phase': 'idempotent_check',
                                                           'attempt': attempt, 'error': str(exc)})
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.5 * (attempt + 1))
                result['checks'].append({'index': i, 'sandbox_id': box.sandbox_id,
                                         'exit_code': response.exit_code, 'seconds': time.monotonic()-start,
                                         'stdout': response.stdout[-300:]})
            except Exception as exc:
                result['errors'].append({'index': i, 'phase': 'exec', 'error': str(exc)})

    try:
        await asyncio.gather(*(create(i) for i in range(128)))
        live = await list_owned('capacity')
        result['observed_simultaneous_running'] = len(live)
        result['observed_ids'] = [b.sandbox_id for b in live]
        print('observed_simultaneous_running', len(live), flush=True)
        await asyncio.gather(*(check(i, b) for i, b in boxes))
        result['commands_passed'] = sum(c['exit_code'] == 0 for c in result['checks'])
        print('commands_passed', result['commands_passed'], flush=True)
    finally:
        cleanup_slots = asyncio.Semaphore(16)

        async def stop(i, box):
            async with cleanup_slots:
                try:
                    await box.kill(request_timeout=60)
                    event('destroyed', phase='capacity', sandbox_id=box.sandbox_id)
                except Exception as exc:
                    result['errors'].append({'index': i, 'phase': 'cleanup', 'error': str(exc)})

        await asyncio.gather(*(stop(i, b) for i, b in boxes))
        result['remaining'] = [b.sandbox_id for b in await list_owned('capacity')]
        result['elapsed_s'] = time.time() - result['started_unix']
        result['success'] = (result.get('observed_simultaneous_running') == 128
                             and result.get('commands_passed') == 128 and not result['remaining']
                             and not result['errors'])
        save(ROOT / 'results/capacity-128.json', result)
        print(json.dumps({k:result[k] for k in ['success', 'elapsed_s', 'remaining']}
                         | {'errors': len(result['errors'])}), flush=True)
    if not result['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
