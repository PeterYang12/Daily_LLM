# E08：Gateway轨迹与后训练准备范围

## 目的

验证真实agent调用能否经Uni-Agent Gateway保留可追溯的token级交互，并明确这些记录距离实际训练还缺什么。任务修复分数、轨迹结构正确和训练有效是不同验收目标。

## 本次怎样记录

正式三组agent都通过Gateway访问vLLM。ReAct/Mini使用OpenAI兼容接口，Claude Code使用Anthropic兼容接口；Gateway转换请求，由backend按token ID请求vLLM并要求返回原始生成token ID。

agent的工具结果在下一轮进入上下文。最终序列用mask区分模型生成部分与非生成上下文；调试文件另外保存消息历史与session状态。它不是简单地把最后一段文字重新分词当成整条轨迹。

主要字段：

| 字段 | 含义 |
|---|---|
| `prompt_ids` | 本条轨迹的初始prompt token |
| `response_ids` | 后续拼接的序列；可能同时包含生成和工具观察 |
| `response_mask` | 与response_ids对齐，区分生成与非生成部分 |
| `response_logprobs` | 如启用，保存对齐的生成logprob及相关拼接值 |
| `num_turns` | 轨迹交互轮数 |
| `reward_score`、`finished` | 训练/任务注释字段；调试导出的值可能尚未关联任务结果 |
| session/instance标识 | 用于连接任务结果、候选补丁与模型记录 |

## 实际检查了什么

检查token ID范围，token/mask长度一致，mask为0/1；对于显式启用logprob的运行，检查其长度对齐且数值有限。采样输出、工具结果和判题结果分别保留，不把工具观察token统计为模型生成token。

| 主实验 | session数 | token/mask有效 | logprob完整session | 保留生成token（mask=1） | 保留观察token（mask=0） |
|---|---:|---:|---:|---:|---:|
| ReAct | 28 | 28 | 0 | 365,885 | 737,366 |
| Claude Code | 28 | 28 | 0 | 141,690 | 643,786 |
| Mini | 28 | 28 | 0 | 227,529 | 351,614 |

主实验建立session时未启用logprob保存，因此历史主数据中的该字段为空；没有事后伪造或补写。它不影响本次独立测试判定，但不能把这些记录说成已验收的logprob完整训练输入。

## 独立logprob验收

另开三个单题运行，在session参数中显式设置 `logprobs=True`，验证三种agent路径都能得到完整记录。

| 路径 | token/mask/logprob检查 | 保留生成token | 保留观察token | 任务结果 |
|---|---|---:|---:|---|
| ReAct | 1/1有效 | 5,324 | 14,936 | 修复通过，正常结束 |
| Claude Code | 1/1有效 | 3,027 | 16,653 | 修复通过，正常结束 |
| Mini | 1/1有效 | 5,264 | 7,691 | 未修复、未正常结束，但轨迹结构有效 |

另一个AITER服务的ReAct单题运行也保留了有效logprob轨迹。这些附加运行用于数据链路验收，不加入正式28题成绩、不增加主实验pass@k。

Mini的记录说明：失败任务也可以有合法轨迹结构；反过来，补丁成功并不保证所有训练字段都已准备好。

## 后训练尚未执行的部分

本次调试路径把真实结果保存在逐题JSON中，通过instance/session标识可以关联。没有运行训练框架中的reward attachment、unfinished masking、loss计算、optimizer更新、参数同步或训练断点恢复。

有些训练算法可以重算logprob或使用其他采样校正方式；本次没有验证这些路径。也不能仅凭轨迹字段齐全就声称模型会变好。

如果继续训练，至少需要验证：

1. 数据进入目标训练器时，模型生成token和工具观察的loss mask语义正确。
2. reward与相应session/候选一致，结束状态处理符合算法。
3. 一次更新前后参数确实改变，模型同步到rollout服务。
4. 完整checkpoint可恢复优化器和数据进度。
5. 在独立、未用于训练的评测集上做base/final对照。

## 证据

- [全部轨迹审计汇总](../evidence/trajectories/audit.json)
- [ReAct独立验收](../evidence/summary/trajectory-logprobs-react.json)、[Claude](../evidence/summary/trajectory-logprobs-claude.json)、[Mini](../evidence/summary/trajectory-logprobs-mini.json)
- [最初主实验driver](../reproduce/scripts/executed/run_swe.py)与[后来显式开启logprob的版本](../reproduce/scripts/run_swe.py)
- 完整token数组保留在原lab各运行的`sessions/`目录，本目录保留审计与索引。
