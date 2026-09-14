# MemAgent：PR 183 与多轨迹权重 issue 185 的本机审计

审计日期：2026-09-11 UTC。本文是训练产物的只读分析；没有切换 reward，也没有修改 Uni-Agent 或 verl 训练源码。保留前 8 步的中途快照，并在末节补充完整 32 步结果，两者明确分开。

本次固定 Uni-Agent `472c875a97f9a2764c81a6ec7581167632bd8bcc`，verl `a9f2985159536a607211dcac730d3f5d55028950`。比较对象为仍开放的 [PR 183](https://github.com/verl-project/uni-agent/pull/183)，审计时 head `b15da7953cc81466355d110fb65d8859a55bf3bd`，以及 [issue 185](https://github.com/verl-project/uni-agent/issues/185)。GitHub 内容可能继续变化，因此保留了 PR 元数据（原始文件：`notes/research/uni-pr-183.json`）、文件 diff（原始文件：`notes/research/uni-pr-183-files.json`）、PR 评论（原始文件：`notes/research/uni-pr-183-comments.json`）、issue 评论（原始文件：`notes/research/uni-issue-185-comments.json`） 和该 head 的 纯 reward 源码（原始文件：`notes/research/pr183-hotpotqa-reward.py`）。

## 对本次实验的直接结论

当前训练确实采用了旧 token LCS reward 和按组标准差归一化的 GRPO，因此 PR 183 涉及的算法差异与本次运行有关。但 PR 不只改这两项；我们的 ROCm 配置也已经启用了 KL、采用对称 clipping 和更小学习率，不能把此次运行描述为完整复现旧官方 recipe，也不能直接套用 PR 的稳定性结论。

多 context 的最终 reward 会广播到所有 context，但 verl **只选每个逻辑 session 的最后一个 context 计算 GRPO，再广播 advantage**。这里不存在把同一答案的 6–7 份 reward 当作 6–7 个独立采样答案计算组均值的错误。真正需要讨论的是 loss 如何累计这些 context，以及它们被拆成多少个 optimizer minibatch。

当前 `loss_agg_mode=token-mean`，issue 185 使用的 `seq-mean-token-mean` 的简单 1:N 行权重例子不能原样套用。当前目标给所有有效生成 token 相同的聚合系数；一个 session 产生更多有效 token，就在目标中占更多 token 质量。实际梯度还取决于 advantage、梯度方向、概率比和 clipping，不能仅由 context 数或 token 数推出实际梯度范数比例。

## PR 183 不只是评分修复

实际配置以 resolved_config.yaml（原始文件：`runs/rl/memagent_separate_32/resolved_config.yaml`） 为准；人写配置是 rl_memagent_separate_32.yaml（原始文件：`configs/rl_memagent_separate_32.yaml`）。

| 设置 | 固定源码/旧官方 recipe → PR 183 | 本次 32 步实验 |
| --- | --- | --- |
| HotpotQA reward | token LCS → 归一化 exact match | 仍为固定 pin 的 token LCS |
| `norm_adv_by_std_in_grpo` | True → False | True |
| actor KL loss | 关闭 → 开启，coef 0.001，low_var_kl | 已开启，coef 0.01 |
| PPO clip low/high | 0.2/0.28 → 0.2/0.2 | 已为 0.2/0.2 |
| rollout top_p | 0.7 → 1.0，并移除 task 对 top_p 的覆盖 | 0.7 |
| learning rate | 1e-6，增加 20 步 warmup | 1e-7，无 warmup |
| shuffle | True → False | True |
| overlong / truncation | 不过滤、error → 过滤、center | 不过滤、error |
| remove padding / gradient checkpointing | 开启二者 | remove_padding=False，gradient_checkpointing=True |
| parameter sync step | 2 → 4 | 1 |

PR 的移除 padding、过滤长度和采样变化也可能改变性能及数据分布。在 ROCm 上开启 remove padding 还涉及不同 kernel 路径，不能只为了“对齐上游”在运行中顺手切换。关联 issue 175 评论（原始文件：`notes/research/uni-issue-175-comments.json`） 讨论过 KL 延缓 spike；这不证明 PR 当前整套更改已解决所有稳定性问题，更不能证明单个差异就是本次波动的原因。

## 两种 reward 的实际差别

固定源码 [reward.py](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/tasks/hotpotqa/reward.py#L6) 读取最后 300 字符中的最后 boxed 答案，用词级 LCS 长度除以两边词数的较大值。PR 的新版本使用归一化字符串完全相等，只给 0 或 1；其 normalization 会移除空格等字符，因此也不是普通字面字符串相等。

本机审计脚本 audit_memagent_pr183_185.py（原始文件：`scripts/audit_memagent_pr183_185.py`） 从 trainer 已消费的 rollout JSONL 中选择每个 session 的最后 context，对相同文本分别重算两种 reward。它使用原始 `model_token_count`，没有重分词；通过 question 和 rollout/session 对应到 trajectory metadata，排除尚未消费的异步预取。

前 8 步共 128 个逻辑 sessions、864 个真实 context rows。存储文件共 896 行，另外 32 行是 verl 的 synthetic padding，脚本按源码规则识别 `pad<32 hex>_<session>_0` 并断言其空输出、零 reward 后排除。128 个 session 均找到唯一 metadata；原 reward 重算与保存值全部一致。

| 训练步 | sessions | 真实 context rows | padding rows | 原 LCS session 均值 | PR exact match 均值 | reward 改变的 sessions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 16 | 104 | 8 | 0.587500 | 0.437500 | 5 |
| 2 | 16 | 108 | 4 | 0.234375 | 0.000000 | 7 |
| 3 | 16 | 112 | 0 | 0.666667 | 0.625000 | 2 |
| 4 | 16 | 108 | 4 | 0.562500 | 0.125000 | 9 |
| 5 | 16 | 108 | 4 | 0.539931 | 0.375000 | 7 |
| 6 | 16 | 104 | 8 | 0.583333 | 0.312500 | 5 |
| 7 | 16 | 108 | 4 | 0.395833 | 0.250000 | 6 |
| 8 | 16 | 112 | 0 | 1.000000 | 0.687500 | 5 |

46/128 个 reward 会改变。其中 **16 个由 LCS=1 变为 PR exact match=0，是因为 PR 删除了旧 `remove_boxed()` 对 `\text{...}` 的解包**。例如 `\boxed{\text{state capital}}` 对标签 `state capital`：旧 reward=1，新 reward=0。若只保留旧解包再做 PR normalization，这 16 个都相等。该结果指出具体的格式处理差异，不能把这 16 个直接归类为错误知识或语义错误。

另一类变化才是预期的部分匹配收紧，例如 `\boxed{\text{Nassau}}` 对 `Nassau County` 的旧 LCS 为 0.5，新 reward 为 0。PR 评分差异同时包含答案完整性和格式规范差异，应分开解释。当前 PR 新增测试没有覆盖这个 `\text` 情况。

GRPO 的变化也不仅是“严格评分降低均值”。当前每题采样 4 个 session，计算 `(r - group_mean)/(sample_std + 1e-6)`；PR 改成 `r - group_mean`。完全相同的 reward 组没有 policy gradient 信号。部分匹配被压成全 0 会让一些原本非均匀的组变为零信号；格式处理也可能把原本全 1 的组分裂。第 8 步正是旧 LCS 全为 1、而 PR EM 产生两个非均匀组，其中包含上述包装差异。不能把这个新信号自动当成更好的学习信号。

同一批 32 个 heldout 的离线双评分如下。每次有 227 个 context rows（29 个 session 为 7 段、3 个为 8 段），均只按最终 context 计一个 session：

| validation checkpoint | 原 LCS | PR exact match |
| --- | ---: | ---: |
| step 0 | 0.3808779762 | 0.250000 |
| step 8 | 0.4206845238 | 0.312500 |

这是 32 个固定题目上的中途观察，样本较少；不能据此宣布显著提升。训练每步使用不同题目，因此上面的训练均值更不构成可比较的学习曲线。

## 多轨迹链路和 loss 权重

1. [HotpotQA task](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/tasks/hotpotqa/task.py#L53) 给最终回答评分；[MemAgent](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/agents/mem_agent/agent.py#L90) 将结果广播到各 context segment。
2. [Framework](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L1042) 对最终 trajectory 评分并广播。`trajectory_selection=all` 时，每段写入一个 TQ row，key 为 `{uid}_{session}_{index}`；[写入字段](https://github.com/verl-project/uni-agent/blob/472c875a97f9a2764c81a6ec7581167632bd8bcc/uni_agent/framework/framework.py#L1096) 包含 token、mask、logprob、末 token reward，没有 session 级 `loss_weight`。
3. [verl 多轨迹 advantage](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/utils.py#L148) 用每个 session 最大 index 的 row 计算一次 GRPO，将得到的标量乘各 row response mask 广播。因此 observation token 不因 reward 广播而被当作动作训练。
4. [GRPO 公式](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/core_algos.py#L268) 按 prompt uid 分组；[token-mean 聚合](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/core_algos.py#L1174) 除以 global 有效 token 数，并补偿数据并行平均。`seq-mean-token-mean` 是另一分支，先对每个 row 做 token 平均，再对 row 平均。
5. [trainer](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/v1/trainer_base.py#L1771) 把 `ppo_mini_batch_size=4` 乘 `rollout.n=4` 得到 16 rows 的 optimizer minibatch；[worker](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine_workers.py#L242) 按此拆分。[FSDP engine](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/workers/engine/fsdp/transformer_impl.py#L719) 在每个 optimizer minibatch 内汇总跨 DP 的有效 token 数，再拆 microbatches。
6. [padding 实现](https://github.com/verl-project/verl/blob/a9f2985159536a607211dcac730d3f5d55028950/verl/trainer/ppo/padding_utils.py#L136) 加入 mask/reward 均为零的行以满足整除。前 8 步每批最终为 112 rows，因此每个 global step 有 7 个 optimizer minibatches，共 56 个；global step 不等于 optimizer step。

忽略 clipping，仅用符号说明聚合方式，一个 optimizer minibatch 的 policy 项近似为 `sum(session, context, valid token)[A_session * token_policy_loss] / valid_token_count`。同一个 session 的多个 context 会共享 advantage，但各有自己的 token 梯度。session 总有效 token 多，结构性累计系数就大；这和明确要求“每个 session 平均后再平均”的目标不同。

在已消费数据里，每个 session 有 6 或 7 个 context；第 1 步最短 session 只有 235 个模型 token，最长为 2141 个，约 9.1 倍。第 8 步范围为 942–3932。完整 token 统计见 审计 JSON（原始文件：`notes/research/memagent-pr183-185-step8.json`）。这些范围描述整个 consumed batch 的 token 质量分布；实际 PPO 分母按 optimizer minibatch 计算，不能把整批比例直接等同于单步梯度比例。

若未来要实现 session 平衡，先定义目标：每 session 等权、每 context 等权、还是每有效 token 等权。简单把 advantage 除以 context 数 N，只能改变 policy 项的分子，不能自动修正 session 分母、token 长度差异、KL 项，以及跨 DP/microbatch/minibatch 的归一化。issue 提及的 verl PR 7580 路径也不能直接假定会生效：Uni-Agent 自己直接写 TQ，绕过了默认 agent_loop_tq 的写入路径。必须确认其字段在 Uni-Agent 行上实际存在且由当前 loss 消费。

把 `trajectory_selection` 改成 `longest` 会丢弃其他记忆更新 context 的训练，是另一个实验目标，并非等价的权重修复。本次保留 `all` 以训练完整 memory 链路。

## 可复查与后续实验边界

本次没有给上游发 issue/评论，也没有热更新运行源码。完整固定配置基线已结束；未来做独立 ablation 时，应固定题目、token 预算和采样策略，分别比较 reward、std normalization、loss aggregation，而不是同时改整套 recipe。若实验采用 PR reward，应首先明确 `\text` 包装的预期语义，并保留原版与新评分的逐题结果。

重跑前 8 步审计：

```bash
cd /path/to/uni-agent-lab
python3 scripts/audit_memagent_pr183_185.py --through-step 8
```

输出为 memagent-pr183-185-step8.json（原始文件：`notes/research/memagent-pr183-185-step8.json`），控制台统计保存于 .log（原始文件：`notes/research/memagent-pr183-185-step8.log`）。使用其他 `--through-step` 时请等相关 rollout、validation 与 trajectory 文件完整，再生成新的版本化快照，不覆盖此中途证据。

## 完整 32 步：保留原评分的最终对照

32 步训练正常结束后，执行 `python3 scripts/audit_memagent_pr183_185.py --through-step 32`，生成独立的 step32.json（原始文件：`notes/research/memagent-pr183-185-step32.json`） 与 step32.log（原始文件：`notes/research/memagent-pr183-185-step32.log`），没有覆盖 step8 快照。

| 固定 32 题验证 checkpoint | 原 LCS | PR 183 exact match | PR exact match 正确题数 |
| --- | ---: | ---: | ---: |
| 0 | 0.3808779762 | 0.250000 | 8/32 |
| 8 | 0.4206845238 | 0.312500 | 10/32 |
| 16 | 0.3477678571 | 0.250000 | 8/32 |
| 24 | 0.4102678571 | 0.312500 | 10/32 |
| 32 | 0.3896910920 | 0.281250 | 9/32 |

最终 LCS 相比初始增加约 0.00881，PR exact match 多 1 题；中途有升有降。这个 32 题小验证集没有给出稳定提升的证据。这里的 PR 分数是对**同一批已经生成的文本**事后打分，不是采用 PR 配置重新训练的结果。五个验证点仍各为 32 个逻辑 session、227 个 context rows，只按每个 session 的最后回答评分。

完整训练消费了 **128 个 prompts × 4 samples = 512 个逻辑 sessions**，展开为 **3460 个真实 context rows + 124 个 synthetic padding rows = 3584 个存储 rows**。388 个 session 有 7 段，124 个有 6 段。所有 context 的 reward 广播一致，所有 512 个最终 reward 都与固定 pin 的 LCS 重算一致。32 个 batch 均补到 112 rows，因此当前每 16 rows 一次 optimizer minibatch 的设置对应每 global step 7 次、全程 224 次 optimizer minibatches；它与 32 个全局训练步是不同计数。

完整双评分中有 **162/512 个 reward 改变**。其中 **40 个 LCS=1 → PR EM=0，全部能由保留旧 `\text` 解包后完全相等解释**，不能当作模型知识错误。这一格式差异也会改变 advantage 信号：128 个 prompt 组中，旧 LCS 有 51 个非均匀组，PR EM 有 33 个；24 个旧 LCS 有信号的组变成 PR 全同 reward，另有 6 个旧全同组变成 PR 非均匀组。PR 同时取消 std normalization，因此这种组结构改变与 advantage 尺度改变应分开分析。

训练末尾还有未消费的异步预取，导致 16 个已消费 session 仅用“问题 + sample index”会找到两个 metadata 候选。最终脚本加入最后一个 context 的完整 user prompt SHA256 匹配，512/512 个 session 都能唯一对应，排除了这些预取候选。真实生成 token 总数为 **940,669**，每 session 范围 **235–4814**；最后一批范围 829–4619。它们是有效模型 token，不含 observation/padding。跨度来自不同题目、不同生成长度；按当前 `token-mean`，这说明 session 之间的结构性 token 质量并不相等，但仍不能直接推出梯度范数比例。

报告读取还遇到一个独立的 JSONL 解析细节：第 24 步文件的 JSON 字符串里包含 8 个合法的 U+2028 字符。Python `str.splitlines()` 会把它们误当成记录边界，导致 `JSONDecodeError`；按文件物理行逐条 `json.loads()` 则 112 行全部合法。只修正了本审计脚本的读取方式，没有改原始 JSONL 或训练行为。这也说明报告解析失败不应立即诊断为 rollout 数据损坏。
