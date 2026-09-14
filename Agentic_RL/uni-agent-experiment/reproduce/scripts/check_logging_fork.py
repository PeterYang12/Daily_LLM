"""Deterministic regression: fork while the log writer holds a buffered-I/O lock."""
import argparse,io,json,os,select,signal,threading,time
from pathlib import Path
from uni_agent.logging.handlers import _dispatch
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--expect-deadlock',action='store_true');a=p.parse_args()
rfd,wfd=os.pipe();stream=io.TextIOWrapper(io.BufferedWriter(io.FileIO(wfd,'wb',closefd=True)),encoding='utf-8')
_dispatch._files['fork-regression']=stream
payload='x'*(1024*1024);_dispatch._submit(('write','fork-regression',payload))
assert select.select([rfd],[],[],3)[0], 'writer did not fill pipe'
def drain():
 time.sleep(.2);n=0
 while n<len(payload):n+=len(os.read(rfd,65536))
t=threading.Thread(target=drain,daemon=True);t.start()
start=time.monotonic();pid=os.fork()
if pid==0:os._exit(0)
end=time.monotonic()+3;deadlock=False;status=None
while True:
 done,status=os.waitpid(pid,os.WNOHANG)
 if done:break
 if time.monotonic()>end:
  deadlock=True;os.kill(pid,signal.SIGKILL);_,status=os.waitpid(pid,0);break
 time.sleep(.01)
t.join(2);_dispatch._shutdown();os.close(rfd)
record={'child_deadlocked':deadlock,'elapsed_s':time.monotonic()-start,'child_status':status,'expected_deadlock':a.expect_deadlock,'passed':deadlock==a.expect_deadlock}
Path(a.output).write_text(json.dumps(record,indent=2));print(json.dumps(record));assert record['passed']
