"""Read non-secret guest capacity and process metrics, once or each second."""
import argparse,glob,json,os,time
from pathlib import Path

def sample():
    mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
    cpu=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
    fc=[]
    for name in glob.glob('/proc/[0-9]*/comm'):
        try:
            if Path(name).read_text().strip()=='firecracker':fc.append(int(Path(name).parent.name))
        except (FileNotFoundError,ProcessLookupError):pass
    disk=os.statvfs('/')
    nbd=Path('/sys/module/nbd/parameters/nbds_max')
    return {'time_unix':time.time(),'cpu_count':os.cpu_count(),'cpu_ticks':cpu,'clock_ticks':os.sysconf('SC_CLK_TCK'),
            'meminfo':{k:mem[k] for k in ['MemTotal','MemAvailable','HugePages_Total','HugePages_Free','HugePages_Rsvd','Hugepagesize']},
            'firecracker_count':len(fc),'firecracker_pids':fc,
            'nbd_devices':int(nbd.read_text()) if nbd.exists() else None,
            'disk_total_bytes':disk.f_blocks*disk.f_frsize,'disk_available_bytes':disk.f_bavail*disk.f_frsize,
            'pressure':{p:Path('/proc/pressure/'+p).read_text().strip() for p in ['cpu','memory','io']}}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--watch',action='store_true');p.add_argument('--stop',default='/tmp/ua-capacity-monitor-stop')
    a=p.parse_args();deadline=time.monotonic()+600
    while True:
        print(json.dumps(sample()),flush=True)
        if not a.watch or Path(a.stop).exists() or time.monotonic()>deadline:break
        time.sleep(1)
