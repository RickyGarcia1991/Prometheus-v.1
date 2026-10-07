from prometheus_assistant.approvals import PendingApprovalStore, permission_request


def test_permission_request_is_least_privilege():
    req=permission_request("r1","code","run tests",read_paths=["repo"],write_paths=["repo/.pytest_cache"])
    assert req.network is False
    assert req.read_paths==("repo",)
    assert req.write_paths==("repo/.pytest_cache",)


def test_pending_approval_round_trip(tmp_path):
    store=PendingApprovalStore(tmp_path/"pending.json")
    original=permission_request("r2","code","edit file",write_paths=["src/a.py"])
    store.save(original)
    assert store.load()==original
    store.clear()
    assert store.load() is None


def test_save_replaces_previous_request_atomically(tmp_path):
    store=PendingApprovalStore(tmp_path/"pending.json")
    store.save(permission_request("old","code","old"))
    current=permission_request("new","research","fetch",network=True)
    store.save(current)
    assert store.load()==current
    assert not (tmp_path/"pending.json.tmp").exists()
