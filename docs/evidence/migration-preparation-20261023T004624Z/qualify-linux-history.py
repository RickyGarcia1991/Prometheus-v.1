"""Fresh-process generated history and verified restore, no live data."""
import hashlib,json,os,pathlib,subprocess,sys
root=pathlib.Path('/home/ricky-garcia/prometheus-linux-dependency-rehearsal-20261023a')
dest=root/'history-rehearsal';dest.mkdir(exist_ok=False)
env=os.environ.copy();env['PYTHONPATH']=str(root/'source-rehearsal/source/src')
python=root/'venv/bin/python'
writer=r'''
import json,pathlib,sys
from prometheus_assistant.memory import MemoryStore
root=pathlib.Path(sys.argv[1])
with MemoryStore(root/'history.sqlite3') as db:
 sid=db.create_session('generated-fixture-no-model')
 db.save_exchange(sid,'Generated history probe','Generated response',label='Fixture',details={'model_used':False})
 (root/'expected.json').write_text(json.dumps({'session':sid,'history':db.history(sid,include_metadata=True)}))
'''
reader=r'''
import hashlib,json,pathlib,sys
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.recovery import backup_memory,restore_memory
root=pathlib.Path(sys.argv[1]);expected=json.loads((root/'expected.json').read_text())
with MemoryStore(root/'history.sqlite3') as db:assert db.history(expected['session'],include_metadata=True)==expected['history']
backup_memory(root/'history.sqlite3',root/'backup.sqlite3')
restore_memory(root/'backup.sqlite3',root/'restored.sqlite3')
with MemoryStore(root/'restored.sqlite3') as db:assert db.history(expected['session'],include_metadata=True)==expected['history']
before=(root/'restored.sqlite3').read_bytes()
try:restore_memory(root/'backup.sqlite3',root/'restored.sqlite3')
except FileExistsError:pass
else:raise AssertionError('Existing restore target overwritten')
assert (root/'restored.sqlite3').read_bytes()==before
print(json.dumps(dict(fresh_process_history_equal=True,restored_history_equal=True,existing_restore_target_preserved=True,generated_data_only=True,production_changed=False)))
'''
subprocess.run([str(python),'-B','-c',writer,str(dest)],env=env,check=True,timeout=30)
out=subprocess.run([str(python),'-B','-c',reader,str(dest)],env=env,check=True,capture_output=True,text=True,timeout=30)
receipt=json.loads(out.stdout);(dest/'result.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
