"""Persist passive VM samples with physical-host NIC and QEMU thread context."""
import argparse
from datetime import datetime,timezone
import gzip
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import time

ROOT=Path('/data2/yuhanya/e2b-local-20260916')
OUT=ROOT/'monitoring/performance'
HZ=os.sysconf('SC_CLK_TCK')


def rotating_logger():
    OUT.mkdir(parents=True,exist_ok=True)
    log=logging.getLogger('vm-performance');log.setLevel(logging.INFO)
    h=RotatingFileHandler(OUT/'samples.jsonl',maxBytes=32*2**20,backupCount=48)
    h.namer=lambda name:name+'.gz'
    def rotate(source,destination):
        with open(source,'rb') as a,gzip.open(destination,'wb',compresslevel=3) as b:shutil.copyfileobj(a,b)
        os.remove(source)
    h.rotator=rotate;h.setFormatter(logging.Formatter('%(message)s'));log.addHandler(h)
    return log


def host_snapshot(pid):
    threads={};net={};disks={}
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name,v=line.split(':');name=name.strip()
        if name.startswith(('en','eth')):net[name]=list(map(int,v.split()))
    p=Path('/proc')/str(pid)
    if p.exists():
        for thread in (p/'task').iterdir():
            try:
                s=(thread/'stat').read_text().rsplit(')',1)[1].split()
                threads[thread.name]={'tid':int(thread.name),'name':(thread/'comm').read_text().strip(),
                                     'start':s[19],'ticks':int(s[11])+int(s[12])}
            except (OSError,IndexError):pass
    for line in Path('/proc/diskstats').read_text().splitlines():
        parts=line.split()
        if re.fullmatch(r'nvme\d+n\d+',parts[2]):disks[parts[2]]=list(map(int,parts[3:]))
    return {'at':time.monotonic(),'threads':threads,'network':net,'disk':disks}


def host_delta(old,new):
    dt=new['at']-old['at'];threads=[];net=[];disks=[]
    for tid,row in new['threads'].items():
        prior=old['threads'].get(tid)
        if prior and prior['start']==row['start']:
            threads.append({**row,'cpu_cores':round(max(0,row['ticks']-prior['ticks'])/HZ/dt,3)})
    threads.sort(key=lambda x:x['cpu_cores'],reverse=True)
    for name,v in new['network'].items():
        if name not in old['network']:continue
        d=[max(0,n-o) for n,o in zip(v,old['network'][name])]
        net.append({'interface':name,'rx_bytes_per_s':round(d[0]/dt),'tx_bytes_per_s':round(d[8]/dt),
                    'rx_drops':d[3],'tx_drops':d[11]})
    for name,v in new['disk'].items():
        if name not in old['disk']:continue
        d=[max(0,n-o) for n,o in zip(v,old['disk'][name])];ios=d[0]+d[4]
        disks.append({'device':name,'read_bytes_per_s':round(d[2]*512/dt),
            'write_bytes_per_s':round(d[6]*512/dt),'iops':round(ios/dt,2),
            'await_ms':round((d[3]+d[7])/ios,2) if ios else 0,'busy_pct':round(d[9]/dt/10,2)})
    return {'qemu_cpu_cores':round(sum(x['cpu_cores'] for x in threads),3),
            'qemu_top_threads':threads[:12],'physical_nic_all_workloads':net,
            'physical_disks_all_workloads':disks}


def main():
    p=argparse.ArgumentParser();p.add_argument('--samples',type=int,default=0);a=p.parse_args()
    os.umask(0o077);log=rotating_logger()
    pid=int(subprocess.check_output(['systemctl','show','e2b-sandbox-vm.service','-p','MainPID','--value']))
    cmd=[str(ROOT/'code/vm-ssh'),'sudo python3 -u -B /opt/e2b-local/code/guest_perf_sampler.py --interval 5'+(f' --samples {a.samples}' if a.samples else '')]
    with (OUT/'ssh.log').open('a') as err:
        child=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
        poll=select.poll();poll.register(child.stdout,select.POLLIN|select.POLLHUP)
        old=host_snapshot(pid);tcp_at=0;tcp_info={};count=0
        try:
            while True:
                if not poll.poll(30000):raise RuntimeError('No VM sample for 30 seconds')
                line=child.stdout.readline()
                if not line:break
                sample=json.loads(line);new=host_snapshot(pid)
                if time.monotonic()-tcp_at>=30:
                    s=subprocess.run(['ss','-tinH','state','established','( sport = :8088 )'],capture_output=True,text=True,timeout=5)
                    rtts=[float(x) for x in re.findall(r'\brtt:([0-9.]+)/',s.stdout)]
                    tcp_info={'utc':datetime.now(timezone.utc).isoformat(),'rtt_ms':rtts,
                              'connections_sample':s.stdout.splitlines()[:100],
                              'scope':'Host TCP endpoint on port 8088; not SDK request elapsed time'};tcp_at=time.monotonic()
                record={'received_utc':datetime.now(timezone.utc).isoformat(),'guest':sample,
                        'host':host_delta(old,new),'front_tcp':tcp_info}
                old=new;log.info(json.dumps(record,separators=(',',':')))
                tmp=OUT/'latest.tmp';tmp.write_text(json.dumps(record,indent=2));tmp.replace(OUT/'latest.json')
                count+=1
            code=child.wait(timeout=5)
            if code:raise RuntimeError('VM sampler exited with '+str(code))
            if not a.samples:raise RuntimeError('Continuous VM sampler unexpectedly stopped')
            print(json.dumps({'samples':count,'status':'passed','directory':str(OUT)}))
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait()


if __name__=='__main__':main()
