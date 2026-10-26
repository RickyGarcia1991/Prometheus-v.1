"""Stage explicitly scoped project source from pinned dev55; leave original untouched."""
import hashlib,json,shutil,subprocess,tarfile
from pathlib import Path,PurePosixPath
B=Path(__file__).resolve().parent
PS=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
vols=json.loads(subprocess.check_output([str(PS),'-NoProfile','-Command','Get-Volume | Select-Object DriveLetter,FileSystemLabel,Size,HealthStatus | ConvertTo-Json'],text=True))
v=[x for x in vols if x['FileSystemLabel']=='Prometheus-2TB' and x['Size']==1950372327424 and x['HealthStatus']=='Healthy'];assert len(v)==1
source=Path(v[0]['DriveLetter']+':/')/'Prometheus-v0.8.0-dev55-followup-applicability-20261019'
manifest=source/'SHA256-MANIFEST.json';pin='f093dcd0d45ee0efeb2d9135596f6a255e1c488f48818e69c6a49a0e6ba0fffa'
assert sha(manifest)==pin
dest=B/'source-overlay-dev55';dest.mkdir(exist_ok=False)
included=[];omitted=[]
for row in json.loads(manifest.read_text()):
 name=row['path'];rel=PurePosixPath(name)
 assert not rel.is_absolute() and '..' not in rel.parts and '\\' not in name
 eligible=name.startswith(('src/prometheus_assistant/','src/prometheus_control_center/')) or name in ('prometheus.py','prometheusctl.py','pyproject.toml','vocabulary.json')
 reason=None
 if not eligible:reason='Outside project-code overlay; platform dependencies come from hash-locked Linux environment'
 elif name.startswith('src/prometheus_assistant/vendor/'):reason='Windows-specific bundled feature dependency closure requires separate Linux qualification'
 elif rel.suffix.lower() in ('.exe','.dll','.pyd','.whl','.cmd','.bat','.ps1'):reason='Platform-specific asset excluded from this source rehearsal'
 if reason:omitted.append(dict(path=name,sha256=row['sha256'],reason=reason));continue
 src=source.joinpath(*rel.parts);assert sha(src)==row['sha256'] and src.stat().st_size==row['size_bytes']
 out=dest.joinpath(*rel.parts);out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,out);assert sha(out)==row['sha256']
 included.append(dict(path=name,sha256=row['sha256'],bytes=row['size_bytes']))
assert included and sha(manifest)==pin
report=dict(baseline_manifest_sha256=pin,included=included,omitted=omitted,signed_linux_release=False,
 full_application_qualified=False,installed_source_changed=False)
(B/'linux-source-overlay-manifest.json').write_text(json.dumps(report,indent=2))
archive=B/'source-overlay-dev55.tar.gz'
with tarfile.open(archive,'w:gz') as tar:tar.add(dest,arcname='source',recursive=True)
receipt=dict(included_files=len(included),omitted_files=len(omitted),archive_bytes=archive.stat().st_size,archive_sha256=sha(archive),baseline_manifest_sha256=pin,full_application_qualified=False)
(B/'linux-source-overlay-public.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
