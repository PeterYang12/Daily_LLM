from pathlib import Path
import json,time
root=Path(__file__).resolve().parents[1]
for path in sorted((root/'results').glob('*')):
 rows=[]
 for p in path.glob('*/result.json'):
  try: rows.append(json.loads(p.read_text()))
  except Exception: pass
 if rows:
  print(path.name, 'completed',len(rows),'resolved',sum(bool(r.get('resolved')) for r in rows),'eval_completed',sum(bool(r.get('eval_completed')) for r in rows),'finished',sum(bool(r.get('finished')) for r in rows),'errors',sum(bool(r.get('error') or r.get('agent_error')) for r in rows))
