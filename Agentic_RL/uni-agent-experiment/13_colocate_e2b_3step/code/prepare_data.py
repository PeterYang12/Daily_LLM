import copy
import hashlib
import json

from datasets import Dataset
from transformers import AutoTokenizer
from runtime_common import ROOT, save


def main():
    source = Dataset.from_parquet(str(ROOT / 'data/recommended-train-original.parquet'))
    rows = []
    tokenizer = AutoTokenizer.from_pretrained('/work/models/Qwen3-Coder-30B-A3B-Instruct')
    for row in source:
        task = row['extra_info']['tools_kwargs']['task']
        metadata = copy.deepcopy(task['metadata'])
        iid = metadata['instance_id']
        prompt = [{'role': 'user', 'content': metadata['problem_statement']}]
        rows.append({'data_source': 'nebius/SWE-rebench', 'prompt': prompt,
                     'extra_info': {'tools_kwargs': {'task': {
                         'name': 'swe_rebench', 'metadata': metadata,
                         'sandbox': {'image': 'swerebench/sweb.eval.x86_64.' + iid.lower().replace('__', '_1776_')}
                     }}}})
    previous = json.loads((ROOT / 'data/previous-templates.json').read_text())
    available = {t['templateID']: t for t in json.loads((ROOT / 'results/initial-templates.json').read_text())}
    reused = {p['instance_id']: p for p in previous
              if p.get('qualified') and p['template_id'] in available
              and available[p['template_id']]['buildStatus'] == 'ready'}
    # A fresh-image import currently fails server-side. Preserve every official row,
    # but exercise already-qualified training environments first for this bounded smoke run.
    rows.sort(key=lambda r: r['extra_info']['tools_kwargs']['task']['metadata']['instance_id'] not in reused)
    dataset = Dataset.from_list(rows)
    path = ROOT / 'data/train.parquet'
    dataset.to_parquet(str(path))
    assert len(dataset) == 1150
    # Match RLHFDataset source-prompt filtering and StatefulDataLoader sampler below.
    eligible = []
    for i, row in enumerate(rows):
        tokens = tokenizer.apply_chat_template(row['prompt'], add_generation_prompt=True)
        if len(tokens) <= 8192:
            eligible.append(i)
    scheduled = [rows[i]['extra_info']['tools_kwargs']['task'] for i in eligible[:48]]
    assert all(t['metadata']['instance_id'] in reused for t in scheduled)
    save(ROOT / 'data/scheduled-tasks.json', scheduled)
    save(ROOT / 'data/reusable-templates.json', reused)
    save(ROOT / 'results/data-preparation.json', {
        'rows': len(rows), 'source_prompt_filter_eligible': len(eligible), 'seed': 20260920,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'scheduled_preview_count': len(scheduled),
        'scheduled_ids': [t['metadata']['instance_id'] for t in scheduled],
        'reusable_scheduled': [t['metadata']['instance_id'] for t in scheduled if t['metadata']['instance_id'] in reused],
        'shuffle': False, 'warmup_batches': 0,
        'note': 'All 1150 official rows retained. Ready environment tasks ordered first due to E2B fresh-image build failures. Three steps consume 48 prompt groups without async warmup prefetch; no model-success filtering.'})
    print(json.dumps({'rows': len(rows), 'eligible': len(eligible), 'scheduled': len(scheduled),
                      'reusable': sum(t['metadata']['instance_id'] in reused for t in scheduled)}), flush=True)


if __name__ == '__main__':
    main()
