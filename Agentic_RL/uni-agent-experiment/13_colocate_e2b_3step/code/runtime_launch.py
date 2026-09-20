import asyncio
import json
import os
import sys
import time

import hydra
import ray
from omegaconf import OmegaConf
from runtime_common import ROOT, RUN_ID, save


@ray.remote(num_cpus=0, max_concurrency=512)
class Admission:
    def __init__(self):
        self.creates = asyncio.Semaphore(3)
        self.execs = asyncio.Semaphore(16)

    async def acquire_create(self):
        await self.creates.acquire()

    async def release_create(self):
        self.creates.release()

    async def acquire_exec(self):
        await self.execs.acquire()

    async def release_exec(self):
        self.execs.release()

    async def ping(self):
        return True


@hydra.main(config_path=str(ROOT/'src/uni-agent/verl/verl/trainer/config'),
            config_name='ppo_megatron_trainer', version_base=None)
def main(config):
    from verl.trainer.constants_ppo import get_ppo_ray_runtime_env
    from verl.trainer.main_ppo import run_ppo, TaskRunnerV1
    from verl.utils.device import auto_set_device
    from verl.utils.logging_utils import configure_verl_logging
    configure_verl_logging()
    auto_set_device(config)
    save(ROOT/'results/resolved-config.json', OmegaConf.to_container(config, resolve=True))
    assert config.trainer.total_training_steps == 3
    assert config.data.train_batch_size == 16 and config.actor_rollout_ref.rollout.n == 8
    assert config.trainer.v1.trainer_mode == 'colocate_async'
    capacity = json.loads((ROOT/'results/capacity-128.json').read_text())
    qualification = json.loads((ROOT/'results/qualification-summary.json').read_text())
    assert capacity['success'] and not capacity['remaining'], '128-sandbox acceptance must pass first'
    assert qualification['target'] == 48 and qualification['qualified'] == 48, 'All scheduled task environments must pass'
    for rank in range(8):
        assert json.loads((ROOT/'results/gpu-mesh'/f'rank-{rank}.json').read_text())['success']
    result = {'started_unix': time.time(), 'status': 'starting', 'exit_code': None}
    save(ROOT/'results/main-exit.json', result)
    try:
        kwargs = OmegaConf.to_container(config.ray_kwargs.ray_init, resolve=True)
        kwargs['runtime_env'] = OmegaConf.to_container(OmegaConf.merge(get_ppo_ray_runtime_env(config),
            config.ray_kwargs.ray_init.runtime_env), resolve=True)
        kwargs['runtime_env'].setdefault('env_vars', {})['TRANSFER_QUEUE_ENABLE'] = '1'
        ray.init(**kwargs)
        gate = Admission.options(name='e2b-admission', namespace=RUN_ID).remote()
        ray.get(gate.ping.remote())
        run_ppo(config, TaskRunnerV1)
        result.update(status='completed', exit_code=0)
    except BaseException as exc:
        result.update(status='failed', exit_code=1, error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        ray.shutdown()
        result['ended_unix'] = time.time()
        save(ROOT/'results/main-exit.json', result)
        from cleanup import main as cleanup_main
        asyncio.run(cleanup_main())


if __name__ == '__main__':
    main()
