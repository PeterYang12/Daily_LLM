import json
from pathlib import Path
r=Path(__file__).resolve().parents[1];d=json.loads((r/'evidence/summary/final-summary.json').read_text());ids=json.loads((r/'evidence/summary/validated-manifest.json').read_text())['instances'];rows={(x['agent'],x['instance_id']):x for x in d['rows']};labels={'react':'ReAct','claude':'Claude Code','mini':'mini-swe-agent'}
s='''# 全部28题的正式结果矩阵

每个单元格链接到本目录的轻量结果JSON；JSON注明原始完整证据路径和SHA。通过/未通过来自统一归因后的新容器判题。模型没有因回放而再次采样。

| Instance | ReAct | Claude Code | Mini |
|---|---|---|---|
'''
for iid in ids:
 cells=[]
 for mode,label in labels.items():
  q=rows[label,iid];text='通过' if q['resolved'] else '未通过'
  if not q['finished']:text+='；未正常结束'
  if q['existing_tests_modified']:text+='；改动既有测试'
  cells.append(f'[{text}](../evidence/cases/{mode}/{iid}/result.json)')
 s+='| '+iid+' | '+' | '.join(cells)+' |\n'
s+='''
## 汇总

| 指标 | ReAct | Claude Code | Mini |
|---|---:|---:|---:|
| 独立判题通过 | 17/28 | 11/28 | 12/28 |
| 剥离测试路径后通过 | 17/28 | 11/28 | 12/28 |
| 正常结束 | 27/28 | 28/28 | 25/28 |
| 判题完成 | 28/28 | 28/28 | 28/28 |
| 修改既有测试 | 0题 | 2题 | 1题 |

CSV保留原实验的84行字段；其中`result_path`相对原始lab根目录，本文矩阵链接则指向文档仓库内的证据副本。[下载CSV](per-case-results.csv) · [精确JSON](../evidence/summary/final-summary.json)。

主统计只使用这28题。先前pilot、API smoke、独立轨迹测试和E2B代码任务不混入分数。
'''
(r/'results/03-per-case-matrix.md').write_text(s)
print('Wrote matrix for',len(ids),'instances')
