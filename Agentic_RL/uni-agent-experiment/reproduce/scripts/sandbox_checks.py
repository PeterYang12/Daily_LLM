import asyncio,dataclasses,json,time,argparse
from pathlib import Path
from lab_runtime import EvidenceDocker,dump
from uni_agent.sandbox.docker import DockerSandbox

parser=argparse.ArgumentParser();parser.add_argument('--output',default='/lab/results/sandbox-checks-v3');args=parser.parse_args()

async def main():
 root=Path(args.output); root.mkdir(exist_ok=False)
 args=['--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','256m','--cpus','1','--pids-limit','128']
 checks={}
 async with EvidenceDocker(image='ua-lab/demo:20260912',evidence_dir=root/'a',pull_policy='never',run_args=args) as a, EvidenceDocker(image='ua-lab/demo:20260912',evidence_dir=root/'b',pull_policy='never',run_args=args) as b:
  names=[a._require_container(),b._require_container()]
  r=await a.exec_shell('for p in /lab /home/yuhanya/uni-agent-lab /var/run/docker.sock /run/ua/docker.sock /dev/kfd /dev/dri; do test ! -e "$p" || exit 1; done; echo no_host_mount_socket_or_gpu')
  checks['host_resources_absent']=r.exit_code==0
  await a.write_file('/tmp/private-marker','sandbox-a')
  checks['file_persistence']=(await a.read_file('/tmp/private-marker'))==b'sandbox-a'
  checks['cross_sandbox_file_isolation']=(await b.exec(['test','!','-e','/tmp/private-marker'])).exit_code==0
  data=(b'\x00hello\xe4\xb8\xad\xe6\x96\x87\n'*100000)
  await a.write_file('/tmp/large.bin',data)
  checks['binary_large_transfer']=(await a.read_file('/tmp/large.bin'))==data
  try:
   await DockerSandbox.write_file(a,'/tmp/upstream-large.bin',data)
   checks['upstream_large_write']='unexpectedly_succeeded'
  except Exception as e: checks['upstream_large_write']={'error_type':type(e).__name__,'error':str(e)[:200]}
  r=await a.exec(['python','-c','import socket; s=socket.socket(); s.settimeout(1); s.connect(("1.1.1.1",443))'])
  checks['network_egress_blocked']=r.exit_code!=0
  r=await a.exec_shell('cat /proc/self/status | sed -n "/CapEff/p;/NoNewPrivs/p"; cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max /sys/fs/cgroup/cpu.max')
  checks['kernel_limits']=r.stdout
  try:
   r=await a.exec(['sleep','5'],timeout=0.3)
   checks['exec_timeout_result']=dataclasses.asdict(r)
   checks['exec_timeout_reported']=r.exit_code==-1 and 'timed out' in r.stderr
  except asyncio.TimeoutError: checks['exec_timeout_reported']=True
  checks['timed_out_process_may_survive_until_sandbox_stop']=(await a.exec_shell('ps -eo comm | grep -c "^sleep$"')).stdout.strip()
  dump(root/'during.json',checks)
 for n in names:
  checks['destroyed_'+n]=(await a._run_docker('inspect',n)).exit_code!=0
 checks['all_required_pass']=all(checks.get(k) for k in ['host_resources_absent','file_persistence','cross_sandbox_file_isolation','binary_large_transfer','network_egress_blocked','exec_timeout_reported']) and all(checks['destroyed_'+n] for n in names)
 dump(root/'summary.json',checks); print(json.dumps(checks,indent=2))
asyncio.run(main())
