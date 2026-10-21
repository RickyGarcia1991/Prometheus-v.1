"""Promotion/recovery boundaries using real temporary release trees, no live SSD."""
from contextlib import contextmanager
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import time

import pytest

from prometheus_assistant import autonomous_promotion as promotion
from prometheus_assistant.self_improvement import fingerprint


def digest(body):
    return hashlib.sha256(body).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')


@pytest.fixture
def case(tmp_path):
    ssd=tmp_path/'ssd';ssd.mkdir()
    baseline=ssd/'Prometheus-v0.8.0-dev18';baseline.mkdir()
    body={
        'prometheus.py':b'# application entry\n',
        'src/prometheus_assistant/math_tools.py':b'def value():\n    return 1\n',
        'src/prometheus_control_center/__init__.py':b'__version__ = "0.8.0.dev18"\n',
        'pyproject.toml':b'[project]\nversion = "0.8.0.dev18"\n',
        'RELEASE-PROVENANCE.json':b'{"release":"Prometheus-v0.8.0-dev18","version":"0.8.0.dev18"}\n',
        'tests/test_fixed.py':b'def test_fixed():\n    assert True\n',
        'tools/Verify-Repair-Core.ps1':b'# fixed verifier\n',
    }
    for name,data in body.items():
        path=baseline/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    rows=[{'path':name,'size_bytes':len(data),'sha256':digest(data)} for name,data in body.items()]
    write_json(baseline/'SHA256-MANIFEST.json',rows)
    baseline_hash=promotion.sha(baseline/'SHA256-MANIFEST.json')
    (baseline/'MANIFEST-PUBLIC.cer').write_bytes(b'public')
    (baseline/'SHA256-MANIFEST.sig').write_bytes(b'signed:'+baseline_hash.encode())
    job=tmp_path/('1'*32);job.mkdir();snapshot=job/'snapshot';snapshot.mkdir()
    for name,data in body.items():
        path=snapshot/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    target='src/prometheus_assistant/math_tools.py';changed=b'def value():\n    return 2\n';(snapshot/target).write_bytes(changed)
    expected={name:digest(changed if name==target else data) for name,data in body.items()}
    checks=[{'id':name,'executed':True,'exit_code':0,'tests_run':7} for name in promotion.REQUIRED_CHECKS]
    receipt={'job_id':job.name,'state':'promotion_requested','goal':'Return two','target':target,'reason':'Correct the requested value.',
        'diff':''.join(difflib.unified_diff(body[target].decode().splitlines(True),changed.decode().splitlines(True),fromfile='before/'+target,tofile='after/'+target)),
        'baseline_manifest_sha256':baseline_hash,'payload_sha256s':expected,'checks':checks}
    receipt['approval_fingerprint']=fingerprint(receipt)
    tools=ssd/'Prometheus-Resources/Tools';tools.mkdir(parents=True)
    guard=tools/'portable-memory-guard.py';guard.write_bytes(b'class Guard:\n    def supported(self):\n        supported = {\n        }\n        return supported\n')
    marker=ssd/'PROMETHEUS-SSD-STATUS.json';write_json(marker,{'code_release':baseline.name,'version':'0.8.0.dev18'})
    memory=ssd/'Prometheus-Data/memory.sqlite3';memory.parent.mkdir();memory.write_bytes(b'owner-memory-sentinel')
    return {'ssd':ssd,'baseline':baseline,'snapshot':snapshot,'job':job,'receipt':receipt,'guard':guard,'marker':marker,'memory':memory}


