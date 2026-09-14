import json,subprocess
from pathlib import Path
root=Path('/lab/cache/tmux'); (root/'lib').mkdir(parents=True,exist_ok=True); (root/'bin').mkdir(exist_ok=True)
image='swebench/sweb.eval.x86_64.pallets_1776_flask-5014'
name='ua-lab-tmux-build'
def run(args,**kw): return subprocess.run(['docker',*args],check=True,**kw)
run(['run','-d','--name',name,'--network','host','--entrypoint','sleep',image,'infinity'])
try:
 run(['exec',name,'bash','-lc','apt-get update -qq && apt-get install -y -qq tmux'])
 run(['cp',name+':/usr/bin/tmux',str(root/'bin/tmux-real')])
 output=subprocess.check_output(['docker','exec',name,'ldd','/usr/bin/tmux'],text=True)
 (root/'ldd.txt').write_text(output)
 for line in output.splitlines():
  if '=>' in line:
   path=line.split('=>')[1].strip().split()[0]
   if path.startswith('/') and not any(x in path for x in ['libc.so','libpthread','libdl.so','librt.so']): run(['cp',name+':'+path,str(root/'lib'/Path(path).name)])
 (root/'tmux').write_text('#!/bin/sh\nexport LD_LIBRARY_PATH=/opt/ua-tmux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexec /opt/ua-tmux/bin/tmux-real "$@"\n')
 (root/'tmux').chmod(0o755)
 (root/'provenance.json').write_text(subprocess.check_output(['docker','exec',name,'bash','-lc',"dpkg-query -W tmux libevent-core-2.1-7 libtinfo6"],text=True))
finally: run(['rm','-f',name])
