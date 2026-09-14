import os,runpy
from pathlib import Path
os.environ.update(dict(line.split('=',1) for line in Path('/lab/secrets/e2b.env').read_text().splitlines()))
os.environ.update(SANDBOX_PROVIDER='e2b_compat',IMAGE='testlab-python-node',DEBUG_MODE='1')
import e2b_provider
runpy.run_path('/lab/src/uni-agent/examples/quickstart/sandbox/demo.py',run_name='__main__')
