import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.decision_history import DecisionHistory
from prometheus_assistant import decision_history

OWNER='a'*64
BINDING='b'*64
SESSION='c'*32


def event(number=1, **changes):
    return dict(schema_version=1,id=f'{number:032x}',owner=OWNER,fingerprint='d'*64,
                session=SESSION,kind='project_edit',decision='request_changes',feedback='Use a smaller change.',decided_at=1000.+number) | changes


def pending(store,kind='project_edit'):
    return store.review(owner=OWNER,binding=BINDING,session=SESSION,kind=kind,
        payload={'source':'PRIVATE_SOURCE_PATH_SECRET'},review={'prompt':'PRIVATE_PROMPT'},policy_revision='policy')


def consume(store,row,**changes):
    return store.consume(**(dict(owner=OWNER,binding=BINDING,session=SESSION,approval_id=row['id'],
        fingerprint=row['fingerprint'],policy_revision='policy',decision='request_changes',feedback='  Make it smaller.  ') | changes))


def test_restart_owner_scope_and_minimal_private_schema(tmp_path):
    history=DecisionHistory(tmp_path)
    history.record(event(1))
    history.record(event(2,owner='e'*64))
    rows=DecisionHistory(tmp_path).history(OWNER)
    assert len(rows)==1 and 'owner' not in rows[0]
    assert set(rows[0])==set(event())-{'owner'}
    assert DecisionHistory(tmp_path).history('f'*64)==[]


def test_recent_fifty_are_ordered_by_timestamp_and_identity(tmp_path):
    history=DecisionHistory(tmp_path)
    for number in range(55):history.record(event(number))
    rows=history.history(OWNER)
    assert len(rows)==50 and rows[0]['id']==f'{54:032x}' and rows[-1]['id']==f'{5:032x}'
    assert len(history.history(OWNER,limit=2))==2
    with pytest.raises(ValueError):history.history(OWNER,limit=51)


def test_exact_duplicate_is_idempotent_conflicts_refused(tmp_path):
    history=DecisionHistory(tmp_path)
    assert history.record(event()) is True
    assert history.record(event()) is False
    with pytest.raises(ValueError,match='conflicting'):
        history.record(event(decision='approve',feedback=''))
    assert history.history(OWNER)[0]['decision']=='request_changes'


def test_hard_capacity_never_evicts_and_duplicate_still_allowed(tmp_path,monkeypatch):
    monkeypatch.setattr(decision_history,'MAX_RECEIPTS',3)
    history=DecisionHistory(tmp_path)
    for n in range(3):history.record(event(n))
    before=history.history(OWNER)
    with pytest.raises(ValueError,match='full'):history.record(event(3))
    assert history.record(event(1)) is False
    assert history.history(OWNER)==before


@pytest.mark.parametrize('changes',[{'schema_version':True},{'extra':'source'},{'owner':'path'},
    {'kind':'private-path'},{'decision':'run'},{'decided_at':float('nan')},{'feedback':'\x00'},
    {'feedback':'x'*2001},{'feedback':' untrimmed '},{'decision':'approve','feedback':'not allowed'}])
def test_invalid_events_do_not_create_storage(tmp_path,changes):
    history=DecisionHistory(tmp_path)
    with pytest.raises(ValueError):history.record(event(**changes))
    assert not history.path.exists()


def test_corruption_is_not_an_empty_success(tmp_path):
    history=DecisionHistory(tmp_path);history.record(event())
    with closing(sqlite3.connect(history.path)) as db, db:
        db.execute("UPDATE decisions SET payload='{}'")
    with pytest.raises(ValueError):history.history(OWNER)


def test_unknown_schema_and_oversize_are_refused(tmp_path,monkeypatch):
    history=DecisionHistory(tmp_path);history.record(event())
    with closing(sqlite3.connect(history.path)) as db, db:db.execute('CREATE TABLE unexpected(secret TEXT)')
    with pytest.raises(ValueError,match='schema'):history.history(OWNER)
    monkeypatch.setattr(decision_history,'MAX_DATABASE_BYTES',1)
    with pytest.raises(ValueError,match='size bound'):history.history(OWNER)


def test_real_cross_process_writers_keep_one_exact_receipt(tmp_path):
    source=Path(__file__).resolve().parents[1]/'src'
    script="import sys,json;sys.path.insert(0,sys.argv[1]);from prometheus_assistant.decision_history import DecisionHistory;print(DecisionHistory(sys.argv[2]).record(json.loads(sys.argv[3])))"
    commands=[[sys.executable,'-B','-c',script,str(source),str(tmp_path),json.dumps(event())] for _ in range(4)]
    children=[subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for cmd in commands]
    replies=[child.communicate(timeout=15) for child in children]
    assert all(child.returncode==0 for child in children),replies
    assert [out.strip() for out,err in replies].count('True')==1
    assert len(DecisionHistory(tmp_path).history(OWNER))==1


