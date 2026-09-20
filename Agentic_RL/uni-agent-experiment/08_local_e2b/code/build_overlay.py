"""Generate index-checked JSON patches without reordering sidecar init containers."""
import json
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
directory=ROOT/'configs/deploy'
base=yaml.safe_load((directory/'upstream/statefulset.yaml').read_text())
spec=base['spec']['template']['spec'];names=[x['name'] for x in spec['initContainers']]
expected=['preflight','host-setup','fetch-artifacts','postgres','redis','clickhouse','vector','db-migrator','clickhouse-migrator','seed','orchestrator','api','client-proxy','base-template']
assert names==expected,names
patch=[]
def modify(name,tail,value,op='replace'):
    index=names.index(name);prefix=f'/spec/template/spec/initContainers/{index}'
    patch.append({'op':'test','path':prefix+'/name','value':name})
    patch.append({'op':op,'path':prefix+tail,'value':value})
modify('host-setup','/env/0/value','16384')
modify('orchestrator','/resources',{'requests':{'hugepages-2Mi':'32Gi','memory':'2Gi'},'limits':{'hugepages-2Mi':'32Gi'}})
modify('clickhouse','/resources',{'requests':{'memory':'512Mi','cpu':'250m'},'limits':{'memory':'4Gi','cpu':'2'}},'add')
modify('base-template','/volumeMounts/-',{'name':'ua-base-template-builder','mountPath':'/app/build-base-template.mjs','subPath':'build-base-template.mjs','readOnly':True},'add')
command='''umask 077
test -s /run/e2b/team-api-key || exit 1
last=""
while :; do
  key="$(cat /run/e2b/team-api-key 2>/dev/null)"
  if [ -n "$key" ] && [ "$key" != "$last" ]; then
    printf 'export E2B_API_KEY=%s\\nexport E2B_API_URL=http://%s:3000\\nexport E2B_SANDBOX_URL=http://%s:3002\\n' "$key" "$NODE_IP" "$NODE_IP" > /run/e2b/sdk.env
    echo 'E2B SDK settings ready (credentials stored in seed-state; not logged)'
    last="$key"
  fi
  sleep 5
done
'''
patch.append({'op':'replace','path':'/spec/template/spec/containers/0/command','value':['/bin/sh','-c',command]})
patch.append({'op':'add','path':'/spec/template/spec/volumes/-','value':{'name':'ua-base-template-builder','configMap':{'name':'ua-base-template-builder'}}})
(directory/'overlay/patches.json').write_text(json.dumps(patch,indent=2)+'\n')
print('Generated patches for pinned init order')
