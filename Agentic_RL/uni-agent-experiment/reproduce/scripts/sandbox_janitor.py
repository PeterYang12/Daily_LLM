"""Independent lease cleanup for explicitly labelled experiment sandboxes only."""
import argparse,http.client,json,socket,time,urllib.parse
class UnixHTTPConnection(http.client.HTTPConnection):
 def __init__(self,path):super().__init__('localhost',timeout=10);self.path=path
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)
def call(path,method,endpoint):
 c=UnixHTTPConnection(path)
 try:
  c.request(method,endpoint);r=c.getresponse();data=r.read();return r.status,json.loads(data) if data else None
 finally:c.close()
def sweep(args):
 filters=json.dumps({'label':['ua-lab.owner='+args.owner]})
 status,rows=call(args.socket,'GET','/containers/json?all=1&filters='+urllib.parse.quote(filters));assert status==200,(status,rows)
 result=[]
 for row in rows:
  labels=row.get('Labels',{});ttl=labels.get('ua-lab.ttl-seconds')
  if not ttl:continue
  try:ttl=int(ttl)
  except ValueError:continue
  if ttl<=0:continue
  age=time.time()-row['Created'];expired=age>ttl
  item={'id':row['Id'],'names':row['Names'],'age_seconds':age,'ttl_seconds':ttl,'expired':expired,'deleted':False}
  if expired and args.delete:
   code,body=call(args.socket,'DELETE','/containers/'+row['Id']+'?force=1&v=1');item['status']=code;item['deleted']=code in (204,404)
  result.append(item)
 print(json.dumps({'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'owner':args.owner,'delete_enabled':args.delete,'containers':result}),flush=True)
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--socket',default='/run/ua/docker.sock');p.add_argument('--owner',default='uni-agent-rocm-lab');p.add_argument('--delete',action='store_true');p.add_argument('--once',action='store_true');p.add_argument('--interval',type=float,default=30);a=p.parse_args()
 while True:
  try:sweep(a)
  except Exception as e:print(json.dumps({'error':repr(e)}),flush=True)
  if a.once:break
  time.sleep(a.interval)
