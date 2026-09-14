# TeX空格诊断的独立结果

按[冻结v1定义](tex-space-only-definition-v1.md)，仅把官方解包答案中的反斜杠+ASCII空格替换成普通空格，原始LCS、训练reward和主paired CI保持不变。逐case结果（原始文件：`results/large-memagent/tex-space-only-v1.json`）记录了定义/helper/native reward/driver hash、输入指纹、原答案和替换后答案。

| 完整评测文件 | N | 官方LCS | TeX-space诊断LCS | 官方LCS=1数 | 诊断LCS=1数 |
|---|---:|---:|---:|---:|---:|
| 32B base-recovery external | 64 | 0.42889385 | 0.45233135 | 21 | 22 |
| 32B长文50 docs | 8 | 0.6000 | 0.6625 | 3 | 4 |
| 32B长文200 docs | 8 | 0.6750 | 0.7375 | 4 | 5 |
| 32B长文800 docs | 4 | 0.3750 | 0.5000 | 1 | 2 |
| 32B长文3200 docs | 2 | 0.5000 | 0.5000 | 1 | 1 |
| 32B长文6400 docs | 1 | 0 | 0 | 0 | 0 |

external64中6题包含该格式，4题的LCS改变：

- row70：`blake\ shelton` → `blake shelton`，0.5→1。
- row72：`january\ 7,\ 1936` → `january 7, 1936`，1/3→2/3。日期顺序、逗号保持原样，未改写成ground truth `7 January 1936`。
- row82：`richmond\ river` → `richmond river`，0→0.5。ground truth仅`Richmond`，没有删除多出的`river`。
- row108：`2016\ u.s.\ presidential\ election` → `2016 u.s. presidential election`，1/6→1/3。没有把缩写或词序改写成ground truth。

50/200/800 docs三处受影响的都是同一个原生key `<no-id>:5`，答案`yg\ entertainment`由0.5→1。它们是同一题在三种context长度下的轨迹，不能汇成三道独立问题。3200/6400没有变化。

这说明部分差分可能来自输出格式；它不自动证明事实推理正确，更不能作为RL收益。未来最终checkpoint按同一v1再计算，仍与主LCS/CI分列。规则在已知YG个案之后、批量计算之前固定，并未宣称事前盲化预注册。

可复用入口：

```bash
docker exec -w /lab ua-lab-cpu /lab/envs/cpu/bin/python \
  scripts/audit_tex_space_only.py \
  --input results/large-memagent/final-step128/results.jsonl \
  --output results/large-memagent/final-step128-tex-space-only-v1.json
```

实际checkpoint目录名以最终产物为准。输入必须有完整、无错误的native summary；脚本再核对原始数据的native sample_key/元数据/reward，拒绝重复成功择优，并拒绝覆盖已有结果。原始长文JSON缺少`id`，使用其原生 `<no-id>:index`，没有伪造全局题ID。

7项窄规则验收（原始文件：`results/large-memagent/tex-space-only-validation.json`）覆盖原生text解包、正文正确但boxed错误仍不修正、其他TeX命令不处理、ground truth不改写等情况。定义不保证诊断分数单调上升；其目的是测量这一个格式因素。
