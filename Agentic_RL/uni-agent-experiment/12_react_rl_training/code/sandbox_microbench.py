"""Measure fixed E2B operations and in-sandbox body time; keep every sample."""
import argparse,asyncio,json,re,shlex,statistics,time
from pathlib import Path
from e2b import AsyncSandbox
from ua_rl.common import ROOT,load_e2b,save

async def main(args):
    load_e2b();out=ROOT/'results'/args.name;out.mkdir(parents=True,exist_ok=False)
    sb=None;rows=[]
    try:
        t=time.monotonic()
        sb=await AsyncSandbox.create('vatm00lmw03h9y86956r',timeout=600,request_timeout=60,metadata={'experiment':'ua-rl-microbench'})
        create_s=time.monotonic()-t
        for operation in ['health','plain_bash','login_bash','output_64KiB','file_1KiB','file_64KiB']:
            for repeat in range(args.repeats+2):
                t=time.monotonic();body_s=None
                if operation=='health':assert await sb.is_running()
                elif operation.startswith('file_'):
                    size=1024 if operation=='file_1KiB' else 65536;content=b'x'*size
                    await sb.files.write('/tmp/ua-micro.bin',content,user='root')
                    assert bytes(await sb.files.read('/tmp/ua-micro.bin',format='bytes',user='root'))==content
                else:
                    body="head -c 65536 /dev/zero | tr '\\0' x" if operation=='output_64KiB' else ':'
                    measured='t=$EPOCHREALTIME; '+body+"; printf '\\nUA_TIME %s %s\\n' \"$t\" \"$EPOCHREALTIME\" >&2"
                    command=shlex.join(['bash','-lc' if operation=='login_bash' else '-c',measured])
                    result=await sb.commands.run(command,user='root',timeout=30)
                    match=re.search(r'UA_TIME ([0-9.]+) ([0-9.]+)',result.stderr)
                    assert result.exit_code==0 and match
                    body_s=float(match[2])-float(match[1])
                elapsed=time.monotonic()-t
                if repeat>=2:rows.append({'operation':operation,'repeat':repeat-2,'wall_s':elapsed,'body_s':body_s,'non_body_s':None if body_s is None else elapsed-body_s})
    finally:
        if sb:await sb.kill()
    summary={}
    for operation in {r['operation'] for r in rows}:
        values=sorted(r['wall_s'] for r in rows if r['operation']==operation)
        summary[operation]={'n':len(values),'mean_ms':statistics.mean(values)*1000,'median_ms':statistics.median(values)*1000,'p95_ms':values[min(len(values)-1,int(.95*len(values)))]*1000}
    save(out/'result.json',{'create_s':create_s,'rows':rows,'summary':summary,'all_stopped':not await sb.is_running(),'note':'Non-body time includes dispatch, Bash startup, transport, and SDK/output handling; not pure network RTT.'})
    print(json.dumps(summary))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--name',required=True);p.add_argument('--repeats',type=int,default=20)
    asyncio.run(main(p.parse_args()))
