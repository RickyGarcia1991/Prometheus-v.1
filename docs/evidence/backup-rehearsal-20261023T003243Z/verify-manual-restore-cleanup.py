import datetime,hashlib,json,math,shutil
from pathlib import Path
B=Path(__file__).resolve().parent
target=json.loads((B/'ubuntu-model-restore-target.private.json').read_text())
root=Path(target['target']);archive=Path(target['archive'])
assert not root.exists()
raw=(archive/'backup-plan.json').read_bytes();assert hashlib.sha256(raw).hexdigest()==target['plan_sha256']
plan=json.loads(raw);parts_checked=0
for row in plan['files']:
 count=math.ceil(row['size']/plan['chunk_size']);folder=archive/'archive'/row['digest']
 expected=[folder/f'part-{i:05d}' for i in range(count)]
 assert set(folder.iterdir())==set(expected)
 for i,p in enumerate(expected):
  assert p.is_file() and p.stat().st_size==min(plan['chunk_size'],row['size']-i*plan['chunk_size'])
  parts_checked+=1
free=shutil.disk_usage(archive).free
result=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),temporary_copy_removed=True,
 actor='User manual cleanup, confirmed by filesystem readback',original_plan_hash_verified=True,
 archive_parts_present_with_expected_sizes=parts_checked,archive_content_rehash_repeated=False,
 earlier_full_archive_and_restore_hash_proofs_retained=True,internal_free_bytes=free,
 estimated_new_backup_bytes=191434736603,working_reserve_bytes=34359738368,
 estimated_margin_before_overhead=free-191434736603-34359738368,full_backup_complete=False)
(B/'ubuntu-model-restore-manual-cleanup-public.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
