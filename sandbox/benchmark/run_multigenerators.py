#!/usr/bin/env python3
"""Run the explicitly selected, sequential four-generator comparisons."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
RUNTIME=Path('/data2/yuhanya/e2b-benchmark/session-20260920')
PYTHON=RUNTIME/'env/bin/python'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--skip-first-service',action='store_true')
    args=parser.parse_args()
    for repeat in (1,2,3):
        for adapter in ('service','sdk'):
            if args.skip_first_service and repeat==1 and adapter=='service':continue
            phase='phase1-service' if adapter=='service' else 'phase2-sdk'
            out=RUNTIME/phase/f'multigen-{adapter}-r{repeat}'
            command=['taskset','-c','0-15',str(PYTHON),
                     str(ROOT/'phase1-service/code/multigenerator.py'),
                     '--adapter',adapter,'--duration','30','--repeat',str(repeat),
                     '--sample-resources','--output-dir',str(out)]
            print(json.dumps({'adapter':adapter,'repeat':repeat,'command':command}),flush=True)
            subprocess.run(command,check=True)

if __name__=='__main__':main()
