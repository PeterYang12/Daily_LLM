# 32B final128：稳定协议外部评测与源文案例复核

固定 64 道 external 题上，Qwen3-32B 的原生题均 LCS 从 **0.4209077381 升至 0.5601190476**，差值 **+0.1392113095**；按题配对、20,000 次 bootstrap 的 95% CI 为 **[+0.0401023065, +0.2401441592]**。原生 `LCS=1` 从 20/64 升至 26/64，19 题增分、7 题降分、38 题不变。

这些是预先固定的 **stable 辅助协议（BI=1 / TRITON_ATTN）**，只比较 base 与预定的 final step128。原 primary 的 GPU 0 超时和新的 GPU 1 primary recovery 是另行记录的执行，不参与本页统计。两次 base 的 64 个最终回答与 reward 完全相同，两次 final 的 64 个回答与 reward 也完全相同；两组配对统计一致。重复执行没有增加独立题目或训练 seed 数量，不能将其合并成 128 道题的置信区间。

独立机器审计重读了四份原始结果、provenance、实际进程 argv/env/backend 与日志前缀证据，重新校验完整 64 题、原生 reward、paired CSV 和 bootstrap。两个格式分析文件的 protocol signature、统计值及全部 64 题的分类字段逐项重建一致，源 JSON 的全部 row hash 与冻结 manifest 相符。详见 final-stable-case-review-independent.json（原始文件：`results/large-memagent/final-stable-case-review-independent.json`）。

## 格式贡献能说明什么

| 原有窄分类 | 题数 | 增 / 降 | 对题均 LCS 的净贡献 |
| --- | ---: | ---: | ---: |
| TeX 空格写法改变，答案文字相同 | 2 | 2 / 0 | +0.0156250000 |
| 其他答案文字或选择变化 | 24 | 17 / 7 | +0.1235863095 |
| reward 不变 | 38 | 0 / 0 | 0 |

两道窄格式 case 贡献了观测净增的 **11.2239%**。这是冻结分类规则下的算术分解，不是“11.22% 格式、88.78% 知识增长”的因果结论。下文可见，`other` 还包含同一日期的词序、引号、别名和答案长度变化；窄分类并不穷尽所有语义相同的表达差异。

## 四个目标答案改善案例

下表使用冻结 external 的 dev row 标识。机器审计保留每个案例的完整 before/after 最终回答、源 context hash、文档编号、原文字符位置和文档原文；这些案例用于展示行为，不取代全部 64 题的统计。

| dev row | 问题目标 | Base → final128 | 原生 LCS |
| --- | --- | --- | ---: |
| 69 | 514th Flight Test Squadron 所在基地的命名对象试飞哪种飞机 | Verville-Sperry R-1 biplane → **B-17 Flying Fortress bomber** | 0 → 1 |
| 81 | Lady Mary-Gaye Curzon 最小的女儿与两位指定演员共同主演的 2017 年电影 | Bottom of the World → **The Bye Bye Man** | 0.25 → 1 |
| 94 | Queen of Blood 与 Battle Beyond the Sun 的共同苏联片源 | Mechte Navstrechu → **Nebo Zovyot** | 0 → 1 |
| 116 | Nerdist Industries CEO 曾作为嘉宾参加的播客 | Maltin on Movies → **Comedy Film Nerds** | 0 → 1 |

**row 69：基地、人物、飞机的关联。** 文档 10 将 514th Flight Test Squadron 定位于 Hill Air Force Base；文档 191 说明基地以 Major Ployer Peter Hill 命名，他死于试飞 B-17 Flying Fortress 原型机。Base 给出的 Verville-Sperry 不在本题源文中，final 给出正确的目标飞机。该题原 context 有 200 个文档、约 11.1 万字符，分为 6 个 source chunk，加一次 final 回答；两条关键证据分布于文档 10 和 191。

**row 81：同时满足人物与共同演员约束。** 文档 109 明确 Cressida Bonas 是 Lady Mary-Gaye Curzon 的最小女儿；文档 154 的 The Bye Bye Man 同时列出 Douglas Smith、Lucien Laviscount 和 Cressida Bonas。干扰项文档 17 的 Bottom of the World 虽是 2017 年电影且含 Douglas Smith，却不能满足其余目标条件。final 从部分线索匹配转为符合全部题目约束的电影。

**row 94：区分单条相关素材与共同来源。** 文档 17 说明 Queen of Blood 重用了 Mechte Navstrechu 和 Nebo Zovyot 两片的特效素材；文档 191 则明确 Battle Beyond the Sun 是 1959 年 Nebo Zovyot 的英语配音和重剪版。final 选择了同时满足两部电影关联及年份条件的片名。