class FilesystemHost(promotion.WindowsPromotionHost):
    """Actual selection/package/recovery files; substitute only OS/trust services."""
    def __init__(self, case, fail=None):
        self.case=case;self.ssd=case['ssd'];self.guard=case['guard'];self.marker=case['marker'];self.host_root=case['job']/'host'
        self.host_root.mkdir();self.calls=[];self.fail=fail;self.trusted=promotion.sha(self.guard);self.locked=False
    def verify(self,root,manifest):
        promotion.baseline_payload(root,manifest)
        if (Path(root)/'SHA256-MANIFEST.sig').read_bytes()!=b'signed:'+manifest.encode():raise ValueError('bad signature')
    def rerun_checks(self,snapshot,baseline,job):
        self.calls.append('tests')
        if self.fail=='test':raise ValueError('regression failed')
        if self.fail=='mutate_test':(snapshot/self.case['receipt']['target']).write_text('changed during tests')
        return {'checks':self.case['receipt']['checks']}
    def sign(self,build,manifest):
        self.calls.append('sign')
        (build/'MANIFEST-PUBLIC.cer').write_bytes(b'public')
        (build/'SHA256-MANIFEST.sig').write_bytes(b'wrong' if self.fail=='signature' else b'signed:'+manifest.encode())
    def capture_selection(self,baseline,manifest):
        return {'guard':self.guard.read_bytes(),'marker':self.marker.read_bytes()}
    def stop(self,release):
        self.calls.append('stop:'+Path(release).name)
        if self.fail=='candidate_stop' and Path(release)!=self.case['baseline']:raise TimeoutError('candidate cannot drain')
        if self.fail=='old_stop' and Path(release)==self.case['baseline']:raise TimeoutError('old cannot drain')
    @contextmanager
    def lock(self,*,accepted_guard_bytes=()):
        allowed={self.trusted,*map(digest,accepted_guard_bytes)}
        assert promotion.sha(self.guard) in allowed,'untrusted guard import'
        self.locked=True
        try:yield
        finally:self.locked=False
    def backup(self,job):
        assert self.locked;self.calls.append('backup')
        if self.fail=='backup':raise ValueError('backup failed')
    def select(self,*args):
        assert self.locked
        super().select(*args);self.calls.append('select')
        if self.fail=='selection':raise ValueError('failure after both atomic pointer writes')
    def health(self,release,job):
        assert self.locked;self.calls.append('health')
        if self.fail=='health':raise ValueError('health failed')
    def launch(self,job,label):
        assert not self.locked;self.calls.append('launch:'+label)
        if label=='new' and self.fail in {'launch','candidate_stop'}:raise ValueError('launcher failed after starting candidate')
    def ready(self,release):
        self.calls.append('ready:'+Path(release).name)
        assert json.loads(self.marker.read_text())['code_release']==Path(release).name
        if self.fail=='readiness' and Path(release)!=self.case['baseline']:raise ValueError('candidate never ready')
    def trust_guard(self,content):
        assert self.guard.read_bytes()==content;self.trusted=digest(content)


def promote(case,host):
    return promotion.promote_snapshot(host,case['snapshot'],case['baseline'],case['receipt'],case['job'])


def test_success_stages_signed_recovery_and_preserves_memory(case):
    host=FilesystemHost(case);old_guard=case['guard'].read_bytes();memory=case['memory'].read_bytes()
    result=promote(case,host)
    assert result['state']=='active'
    selected=json.loads(case['marker'].read_text())['code_release']
    assert selected.startswith('Prometheus-v0.8.0-dev18-self-')
    assert case['baseline'].is_dir() and (case['ssd']/selected).is_dir()
    assert case['memory'].read_bytes()==memory
    assert case['guard'].read_bytes()!=old_guard
    provenance=json.loads((case['ssd']/selected/'BUILD-PROVENANCE.json').read_text())
    assert provenance['approval_fingerprint']==case['receipt']['approval_fingerprint']
    assert provenance['tested_payload_sha256s']==case['receipt']['payload_sha256s']
    assert host.calls.index('tests')<host.calls.index('sign')<host.calls.index('backup')<host.calls.index('select')
    assert (host.host_root/'Backups/Prometheus-Recovery'/('Self-'+case['job'].name)/'core/SHA256-MANIFEST.sig').is_file()


@pytest.mark.parametrize('failure',['test','mutate_test','signature','old_stop'])
def test_preselection_failures_preserve_live_pointers(case,failure):
    host=FilesystemHost(case,failure);before=(case['guard'].read_bytes(),case['marker'].read_bytes(),case['memory'].read_bytes())
    with pytest.raises((ValueError,TimeoutError)):promote(case,host)
    assert before==(case['guard'].read_bytes(),case['marker'].read_bytes(),case['memory'].read_bytes())
    assert 'select' not in host.calls