def test_decision_saved_before_consumption_and_never_restores_pending(tmp_path):
    store=ActionApprovals(history_root=tmp_path);row=pending(store)
    assert consume(store,row)['id']==row['id']
    saved=store.history(owner=OWNER)[0]
    assert saved['decision']=='request_changes' and saved['feedback']=='Make it smaller.'
    assert 'PRIVATE' not in json.dumps(saved)
    assert store.pending(owner=OWNER,binding=BINDING) is None
    restarted=ActionApprovals(history_root=tmp_path)
    assert restarted.history(owner=OWNER)==[saved]
    assert restarted.pending(owner=OWNER,binding=BINDING) is None
    with pytest.raises(ValueError,match='missing'):consume(restarted,row)


def test_storage_failure_keeps_exact_pending(tmp_path,monkeypatch):
    store=ActionApprovals(history_root=tmp_path);row=pending(store)
    def failed(event):raise OSError('storage unavailable')
    monkeypatch.setattr(store.decision_history,'record',failed)
    with pytest.raises(OSError):consume(store,row)
    assert store.pending(owner=OWNER,binding=BINDING)==row


@pytest.mark.parametrize('decision,feedback,kind',[('request_changes','','project_edit'),
    ('request_changes','  ','project_edit'),('request_changes','x'*2001,'project_edit'),
    ('request_changes','\x00','project_edit'),('request_changes','Change','scheduled_syntax'),
    ('approve','feedback','project_edit'),('reject',' ','project_edit')])
def test_bad_feedback_never_consumes_or_records(tmp_path,decision,feedback,kind):
    store=ActionApprovals(history_root=tmp_path);row=pending(store,kind)
    with pytest.raises(ValueError):consume(store,row,decision=decision,feedback=feedback)
    assert store.pending(owner=OWNER,binding=BINDING)==row
    assert store.history(owner=OWNER)==[]


def test_wrong_identity_cannot_write_and_policy_change_revokes(tmp_path):
    store=ActionApprovals(history_root=tmp_path);row=pending(store)
    with pytest.raises(ValueError):consume(store,row,fingerprint='0'*64)
    assert store.history(owner=OWNER)==[]
    with pytest.raises(ValueError,match='policy changed'):consume(store,row,policy_revision='changed')
    assert store.pending(owner=OWNER,binding=BINDING) is None
    assert store.history(owner=OWNER)==[]


def test_concurrent_consume_one_receipt(tmp_path):
    store=ActionApprovals(history_root=tmp_path);row=pending(store)
    def attempt(_):
        try:consume(store,row);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(attempt,range(8)))
    assert results.count(True)==1 and len(store.history(owner=OWNER))==1


def test_legacy_disabled_history_and_unknown_kind_generic(tmp_path):
    store=ActionApprovals();row=pending(store)
    consume(store,row)
    with pytest.raises(ValueError,match='not configured'):store.history(owner=OWNER)
    store=ActionApprovals(history_root=tmp_path);row=pending(store,'private_kind')
    consume(store,row,decision='reject',feedback='')
    assert store.history(owner=OWNER)[0]['kind']=='other'


def test_symlink_root_or_sidecar_is_rejected(tmp_path):
    target=tmp_path/'target';target.mkdir()
    linked=tmp_path/'linked'
    try:linked.symlink_to(target,target_is_directory=True)
    except OSError:pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ValueError,match='Linked'):DecisionHistory(linked)
    store=DecisionHistory(target)
    Path(str(store.path)+'-journal').symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='Linked'):store.history(OWNER)


def test_full_store_refuses_new_decision_without_consuming_pending(tmp_path,monkeypatch):
    monkeypatch.setattr(decision_history,'MAX_RECEIPTS',1)
    store=ActionApprovals(history_root=tmp_path)
    store.decision_history.record(event())
    row=pending(store)
    with pytest.raises(ValueError,match='full'):consume(store,row)
    assert store.pending(owner=OWNER,binding=BINDING)==row
    assert len(store.history(owner=OWNER))==1


def test_read_only_empty_does_not_create_database(tmp_path):
    target=tmp_path/'absent'
    assert DecisionHistory(target).history(OWNER)==[]
    assert not target.exists()


def test_runtime_configuration_kind_retained_without_payload(tmp_path):
    store=ActionApprovals(history_root=tmp_path);row=pending(store,'runtime_config')
    consume(store,row,decision='approve',feedback='')
    assert store.history(owner=OWNER)[0]['kind']=='runtime_config'
    assert 'PRIVATE' not in json.dumps(store.history(owner=OWNER))
