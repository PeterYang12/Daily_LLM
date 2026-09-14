# Coder30B 扩展 29 例：完整结果与两种代表案例

这一组使用固定的 29 个 SWE-bench Verified 诊断 case、原 ReAct prompt、100-turn budget。完整结果为 **8/29 resolved、28/29 finished、8/29 两者均满足、0 外层未评分异常**。按仓库分别是 Django 3/8、Sphinx 1/8、SymPy 1/8、xarray 3/5 resolved。它是通过镜像与 baseline/gold 控制筛选的子集，不是完整基准分数，不能与六题 40-turn 结果直接合并。

全部 29 例的 patch、判题和约束审计在 cases.csv（原始文件：`results/large-swe/coder30b-expanded-recovery/case-audit/cases.csv`） 与 case-audit.json（原始文件：`results/large-swe/coder30b-expanded-recovery/case-audit/case-audit.json`）。仅 `sympy__sympy-15345`、`sympy__sympy-21596` 观察到对已有测试文件的内容修改，两例均未通过官方 verifier。mode-only、未追踪新文件、修改旧测试和新增测试按此前规则区分。

## 先核对镜像已有内容，再计算模型变更

原始 `candidate.content.patch` 是相对 dataset base commit 的 diff，文件名不保证只有模型新增内容。八个 Sphinx 镜像初始工作树已修改 `setup.py` 来锁定依赖版本，并在 `tox.ini` 为 pytest 增加 `-rA`。这 16 个完整文件 diff 块与各自模型前 baseline 的块**逐字节相同**，没有归因给模型。

排除这些已有内容后，29 例合计有 32 次文件内容变更（原始 diff 中为 48 次；按 case 分别计数），其中包括上述 2 次已有测试文件修改。部分镜像还带有大量已有 mode-only 差异，例如 Django13158 有 6256 个仅 mode 文件块；这些没有计入内容规模。

`audit_swe_saved_cases.py --baseline-dir results/swe-expanded-v1/baseline` 只排除与 baseline 整个块字节一致的内容。如果模型继续改动了同一个已有变更文件、或撤销了该变更，脚本会停止并要求重建文件状态，不会凭行数相减猜测模型修改。最初尚未对照 baseline 的派生审计保留在 `case-audit-before-baseline-check/`，正式审计使用 `case-audit/`。原始模型输出、patch、reward 没有改变。

## 代表案例选择规则

下列两个 case 按事先写入 representative-selection-rule.json（原始文件：`results/large-swe/coder30b-expanded-recovery/representative-selection-rule.json`） 的确定性规则选出，完整候选顺序与选择结果在 representative-cases.json（原始文件：`results/large-swe/coder30b-expanded-recovery/representative-cases.json`）。没有重试模型，也没有只展示八个成功案例。

- 成功例：在 resolved 且 finished、有非测试内容变更的案例中，选非测试增删行数最少者；同值按 instance_id 排序。测试约束违反不会被静默过滤。
- 失败例：在未 resolved 且出现 PASS_TO_PASS 回归的案例中，选失败条目最多者；同值按 instance_id 排序。

## 成功：Django11119 正确传递 autoescape 配置

触发条件是一个设置了 `autoescape=False` 的 `Engine`，调用 `render_to_string()` 时传入普通字典 context。原实现新建 `Context(context)`，没有传递 engine 的设置，因此仍按默认规则转义。模型只修改了一行调用：

```diff
- return t.render(Context(context))
+ return t.render(Context(context, autoescape=self.autoescape))
```

变更只涉及 `django/template/engine.py`，+1/−1 行，没有已有测试内容修改。Agent 正常结束；官方 verifier 的 `test_autoescape_off (template_tests.test_engine.RenderToStringTest)` 从失败变为通过，另外 7 个 PASS_TO_PASS 全部通过。这里展示的是一个可以用具体触发条件解释、且有回归检查的真实源码修复。

证据：source patch（原始文件：`results/large-swe/coder30b-expanded-recovery/case-audit/django__django-11119.content-only.patch`）、verifier result（原始文件：`results/large-swe/coder30b-expanded-recovery/run/django__django-11119/verifier.result.json`）。

## 失败：SymPy21596 修复一个例子时破坏了集合语义

题目中的像集为 `{n + i(n−1)(n+1) | n∈Z}`，与实数集相交应为 `{-1, 1}`。模型在 `sympy/sets/handlers/intersection.py` 中增加两处 `solve(im, n)`，并把一般返回路径改为根据有限解直接构造 `FiniteSet`，空列表则返回 `EmptySet`：

```python
solns = solve(im, n)
if solns:
    return FiniteSet(*[f.subs(n, s) for s in solns])
else:
    return S.EmptySet
```

这种返回逻辑没有保留恒实表达式和无限像集等一般情况。源码改动为 +29/−6 行；模型还把已有 `test_fancysets.py` 的预期从 `Complement(...)` 改为 `FiniteSet(-1, 1)`，这是额外的任务约束违反。Agent 虽然 finished，但官方测试仍是 **1 个 FAIL_TO_PASS 失败、4 个 PASS_TO_PASS 回归**：

- `test_imageset_intersect_real`：目标测试仍失败。
- `test_ImageSet_contains`：原本应得到 `S.Integers` 的交集断言失败。
- `test_issue_11938`：与实数相交应为 `Interval(-1, 1)` 的断言失败。
- `test_issue_9543`：自然数平方的像集应是实数子集的断言失败。
- `test_imageset_intersection`：一般 ImageSet 交集断言失败。

这些回归可直接在保存的 stdout 找到，不是凭最终文字答复判断。它说明 finished 只代表 Agent 结束，不能代替正确性；真实 verifier 的 reward 必须覆盖目标修复与原有行为。这里没有声称已隔离证明每一处回归分别由哪一行引起，而是将实际 patch 的一般返回路径变化与真实测试证据并列展示。

证据：source/test patch（原始文件：`results/large-swe/coder30b-expanded-recovery/case-audit/sympy__sympy-21596.content-only.patch`）、verifier result（原始文件：`results/large-swe/coder30b-expanded-recovery/run/sympy__sympy-21596/verifier.result.json`）、raw stdout（原始文件：`results/large-swe/coder30b-expanded-recovery/run/sympy__sympy-21596/verifier.stdout`）。

## CPU 审计复现

```bash
python3 scripts/audit_swe_saved_cases.py \
  --run-dir results/large-swe/coder30b-expanded-recovery/run \
  --baseline-dir results/swe-expanded-v1/baseline \
  --output-dir results/large-swe/coder30b-expanded-recovery/case-audit-new
```

输出目录必须是新的。脚本仅分析保存的内容，不启动模型或 sandbox，也不重新计算、替换原 reward。
