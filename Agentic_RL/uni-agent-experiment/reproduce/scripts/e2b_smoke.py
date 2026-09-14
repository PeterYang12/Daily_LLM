import json,os,time,traceback
from pathlib import Path
from e2b import Sandbox
cfg=dict(line.split('=',1) for line in Path('/lab/secrets/e2b.env').read_text().splitlines()); os.environ.update(cfg)
root=Path('/lab/results/e2b'); rows=[]; sb=None
start=time.time()
try:
 sb=Sandbox.create(template='testlab-python-node',timeout=300,metadata={'experiment':'uni-agent-rocm-lab','owner':'user-authorized'},request_timeout=30)
 rows.append({'event':'create','sandbox_id':sb.sandbox_id,'elapsed_s':time.time()-start,'envd_api_url':sb.envd_api_url})
 for command in ['uname -a; id; python3 --version; command -v tmux || true',"printf sandbox-persist > /tmp/ua-marker; cat /tmp/ua-marker"]:
  r=sb.commands.run(command,timeout=20); rows.append({'event':'exec','command':command,'exit_code':r.exit_code,'stdout':r.stdout,'stderr':r.stderr})
 sb.files.write('/tmp/ua-binary.txt','hello sandbox 中文\n')
 rows.append({'event':'file_read','content':sb.files.read('/tmp/ua-binary.txt')})
except Exception as e: rows.append({'event':'error','type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc()})
finally:
 if sb:
  try: rows.append({'event':'kill','result':sb.kill()})
  except Exception as e: rows.append({'event':'kill_error','message':str(e)})
 text=json.dumps(rows,indent=2,default=str).replace(cfg['E2B_API_KEY'],'[REDACTED]')
 (root/'sdk-smoke-v2.json').write_text(text); print(text)
