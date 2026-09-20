import argparse
import asyncio
import json
import os
import time

from runtime_common import CURRENT, ROOT, event, load_secrets, save
from uni_agent.sandbox import SandboxConfig, build_sandbox
from uni_agent.tasks.swe_rebench.task import _clean_git_history
from uni_agent.tasks.swe_rebench.reward import compute_reward


async def main(args):
    load_secrets()
    os.environ['UA_PHASE'] = 'qualification'
    tasks = json.loads((ROOT / 'data/scheduled-tasks.json').read_text())[:args.limit]
    templates = json.loads((ROOT / 'data/reusable-templates.json').read_text())
    slots = asyncio.Semaphore(args.concurrency)
    records = {}

    async def one(task):
        meta = task['metadata']
        iid = meta['instance_id']
        path = ROOT / 'results/qualification' / iid / 'result.json'
        if path.exists():
            prior = json.loads(path.read_text())
            if prior.get('qualified'):
                records[iid] = prior
                return
        async with slots:
            start = time.time()
            record = {'instance_id': iid, 'template_id': templates[iid]['template_id'], 'qualified': False}
            token = CURRENT.set({'instance_id': iid, 'phase': 'qualification'})
            try:
                for mode in ['baseline', 'oracle']:
                    config = SandboxConfig(provider='e2b', image=record['template_id'], runtime_timeout=1200)
                    async with build_sandbox(config) as sandbox:
                        checks = await sandbox.exec_shell('command -v tmux && test -d /testbed/.git', workdir='/testbed', timeout=30)
                        record['tools_exit_code'] = checks.exit_code
                        if checks.exit_code:
                            raise RuntimeError('Template is missing tmux or /testbed/.git')
                        await _clean_git_history(sandbox, meta['base_commit'])
                        if mode == 'oracle':
                            await sandbox.write_file('/tmp/reference.patch', meta['patch'])
                            patch = await sandbox.exec(['git', 'apply', '--whitespace=fix', '/tmp/reference.patch'], workdir='/testbed', timeout=30)
                            if patch.exit_code:
                                raise RuntimeError('Reference patch cannot be applied: ' + patch.stderr[-500:])
                        score = await compute_reward(meta, sandbox, eval_timeout=600)
                        record[mode] = score
                        save(path, record)
                record['qualified'] = (not record['baseline']['resolved'] and record['oracle']['resolved']
                                       and record['baseline']['eval_report']['found_eval_status']
                                       and record['oracle']['eval_report']['found_eval_status'])
                print('qualified', iid, record['qualified'], flush=True)
            except Exception as exc:
                record['error'] = type(exc).__name__ + ': ' + str(exc)
                print('qualification_error', iid, str(exc)[:180], flush=True)
            finally:
                CURRENT.reset(token)
                record['elapsed_s'] = time.time()-start
                records[iid] = record
                save(path, record)
                save(ROOT / 'results/qualification-summary.json', {
                    'target': len(tasks), 'completed': len(records),
                    'qualified': sum(r.get('qualified', False) for r in records.values()), 'records': records})
    await asyncio.gather(*(one(task) for task in tasks))
    print('qualification_total', len(records), sum(r.get('qualified', False) for r in records.values()), flush=True)
    if not all(r.get('qualified') for r in records.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=48)
    parser.add_argument('--concurrency', type=int, default=4)
    asyncio.run(main(parser.parse_args()))
