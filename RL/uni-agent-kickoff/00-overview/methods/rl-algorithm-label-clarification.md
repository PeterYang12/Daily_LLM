# 实际 GRPO 配方与历史简称澄清

记录时间：2026-09-12T18:26:58.407737+00:00。此文只澄清报告中的算法称谓；32B、8B的冻结协议、配置、已执行更新和正在运行的训练均未改变。

推荐最终报告名称：**GRPO：组内 reward 中心化，不除以组内标准差；token-mean loss；actor KL 系数0.01。**

实际优势在每组4条会话中按 reward 减去组均值计算，并广播到该会话的memory/final训练context。`norm_adv_by_std_in_grpo=false` 关闭组内标准差缩放。策略损失按有效token总数归一化；actor另加系数0.01的KL，reward本身不加入KL。

|实际配置|32B durable|8B durable|
|---|---|---|
|algorithm.adv_estimator|grpo|grpo|
|algorithm.norm_adv_by_std_in_grpo|false|false|
|actor.loss_agg_mode|token-mean|token-mean|
|actor.use_kl_loss|true|true|
|actor.kl_loss_coef|0.01|0.01|
|algorithm.use_kl_in_reward|false|false|

历史记录中的“DR-GRPO”是对去标准差优势的简称。固定原生源码的优势函数确实写了“not scaled, as in Dr.GRPO”；但同一版本的完整 DrGRPO 配方还列出 `seq-mean-token-sum-norm` 和关闭KL。当前实验没有采用那套完整配方。最终报告应使用上述显式配置名称，避免读者误以为严格复现完整DrGRPO。

- 32B resolved_config.yaml:97（原始文件：`runs/rl/memagent_32b_128_durable/resolved_config.yaml:97`） 与 8B resolved_config.yaml:97（原始文件：`runs/rl/memagent_8b_128_durable/resolved_config.yaml:97`）：实际loss聚合为token-mean。
- [core_algos.py:296](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/core_algos.py#L296)：关于去标准差优势的Dr.GRPO注释；[实现:328](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/core_algos.py#L328)为reward减组均值。
- [core_algos.py:1170](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/core_algos.py#L1170)：token-mean用当前global有效token数作分母；后续分支是seq-mean-token-sum-norm。
- [固定版本 DrGRPO 配方:53](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/docs/algo/grpo.md#L53)：完整配方列出的其它选择。

固定verl commit：`a9f2985159536a607211dcac730d3f5d55028950`。以下hash绑定实际文件：

- `runs/rl/memagent_32b_128_durable/resolved_config.yaml`: `2f724b1f72a65652080fba7f37bdacf6f8257cdcdbf8f0d89f5465a3f22b0646`
- `runs/rl/memagent_8b_128_durable/resolved_config.yaml`: `306e8ef85eeed8159a7c1515d7c533476b491a0f4ba8366997c0fea688d56e23`
- `src/verl-rl/verl/trainer/ppo/core_algos.py`: `a54b6d812db4441e8b0989ae69d2c46b0a0a7b584296f73d8316f264462c37cb`
- `src/verl-rl/docs/algo/grpo.md`: `d43fb13fba50523c0a39fec81e56bb300dd4468ddfd331b193a9a6bedf646b9c`

训练协议的历史文本和hash保留原样。后续结果解释以实际配置和上述显式名称为准，不需要修改、重跑或重启这两次实验。
