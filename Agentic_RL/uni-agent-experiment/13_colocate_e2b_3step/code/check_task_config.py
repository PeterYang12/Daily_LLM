import asyncio
import json
from datasets import Dataset
from runtime_common import CURRENT, ROOT, save
from uni_agent.tasks import TaskConfigResolver, get_task


async def main():
    resolver = TaskConfigResolver.from_file(str(ROOT/'configs/task_config.yaml'))
    rows = Dataset.from_parquet(str(ROOT/'data/train.parquet'))
    expected = json.loads((ROOT/'data/reusable-templates.json').read_text())
    checked = []
    token = CURRENT.set({'phase': 'config-check'})
    try:
        for row in rows.select(range(48)):
            sample = dict(row['extra_info']['tools_kwargs']['task'])
            sample['prompt'] = row['prompt']
            resolved = resolver.resolve(sample, runtime_model={'base_url': 'http://127.0.0.1:1/v1',
                                         'model_name': 'Qwen3-Coder-30B-A3B-Instruct'})
            task = get_task(resolved)
            box = task.build_sandbox()
            iid = sample['metadata']['instance_id']
            assert box.template == expected[iid]['template_id'], iid
            assert box.runtime_timeout == 3600
            checked.append({'instance_id': iid, 'template_id': box.template})
            if len(checked) == 1:
                async with box:
                    output = await box.exec_shell('tmux -V; test -d /testbed/.git', timeout=30)
                    assert output.exit_code == 0
                    print('resolved_config_sandbox_smoke', output.stdout.strip(), flush=True)
        save(ROOT/'results/task-config-check.json', {'success': True, 'n': len(checked), 'rows': checked})
        print('task_configuration_checked', len(checked), flush=True)
    finally:
        CURRENT.reset(token)


if __name__ == '__main__':
    asyncio.run(main())
