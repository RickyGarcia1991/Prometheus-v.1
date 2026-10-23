"""Restore archived model into a new isolated local folder, then independently hash it."""
import base64, datetime, hashlib, json, shutil, subprocess, sys, uuid
from pathlib import Path
B=Path(__file__).resolve().parent
archive=Path('C:/Users/rolon/Documents/Codex/Recovery-Archives/Flash-Gemma4-26b-20261022')
helper=archive/'model_transfer.py'
def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
assert sha(helper)=='7f3eb0796baf1c2949b2495ca7183d72ba1fd0f29a1d29143d093e6f97c9fc77'
plan_path=archive/'backup-plan.json';plan_sha=sha(plan_path)
plan=json.loads(plan_path.read_text());receipt=json.loads((B/'ubuntu-existing-model-backup-public.json').read_text())
assert receipt['reusable_bytes']==sum(e['size'] for e in plan['files'])
assert {(e['digest'],e['size']) for e in plan['files']}=={(e['sha256'],e['bytes']) for e in receipt['files']}
parent=Path('C:/Users/rolon/Documents/Codex/Recovery-Archives')
assert shutil.disk_usage(parent).free>receipt['reusable_bytes']+32*1024**3
root=parent/('Isolated-Gemma-Restore-'+uuid.uuid4().hex)
root.mkdir(exist_ok=False)
(B/'ubuntu-model-restore-target.private.json').write_text(json.dumps(dict(target=str(root),archive=str(archive),plan_sha256=plan_sha),indent=2))
with (B/'ubuntu-model-restore.log').open('w') as log:
 result=subprocess.run([sys.executable,str(helper),'restore','--source-root',str(root),'--backup-dir',str(archive)],stdout=log,stderr=subprocess.STDOUT,timeout=1800,creationflags=subprocess.CREATE_NO_WINDOW)
if result.returncode:raise RuntimeError('Isolated restore failed; preserve output and inspect log')
rows=[]
for e in plan['files']:
 path=root/'blobs'/('sha256-'+e['digest'])
 assert path.stat().st_size==e['size'] and sha(path)==e['digest']
 rows.append(dict(sha256=e['digest'],bytes=e['size'],readback_verified=True))
 print(json.dumps(rows[-1]),flush=True)
manifest=root/'manifests/registry.ollama.ai/library/gemma4/26b'
assert manifest.read_bytes()==base64.b64decode(plan['manifest_base64'],validate=True)
assert sha(manifest)==plan['manifest_sha256'] and sha(plan_path)==plan_sha
public=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),passed=True,files=rows,
 manifest_verified=True,restored_bytes=sum(e['bytes'] for e in rows),helper_sha256=sha(helper),
 plan_sha256=plan_sha,model_started=False,production_model_directory_changed=False,full_ssd_backup_complete=False,
 restore_copy_retained=True)
(B/'ubuntu-model-restore-public.json').write_text(json.dumps(public,indent=2))
print(json.dumps(public),flush=True)
