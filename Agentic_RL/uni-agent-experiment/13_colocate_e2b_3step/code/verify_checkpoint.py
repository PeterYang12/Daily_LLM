import json
import math
import time
from pathlib import Path
from safetensors import safe_open
from runtime_common import ROOT, save


def weight_map(folder):
    index = folder/'model.safetensors.index.json'
    if index.exists():
        return json.loads(index.read_text())['weight_map']
    result = {}
    for path in folder.glob('*.safetensors'):
        with safe_open(path, framework='pt', device='cpu') as f:
            result.update({key: path.name for key in f.keys()})
    return result


def main():
    original = Path('/work/models/Qwen3-Coder-30B-A3B-Instruct')
    checkpoint = ROOT/'checkpoints/global_step_3/actor/model/huggingface'
    final_map, original_map = weight_map(checkpoint), weight_map(original)
    assert final_map and set(final_map) == set(original_map), 'HF weight keys must match the base model'
    missing = [name for name in set(final_map.values()) if not (checkpoint/name).is_file()]
    assert not missing, missing
    compared = []
    for key in [k for k in final_map if 'self_attn.q_proj.weight' in k][:4]:
        with safe_open(checkpoint/final_map[key], framework='pt', device='cpu') as f:
            after = f.get_tensor(key).float()
        with safe_open(original/original_map[key], framework='pt', device='cpu') as f:
            before = f.get_tensor(key).float()
        compared.append({'parameter': key, 'elements': after.numel(), 'changed': int((after != before).sum()),
                         'max_abs_delta': float((after-before).abs().max())})
    assert compared and sum(r['changed'] for r in compared) > 0
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(checkpoint)
    prompt = tokenizer.apply_chat_template([{'role': 'user', 'content':
        'Write a Python function add(a, b) that returns their sum. Return only the function.'}],
        tokenize=False, add_generation_prompt=True)
    llm = LLM(model=str(checkpoint), tensor_parallel_size=2, dtype='bfloat16',
              max_model_len=4096, max_num_seqs=2, gpu_memory_utilization=.35,
              enforce_eager=True, disable_custom_all_reduce=True, logprobs_mode='processed_logprobs')
    output = llm.generate([prompt], SamplingParams(temperature=0, max_tokens=128, logprobs=1))[0].outputs[0]
    finite = bool(output.logprobs) and all(math.isfinite(item.logprob)
                 for token in output.logprobs for item in token.values())
    result = {'success': bool(output.text.strip()) and bool(output.token_ids) and finite,
              'checkpoint': str(checkpoint), 'weight_key_count': len(final_map),
              'compared_to_original': compared, 'fresh_vllm_load': True,
              'elapsed_s': time.monotonic()-started, 'generated_tokens': len(output.token_ids),
              'finite_logprobs': finite, 'output_text': output.text,
              'note': 'Checkpoint load/generation validation only; this is not a benchmark evaluation.'}
    save(ROOT/'results/checkpoint-verification.json', result)
    print(json.dumps(result), flush=True)
    assert result['success']


if __name__ == '__main__':
    main()
