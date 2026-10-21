"""Exact same-owner capability lineage is durable; authority remains RAM-only."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.decision_history import CAPABILITY_REVISION_KINDS
from prometheus_assistant import capability_revisions

O='a'*64; B='b'*64; S='c'*32; H='d'*64


def review(app, kind='runtime_config', **extra):
    return app.review(owner=O,binding=B,session=S,kind=kind,payload={'private':'never store this'},
                      review={'description':'private review'},policy_revision='p',capability_subject=H,**extra)


def decide(app,row,decision='request_changes'):
    return app.consume(owner=O,binding=B,session=S,approval_id=row['id'],fingerprint=row['fingerprint'],
                       policy_revision='p',decision=decision,feedback='Use a smaller change.' if decision=='request_changes' else '')


def setup(path,kind='runtime_config',clock=lambda:100):
    app=ActionApprovals(history_root=path,clock=clock);parent=review(app,kind);decide(app,parent)
    context=app.capability_revision_context(owner=O,binding=B,session=S,kind=kind,subject_hash=H,parent_row=parent)
    return app,parent,context


@pytest.mark.parametrize('kind',sorted(CAPABILITY_REVISION_KINDS))
def test_supported_lineage_reopen_and_subject_fingerprint(tmp_path,kind):
    app,parent,context=setup(tmp_path,kind)
    document={key:parent[key] for key in ('kind','payload','review','session','policy_revision','capability_subject')}
    assert parent['fingerprint']==hashlib.sha256(json.dumps(document,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    change_path=tmp_path/'CapabilityChangeRequests'/'decisions.sqlite3'
    before=change_path.read_bytes()
    child=review(app,kind,capability_revision_token=context['capability_revision_token'])
    assert child['id']!=parent['id'] and child['origin_approval']==parent['id']
    assert change_path.read_bytes()==before and not app.decision_history.path.exists()
    assert not (tmp_path/'RevisionLinks').exists() and not (tmp_path/'CapabilityLinks').exists()
    assert app.history(owner=O)[0]['capability_revisions'][0]['child_state']=='unrecorded'
    reopened=ActionApprovals(history_root=tmp_path)
    assert reopened.peek(owner=O,binding=B) is None
    assert reopened.history(owner=O)[0]['capability_revisions'][0]['child_id']==child['id']
    with pytest.raises(ValueError):reopened.validate_capability_revision_context(owner=O,binding=B,session=S,
        kind=kind,subject_hash=H,capability_revision_token=context['capability_revision_token'])
    decide(app,child,'approve')
    assert all(row['capability_revisions'][0]['child_state']=='recorded' for row in app.history(owner=O))
    assert app.history(owner='f'*64)==[]
    assert 'never store this' not in app._capability_revisions().path.read_bytes().decode('latin1')


@pytest.mark.parametrize('change',[{'owner':'e'*64},{'binding':'e'*64},{'session':'e'*32},
    {'kind':'idle_config'},{'capability_subject':'e'*64}])
def test_revision_mismatch_does_not_consume_token(tmp_path,change):
    app,parent,context=setup(tmp_path)
    args=dict(owner=O,binding=B,session=S,kind='runtime_config',payload={},review={},policy_revision='p',
              capability_subject=H,capability_revision_token=context['capability_revision_token'])
    with pytest.raises(ValueError):app.review(**(args|change))
    assert app.peek(owner=O,binding=B) is None
    app.review(**args)
    assert app.peek(owner=O,binding=B)['origin_approval']==parent['id']


@pytest.mark.parametrize('change',[{'capability_subject':'e'*64},{'kind':'idle_config'},
    {'session':'e'*32},{'payload':{'private':'forged'}},{'fingerprint':'e'*64},{'id':'e'*32}])
def test_forged_parent_cannot_bind_context(tmp_path,change):
    app,parent,context=setup(tmp_path)
    with pytest.raises(ValueError):app.capability_revision_context(owner=O,binding=B,session=S,
        kind='runtime_config',subject_hash=H,parent_row=parent|change)


def test_context_requires_saved_request_changes_and_current_subject(tmp_path):
    app=ActionApprovals(history_root=tmp_path);parent=review(app)
    args=dict(owner=O,binding=B,session=S,kind='runtime_config',subject_hash=H,parent_row=parent)
    with pytest.raises(ValueError):app.capability_revision_context(**args)
    decide(app,parent,'approve')
    with pytest.raises(ValueError):app.capability_revision_context(**args)
    old=app.review(owner=O,binding=B,session=S,kind='runtime_config',payload={},review={},policy_revision='p')
    with pytest.raises(ValueError):decide(app,old)
    assert app.pending(owner=O,binding=B)['id']==old['id']


def test_early_validation_expiry_clock_revoke_and_one_use(tmp_path):
    clock=[100];app,parent,context=setup(tmp_path,clock=lambda:clock[0])
    args=dict(owner=O,binding=B,session=S,kind='runtime_config',subject_hash=H,**{'capability_revision_token':context['capability_revision_token']})
    assert app.validate_capability_revision_context(**args)=={'origin_approval':parent['id']}
    assert not app._capability_revisions().path.exists()
    clock[0]=99
    with pytest.raises(ValueError):app.validate_capability_revision_context(**args)
    clock[0]=700
    with pytest.raises(ValueError):app.validate_capability_revision_context(**args)
    clock[0]=100
    app.revoke_binding(B)
    with pytest.raises(ValueError):app.validate_capability_revision_context(**args)
    context=app.capability_revision_context(owner=O,binding=B,session=S,kind='runtime_config',subject_hash=H,parent_row=parent)
    child=review(app,capability_revision_token=context['capability_revision_token']);decide(app,child,'reject')
    with pytest.raises(ValueError):review(app,capability_revision_token=context['capability_revision_token'])


def test_storage_failure_before_publication_preserves_intent(tmp_path,monkeypatch):
    app,parent,context=setup(tmp_path)
    original=capability_revisions.CapabilityRevisions.record
    monkeypatch.setattr(capability_revisions.CapabilityRevisions,'record',lambda *args:(_ for _ in ()).throw(OSError('store unavailable')))
    with pytest.raises(OSError):review(app,capability_revision_token=context['capability_revision_token'])
    assert app.peek(owner=O,binding=B) is None
    monkeypatch.setattr(capability_revisions.CapabilityRevisions,'record',original)
    review(app,capability_revision_token=context['capability_revision_token'])


def test_store_idempotence_conflict_capacity_and_corruption(tmp_path,monkeypatch):
    app,parent,context=setup(tmp_path);review(app,capability_revision_token=context['capability_revision_token'])
    store=app._capability_revisions();row=store.read()[0]
    assert store.record(row) is False
    with pytest.raises(ValueError):store.record(row|{'created_at':row['created_at']+1})
    monkeypatch.setattr(capability_revisions,'MAX_LINKS',1)
    with pytest.raises(ValueError):store.record(row|{'child_id':'f'*32})
    with sqlite3.connect(store.path) as db:db.execute("UPDATE revisions SET payload='{}'")
    with pytest.raises(ValueError):app.history(owner=O)


def test_thread_race_publishes_only_one_child(tmp_path):
    app,parent,context=setup(tmp_path)
    def prepare(_):
        try:return review(app,capability_revision_token=context['capability_revision_token'])['id']
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(prepare,range(2)))
    assert len([item for item in results if item is not None])==1
    assert len(app._capability_revisions().read())==1


def test_native_reopen_has_history_but_no_pending_authority(tmp_path):
    app,parent,context=setup(tmp_path);review(app,capability_revision_token=context['capability_revision_token'])
    code='import sys; sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]/'src'))+'); '
    code+="from prometheus_assistant.action_approvals import ActionApprovals; a=ActionApprovals(history_root=sys.argv[1]); assert a.history(owner='a'*64)[0]['capability_revisions'][0]['child_state']=='unrecorded'; assert a.peek(owner='a'*64,binding='b'*64) is None"
    subprocess.run([sys.executable,'-B','-c',code,str(tmp_path)],check=True,capture_output=True,timeout=15)


def test_native_process_duplicate_links_remain_one_record(tmp_path):
    app,parent,context=setup(tmp_path);review(app,capability_revision_token=context['capability_revision_token'])
    row=app._capability_revisions().read()[0]
    code='import sys,json; sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]/'src'))+'); '
    code+='from prometheus_assistant.action_approvals import ActionApprovals; a=ActionApprovals(history_root=sys.argv[1]); assert a._capability_revisions().record(json.loads(sys.argv[2])) is False'
    children=[subprocess.Popen([sys.executable,'-B','-c',code,str(tmp_path),json.dumps(row)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    for child in children:
        out,error=child.communicate(timeout=15)
        assert child.returncode==0,error
    assert app._capability_revisions().read()==[row]


def test_chain_subject_and_bounds(tmp_path,monkeypatch):
    app,parent,context=setup(tmp_path)
    child=review(app,capability_revision_token=context['capability_revision_token']);decide(app,child)
    context=app.capability_revision_context(owner=O,binding=B,session=S,kind='runtime_config',subject_hash=H,parent_row=child)
    monkeypatch.setattr(capability_revisions,'MAX_CHAIN',1)
    with pytest.raises(ValueError):review(app,capability_revision_token=context['capability_revision_token'])
    assert app.peek(owner=O,binding=B) is None


def test_missing_store_readonly_and_mixed_context_refused(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    assert app.history(owner=O)==[] and not tmp_path.joinpath('CapabilityRevisionLinks').exists()
    with pytest.raises(ValueError):review(app,revision_token='e'*32,capability_revision_token='f'*32)
    with pytest.raises(ValueError):review(app,kind='project_edit')


def test_linked_store_refused(tmp_path):
    app,parent,context=setup(tmp_path);elsewhere=tmp_path/'elsewhere';elsewhere.mkdir()
    try:(tmp_path/'CapabilityRevisionLinks').symlink_to(elsewhere,target_is_directory=True)
    except OSError:pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ValueError):review(app,capability_revision_token=context['capability_revision_token'])
    assert app.peek(owner=O,binding=B) is None
