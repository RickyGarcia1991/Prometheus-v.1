"""Bind staged Linux wheels to exact PyPI release metadata; no installation."""
import datetime,email,hashlib,json,urllib.request,zipfile
from pathlib import Path
B=Path(__file__).resolve().parent
expected={'fido2':'2.2.1','cryptography':'50.0.2','cffi':'2.1.1','pycparser':'3.11','pypdf':'6.20.1'}
rows=[]
for p in sorted((B/'wheelhouse').glob('*.whl')):
 with zipfile.ZipFile(p) as z:
  names=[n for n in z.namelist() if n.endswith('.dist-info/METADATA')]
  assert len(names)==1
  meta=email.message_from_bytes(z.read(names[0]));name=meta['Name'].lower();version=meta['Version']
  assert expected.get(name)==version
  wheel=[n for n in z.namelist() if n.endswith('.dist-info/WHEEL')];assert len(wheel)==1
  tags=email.message_from_bytes(z.read(wheel[0])).get_all('Tag',[])
 with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json',timeout=30) as response:release=json.load(response)
 matches=[r for r in release['urls'] if r['filename']==p.name];assert len(matches)==1
 remote=matches[0]
 with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 assert digest==remote['digests']['sha256'] and p.stat().st_size==remote['size'] and not remote['yanked']
 rows.append(dict(name=name,version=version,filename=p.name,sha256=digest,bytes=p.stat().st_size,
  url=remote['url'],wheel_tags=tags,requires_python=meta['Requires-Python'],requires_dist=meta.get_all('Requires-Dist',[])))
assert {r['name'] for r in rows}==set(expected) and len(rows)==len(expected)
result=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),target='CPython 3.14 Linux x86_64',
 origin='https://pypi.org',artifacts=rows,installed=False,linux_import_tested=False,full_application_qualified=False)
(B/'linux-wheelhouse-verification.json').write_text(json.dumps(result,indent=2))
(B/'requirements-linux-rehearsal.lock').write_text('\n'.join(f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}" for r in rows)+'\n')
print(json.dumps(dict(verified=len(rows),bytes=sum(r['bytes'] for r in rows),installed=False)))
