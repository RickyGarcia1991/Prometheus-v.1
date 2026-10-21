import hashlib
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from prometheus_assistant import agent_memory_service as service
from prometheus_assistant.agent_memory import AgentMemoryError


@pytest.fixture
def enabled(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(service,'QUALIFIED',True)
    monkeypatch.setattr(service,'require_dispatch',calls.append)
    monkeypatch.setattr(service,'_runtime',lambda root,event:(tmp_path/'bundle',tmp_path/'node'))
    return calls


def invoke_fixture(bundle,node,scratch,action,args,event):
    store=scratch/'state'/'synthetic.bin'
    if action=='remember':store.write_text(args['content'])
    if action=='forget':store.unlink(missing_ok=True)
    return {'result':{'saved':'mem_fixture'} if action=='remember' else {'results':[]},'seconds':.1}


def test_unknown_commands_paths_and_ports_are_rejected():
    for bad in ({'project':'x','content':'note','port':1234},{'project':'../x','content':'note'},
                {'project':'x','content':'note','command':'run'}, {'project':'x','content':'note','path':'C:/'}):
        with pytest.raises(AgentMemoryError):service.canonical_arguments('remember',bad)


def test_manifest_tampering_does_not_look_installed(tmp_path):
    folder=tmp_path/service.BUNDLE;folder.mkdir(parents=True);(folder/'manifest.json').write_text('{}')
    row=service.status(tmp_path)
    assert not row['available']
    assert row['integrity']=='missing-or-unverified'


def test_missing_runtime_never_opens_data(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'QUALIFIED',True)
    monkeypatch.setattr(service,'require_dispatch',lambda _:None)
    data=tmp_path/'data'
    with pytest.raises(AgentMemoryError):service.execute('remember',{'project':'alpha','content':'note'},data_root=data,resource_root=tmp_path/'missing')
    assert not data.exists()


def test_status_requires_exact_shared_node(monkeypatch,tmp_path):
    bundle=tmp_path/'bundle';(bundle/'engine').mkdir(parents=True)
    (bundle/'engine/iii.exe').write_bytes(b'engine')
    monkeypatch.setattr(service,'_manifest',lambda _: (bundle,{}))
    monkeypatch.setattr(service,'QUALIFIED',True)
    assert not service.status(tmp_path)['available']
    node=tmp_path/service.NODE;node.parent.mkdir(parents=True);node.write_bytes(b'changed')
    assert not service.status(tmp_path)['available']
    monkeypatch.setattr(service,'NODE_SHA256',hashlib.sha256(b'changed').hexdigest())
    assert service.status(tmp_path)['available']


def test_policy_denial_precedes_runtime_or_owner_data(monkeypatch,tmp_path):
    def deny(_):raise PermissionError('disabled')
    monkeypatch.setattr(service,'require_dispatch',deny)
    monkeypatch.setattr(service,'_runtime',lambda *_:pytest.fail('must not verify or launch'))
    with pytest.raises(PermissionError):service.execute('remember',{'project':'alpha','content':'note'},data_root=tmp_path/'data',resource_root=tmp_path)
    assert not (tmp_path/'data').exists()


def test_cancelled_request_never_launches(enabled,monkeypatch,tmp_path):
    event=threading.Event();event.set()
    monkeypatch.setattr(service,'_invoke',lambda *_:pytest.fail('must not launch'))
    with pytest.raises(AgentMemoryError,match='cancelled'):
        service.execute('remember',{'project':'alpha','content':'note'},data_root=tmp_path,resource_root=tmp_path,cancel_event=event)


def test_unqualified_provider_refuses_launch(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'QUALIFIED',False);monkeypatch.setattr(service,'require_dispatch',lambda _:None)
    monkeypatch.setattr(service,'_runtime',lambda *_:pytest.fail('must not launch'))
    with pytest.raises(AgentMemoryError,match='qualification'):
        service.execute('remember',{'project':'alpha','content':'note'},data_root=tmp_path,resource_root=tmp_path)


def test_generation_publication_and_project_separation(enabled,monkeypatch,tmp_path):
    monkeypatch.setattr(service,'_invoke',invoke_fixture)
    for name in ['alpha','bravo']:
        result=service.execute('remember',{'project':name,'content':name},data_root=tmp_path,resource_root=tmp_path)
        assert result['provider']=='agentmemory_service'
        assert not result['network_used']
    base=tmp_path/'Integrations/AgentMemoryService'
    for name in ['alpha','bravo']:
        _,generation,_=service._current(base/('project-'+name))
        assert (generation/'synthetic.bin').read_text()==name
    assert enabled.count('integrations.agentmemory_service')>=6


def test_cancel_during_worker_preserves_prior_generation(enabled,monkeypatch,tmp_path):
    monkeypatch.setattr(service,'_invoke',invoke_fixture)
    service.execute('remember',{'project':'alpha','content':'original'},data_root=tmp_path,resource_root=tmp_path)
    project=tmp_path/'Integrations/AgentMemoryService/project-alpha'
    before=service._current(project)
    event=threading.Event()
    def cancel(*args):
        value=invoke_fixture(*args);event.set();return value
    monkeypatch.setattr(service,'_invoke',cancel)
    with pytest.raises(AgentMemoryError,match='cancelled'):
        service.execute('remember',{'project':'alpha','content':'new'},data_root=tmp_path,resource_root=tmp_path,cancel_event=event)
    assert service._current(project)==before
    assert not list(project.glob('transaction-*'))


def test_worker_failure_preserves_prior_generation(enabled,monkeypatch,tmp_path):
    monkeypatch.setattr(service,'_invoke',invoke_fixture)
    service.execute('remember',{'project':'alpha','content':'original'},data_root=tmp_path,resource_root=tmp_path)
    project=tmp_path/'Integrations/AgentMemoryService/project-alpha';before=service._current(project)
    def fail(*_):raise AgentMemoryError('failed')
    monkeypatch.setattr(service,'_invoke',fail)
    with pytest.raises(AgentMemoryError):service.execute('forget',{'project':'alpha','memory_ids':['mem_fixture'],'reason':'test'},data_root=tmp_path,resource_root=tmp_path)
    assert service._current(project)==before


@pytest.mark.parametrize('failure',['cancel-after-move','pointer-replace'])
def test_precommit_failure_removes_only_owned_new_generation(enabled,monkeypatch,tmp_path,failure):
    monkeypatch.setattr(service,'_invoke',invoke_fixture)
    service.execute('remember',{'project':'alpha','content':'original'},data_root=tmp_path,resource_root=tmp_path)
    project=tmp_path/'Integrations/AgentMemoryService/project-alpha';before=service._current(project)
    unrelated=project/'generations'/'unrelated-owner-file';unrelated.write_text('preserve')
    generations=set((project/'generations').iterdir());event=threading.Event();replace=service.os.replace
    def fail_at_boundary(source,destination):
        if failure=='pointer-replace' and Path(destination).name=='current.json':raise OSError('synthetic pointer failure')
        result=replace(source,destination)
        if failure=='cancel-after-move' and Path(source).name=='state':event.set()
        return result
    monkeypatch.setattr(service.os,'replace',fail_at_boundary)
    with pytest.raises(AgentMemoryError):
        service.execute('remember',{'project':'alpha','content':'new'},data_root=tmp_path,resource_root=tmp_path,cancel_event=event)
    assert service._current(project)==before
    assert set((project/'generations').iterdir())==generations
    assert unrelated.read_text()=='preserve'
    assert not list(project.glob('transaction-*'))
    assert not list(project.glob('pointer-*.tmp'))


def test_successful_write_cleans_only_previous_generation(enabled,monkeypatch,tmp_path):
    monkeypatch.setattr(service,'_invoke',invoke_fixture)
    service.execute('remember',{'project':'alpha','content':'one'},data_root=tmp_path,resource_root=tmp_path)
    project=tmp_path/'Integrations/AgentMemoryService/project-alpha';_,previous,_=service._current(project)
    unrelated=project/'unrelated-owner-file';unrelated.write_text('preserve')
    service.execute('remember',{'project':'alpha','content':'two'},data_root=tmp_path,resource_root=tmp_path)
    assert not previous.exists()
    assert unrelated.read_text()=='preserve'
    assert len(list((project/'generations').iterdir()))==1


def test_recall_does_not_publish_access_metadata(enabled,monkeypatch,tmp_path):
    monkeypatch.setattr(service,'_invoke',invoke_fixture)
    service.execute('remember',{'project':'alpha','content':'one'},data_root=tmp_path,resource_root=tmp_path)
    project=tmp_path/'Integrations/AgentMemoryService/project-alpha';before=service._current(project)
    def recall(bundle,node,scratch,action,args,event):
        (scratch/'state'/'access.bin').write_text('ephemeral query metadata')
        return {'result':{'results':[]},'seconds':.1}
    monkeypatch.setattr(service,'_invoke',recall)
    service.execute('recall',{'project':'alpha','query':'anything'},data_root=tmp_path,resource_root=tmp_path)
    assert service._current(project)==before


def test_pointer_path_escape_is_rejected(tmp_path):
    (tmp_path/'current.json').write_text(json.dumps({'generation':'../owner'}))
    with pytest.raises(AgentMemoryError,match='pointer'):service._current(tmp_path)


def test_store_size_limit_is_enforced(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'MAX_STORE_BYTES',4);(tmp_path/'file').write_bytes(b'12345')
    with pytest.raises(AgentMemoryError,match='limit'):service._tree(tmp_path)


def test_single_global_operation_slot(enabled,monkeypatch,tmp_path):
    service._SLOT.acquire()
    try:
        with pytest.raises(AgentMemoryError,match='Another'):
            service.execute('recall',{'project':'alpha','query':'anything'},data_root=tmp_path,resource_root=tmp_path)
    finally:service._SLOT.release()


def test_native_helper_uses_loopback_and_disabled_viewer():
    asset=Path(service.__file__).with_name('vendor')/'agent_memory_service_host.py'
    spec=importlib.util.spec_from_file_location('memory_host',asset);helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    config=helper.engine_config([49001,49002,49003,49004],Path('C:/project'))
    rows={r['name']:r['config'] for r in config['workers']}
    for name in ['iii-worker-manager','iii-http','iii-stream']:assert rows[name]['host']=='127.0.0.1'
    assert 'iii-exec' not in rows
    worker=asset.with_name('agent_memory_service_worker.mjs').read_text()
    assert "Number(process.env.III_VIEWER_PORT)) return this" in worker
    assert "Unexpected Node HTTP listener refused" in worker
