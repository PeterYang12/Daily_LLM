# 512/64/64分区的题目独立性核对

CPU审计结果（原始文件：`results/large-memagent/split-independence-audit.json`）核对了固定train512、dev64、external64的全部640行。各parquet SHA256和每行完整内容hash都与冻结manifest相符，未改变样本或选择规则。

在每个分区内部，以及train–dev、train–external、dev–external三组之间，以下规则的重复/重叠组数均为 **0**：

- 原始question文本精确相等。
- Unicode NFKC + casefold + Unicode空白压缩/首尾去空白后的question相等。
- 在上述结果上仅保留Unicode字母数字的进一步词面筛查。
- 原始完整row hash相同。
- 保留的`extra_info.index`在同一来源parquet命名空间内相同。

prompt中的问题与extra_info.question经主规范化后也全部一致。逐行问题文本、规范化文本、source row、source-scoped index和hash均保留在JSON，便于复核。

**原始HotpotQA全局question ID不可用。** 三个选定parquet没有`_id/qid/question_id`等字段；额外读取原始来源parquet的schema（原始文件：`results/large-memagent/split-source-schema-audit.json`）也没有发现这些字段，只有`extra_info.index`和整数pandas index。不能声称完成了按原始全局ID的再次验证。

直接忽略来源比较整数`extra_info.index`会产生train–dev的35个、train–external的38个数值碰撞，但这些对应不同问题。dev与external来自同一源文件，其保留index无交集。`external.json`内人为生成的`hotpotqa_dev_row_N`只是行定位符，不是原始HotpotQA题ID。

审计支持“本次训练、dev、external未发现相同规范化问题”的结论，不排除改写问题、共享文档/实体、底座模型预训练接触过题目。source row hash不重叠本身不足以证明问题独立，因此本次把词面检查和ID可用性边界另行记录；没有因观察结果而删题。

复现（使用新的输出文件）：

```bash
docker exec -w /lab ua-lab-cpu /lab/envs/cpu/bin/python \
  scripts/audit_memagent_split_independence.py \
  --output results/large-memagent/split-independence-audit-replay.json
```

脚本只读取640行已固定parquet与manifest，不调用GPU，不加载模型，不修改训练或评估数据。
