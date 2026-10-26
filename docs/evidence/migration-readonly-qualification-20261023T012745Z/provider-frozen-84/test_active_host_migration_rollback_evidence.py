"""Targeted real-DPAPI storage negative; synthetic generated authority only."""
import pytest
from test_active_host_migration_store_integration import setup,through_backup,core


@pytest.mark.parametrize('phase',['restore','start','complete'])
def test_post_replace_rollback_refuses_missing_replacement_receipt(setup,phase):
    open_store,plan,root,old,new=setup
    store=open_store(True);through_backup(store,plan);store.replace_owned(plan)
    for step in ('restore','start','complete'):
        store.write_journal(core.seal(plan,step,store.key))
        if step==phase:break
    receipt=store.directory/'replacement-receipt.json'
    receipt.rename(store.directory/'retained-original-replacement-receipt.json')
    before=store.journal.read_bytes()
    with pytest.raises((ValueError,OSError)):open_store(mode='rollback')
    assert store.journal.read_bytes()==before
    assert not (store.directory/'rollback-intent.json').exists()
    assert (root/plan['payload']).read_bytes()==new
