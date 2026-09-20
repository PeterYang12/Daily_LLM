import json
import sys
import time
from runtime_common import ROOT, save

code = int(sys.argv[1])
state = json.loads((ROOT/'state.json').read_text())
state.update(status='process_exited' if code == 0 else 'failed', exit_code=code, exited_unix=time.time())
save(ROOT/'state.json', state)
save(ROOT/'results'/f'attempt-{state.get("attempt", 1)}-exit.json', state)
