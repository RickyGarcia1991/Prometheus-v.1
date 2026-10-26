"""Full transaction temp fixture using real signed verifier and actual protected floor."""
import json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
import controller_pointer_windows_fixture as f
BASE=Path(__file__).resolve().parent
SOURCE=f.CORE.with_name('Prometheus-Host-Agent.ps1')
BRIDGE=BASE/'controller-real-provider-bridge.ps1'
PINS={'candidate':'e5e542c98dd55d8ea08e46d3fb5b73d329f28ca1d70fb7cc1b758d5fcb23fd54','recovery':'3f7f9e72e7aaca412b130b03fca6180039aa3f3fa2d38d0efe609cb2c1ff03b2'}

class RealProvider(f.Provider):
 def call(self,mode,path=None,version=None):
  args=[shutil.which('pwsh'),'-NoProfile','-NonInteractive','-File',str(BRIDGE),'-Source',str(SOURCE),'-Scratch',str(self.root),'-Mode',mode]
  if path: args+=['-PackageRoot',str(path)]
  if version: args+=['-Version',version]
  result=subprocess.run(args,capture_output=True,text=True,timeout=20)
  if result.returncode: raise ValueError(result.stderr)
  return json.loads(result.stdout)
 def location(self,pkg):
  if pkg==self.plan.candidate:return self.root/'candidate'
  if pkg==self.plan.recovery:return self.root/'recovery'
  raise ValueError('Unknown package')
 def verify(self,pkg):
  source=self.location(pkg)
  if not source.is_dir():raise ValueError('Required signed source missing')
  for path in (source,self.root/('cache-'+pkg.manifest)):
   if path.exists():
    result=self.call('verify',path)
    if result!={'version':pkg.version,'manifest':pkg.manifest}:raise ValueError('Verified identity mismatch')
 def stage(self,pkg):
  target=self.root/('cache-'+pkg.manifest)
  if not target.exists():
   staging=self.root/('stage-'+pkg.manifest)
   shutil.copytree(self.location(pkg),staging)
   result=self.call('verify',staging)
   if result['manifest']!=pkg.manifest:raise ValueError('Staged mismatch')
   os.replace(staging,target)
  if not target.is_dir():raise ValueError('Staged signed cache missing')
  self.verify(pkg)
 def read_floor(self):return self.call('read-floor')['floor']
 def set_floor(self,value):self.call('set-floor',version=value)


def setup(root):
 nonce=f.setup(root)
 plan=f.core.Plan('a'*32,'synthetic-host','synthetic-volume',b'{"controller_release":"Prometheus-Controller-v0.5.10","core":{"v":55},"preserved":[1,2]}',f.core.Package('Prometheus-Controller-v0.5.11',PINS['candidate']),f.core.Package('Prometheus-Controller-v0.5.12',PINS['recovery']))
 (root/'fixture-plan.json').write_bytes(f.core.canonical(plan.record()));(root/'pointer.json').write_bytes(plan.previous)
 for original,name in (('candidate-after-recovery','candidate'),('recovery-at-new-floor','recovery')):shutil.copytree(BASE/'real-recovery-check'/original,root/name)
 p=RealProvider(root,nonce);p.call('set-floor',version='0.5.10')
 return nonce

if __name__=='__main__':
 p=RealProvider(sys.argv[1],sys.argv[2],sys.argv[3])
 print(json.dumps(f.core.execute(p,p.plan,p.key,forward_recovery='--forward' in sys.argv)))
