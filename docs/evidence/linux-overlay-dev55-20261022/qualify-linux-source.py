"""Isolated source/import rehearsal; never launches app, models or workers."""
import hashlib, json, pathlib, subprocess, tarfile, urllib.request
root=pathlib.Path('/home/ricky-garcia/prometheus-linux-dependency-rehearsal-20261023a')
dest=root/'source-rehearsal';dest.mkdir(exist_ok=False)
base='https://raw.githubusercontent.com/RickyGarcia1991/Prometheus-v.1/2186df9599ab3d465b1c8275877c67468f80d879/docs/evidence/linux-overlay-dev55-20261022/'
data=urllib.request.urlopen(base+'source-overlay-dev55.tar.gz',timeout=60).read()
assert hashlib.sha256(data).hexdigest()=='d42304c98442d80ebefad32592fa2969341937eec9767a0c1b4495952a7d9f42'
archive=dest/'source.tar.gz';archive.write_bytes(data)
manifest=json.loads(urllib.request.urlopen(base+'linux-source-overlay-manifest.json',timeout=60).read())
with tarfile.open(archive) as t:
 for m in t.getmembers():
  assert m.isfile() or m.isdir()
 t.extractall(dest,filter='data')
source=dest/'source'
for row in manifest['included']:
 p=source/row['path'];p.resolve().relative_to(source.resolve())
 assert p.stat().st_size==row['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']
probe=r'''
import importlib,json,pathlib,sys
source=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(source/'src'))
compiled=0
for p in source.rglob('*.py'):
 compile(p.read_bytes(),str(p),'exec');compiled+=1
results=[]
for name in ['prometheus_assistant.cli','prometheus_assistant.owner_access','prometheus_assistant.approvals','prometheus_assistant.memory']:
 try:importlib.import_module(name);results.append(dict(module=name,passed=True))
 except Exception as e:results.append(dict(module=name,passed=False,error=type(e).__name__+': '+str(e)))
from prometheus_assistant.owner_access import OwnerAccess
gate=source.parent/'generated-owner-gate';gate.mkdir();(gate/'required.json').write_text('{}')
owner=OwnerAccess(gate,'http://localhost:54999')
assert owner.required and owner.ready and not owner.authenticated('')
print(json.dumps(dict(compiled_files=compiled,imports=results,required_owner_gate_locked=True,actual_owner_enrollment=False,full_application_qualified=False,production_changed=False)))
'''
result=subprocess.run([str(root/'venv/bin/python'),'-B','-c',probe,str(source)],capture_output=True,text=True,timeout=60)
receipt=dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,archive_sha256=hashlib.sha256(data).hexdigest(),verified_source_files=len(manifest['included']))
(dest/'result.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
raise SystemExit(result.returncode)