@pytest.mark.parametrize('failure',['backup','selection','health','launch','readiness'])
def test_post_shutdown_failures_restore_exact_old_selection(case,failure):
    host=FilesystemHost(case,failure);before=(case['guard'].read_bytes(),case['marker'].read_bytes(),case['memory'].read_bytes())
    with pytest.raises(ValueError):promote(case,host)
    assert before==(case['guard'].read_bytes(),case['marker'].read_bytes(),case['memory'].read_bytes())
    result=json.loads((case['job']/'result.json').read_text())
    assert result['state']=='rolled_back' and result['personal_data_overwritten'] is False
    assert 'launch:rollback' in host.calls


def test_candidate_that_wont_drain_blocks_pointer_rollback(case):
    host=FilesystemHost(case,'candidate_stop')
    with pytest.raises(ValueError):promote(case,host)
    result=json.loads((case['job']/'result.json').read_text())
    assert result['state']=='recovery_required'
    assert json.loads(case['marker'].read_text())['code_release']!=case['baseline'].name
    assert 'launch:rollback' not in host.calls


@pytest.mark.parametrize('failure',[None,'test','health','candidate_stop'])
def test_every_native_result_phase_keeps_exact_candidate_identity(case,monkeypatch,failure):
    recorded=[];original=promotion._save
    def capture(path,value):
        if Path(path).name=='result.json':recorded.append(dict(value))
        return original(path,value)
    monkeypatch.setattr(promotion,'_save',capture)
    host=FilesystemHost(case,failure)
    if failure:
        with pytest.raises(ValueError):promote(case,host)
    else:promote(case,host)
    assert recorded
    assert all(row['job_id']==case['receipt']['job_id'] and
               row['approval_fingerprint']==case['receipt']['approval_fingerprint'] for row in recorded)
    assert recorded[-1]['state']=={None:'active','test':'failed','health':'rolled_back','candidate_stop':'recovery_required'}[failure]


def test_changed_candidate_or_extra_file_rejected_before_tests(case):
    (case['snapshot']/'unreviewed.py').write_text('raise SystemExit')
    host=FilesystemHost(case)
    with pytest.raises(ValueError,match='Unreviewed'):promote(case,host)
    assert host.calls==[]


@pytest.mark.parametrize('mutation',['approval','diff','tests','baseline','two_files','state'])
def test_approval_boundaries_reject_forged_or_stale_inputs(case,mutation):
    receipt=case['receipt']
    if mutation=='approval':receipt['approval_fingerprint']='0'*64
    elif mutation=='diff':receipt['diff']='a harmless change';receipt['approval_fingerprint']=fingerprint(receipt)
    elif mutation=='tests':receipt['checks'][0]['tests_run']=0;receipt['approval_fingerprint']=fingerprint(receipt)
    elif mutation=='baseline':(case['baseline']/receipt['target']).write_text('baseline changed')
    elif mutation=='two_files':
        name='tests/test_fixed.py';(case['snapshot']/name).write_text('def test_fixed(): pass\n')
        receipt['payload_sha256s'][name]=promotion.sha(case['snapshot']/name);receipt['approval_fingerprint']=fingerprint(receipt)
    else:receipt['state']='ready_for_approval'
    host=FilesystemHost(case)
    with pytest.raises(ValueError):promote(case,host)
    assert host.calls==[]


def test_guard_update_is_additive_and_rejects_duplicate(case):
    before=case['guard'].read_bytes();name='Prometheus-v0.8.0-dev18-self-abcdef'
    after=promotion.add_guard_pin(before,name,'a'*64,'Prometheus-Recovery/Self-abcdef/core')
    assert before.split(b'        supported = {\n')[1] in after
    with pytest.raises(ValueError):promotion.add_guard_pin(after,name,'a'*64,'Prometheus-Recovery/Self-abcdef/core')
    with pytest.raises(ValueError):promotion.add_guard_pin(before,name,'a'*64,'../other')


