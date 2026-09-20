"""Compare direct envd execution with the SDK wrapper and validate tool semantics."""
import asyncio,json,statistics,time
from pathlib import Path
from uni_agent.tools import Toolbox
from ua_rl.common import ROOT,Profile,save
from ua_rl.provider import E2BRL
from ua_rl.tasks import prepare,reward

async def main():
    p=Profile('native-exec-acceptance',ROOT/'results/native-exec-acceptance')
    meta=json.loads((ROOT/'data/candidates.json').read_text())['validation'][0]['row']['extra_info']['tools_kwargs']['task']['metadata']
    report={'checks':{},'latencies':[]}
    with p.active():
        sb=E2BRL('vatm00lmw03h9y86956r',600,native_exec=True)
        try:
            await sb.start();await prepare(sb,meta,tools=True)
            for repeat in range(22):
                modes=['native','sdk'] if repeat%2 else ['sdk','native']
                for mode in modes:
                    sb.native_exec=mode=='native';t=time.monotonic()
                    r=await sb.exec(['/bin/bash','-c',':']);assert r.exit_code==0
                    if repeat>=2:report['latencies'].append({'mode':mode,'repeat':repeat-2,'wall_s':time.monotonic()-t})
            sb.native_exec=True
            r=await sb.exec(['/bin/bash','-c','printf "first\\nsecond\\n"; printf "err\\n" >&2; exit 7'])
            report['checks']['output_and_exit']=r.exit_code==7 and r.stdout=='first\nsecond\n' and r.stderr=='err\n'
            r=await sb.exec(['/usr/bin/printf','%s','x;$(echo wrong)'])
            report['checks']['literal_argv']=r.stdout=='x;$(echo wrong)'
            r=await sb.exec(['/bin/bash','-c','printf "%s:%s" "$PWD" "$UA_TEST"'],workdir='/tmp',env={'UA_TEST':'kept'})
            report['checks']['cwd_env']=r.stdout=='/tmp:kept'
            r=await sb.exec(['/bin/bash','-c',"printf '\\n\\n'; head -c 100000 /dev/zero | tr '\\0' x; printf '\\n'"])
            report['checks']['large_output']=r.stdout=='\n\n'+'x'*100000+'\n'
            payload=bytes(range(256))*4096
            await sb.write_file('/tmp/ua-native.bin',payload)
            report['checks']['binary_file']=await sb.read_file('/tmp/ua-native.bin')==payload
            tools=Toolbox.from_specs([{'name':'stateful_shell','command_timeout':10}],sandbox=sb)
            async with tools:
                await tools.call('shell',{'command':'mkdir -p /tmp/ua-native-state; cd /tmp/ua-native-state; export UA_PERSIST=retained'})
                result=await tools.call('shell',{'command':'printf "%s:%s" "$PWD" "$UA_PERSIST"'})
                report['checks']['persistent_shell']='/tmp/ua-native-state:retained' in result.text
                result=await tools.call('shell',{'command':"python -c \"import sys, flask; assert 'envs/testbed' in sys.executable; print('CONDA_OK')\""})
                report['checks']['conda_environment']='CONDA_OK' in result.text and '[exit code: 0]' in result.text
            result=await sb.exec(['/bin/bash','-c','sleep 2; touch /tmp/native-timeout-marker'],timeout=.2)
            report['checks']['timeout_nonzero']=result.exit_code!=0
            future=asyncio.create_task(sb.exec(['/bin/bash','-c','sleep 2; touch /tmp/native-cancel-marker'],timeout=10))
            await asyncio.sleep(.2);future.cancel()
            try:await future
            except asyncio.CancelledError:report['checks']['cancellation_propagates']=True
            await asyncio.sleep(2.1)
            for kind in ['timeout','cancel']:
                result=await sb.exec(['/usr/bin/test','-e',f'/tmp/native-{kind}-marker'])
                report['checks'][kind+'_stops_process']=result.exit_code==1
            baseline=await reward('swe_bench',meta,sb,300);save(p.folder/'baseline.json',baseline)
            report['checks']['baseline']=baseline['eval_completed'] and not baseline['resolved']
        finally:await sb.stop()
        sb=E2BRL('vatm00lmw03h9y86956r',600,native_exec=True)
        try:
            await sb.start();await prepare(sb,meta)
            await sb.write_file('/tmp/native-gold.patch',meta['patch'])
            r=await sb.exec(['git','apply','/tmp/native-gold.patch'],workdir='/testbed');assert r.exit_code==0
            oracle=await reward('swe_bench',meta,sb,300);save(p.folder/'oracle.json',oracle)
            report['checks']['oracle']=oracle['eval_completed'] and oracle['resolved']
        finally:await sb.stop();p.save()
    report['latency_summary']={}
    for mode in ['sdk','native']:
        values=sorted(r['wall_s'] for r in report['latencies'] if r['mode']==mode)
        report['latency_summary'][mode]={'mean_ms':statistics.mean(values)*1000,'median_ms':statistics.median(values)*1000,'p95_ms':values[18]*1000,'n':len(values)}
    report['success']=all(report['checks'].values());save(p.folder/'summary.json',report)
    print(json.dumps(report));assert report['success']

if __name__=='__main__':asyncio.run(main())
