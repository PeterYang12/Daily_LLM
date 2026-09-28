#!/usr/bin/env python3
"""Serial, resumable execution of explicit benchmark cells with finite sampling."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUNTIME = Path('/data2/yuhanya/e2b-benchmark/session-20260920')
PYTHON = RUNTIME / 'env/bin/python'
RUNNER = ROOT / 'phase1-service/code/runner.py'
SAMPLER = ROOT / 'phase0-environment/sample_resources.py'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('matrix',type=Path)
    parser.add_argument('--only', help='run only matching cell id prefix')
    args=parser.parse_args()
    cells=json.loads(args.matrix.read_text())['cells']
    if args.only: cells=[c for c in cells if c['id'].startswith(args.only)]
    for number,cell in enumerate(cells,1):
        phase=cell['phase']
        directory=RUNTIME/phase
        directory.mkdir(parents=True,exist_ok=True)
        out=directory/(cell['id']+'.jsonl')
        if out.exists():
            rows=[json.loads(l) for l in out.read_text().splitlines()]
            final=next((r for r in reversed(rows) if r['record_type']=='run'),None)
            if final and final.get('cleanup_ok') and final.get('stop_reason')=='completed':
                print(json.dumps({'cell':cell['id'],'state':'already_complete'}),flush=True)
                continue
            raise RuntimeError(f'incomplete existing cell needs review: {out}')
        cmd=[str(PYTHON),str(RUNNER)]
        if cell.get('script'):
            cmd=[str(PYTHON),str(ROOT/cell['script'])]
        for k,v in cell['args'].items():
            cmd.extend(['--'+k.replace('_','-'),str(v)])
        cmd.extend(['--output',str(out)])
        # Guest QEMU is pinned to 192-383; client stays on a separate host subset.
        cmd=['taskset','-c','0-15',*cmd]
        stop=directory/(cell['id']+'.sampler-stop')
        resources=directory/(cell['id']+'.resources.jsonl')
        logfile=directory/(cell['id']+'.driver.log')
        stop.unlink(missing_ok=True)
        print(json.dumps({'cell':cell['id'],'position':number,'total':len(cells),
                          'state':'starting','utc':datetime.now(timezone.utc).isoformat()}),flush=True)
        manifest={'cell':cell,'command':cmd,'runner_sha256':hashlib.sha256(Path(cmd[4]).read_bytes()).hexdigest(),
                  'started_utc':datetime.now(timezone.utc).isoformat()}
        (directory/(cell['id']+'.manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
        with logfile.open('x') as log:
            child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
            sample=subprocess.Popen(['python3','-B',str(SAMPLER),'--output',str(resources),
                 '--interval','2','--samples','600','--pid',str(child.pid),'--stop-file',str(stop)],
                 stdout=log,stderr=subprocess.STDOUT)
            try:
                result=child.wait(timeout=1100)
            except BaseException:
                child.send_signal(signal.SIGINT)
                try:child.wait(timeout=150)
                except subprocess.TimeoutExpired: child.terminate()
                raise
            finally:
                stop.touch()
                try:sample.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    sample.terminate();sample.wait(timeout=10)
            manifest.update(returncode=result,sampler_returncode=sample.returncode,
                            finished_utc=datetime.now(timezone.utc).isoformat())
            (directory/(cell['id']+'.manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps({'cell':cell['id'],'state':'finished','returncode':result,
                          'tail':logfile.read_text()[-1500:]}),flush=True)
        if result:raise RuntimeError('cell failed; remaining cells not started')
        time.sleep(1)


if __name__=='__main__': main()