def test_restore_refuses_concurrent_pointer_edit(case):
    host=FilesystemHost(case);previous=host.capture_selection(case['baseline'],case['receipt']['baseline_manifest_sha256'])
    current=json.loads(case['marker'].read_text());current['code_release']='Prometheus-vSomeoneElse';write_json(case['marker'],current)
    modified=case['marker'].read_bytes()
    with pytest.raises(ValueError,match='Another release'):host.restore_selection(previous)
    assert case['marker'].read_bytes()==modified


def test_windows_argument_identity_does_not_match_prefix_or_injected_suffix():
    exact=Path(r'E:\Prometheus-v0.8.0-dev18\prometheus.py')
    assert promotion.WindowsPromotionHost._argument('python "'+str(exact)+'" --shutdown-request x',exact)
    assert not promotion.WindowsPromotionHost._argument('python '+str(exact)+'.other',exact)


def test_fixed_runner_requires_real_execution_and_exact_contract():
    with pytest.raises(ValueError):promotion._checks([{'id':'python-regression','executed':True,'exit_code':0,'tests_run':40}])
    with pytest.raises(ValueError):promotion._checks([{'id':name,'executed':True,'exit_code':0,'tests_run':False} for name in promotion.REQUIRED_CHECKS])


def test_host_result_is_bounded_by_known_job_identity(tmp_path):
    host=promotion.WindowsPromotionHost.__new__(promotion.WindowsPromotionHost);host.jobs=tmp_path
    job_id='a'*32;folder=tmp_path/job_id;folder.mkdir()
    assert host.result(job_id)=={'job_id':job_id,'state':'promotion_pending'}
    write_json(folder/'result.json',{'job_id':job_id,'state':'active','release':'Prometheus-v1-self-test'})
    assert host.result(job_id)['state']=='active'
    with pytest.raises(ValueError):host.result('../secrets')
    write_json(folder/'result.json',{'job_id':'b'*32,'state':'active'})
    with pytest.raises(ValueError):host.result(job_id)
    (folder/'result.json').write_bytes(b' '*100001)
    with pytest.raises(ValueError):host.result(job_id)


@pytest.mark.skipif(os.name!='nt',reason='Concrete Windows test-job ownership')
def test_windows_adapter_runs_only_fixed_pinned_runner(tmp_path):
    helper=tmp_path/'fixed-runner.py'
    helper.write_text('''import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('candidate','baseline','report','cancel-file'):p.add_argument('--'+name,required=True)
a=p.parse_args();time.sleep(.1)
rows=[{'id':name,'executed':True,'exit_code':0,'tests_run':2} for name in ('python-regression','javascript-regression')]
Path(a.report).write_text(json.dumps({'checks':rows,'candidate':a.candidate,'baseline':a.baseline,'state':'passed','payloads_unchanged':True,'isolation_clean':True}))
''')
    host=promotion.WindowsPromotionHost.__new__(promotion.WindowsPromotionHost)
    host.tools={'python':{'path':sys.executable,'sha256':promotion.sha(Path(sys.executable))},
                'test_runner':{'path':str(helper),'sha256':promotion.sha(helper)}}
    result=host.rerun_checks(tmp_path/'candidate',tmp_path/'baseline',tmp_path)
    assert result['candidate']==str(tmp_path/'candidate') and result['baseline']==str(tmp_path/'baseline')
    assert {x['id'] for x in result['checks']}==set(promotion.REQUIRED_CHECKS)
    helper.write_text('raise RuntimeError("changed pinned host tool")\n')
    with pytest.raises(ValueError,match='Pinned host tool changed'):host.rerun_checks(tmp_path/'candidate',tmp_path/'baseline',tmp_path)


def test_selection_partial_write_can_restore_old_guard(case,monkeypatch):
    host=FilesystemHost(case);old=(case['guard'].read_bytes(),case['marker'].read_bytes())
    previous=host.capture_selection(case['baseline'],case['receipt']['baseline_manifest_sha256'])
    original=promotion._write_atomic
    def fail_marker_once(path,body,*args):
        if Path(path)==case['marker']:
            raise PermissionError('simulated sharing lock on marker')
        return original(path,body,*args)
    monkeypatch.setattr(promotion,'_write_atomic',fail_marker_once)
    identity={'release':'Prometheus-v0.8.0-dev18-self-test','version':'0.8.0.dev18','manifest_sha256':'a'*64}
    new=promotion.add_guard_pin(old[0],identity['release'],'a'*64,'Prometheus-Recovery/test/core')
    host.locked=True
    with pytest.raises(PermissionError):host.select(previous,new,identity,case['receipt'])
    assert case['guard'].read_bytes()==new and case['marker'].read_bytes()==old[1]
    monkeypatch.setattr(promotion,'_write_atomic',original)
    host.restore_selection(previous)
    assert (case['guard'].read_bytes(),case['marker'].read_bytes())==old


