import copy
import json
from pathlib import Path

import yaml
from datasets import Dataset
from runtime_common import ROOT, RUN_ID, save


def main():
    source = ROOT / 'src/uni-agent'
    task_configs = yaml.safe_load((source / 'examples/quickstart/training/task_config_react.yaml').read_text())
    task = copy.deepcopy(next(t for t in task_configs if t['name'] == 'swe_rebench'))
    templates = json.loads((ROOT / 'data/reusable-templates.json').read_text())
    task['sandbox'] = {'provider': 'e2b', 'runtime_timeout': 3600, 'image_map': [
        {'from': 'swerebench/sweb.eval.x86_64.' + iid.lower().replace('__', '_1776_'), 'to': record['template_id']}
        for iid, record in templates.items()]}
    task['eval_timeout'] = 600
    (ROOT / 'configs/task_config.yaml').write_text(yaml.safe_dump([task], sort_keys=False))
    raw_val = Dataset.from_parquet(str(ROOT / 'data/verified-original.parquet'))
    val_rows = []
    for row in raw_val:
        meta = {k: row[k] for k in ['instance_id', 'repo', 'base_commit', 'patch', 'test_patch',
                                   'problem_statement', 'FAIL_TO_PASS', 'PASS_TO_PASS', 'version']}
        meta['version'] = str(meta['version'])
        val_rows.append({'data_source': 'princeton-nlp/SWE-bench_Verified',
                         'prompt': [{'role': 'user', 'content': meta['problem_statement']}],
                         'extra_info': {'tools_kwargs': {'task': {'name': 'swe_bench', 'metadata': meta,
                             'sandbox': {'image': 'swebench/sweb.eval.x86_64.' + meta['instance_id'].lower().replace('__', '_1776_')}}}}})
    Dataset.from_list(val_rows).to_parquet(str(ROOT / 'data/val.parquet'))
    scheduled = json.loads((ROOT / 'data/scheduled-tasks.json').read_text())
    val_ids = {r['instance_id'] for r in raw_val}
    assert not {t['metadata']['instance_id'] for t in scheduled} & val_ids
    pythonpath = ':'.join([str(ROOT/'code'), str(source), str(source/'verl')])
    env = {'PYTHONPATH': pythonpath, 'PYTHONNOUSERSITE': '1', 'TOKENIZERS_PARALLELISM': 'false',
           'OMP_NUM_THREADS': '1', 'PYTORCH_ALLOC_CONF': 'expandable_segments:False',
           'TORCH_NCCL_AVOID_RECORD_STREAMS': '1', 'CUDA_DEVICE_MAX_CONNECTIONS': '1',
           'VLLM_ROCM_USE_AITER': '1', 'VLLM_ROCM_USE_AITER_MOE': '0',
           'USE_ROCM_AITER_ROPE_BACKEND': '0', 'TRANSFER_QUEUE_ENABLE': '1',
           'UA_PHASE': 'train', 'UA_OBSERVE': '1', 'HYDRA_FULL_ERROR': '1',
           'UA_MAX_SUBMITTED_PROMPTS': '48',
           'VERL_FILE_LOGGER_PATH': str(ROOT/'results/train-metrics.jsonl')}
    cfg = {
      'defaults': ['ppo_megatron_trainer', '_self_'],
      'data': {'train_files': str(ROOT/'data/train.parquet'), 'val_files': str(ROOT/'data/val.parquet'),
               'train_batch_size': 16, 'gen_batch_size': 1, 'shuffle': False, 'seed': 20260920,
               'dataloader_num_workers': 0, 'prompt_key': 'prompt', 'max_prompt_length': 8192,
               'max_response_length': 131072, 'filter_overlong_prompts': True,
               'truncation': 'error', 'return_raw_chat': True},
      'algorithm': {'adv_estimator': 'grpo', 'use_kl_in_reward': False,
                    'rollout_correction': {'bypass_mode': False, 'rollout_is': 'token',
                        'rollout_is_threshold': 2.0, 'rollout_is_batch_normalize': False, 'rollout_rs': None}},
      'actor_rollout_ref': {
        'nccl_timeout': 9600,
        'model': {'path': '/work/models/Qwen3-Coder-30B-A3B-Instruct', 'lora_rank': 0,
                  'use_remove_padding': True, 'enable_gradient_checkpointing': True, 'use_fused_kernels': False},
        'actor': {'ppo_mini_batch_size': 16, 'ppo_micro_batch_size_per_gpu': 1, 'ppo_epochs': 1,
                  'use_dynamic_bsz': True, 'ppo_max_token_len_per_gpu': 34816, 'use_kl_loss': False,
                  'entropy_coeff': 0.0, 'loss_agg_mode': 'token-mean', 'clip_ratio_low': 0.0004,
                  'clip_ratio_high': 0.0004, 'clip_ratio_c': 10.0, 'policy_loss': {'loss_mode': 'gspo'},
                  'optim': {'lr': 1e-6, 'lr_warmup_steps': 0, 'lr_warmup_steps_ratio': 0.0,
                            'lr_decay_steps': 10000, 'lr_decay_style': 'constant', 'weight_decay': 0.1, 'clip_grad': 1.0},
                  'checkpoint': {'save_contents': ['model', 'optimizer', 'extra'], 'async_save': False},
                  'megatron': {'use_mbridge': True, 'vanilla_mbridge': True, 'use_dist_checkpointing': False,
                      'param_offload': True, 'optimizer_offload': True,
                      'tensor_model_parallel_size': 1, 'pipeline_model_parallel_size': 1,
                      'context_parallel_size': 4, 'expert_model_parallel_size': 8, 'expert_tensor_parallel_size': 1,
                      'sequence_parallel': False, 'router_replay': {'mode': 'R3'},
                      'override_transformer_config': {'recompute_method': 'uniform', 'recompute_granularity': 'full',
                          'recompute_num_layers': 1, 'attention_backend': 'flash', 'moe_grouped_gemm': True,
                          'moe_token_dispatcher_type': 'alltoall', 'moe_router_dtype': 'fp32',
                          'moe_permute_fusion': False, 'gradient_accumulation_fusion': True}}},
        'rollout': {'name': 'vllm', 'mode': 'async', 'n': 8, 'calculate_log_probs': True,
                    'logprobs_mode': 'processed_logprobs', 'tensor_model_parallel_size': 2,
                    'gpu_memory_utilization': 0.7, 'max_model_len': 139264,
                    'max_num_batched_tokens': 16384, 'max_num_seqs': 64, 'enforce_eager': True,
                    'free_cache_engine': True, 'enable_chunked_prefill': True, 'enable_rollout_routing_replay': True,
                    'engine_kwargs': {'vllm': {'disable_custom_all_reduce': True}},
                    'temperature': 1.0, 'top_p': 1.0, 'top_k': -1,
                    'log_prob_use_dynamic_bsz': True, 'log_prob_max_token_len_per_gpu': 34816,
                    'val_kwargs': {'temperature': 1.0, 'top_p': 0.95, 'do_sample': True, 'n': 1},
                    'multi_turn': {'enable': True, 'max_parallel_calls': 1, 'format': 'qwen3_coder'},
                    'agent': {'num_workers': 8, 'agent_loop_manager_class': 'uni_agent.framework.entry.AgentFrameworkRolloutAdapter'},
                    'custom': {'agent_framework': {'gateway_count': 4,
                        'log_dir': str(ROOT/'results/trajectories'), 'mask_unfinished_episode': True,
                        'agent_runners': {'task': {'runner_fqn': 'run_runner.run_task', 'dispatch_mode': 'ray_task',
                            'max_concurrent_sessions': 128, 'trajectory_selection': 'all',
                            'runner_kwargs': {'task_config_path': str(ROOT/'configs/task_config.yaml'),
                                'model_name': 'Qwen3-Coder-30B-A3B-Instruct'}}}}}},
      },
      'reward': {'reward_manager': {'name': 'dapo'},
                 'custom_reward_function': {'path': 'pkg://uni_agent.framework.task_runner', 'name': 'score_from_runner_result'}},
      'transfer_queue': {'enable': True},
      'trainer': {'use_v1': True, 'v1': {'trainer_mode': 'colocate_async', 'colocate_async': {'num_warmup_batches': 0}},
                   'nnodes': 1, 'n_gpus_per_node': 8, 'total_training_steps': 3, 'total_epochs': 10,
                   'save_freq': 3, 'max_actor_ckpt_to_keep': 1, 'max_critic_ckpt_to_keep': 1,
                   'resume_mode': 'disable', 'test_freq': -1, 'val_before_train': False,
                   'project_name': 'uni-agent-swe-rl-rocm', 'experiment_name': RUN_ID,
                   'logger': ['console', 'file'], 'default_local_dir': str(ROOT/'checkpoints'),
                   'rollout_data_dir': str(ROOT/'results/consumed-rollouts')},
      'ray_kwargs': {'ray_init': {'num_cpus': 160, 'namespace': RUN_ID, 'include_dashboard': False,
                                 'object_store_memory': 32*1024**3,
                                 'runtime_env': {'env_vars': env}}}
    }
    (ROOT/'configs/train.yaml').write_text(yaml.safe_dump(cfg, sort_keys=False))
    save(ROOT/'configs/worker-env.json', env)
    save(ROOT/'results/implementation-decisions.json', {
        'dataset_order': 'Existing qualified environments first, retaining all 1150 official rows.',
        'prefetch_warmup_batches': 0, 'reason': 'Fresh E2B image imports fail server-side; bound the 3-step smoke workload to ready tasks.',
        'rocm_changes': ['actor TP1 PP1 CP4 EP8', 'rollout TP2', 'eager rollout',
                         'chunked prefill batch 16384', 'unsupported fused kernels disabled'],
        'task_budget': {'max_steps': task['agent']['max_steps'], 'max_total_tokens': task['agent']['model']['max_total_tokens']},
        'scheduled_train_eval_overlap': 0})
    assert task['agent']['max_steps'] == 200 and task['agent']['model']['max_total_tokens'] == 131072
    print('configuration_written', ROOT/'configs/train.yaml', flush=True)


if __name__ == '__main__':
    main()