**row 116：区分“平台上的节目”和“本人作为嘉宾出现”。** 文档 191 确认 Chris Hardwick 是 Nerdist Industries CEO；文档 154 的 Comedy Film Nerds 嘉宾列表包含 Chris Hardwick。文档 7 只说明 Maltin on Movies 在 Nerdist 平台上发布。final 的目标播客得到源文中“嘉宾”关系的明确支持。

上述四例支持“这些题的最终目标答案更符合给定材料”，没有证明中间 memory 在哪一步改善，也没有单独证明模型获得了这些事实的新知识。

## 两个真实回退

| dev row | Base → final128 | 原生 LCS | 本地源文支持的判断 |
| --- | --- | ---: | --- |
| 123 | Hidden America with Jonah Ray → **Free Radio** | 1 → 0 | 目标主持人为 Ralph Garman；Free Radio 属于另一位与 Joe Schmo 有关联的演员 Lance Krall 的节目。 |
| 124 | 1943 → **1991** | 1 → 0 | Mborja 所属 Albanian Fascist Party 的目标终止年份是 1943，final 转答另一个党派时期。 |

**row 123：人物角色和节目错配。** 文档 109 把 Ralph Garman 明确描述为 The Joe Schmo Show 的主持人，文档 10 将他列为旅行恶搞节目 Hidden America with Jonah Ray 的嘉宾。文档 154 和 191 的 Free Radio 对应 Lance Krall。final 的最终说明还混入 Matt Kennedy Gould，随后框选 Free Radio，已经偏离目标人物及节目类型。这是实际目标答案回退；最终回答中的“from the memory”不能作为某一次 memory 更新出错的证据。

**row 124：最终说明含正确事实，框选却答了别的问题。** 文档 109 将 Mborja 关联到 Albanian Fascist Party，文档 177 写明该党自 1939 年掌握名义权力直到 1943 年。final 的正文仍准确写出这两个年份，随后转到共产党时期并框选 1991。可直接观察到的是最终答案选择与题目目标不一致；不能据此声称 memory 丢掉了 1943 这个事实。

## 指标变化不等于事实变化

| dev row | 表达变化 | 原生 LCS | 解读 |
| --- | --- | ---: | --- |
| 72 | January 7, 1936（带 TeX 空格转义）→ 7 January 1936 | 1/3 → 1 | 文档 177 的 Hunter Davies 出生日期相同；不是新事实。 |
| 92 | `"Teach the Controversy"` → `Teach the Controversy` | 0.75 → 0.25 | 文档 7/10 指向同一 campaign；引号参与 literal token 匹配。 |
| 104 | Las Vegas → Las Vegas, Nevada | 1 → 1/3 | 文档 96 的学校服务 Las Vegas Valley；新增州名和逗号改变分词匹配，目标城市仍相同。 |
| 112 | Justin Hurwitz and Pasek & Paul → Benj Pasek and Justin Paul | 0.5 → 0.4 | 文档 88 明确两人就是 Pasek and Paul；final 去掉额外的人名、改用成员本名，却仍降分。 |

row 112 的源文还区分 Justin Hurwitz 的 music 与 Pasek/Paul 的 lyrics，因此题目本身将 La La Land 的工作统称为“composed music”也不够严谨。这里保留原题与原 gold，不补标签、不改 reward。原有三道满分题的丢失中，row 123/124 是上面的真实回退，另一道 row 104 是城市扩写的指标回退。

## memory 机制的证据边界

四份正式评测目录只保存 `results.jsonl`、`summary.json`、`infer.log` 和 `provenance.json`。结果含最终 `response`、chunk/context/step 数以及 reward；每轮共报告 388 个 source chunk、452 个含 final 的 context。`infer.py` 的序列化字段没有中间 memory，`infer.log` 也只有逐题进度和汇总，没有完整的逐轮 prompt、memory update 或 token 轨迹。

因此本次能够核对的是同一材料和协议下的最终回答、得分与重复性，不能定位到“第几块记忆保留了证据”“哪一次压缩丢失了实体”，也不能用最终回答自述来填补这一缺口。训练集或 monitor 的轨迹也不能替代这些 external case 自身缺失的 memory 记录。

可用于汇报的表述是：**在这个固定 64 题长上下文 QA 外部集及 stable 协议上，32B 完成预定 128 步 RL 后，原生 LCS 和满分题数提高，重复执行一致；源文核对支持若干实体与关系选择改善，同时存在真实回退及明显的指标表达敏感性。** 结论仍限于一次训练 seed、这一小规模 external 集和所记录的推理协议。

机器复核入口为 review_final_stable_cases.py（原始文件：`scripts/review_final_stable_cases.py`），以 CPU Docker 执行，无模型调用：

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/cpu/bin/python /lab/scripts/review_final_stable_cases.py \
  --output /lab/results/large-memagent/final-stable-case-review-independent.json
```

该命令已经成功执行；复跑应选择新输出名，脚本拒绝覆盖已有审计。
