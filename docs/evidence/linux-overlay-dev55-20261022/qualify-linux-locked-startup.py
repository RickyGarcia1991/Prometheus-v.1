"""Generated data only; locked HTTP startup/restart with no dispatch."""
import http.client,json,os,pathlib,sys,threading
root=pathlib.Path('/home/ricky-garcia/prometheus-linux-dependency-rehearsal-20261023a')
data=root/'locked-startup';data.mkdir(exist_ok=False)
os.environ['XDG_STATE_HOME']=str(data/'state')
os.environ['XDG_DATA_HOME']=str(data/'data')
sys.path.insert(0,str(root/'source-rehearsal/source/src'))
from prometheus_assistant.ui_server import Interface,Server
gate=data/'OwnerAccess';gate.mkdir();(gate/'required.json').write_text('{}')
checks=[]
for attempt in range(2):
 app=Interface(data/'memory.sqlite3');server=None;thread=None
 try:
  server=Server(('127.0.0.1',0),app)
  assert server.owner_access.required and server.owner_access.ready
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  conn=http.client.HTTPConnection('localhost',server.server_port,timeout=10)
  conn.request('GET','/');response=conn.getresponse();body=response.read()
  assert response.status==200 and b'Unlock Prometheus' in body
  conn.close()
  assert not server.owner_access.authenticated('') and not app.jobs and app.current is None
  checks.append(dict(attempt=attempt+1,locked_page=True,no_jobs=True))
 finally:
  if server:
   server.shutdown();server.server_close()
  if thread:thread.join(timeout=10);assert not thread.is_alive()
  app.close()
receipt=dict(passed=True,checks=checks,actual_owner_enrollment=False,full_application_qualified=False,production_changed=False)
(data/'result.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
