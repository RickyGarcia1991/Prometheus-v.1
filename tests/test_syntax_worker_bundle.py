import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest


@pytest.fixture
def builder():
    path=Path(__file__).resolve().parents[1]/'tools/build-syntax-worker.py'
    spec=importlib.util.spec_from_file_location('worker_bundle_builder_test',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def invoke(root,*args):
    return subprocess.run([sys.executable,'-I',str(root/'run-worker.py'),*args],input=b'{}',
        capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def configure(root,digest):
    (root/'worker-config.json').write_text(json.dumps(dict(schema_version=1,enabled=False,
        device_id='fixture',worker_digest=digest,controller_certificate_sha256='a'*64,
        receipt_certificate_sha256='b'*64)))


def test_build_is_inert_exact_manifest_and_reproducible(builder,tmp_path):
    first=builder.build(tmp_path/'first'); second=builder.build(tmp_path/'second')
    assert first['worker_digest']==second['worker_digest']
    assert first['installed'] is first['enabled'] is first['credentials_created'] is False
    root=Path(first['directory']); manifest_bytes=(root/'bundle-manifest.json').read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest()==first['worker_digest']
    manifest=json.loads(manifest_bytes)
    assert len(manifest)==first['files']==len(builder.MODULES)+2
    for name,digest in manifest.items(): assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    assert not any((root/name).exists() for name in ('worker-config.json','receipt-key.pem','jobs.sqlite3'))
    configure(root,first['worker_digest'])
    result=invoke(root,'--root',str(root))
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['state']=='rejected'
    assert not (root/'jobs.sqlite3').exists()


@pytest.mark.parametrize('change',['missing','changed','manifest','root'])
def test_wrapper_refuses_wrong_material_before_service(builder,tmp_path,change):
    result=builder.build(tmp_path/'bundle'); root=Path(result['directory']);configure(root,result['worker_digest'])
    module=root/'prometheus_assistant/remote_worker_service.py'
    if change=='missing': module.unlink()
    elif change=='changed': module.write_bytes(module.read_bytes()+b'\n# modified\n')
    elif change=='manifest': (root/'bundle-manifest.json').write_text('{}')
    args=['--root',str(tmp_path/'wrong') if change=='root' else str(root)]
    process=invoke(root,*args)
    assert process.returncode!=0 and not process.stdout
    assert not (root/'jobs.sqlite3').exists()


def test_builder_existing_or_relative_destination_rejected(builder,tmp_path):
    with pytest.raises(ValueError): builder.build('relative-worker')
    with pytest.raises(ValueError): builder.build(tmp_path)


def test_duplicate_root_cannot_override_checked_root(builder,tmp_path):
    result=builder.build(tmp_path/'bundle');root=Path(result['directory']);configure(root,result['worker_digest'])
    process=invoke(root,'--root',str(root),'--root',str(tmp_path/'other'))
    assert process.returncode!=0


def test_wrapper_must_not_execute_unverified_cached_module(builder,tmp_path):
    result=builder.build(tmp_path/'bundle');root=Path(result['directory']);configure(root,result['worker_digest'])
    source=root/'prometheus_assistant/remote_worker_service.py'
    marker=tmp_path/'cache-executed'
    code=('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\ndef main(): pass\n').encode()
    malicious=tmp_path/'fixture-source.py'
    malicious.write_bytes(code+b'#'*(source.stat().st_size-len(code)))
    os.utime(malicious,(source.stat().st_atime,source.stat().st_mtime))
    cache=Path(importlib.util.cache_from_source(str(source))); cache.parent.mkdir()
    py_compile.compile(str(malicious),cfile=str(cache),doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    result=invoke(root,'--root',str(root))
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['state']=='rejected'
    assert not marker.exists()


def test_resource_mode_is_in_bundle_and_never_creates_a_ledger(builder,tmp_path):
    result=builder.build(tmp_path/'bundle');root=Path(result['directory']);configure(root,result['worker_digest'])
    assert 'remote_resource_wire.py' in builder.MODULES
    process=invoke(root,'--root',str(root),'--resources')
    assert process.returncode==0,process.stderr
    assert json.loads(process.stdout)['state']=='rejected'
    assert not (root/'jobs.sqlite3').exists()
    process=invoke(root,'--root',str(root),'--query','--resources')
    assert process.returncode!=0 and not process.stdout
