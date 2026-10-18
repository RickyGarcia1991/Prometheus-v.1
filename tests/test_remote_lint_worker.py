import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from prometheus_assistant.remote_job_protocol import normalize_request,validate_receipt
from prometheus_assistant.remote_lint_worker import pinned_executor
from prometheus_assistant.remote_job_store import JobStore
from prometheus_assistant.remote_syntax_worker import run_job


@pytest.fixture
def bundle(tmp_path):
    path=Path(__file__).resolve().parents[1]/'tools/build-lint-worker.py'
    spec=importlib.util.spec_from_file_location('lint_builder_test',path)
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    binary=tmp_path/'fixture-ruff';binary.write_bytes(b'\x7fELFfake-inert-fixture')
    root=tmp_path/'worker';built=builder.build(root,binary,'0.14.0')
    req=normalize_request(dict(schema_version=2,operation='python-ruff-check-v1',profile='ruff-python-v1',
        tool=built['tool'],worker_digest=built['worker_digest'],device_id='surface',job_id='a'*32,
        timeout_seconds=10,files=[dict(path='a.py',content='answer = missing\n')]))
    return root,built,req,builder,binary


def test_lint_bundle_inert_reproducible_and_exactly_hash_bound(bundle,tmp_path):
    root,built,req,builder,binary=bundle
    assert built['installed'] is built['enabled'] is built['credentials_created'] is False
    assert not built['native_tool_version_verified']
    assert not (root/'worker-config.json').exists()
    assert builder.build(tmp_path/'second',binary,'0.14.0')['worker_digest']==built['worker_digest']
    manifest=json.loads((root/'bundle-manifest.json').read_bytes())
    for name,digest in manifest.items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    assert manifest['tools/ruff']==req['tool']['sha256']


@pytest.mark.parametrize('changed',[False,True])
def test_generated_entry_checks_binary_before_disabled_service(bundle,changed):
    root,built,req,_,_=bundle
    (root/'worker-config.json').write_text(json.dumps(dict(schema_version=1,enabled=False,
        device_id='surface',worker_digest=built['worker_digest'],
        controller_certificate_sha256='a'*64,receipt_certificate_sha256='b'*64)))
    if changed: (root/'tools/ruff').write_bytes(b'\x7fELFmodified')
    result=subprocess.run([sys.executable,'-I',str(root/'run-worker.py'),'--root',str(root)],
        input=b'{}',capture_output=True,timeout=10,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if changed: assert result.returncode!=0 and not result.stdout
    else: assert result.returncode==0 and json.loads(result.stdout)['state']=='rejected'
    assert not (root/'jobs.sqlite3').exists()


@pytest.mark.parametrize('change',['manifest','tool','request','binary_missing','binary_changed'])
def test_unreviewed_tool_rejected_before_runner(bundle,change):
    root,built,req,_,_=bundle
    if change=='manifest': (root/'bundle-manifest.json').write_text('{}')
    elif change=='tool': (root/'ruff-tool.json').write_text('{}')
    elif change=='binary_missing': (root/'tools/ruff').unlink()
    elif change=='binary_changed': (root/'tools/ruff').write_bytes(b'\x7fELFchanged')
    else:
        req.pop('request_digest');req['tool']['version']='0.15.0'
    with pytest.raises(ValueError): pinned_executor(root,req,runner=lambda *a,**k:pytest.fail('executed'))


@pytest.mark.parametrize('status',['passed','findings','tool_failed','unavailable'])
def test_receipt_and_durable_recovery_do_not_repeat_executor(bundle,status):
    root,_,req,_,_=bundle;calls=[]
    def runner(files,**options):
        calls.append((files,options))
        return dict(state='completed' if status in ('passed','findings') else 'failed',status=status,
            diagnostics=[dict(path='a.py',code='F821',message='Undefined name',line=1,column=10,
                end_line=1,end_column=17)] if status=='findings' else [],
            reason='' if status in ('passed','findings') else 'tool_unavailable',source_unchanged=True)
    executor=pinned_executor(root,req,runner=runner)
    facts=dict(enabled=True,device_id='surface',worker_digest=req['worker_digest'],observed_at=100,
        available_ram_gib=4,free_disk_gib=10,cpu_percent=10,plugged_in=True)
    db=root/'fixture.sqlite3'
    first=run_job(req,JobStore(db),lambda:facts,executor=executor,clock=lambda:100)
    assert first['receipt']['status']==status
    assert validate_receipt(req,first['receipt'])['status']==('passed' if status=='passed' else 'failed')
    again=run_job(req,JobStore(db),lambda:facts,executor=executor,clock=lambda:100)
    assert again['replayed'] and len(calls)==1 and again['receipt']==first['receipt']
