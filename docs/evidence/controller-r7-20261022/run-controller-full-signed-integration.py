import hashlib,json,subprocess,sys,tempfile
from pathlib import Path
import controller_pointer_real_signed_fixture as real
with tempfile.TemporaryDirectory(prefix='prometheus-full-signed-') as temp:
 root=Path(temp);nonce=real.setup(root);results=[]
 for fault,forward in [('after-pointer',False),('none',False),('none',True),('none',False)]:
  proc=subprocess.run([sys.executable,str(Path(real.__file__)),str(root),nonce,fault]+(['--forward'] if forward else []),capture_output=True,text=True,timeout=150)
  expected=77 if fault=='after-pointer' else 0
  if proc.returncode!=expected:raise RuntimeError(proc.stdout+proc.stderr)
  results.append({'fault':fault,'forward':forward,'exit':proc.returncode,'receipt':json.loads(proc.stdout) if proc.stdout else None})
 p=real.RealProvider(root,nonce)
 assert p.read_floor()=='0.5.12'
 p.verify(p.plan.candidate) # Pure verification does not pretend to admit below floor.
 try:p.call('admit',p.location(p.plan.candidate))
 except ValueError as error:
  assert 'Anti-rollback blocked' in str(error)
 else:raise AssertionError('Normal admission bypassed')
 assert json.loads(p.read_pointer())['core']=={'v':55}
 assert len(list(root.glob('receipt-*.json')))==2
 report={'passed':True,'runs':results,'floor':'0.5.12','pure_candidate_verified':True,'normal_candidate_admission_refused':True,'live_changes':False,'files':{str(path.name):hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(real.__file__),real.BRIDGE,real.SOURCE]}}
 (real.BASE/'controller-full-signed-integration-results.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({'passed':True,'runs':4,'floor':'0.5.12'}))
