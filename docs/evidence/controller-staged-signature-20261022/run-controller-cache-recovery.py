import subprocess,json,datetime,os
from pathlib import Path
b=Path('work/companion-release').resolve()
source=Path('C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/source/tools/Prometheus.HostAgent/Prometheus-Host-Agent.ps1')
script=(b/'check-controller-cache-recovery.ps1').read_text()
q=lambda p:"'"+str(p).replace("'","''")+"'"
out=[]
env={k:v for k,v in os.environ.items() if k.lower() != 'psmodulepath'}
for name,exe in [('pwsh7',Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'),('powershell5',Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'))]:
 scratch=b/('cache-recovery-'+name+'-'+datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
 for reload in (False,True):
  cmd='& {'+script+'} -Source '+q(source)+' -Scratch '+q(scratch)+' -Packages '+q(b/'real-recovery-check')+(' -ReloadOnly' if reload else '')
  r=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,text=True,timeout=60,env=env)
  if r.returncode:
   (b/'forward-cache-recovery-failure.txt').write_text(r.stdout+r.stderr)
   raise RuntimeError(name+str(reload)+r.stdout+r.stderr)
  out.append(json.loads(r.stdout))
(b/'forward-cache-recovery-results.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out))



