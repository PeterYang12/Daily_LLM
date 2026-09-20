"""After the main runs, isolate Bash login initialization inside one disposable VM."""
import argparse,asyncio,json,re,statistics,sys,time
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
from run_e2b_e2e import load_environment
from e2b import AsyncSandbox

async def main(root):
    while not (root/'COMPLETE').exists():await asyncio.sleep(3)
    protocol=json.loads((root/'protocol.json').read_text());path=Path(protocol['variants']['local_e2b'])
    template=json.loads((path/'templates.json').read_text())[0]['build']['template_id']
    load_environment('/lab/secrets/local-e2b/container.env');sb=None
    report={'scope':'Diagnostic only, after all main rollouts; no profile files modified.'}
    try:
        sb=await AsyncSandbox.create(template,timeout=180,metadata={'experiment':root.name,'purpose':'login-startup-diagnosis'})
        script='''for i in $(seq 1 10); do
if [ $((i % 2)) -eq 0 ]; then modes="login plain"; else modes="plain login"; fi
for mode in $modes; do
TIMEFORMAT="UA_BASH_TIME $mode %R %U %S"
if [ "$mode" = login ]; then time /bin/bash -l -c true; else time /bin/bash --noprofile --norc -c true; fi
done
done'''
        result=await sb.commands.run(script,user='root',timeout=60)
        rows=[{'mode':mode,'wall_s':float(w),'user_s':float(u),'system_s':float(s)} for mode,w,u,s in re.findall(r'UA_BASH_TIME (login|plain) ([0-9.]+) ([0-9.]+) ([0-9.]+)',result.stderr)]
        assert len(rows)==20,(result.stdout,result.stderr)
        report['samples']=rows;report['median_s']={m:statistics.median(x['wall_s'] for x in rows if x['mode']==m) for m in ['login','plain']}
        files={}
        for file in ['/etc/profile','/root/.bashrc','/root/.profile']:
            content=await sb.files.read(file,user='root')
            files[file]=[{'line':i+1,'text':line} for i,line in enumerate(content.splitlines()) if any(k in line for k in ['conda','activate','.bashrc','.profile'])]
        report['initialization_lines']=files;report['success']=True
    except Exception as exc:report['success']=False;report['error']=str(exc)
    finally:
        if sb:report['kill_ack']=await sb.kill()
        (root/'login-probe.json').write_text(json.dumps(report,indent=2)+'\n')
        (root/'LOGIN_PROBE_COMPLETE').write_text(str(time.time())+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ['samples','initialization_lines']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);asyncio.run(main(p.parse_args().root))
