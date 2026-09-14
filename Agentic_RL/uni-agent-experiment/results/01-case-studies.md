# 五个真实案例：agent到底做了什么

以下案例均属于固定28题正式比较，用来解释结果含义，不另行组成一个挑选后的分数。每个链接指向本目录保留的结果或候选补丁。

## 1. Flask5014：Blueprint名称不能为空

任务要求在创建Flask Blueprint时拒绝空名称。它是一个实际仓库中的输入校验问题，agent需要找到构造函数、理解现有名称检查、修改并验证。

三种harness均通过独立测试并正常结束，均未修改既有测试。

| ReAct | Claude Code | Mini |
|---|---|---|
| [通过结果](../evidence/cases/react/pallets__flask-5014/result.json) | [通过结果](../evidence/cases/claude/pallets__flask-5014/result.json) | [通过结果](../evidence/cases/mini/pallets__flask-5014/result.json) |
| [候选patch](../evidence/cases/react/pallets__flask-5014/candidate-delta.patch) | [候选patch](../evidence/cases/claude/pallets__flask-5014/candidate-delta.patch) | [候选patch](../evidence/cases/mini/pallets__flask-5014/candidate-delta.patch) |

候选总大小分别约10KB、2KB、57KB，其中包括新建复现脚本等文件。独立判题通过不能说明补丁已经是适合直接合并的最小改动，真实工程仍需整理和review。

**这个案例证明**：三条harness路径都能读真实代码、生成可用修复并通过标准测试，不能把实验理解为只让模型回答一段文字。

## 2. xarray4075：布尔权重的weighted mean

任务涉及传入布尔权重时的加权均值行为。

| Agent | 独立判题 | 正常结束 | 修改既有测试 |
|---|---|---|---|
| ReAct | 通过 | 是 | 无 |
| Claude Code | 未通过 | 是 | `xarray/tests/test_weighted.py` |
| Mini | 通过 | 是 | 无 |

Claude的CLI进程正常结束，但没有得到可通过独立测试的修复，并且改变了既有测试。这说明执行成功和任务成功需要由不同证据判断。

[ReAct结果](../evidence/cases/react/pydata__xarray-4075/result.json) · [Claude结果/测试统计](../evidence/cases/claude/pydata__xarray-4075/result.json) · [Claude候选](../evidence/cases/claude/pydata__xarray-4075/candidate-delta.patch) · [Mini结果](../evidence/cases/mini/pydata__xarray-4075/result.json)。

## 3. Sphinx9367：单元素tuple渲染

单元素tuple需要保留其元组语义，不能渲染成含义不同的普通括号表达式。三种agent均通过了独立测试。

ReAct没有改动既有测试。Claude与Mini修改了 `tests/test_pycode_ast.py`；将这些测试路径hunk剥离后，其余改动仍通过评分。

**这里同时成立两个事实**：保留下来的代码改动有效；原始agent行为违反了不改既有测试的要求。不能因为复验通过，就删掉原始约束记录。

[ReAct结果](../evidence/cases/react/sphinx-doc__sphinx-9367/result.json) · [Claude结果](../evidence/cases/claude/sphinx-doc__sphinx-9367/result.json) · [Claude剥离测试路径补丁](../evidence/cases/claude/sphinx-doc__sphinx-9367/candidate-source-only.patch) · [Mini结果](../evidence/cases/mini/sphinx-doc__sphinx-9367/result.json)。

## 4. pytest7521：capfd把回车转换为换行

题目要求修复 `capfd.readouterr()`对`\r`和`\n`的处理。最终结果：

| Agent | resolved | finished |
|---|---|---|
| ReAct | true | false |
| Claude Code | true | true |
| Mini | false | false |

ReAct达到100轮上限，但已经留下可以通过测试的补丁。只按finished统计会漏掉这个功能修复；只按resolved统计又会忽略它没有正常结束的事实。后续训练对未完成轨迹的处理必须显式定义。

[ReAct结果](../evidence/cases/react/pytest-dev__pytest-7521/result.json) · [候选patch](../evidence/cases/react/pytest-dev__pytest-7521/candidate-delta.patch) · [Claude结果](../evidence/cases/claude/pytest-dev__pytest-7521/result.json) · [Mini结果](../evidence/cases/mini/pytest-dev__pytest-7521/result.json)。

## 5. Django14122：Meta.ordering与GROUP BY

任务要求处理默认排序字段进入GROUP BY的问题。三种agent都结束并生成了候选，但独立测试均未认可修复。

这保留了当前30B、prompt和预算的能力边界；没有因为agent给出完成说明就判为成功，也没有为失败题重新采样挑选更好结果。

[ReAct](../evidence/cases/react/django__django-14122/result.json) · [Claude](../evidence/cases/claude/django__django-14122/result.json) · [Mini](../evidence/cases/mini/django__django-14122/result.json)。

## 从案例得到的判断方法

看一个agent任务，至少同时检查：最终测试、agent结束状态、实际修改、测试约束，以及原始交互。候选大小、CLI的成功文字、一次命令退出码都只是局部信息。

更完整的结果在[28题矩阵](03-per-case-matrix.md)，测试通过数与配对关系在[三agent对照](../experiments/03-swe-agent-comparison.md)。
