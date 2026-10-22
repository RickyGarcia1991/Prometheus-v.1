import hashlib,json,os,shutil,subprocess,tempfile
from pathlib import Path
BASE=Path(__file__).resolve().parent
SOURCE=Path('C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/source/tools/Prometheus.HostAgent/Prometheus-Host-Agent.ps1')
SCRIPT=BASE/'check-controller-signed-pointer-isolated.ps1'
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
orig=BASE/'real-recovery-check'
pins={
 'candidate-after-recovery/Prometheus-Controller-v0.5.11/SHA256-MANIFEST.json':'e5e542c98dd55d8ea08e46d3fb5b73d329f28ca1d70fb7cc1b758d5fcb23fd54',
 'recovery-at-new-floor/Prometheus-Controller-v0.5.12/SHA256-MANIFEST.json':'3f7f9e72e7aaca412b130b03fca6180039aa3f3fa2d38d0efe609cb2c1ff03b2'}
for path,expected in pins.items():
 if digest(orig/path)!=expected: raise ValueError('Signed fixture manifest changed')
rows=[]
env={k:v for k,v in os.environ.items() if k.lower()!='psmodulepath'}
for exe in (shutil.which('pwsh'),r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'):
 with tempfile.TemporaryDirectory(prefix='prometheus-signed-pointer-') as tmp:
  root=Path(tmp)
  bindings=[]
  for src,name in ((orig/'candidate-after-recovery','candidate'),(orig/'recovery-at-new-floor','recovery')):
   shutil.copytree(src,root/name)
   for file in src.rglob('*'):
    if file.is_file():
     dest=root/name/file.relative_to(src)
     assert digest(file)==digest(dest)
     bindings.append({'package':name,'path':str(file.relative_to(src)),'sha256':digest(file)})
  outcomes=[]
  for mode in ('verify','interrupt','resume'):
   quote=lambda value: chr(39)+str(value).replace(chr(39),chr(39)*2)+chr(39)
   command='& {'+SCRIPT.read_text(encoding='utf-8-sig')+'} -Source '+quote(SOURCE)+' -Scratch '+quote(root)+' -Mode '+quote(mode)
   result=subprocess.run([exe,'-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,timeout=45,env=env)
   expected=77 if mode=='interrupt' else 0
   if result.returncode!=expected:
    (BASE/'controller-signed-pointer-failure.log').write_text(result.stdout+result.stderr)
    raise RuntimeError((exe,mode,result.returncode,result.stdout,result.stderr))
   outcome={'mode':mode,'exit':result.returncode}
   if mode!='interrupt': outcome['result']=json.loads(result.stdout)
   else:
    assert json.loads((root/'security-state.json').read_text(encoding='utf-8-sig'))['highest_controller_version']=='0.5.10'
    assert json.loads((root/'security-state.json.bak2').read_text(encoding='utf-8-sig'))['highest_controller_version']=='0.5.12'
    outcome['recovery_slot_flushed_before_primary']=True
   outcomes.append(outcome)
  rows.append({'runtime':exe,'bindings':bindings,'outcomes':outcomes})
report={'passed':True,'source_sha256':digest(SOURCE),'script_sha256':digest(SCRIPT),'runs':rows,'production_ready':False,'scope':'Real signed temp source/stage verification and extracted actual floor recovery; test wrapper exits after verified recovery slot, no Sync-Controller or top-level startup'}
(BASE/'controller-signed-pointer-isolated-results.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'passed':True,'runtimes':len(rows),'subprocesses':6}))