def test_portable_tool_resolves_current_ssd_root_and_rejects_traversal(tmp_path):
    host=promotion.WindowsPromotionHost.__new__(promotion.WindowsPromotionHost)
    host.ssd=tmp_path/'different-mounted-root';exe=host.ssd/'Prometheus-Resources/Python/python.exe'
    exe.parent.mkdir(parents=True);exe.write_bytes(b'portable interpreter fixture')
    host.tools={'python':{'ssd_relative':'Prometheus-Resources/Python/python.exe','sha256':promotion.sha(exe)}}
    assert host.tool('python')==exe
    for relative in ('../Python/python.exe','Prometheus-Data/private.exe','/Prometheus-Resources/Python/python.exe'):
        host.tools['python']['ssd_relative']=relative
        with pytest.raises(ValueError):host.tool('python')
    host.tools['python']={'path':'relative-python.exe','sha256':'a'*64}
    with pytest.raises(ValueError):host.tool('python')


@pytest.mark.parametrize('problem',[None,'parent','selection','owner_gate'])
def test_windows_readiness_requires_exact_guard_child_selection_and_owner_gate(case,monkeypatch,problem):
    host=promotion.WindowsPromotionHost.__new__(promotion.WindowsPromotionHost)
    host.guard=case['guard'];host.marker=case['marker']
    records=[{'ProcessId':1,'ParentProcessId':0,'CommandLine':'python "'+str(case['guard'])+'"'},
             {'ProcessId':2,'ParentProcessId':999 if problem=='parent' else 1,'CommandLine':'python "'+str(case['baseline']/'prometheus.py')+'"'}]
    host._processes=lambda:records
    if problem=='selection':write_json(case['marker'],{'code_release':'Prometheus-vOther'})
    class Opener:
        def open(self,*args,**kwargs):
            return io.BytesIO(json.dumps({'owner_access':{'required':True,'ready':problem!='owner_gate','enrolled':True}}).encode())
    monkeypatch.setattr(promotion,'build_opener',lambda *args:Opener())
    times=iter([0,0,46]);monkeypatch.setattr(promotion.time,'monotonic',lambda:next(times))
    monkeypatch.setattr(promotion.time,'sleep',lambda seconds:None)
    if problem:
        with pytest.raises(TimeoutError):host.ready(case['baseline'])
    else:host.ready(case['baseline'])


def test_launcher_does_not_wait_for_detached_child_output_eof(tmp_path,monkeypatch):
    fixture=tmp_path/'launcher.py'
    fixture.write_text('import subprocess,sys\nsubprocess.Popen([sys.executable,"-c","import time;time.sleep(4)"],stdout=sys.stdout,stderr=sys.stderr)\nprint("launcher finished",flush=True)\n')
    host=promotion.WindowsPromotionHost.__new__(promotion.WindowsPromotionHost)
    host.tool=lambda name:Path(sys.executable) if name=='powershell' else fixture
    real_run=subprocess.run
    def launcher(command,**kwargs):
        assert command[1:3]==['-NoProfile','-File'] and command[-1]=='-NoBrowser'
        assert kwargs.get('capture_output') is not True and hasattr(kwargs.get('stdout'),'fileno')
        return real_run([sys.executable,str(fixture)],**kwargs)
    monkeypatch.setattr(promotion.subprocess,'run',launcher)
    started=time.monotonic();host.launch(tmp_path,'new');elapsed=time.monotonic()-started
    assert elapsed<3,'Detached application output handles delayed launcher completion'
    assert 'launcher finished' in (tmp_path/'new-launcher.log').read_text()
