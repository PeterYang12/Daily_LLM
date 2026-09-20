"""Load the final exported checkpoint in a fresh vLLM instance after training."""
import hashlib,json,math,os,time
from pathlib import Path

ROOT=Path('/lab/rl-swe-e2b-20260916')

def main():
    import torch
    from safetensors import safe_open
    from transformers import AutoTokenizer
    from vllm import LLM,SamplingParams
    exit_state=json.loads((ROOT/'results/main-exit.json').read_text())
    assert exit_state['exit_code']==0
    final=ROOT/'checkpoints/global_step_300/actor/model/huggingface'
    original=Path('/lab/models/Qwen3-Coder-30B-A3B-Instruct')
    index=json.loads((final/'model.safetensors.index.json').read_text())['weight_map']
    original_index=json.loads((original/'model.safetensors.index.json').read_text())['weight_map']
    assert all((final/f).exists() for f in set(index.values()))
    compared=[]
    for name in [k for k in index if 'self_attn.q_proj.weight' in k][:4]:
        with safe_open(final/index[name],framework='pt',device='cpu') as f:a=f.get_tensor(name).float()
        with safe_open(original/original_index[name],framework='pt',device='cpu') as f:b=f.get_tensor(name).float()
        compared.append({'parameter':name,'elements':a.numel(),'changed':int((a!=b).sum()),'max_delta':float((a-b).abs().max())})
    assert compared and sum(r['changed'] for r in compared)>0
    start=time.monotonic()
    llm=LLM(model=str(final),tensor_parallel_size=2,dtype='bfloat16',max_model_len=4096,
            gpu_memory_utilization=0.45,max_num_seqs=2,enforce_eager=True,disable_custom_all_reduce=True)
    tokenizer=AutoTokenizer.from_pretrained(final)
    prompt=tokenizer.apply_chat_template([{'role':'user','content':'Write a Python function add(a, b) that returns the sum. Return only the function.'}],tokenize=False,add_generation_prompt=True)
    outputs=llm.generate([prompt],SamplingParams(temperature=0,max_tokens=96,logprobs=1))
    output=outputs[0].outputs[0]
    finite=bool(output.logprobs) and all(math.isfinite(lp.logprob) for row in output.logprobs for lp in row.values())
    success=bool(output.text.strip()) and bool(output.token_ids) and finite
    result={'success':success,'checkpoint_step':300,'checkpoint_directory':str(final),'fresh_vllm_load':True,
            'tensor_parallel_size':2,'elapsed_s':time.monotonic()-start,'generated_tokens':len(output.token_ids),
            'finite_logprobs':finite,'output_text':output.text,'compared_to_original':compared,
            'note':'Fresh checkpoint load and generation smoke test; the independent 16-task evaluation is logged by the trainer.'}
    (ROOT/'results/saved-model-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));assert success

if __name__=='__main__':main()
