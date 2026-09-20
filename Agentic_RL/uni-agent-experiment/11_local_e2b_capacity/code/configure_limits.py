"""Set a local database project-limit override; preserve other effective limits."""
import json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-e2b-large']
def sql(query):
    p=subprocess.run(SSH+['sudo k3s kubectl -n e2b exec -i e2b-0 -c postgres -- psql -X -U postgres -d postgres -v ON_ERROR_STOP=1 -At'],input=query,text=True,capture_output=True,check=True)
    return p.stdout.strip()
query="SELECT row_to_json(l) FROM public.team_limits l JOIN public.teams t ON t.id=l.id WHERE t.name='local-dev team';"
before=json.loads(sql(query))
team=before['id']
assert sql(f"SELECT count(*) FROM public.teams WHERE id='{team}' AND name='local-dev team';")=='1'
columns=['max_length_hours','concurrent_sandboxes','concurrent_template_builds','max_vcpu','max_ram_mb','disk_mb','events_ttl_days','default_free_disk_size_mb','max_disk_size_mb','max_free_disk_size_mb']
values={k:before[k] for k in columns};values['concurrent_sandboxes']=64
statement="INSERT INTO public.project_limits (team_id,"+','.join(columns)+") VALUES ('"+team+"',"+','.join(str(int(values[k])) for k in columns)+") ON CONFLICT (team_id) DO UPDATE SET concurrent_sandboxes=EXCLUDED.concurrent_sandboxes, updated_at=now();"
(ROOT/'configs/project-limits.sql').write_text(statement+'\n')
sql(statement)
after=json.loads(sql(query))
assert after['concurrent_sandboxes']==64
assert all(before[k]==after[k] for k in before if k!='concurrent_sandboxes')
(ROOT/'results/project-limits.json').write_text(json.dumps({'time_unix':time.time(),'method':'local administrator SQL override of public.project_limits; no billing service','before':before,'after':after},indent=2)+'\n')
print('Local team concurrency: '+str(before['concurrent_sandboxes'])+' -> 64; other limits preserved.')
