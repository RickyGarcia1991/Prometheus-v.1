from pathlib import Path
from test_maintenance import make_memory
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.maintenance import create_backup,restore_backup


def test_decision_history_survives_verified_restore_without_authority(tmp_path):
    memory=tmp_path/'memory.sqlite3'
    make_memory(memory).close()
    maintenance=tmp_path/'data/Maintenance'
    approvals=ActionApprovals(history_root=maintenance/'DecisionHistory')
    owner,binding='a'*64,'b'*64
    row=approvals.review(owner=owner,binding=binding,session='c'*32,kind='project_edit',
        payload={},review={},policy_revision='fixture')
    approvals.consume(owner=owner,binding=binding,session=row['session'],approval_id=row['id'],
        fingerprint=row['fingerprint'],policy_revision='fixture',decision='request_changes',feedback='Preserve existing inputs.')
    before=approvals.history(owner=owner)
    backup=create_backup(memory,tmp_path/'backups',data_dirs={'Maintenance':maintenance},require_separate_volume=False)
    assert backup['verified'] and backup['source_unchanged']
    restored=restore_backup(backup['path'],tmp_path/'restored')
    assert restored['verified'] and not restored['live_data_overwritten']
    reopened=ActionApprovals(history_root=Path(restored['path'])/'Maintenance/DecisionHistory')
    assert reopened.history(owner=owner)==before
    assert reopened.pending(owner=owner,binding=binding) is None
