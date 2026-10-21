"""Interpretation changes retain observations and never restore authority."""
import copy
import json
import multiprocessing
from pathlib import Path
import sqlite3
import time
import uuid

import pytest
from prometheus_assistant.evidence_resolution import canonical, digest, proposal
from prometheus_assistant.evidence_interpretation_lifecycle import EvidenceInterpretationLifecycle, review_document
from test_evidence_resolution import make_app, family, prepare, decide, O, B, S
from test_evidence_resolution import files
from test_shared_capability_evidence import complete, approved


def setup_root(path, source='worker', **options):
    app = make_app(path, **options)
    target = family(app, source)
    original = decide(app, prepare(app, target))['approval']
    return app, target, original


def item(app, original):
    return app._interpretation_change_inputs(O, original['id'], original['fingerprint'])[0]


def change(app, original, operation='revise', **overrides):
    current, conflicts = app._interpretation_change_inputs(O, original['id'], original['fingerprint'])
    args = dict(owner=O, binding=B, session=S, interpretation_id=original['id'],
        interpretation_fingerprint=original['fingerprint'], expected_state_sha256=current['state_sha256'],
        operation=operation, reason='An explicit revised explanation.', policy_revision='p')
    if operation == 'revise':
        conflict = conflicts[current['target_id'], current['target_fingerprint']]
        args.update(snapshot_sha256=conflict['snapshot_sha256'], fact_sha256=conflict['variants'][-1]['fact_sha256'])
    return app.prepare_interpretation_change(**(args | overrides))


def choose(app, pending, decision='approve', **overrides):
    args = dict(owner=O, binding=B, session=S, approval_id=pending['id'], fingerprint=pending['fingerprint'],
                decision=decision, policy_revision='p')
    return app.decide_interpretation_change(**(args | overrides))


def consume_only(app, pending, decision='approve'):
    return app.consume(owner=O, binding=B, session=S, approval_id=pending['id'], fingerprint=pending['fingerprint'],
                       decision=decision, policy_revision='p')


def completion(pending, decision='approve'):
    return dict(schema_version=1, owner=O, approval_id=pending['id'], fingerprint=pending['fingerprint'],
                decision=decision, recorded_at=time.time())


def add_native(app, target, source):
    if source == 'worker':
        complete(app, marker='Another generated native observation')
    elif source == 'application':
        from test_application_outcomes import observe
        observe(app, 'recovery_required')
    else:
        event = copy.deepcopy(next(r for r in app.history(owner=O) if r['id']==target['id'])['outcomes'][-1])
        event.update(event_id=uuid.uuid4().hex, observed_at=event['observed_at']+1,
                     phase='undo_started', status='started', checks=None, rollback='unknown', evidence_sha256=None)
        app.record_outcome(owner=O, approval_id=target['id'], fingerprint=target['fingerprint'], event=event)


def _process_link(path, value, ready, start, results):
    store = make_app(Path(path)).interpretation_lifecycle()
    ready.put(True)
    if not start.wait(20):
        raise RuntimeError('Generated race did not start')
    try:
        results.put(('linked', value['approval_id'], store.link(value)))
    except ValueError as error:
        results.put(('refused', value['approval_id'], str(error)))


def _process_finish(path, value, results):
    try:
        results.put(('finished', make_app(Path(path)).interpretation_lifecycle().finish(value)))
    except ValueError as error:
        results.put(('refused', str(error)))


@pytest.mark.parametrize('source', ['worker', 'project', 'application'])
@pytest.mark.parametrize('operation', ['revise', 'withdraw'])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_all_sources_change_only_the_separate_lifecycle(tmp_path, source, operation, decision):
    app, target, original = setup_root(tmp_path, source)
    before_item = item(app, original)
    before = files(tmp_path)
    pending = change(app, original, operation)
    assert pending['kind'] == 'evidence_interpretation_change' and files(tmp_path) == before
    assert pending['review'] == review_document(pending['payload'])
    result = choose(app, pending, decision)['interpretation_change']
    assert result['source'] == source and result['total_changes'] == 1
    assert result['changes'][0]['decision'] == decision and result['changes'][0]['recording_state'] == 'recorded'
    assert result['state_sha256'] != before_item['state_sha256']
    assert result['state'] == ('withdrawn' if operation == 'withdraw' and decision == 'approve' else 'active')
    assert result['effective_review_id'] == (pending['id'] if decision == 'approve' else original['id'])
    assert result['effective_payload'] == (pending['payload']['replacement']
        if operation == 'revise' and decision == 'approve' else original['payload'])
    assert result['effective_change'] == (result['changes'][0] if decision == 'approve' else None)
    after = files(tmp_path)
    for name, raw in before.items():
        if name != 'history/decisions.sqlite3':
            assert after[name] == raw
    saved = app.history(owner=O)
    current = next(r for r in saved if r['id'] == target['id'])
    for name in ('outcomes', 'capability_outcomes', 'application_outcomes', 'evidence_lifecycle', 'evidence_conflict'):
        assert current[name] == target[name]
    receipt = next(r for r in saved if r['id'] == pending['id'])
    assert receipt['kind'] == 'other' and receipt['interpretation_lifecycle']['items'] == [result]
    assert not app._application_starts and not app._pending
    read_before = files(tmp_path)
    reopened = make_app(tmp_path)
    assert reopened.history(owner=O) == saved and files(tmp_path) == read_before
    with pytest.raises(ValueError):
        choose(reopened, pending)


def test_revision_then_withdrawal_is_terminal_and_other_root_remains_independent(tmp_path):
    app, target, original = setup_root(tmp_path)
    other = decide(app, prepare(app, target, reason='Another independent interpretation'))['approval']
    original_bytes = app.evidence_resolutions().path.read_bytes()
    revised = choose(app, change(app, original))['interpretation_change']
    withdrawn = choose(app, change(app, original, 'withdraw'))['interpretation_change']
    assert withdrawn['effective_payload'] == revised['effective_payload']
    assert withdrawn['effective_change']['operation'] == 'withdraw'
    assert item(app, other)['state'] == 'active'
    before = files(tmp_path)
    with pytest.raises(ValueError, match='withdrawn'):
        change(app, original)
    assert files(tmp_path) == before and app.evidence_resolutions().path.read_bytes() == original_bytes
    fresh = decide(app, prepare(app, target, reason='Fresh independent interpretation after withdrawal'))['approval']
    assert item(app, fresh)['state'] == 'active' and item(app, fresh)['effective_change'] is None


def test_effective_conflict_uses_facts_not_wording_and_history_stays_visible(tmp_path):
    app, target, original = setup_root(tmp_path)
    first_fact, second_fact = [v['fact_sha256'] for v in target['evidence_conflict']['variants']]
    other = decide(app, prepare(app, target, fact_sha256=second_fact))['approval']
    assert item(app, original)['history_conflict'] and item(app, other)['interpretation_conflict']
    revised = choose(app, change(app, original, fact_sha256=second_fact))['interpretation_change']
    assert revised['history_conflict'] and not revised['interpretation_conflict']
    assert item(app, other)['history_conflict'] and not item(app, other)['interpretation_conflict']
    result = choose(app, change(app, original, fact_sha256=second_fact, reason='Same fact, clearer wording'))['interpretation_change']
    assert result['total_changes'] == 2 and not result['interpretation_conflict']
    choose(app, change(app, original, fact_sha256=first_fact))
    assert item(app, other)['interpretation_conflict']
    choose(app, change(app, original, 'withdraw'))
    assert not item(app, other)['interpretation_conflict'] and item(app, other)['history_conflict']


@pytest.mark.parametrize('source', ['worker', 'project', 'application'])
def test_native_staleness_does_not_change_digest_and_withdrawal_remains_possible(tmp_path, source):
    app, target, original = setup_root(tmp_path, source)
    pending = change(app, original)
    before_item = item(app, original)
    add_native(make_app(tmp_path), target, source)
    current = item(app, original)
    assert current['state_sha256'] == before_item['state_sha256'] and current['currency'] == 'stale'
    before = files(tmp_path)
    with pytest.raises(ValueError, match='changed'):
        choose(app, pending)
    with pytest.raises(ValueError, match='current'):
        change(app, original, snapshot_sha256=pending['payload']['replacement']['snapshot_sha256'])
    assert files(tmp_path) == before
    # The failed approval keeps its exact live review available for denial.
    # Close it explicitly before preparing another change in this browser.
    assert choose(app, pending, 'reject')['interpretation_change']['state'] == 'active'
    assert choose(app, change(app, original, 'withdraw'))['interpretation_change']['state'] == 'withdrawn'


@pytest.mark.parametrize('override', [dict(owner='f'*64), dict(binding='f'*64), dict(session='f'*32),
    dict(approval_id='f'*32), dict(fingerprint='f'*64), dict(decision='request_changes')])
def test_exact_live_decision_identity_required(tmp_path, override):
    app, _, original = setup_root(tmp_path)
    pending = change(app, original)
    before = files(tmp_path)
    with pytest.raises(ValueError):
        choose(app, pending, **override)
    assert files(tmp_path) == before and app.peek(owner=O, binding=B)['id'] == pending['id']


@pytest.mark.parametrize('override', [dict(owner='f'*64), dict(interpretation_id='f'*32),
    dict(interpretation_fingerprint='f'*64), dict(expected_state_sha256='f'*64), dict(operation='retry'),
    dict(reason=''), dict(reason='x'*2001), dict(reason='bad\x00'), dict(reason='bad\ud800'),
    dict(fact_sha256='f'*64), dict(snapshot_sha256='f'*64), dict(policy_revision='')])
def test_invalid_prepare_never_writes(tmp_path, override):
    app, _, original = setup_root(tmp_path)
    before = files(tmp_path)
    with pytest.raises(ValueError):
        change(app, original, **override)
    assert files(tmp_path) == before and app.peek(owner=O, binding=B) is None


@pytest.mark.parametrize('mode', ['policy', 'expiry', 'revoke', 'restart'])
def test_authority_cannot_survive_revocation_expiry_policy_or_restart(tmp_path, mode):
    clock = [1]
    app, _, original = setup_root(tmp_path, clock=lambda: clock[0], lifetime=2)
    pending = change(app, original)
    before = files(tmp_path)
    options = {}
    if mode == 'policy': options['policy_revision'] = 'changed'
    elif mode == 'expiry': clock[0] = 4
    elif mode == 'revoke': app.revoke_binding(B)
    else: app = make_app(tmp_path)
    with pytest.raises(ValueError):
        choose(app, pending, **options)
    assert files(tmp_path) == before and app.peek(owner=O, binding=B) is None


def test_only_completed_approved_originals_can_be_changed(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    target = family(app, 'worker')
    rejected = decide(app, prepare(app, target), 'reject')['approval']
    with pytest.raises(ValueError, match='completed approved'):
        change(app, rejected)
    from prometheus_assistant.evidence_resolution import EvidenceResolutions
    monkeypatch.setattr(EvidenceResolutions, 'finish', lambda *a, **k: (_ for _ in ()).throw(OSError('generated failure')))
    pending = prepare(app, target)
    with pytest.raises(ValueError, match='unconfirmed'): decide(app, pending)
    with pytest.raises(ValueError, match='completed approved'): change(app, pending)
    assert app.history(owner=O)[0]['interpretation_lifecycle']['total'] == 0


def test_before_link_failure_preserves_pending_and_has_no_audit(tmp_path, monkeypatch):
    app, _, original = setup_root(tmp_path)
    pending = change(app, original)
    before = files(tmp_path)
    def fail(*a, **k): raise OSError('generated link failure')
    monkeypatch.setattr(EvidenceInterpretationLifecycle, 'link', fail)
    with pytest.raises(OSError): choose(app, pending)
    assert files(tmp_path) == before and app.peek(owner=O, binding=B)['id'] == pending['id']


def test_after_link_failure_is_visible_and_exact_retry_does_not_append_twice(tmp_path, monkeypatch):
    app, _, original = setup_root(tmp_path)
    pending = change(app, original)
    before_head = item(app, original)['state_sha256']
    record = app.decision_history.record
    def fail(*a, **k): raise ValueError('generated canonical failure')
    monkeypatch.setattr(app.decision_history, 'record', fail)
    with pytest.raises(ValueError, match='canonical'): choose(app, pending)
    linked = item(app, original)
    assert linked['state_sha256'] != before_head and linked['completion_unconfirmed']
    assert linked['changes'][0]['decision'] == linked['changes'][0]['recording_state'] == 'unrecorded'
    proposal_before = app.interpretation_lifecycle().read()[0]
    monkeypatch.setattr(app.decision_history, 'record', record)
    result = choose(app, pending)['interpretation_change']
    assert result['total_changes'] == 1 and not result['completion_unconfirmed']
    assert app.interpretation_lifecycle().read()[0] == proposal_before


def test_after_consumption_failure_never_replays_and_fresh_change_can_complete(tmp_path, monkeypatch):
    app, _, original = setup_root(tmp_path)
    pending = change(app, original)
    finish = EvidenceInterpretationLifecycle.finish
    def fail(*a, **k): raise OSError('generated completion failure')
    monkeypatch.setattr(EvidenceInterpretationLifecycle, 'finish', fail)
    with pytest.raises(ValueError, match='unconfirmed'): choose(app, pending)
    unconfirmed = item(app, original)
    assert unconfirmed['effective_change'] is None and unconfirmed['completion_unconfirmed']
    assert unconfirmed['changes'][0]['decision'] == 'approve' and unconfirmed['changes'][0]['recording_state'] == 'unrecorded'
    assert app.peek(owner=O, binding=B) is None
    before = files(tmp_path)
    with pytest.raises(ValueError): choose(make_app(tmp_path), pending)
    assert files(tmp_path) == before
    monkeypatch.setattr(EvidenceInterpretationLifecycle, 'finish', finish)
    newer = make_app(tmp_path)
    fresh = change(newer, original)
    current = choose(newer, fresh)['interpretation_change']
    assert current['effective_review_id'] == fresh['id'] and current['completion_unconfirmed']
    with pytest.raises(ValueError, match='superseded'):
        app.interpretation_lifecycle().finish(completion(pending))


def test_response_loss_after_completion_does_not_repeat_a_change(tmp_path, monkeypatch):
    app, _, original = setup_root(tmp_path)
    pending = change(app, original)
    actual = app._interpretation_change_inputs
    def fail(*a, **k): raise OSError('generated response loss')
    monkeypatch.setattr(app, '_interpretation_change_inputs', fail)
    with pytest.raises(OSError): choose(app, pending)
    monkeypatch.setattr(app, '_interpretation_change_inputs', actual)
    assert item(app, original)['effective_review_id'] == pending['id']
    before = files(tmp_path)
    with pytest.raises(ValueError): choose(app, pending)
    assert files(tmp_path) == before


@pytest.mark.parametrize('boundary', ['link', 'consume'])
def test_native_changes_at_write_boundaries_do_not_become_effective(tmp_path, monkeypatch, boundary):
    app, target, original = setup_root(tmp_path)
    pending = change(app, original)
    if boundary == 'link':
        actual = EvidenceInterpretationLifecycle.link
        def intercept(store, value, **kwargs):
            result = actual(store, value, **kwargs)
            add_native(make_app(tmp_path), target, 'worker')
            return result
        monkeypatch.setattr(EvidenceInterpretationLifecycle, 'link', intercept)
    else:
        actual = app.consume
        def intercept(**kwargs):
            result = actual(**kwargs)
            add_native(make_app(tmp_path), target, 'worker')
            return result
        monkeypatch.setattr(app, 'consume', intercept)
    with pytest.raises(ValueError): choose(app, pending)
    current = item(app, original)
    assert current['effective_change'] is None and current['completion_unconfirmed']
    assert current['changes'][0]['decision'] == ('unrecorded' if boundary == 'link' else 'approve')


def test_two_processes_cannot_link_against_same_head(tmp_path):
    app, _, original = setup_root(tmp_path)
    left = change(app, original, reason='First process proposal')
    right = change(make_app(tmp_path), original, reason='Second process proposal')
    ctx = multiprocessing.get_context('spawn')
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    children = [ctx.Process(target=_process_link, args=(str(tmp_path), proposal(O, pending, 7), ready, start, results))
                for pending in (left, right)]
    try:
        for child in children: child.start()
        for _ in children: assert ready.get(timeout=30)
        start.set()
        outcomes = [results.get(timeout=30) for _ in children]
        for child in children:
            child.join(30)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive(): child.terminate(); child.join(10)
    assert sorted(result[0] for result in outcomes) == ['linked', 'refused']
    saved = item(app, original)
    assert saved['total_changes'] == 1 and saved['effective_change'] is None
    assert all(c['decision'] == 'unrecorded' for c in saved['changes'])


@pytest.mark.parametrize('newer_decision', [None, 'reject', 'approve'])
def test_newer_link_permanently_blocks_old_completion_in_another_process(tmp_path, newer_decision):
    app, _, original = setup_root(tmp_path)
    old = change(app, original)
    store = app.interpretation_lifecycle()
    store.link(proposal(O, old, 100))
    consume_only(app, old)
    newer = change(app, original, reason='Newer exact review')
    store.link(proposal(O, newer, 1))  # Backward wall-clock does not change sequence.
    if newer_decision:
        consume_only(app, newer, newer_decision)
        store.finish(completion(newer, newer_decision))
    before = files(tmp_path)
    ctx = multiprocessing.get_context('spawn'); results = ctx.Queue()
    child = ctx.Process(target=_process_finish, args=(str(tmp_path), completion(old), results))
    try:
        child.start(); outcome = results.get(timeout=30); child.join(30)
        assert child.exitcode == 0 and outcome[0] == 'refused' and 'superseded' in outcome[1]
    finally:
        if child.is_alive(): child.terminate(); child.join(10)
    assert files(tmp_path) == before
    current = item(make_app(tmp_path), original)
    assert current['changes'][0]['approval_id'] == newer['id'] and current['completion_unconfirmed']
    assert current['effective_review_id'] == (newer['id'] if newer_decision == 'approve' else original['id'])


def test_digest_ignores_own_canonical_receipt_unrelated_roots_and_native_currency(tmp_path):
    app, target, original = setup_root(tmp_path)
    pending = change(app, original)
    store = app.interpretation_lifecycle()
    store.link(proposal(O, pending, 3))
    linked = item(app, original)['state_sha256']
    consume_only(app, pending)
    assert item(app, original)['state_sha256'] == linked
    other = decide(app, prepare(app, target, reason='Unrelated root'))['approval']
    choose(app, change(app, other), 'reject')
    complete(app, owner='f'*64)
    assert item(app, original)['state_sha256'] == linked
    assert store.finish(completion(pending))
    assert item(app, original)['state_sha256'] != linked


def test_unicode_bound_and_truncated_context_keep_effective_change_proof(tmp_path, monkeypatch):
    import prometheus_assistant.evidence_interpretation_lifecycle as module
    app, _, original = setup_root(tmp_path)
    reason = '\U0001f50d'*2000
    effective = change(app, original, reason=reason)
    assert choose(app, effective)['interpretation_change']['effective_payload']['reason'] == reason
    # Three demonstrates the same boundary with less disk work; the public
    # production cap remains explicitly asserted as sixteen.
    assert module.MAX_ITEMS == 16
    monkeypatch.setattr(module, 'MAX_ITEMS', 3)
    older = None
    for n in range(4):
        pending = change(app, original, reason=f'Rejected audit attempt {n}')
        older = older or pending
        choose(app, pending, 'reject')
    history = app.history(owner=O)
    root = next(r for r in history if r['id'] == original['id'])['interpretation_lifecycle']['items'][0]
    old_receipt = next(r for r in history if r['id'] == older['id'])['interpretation_lifecycle']['items'][0]
    assert root['total_changes'] == 5 and root['changes_truncated'] and len(root['changes']) == 3
    assert older['id'] not in {c['approval_id'] for c in root['changes']}
    assert older['id'] in {c['approval_id'] for c in old_receipt['changes']}
    assert root['effective_change']['approval_id'] == effective['id']
    assert root['effective_change']['recording_state'] == 'recorded' and root['effective_change']['decision'] == 'approve'
    assert root | {'changes': []} == old_receipt | {'changes': []}


def test_legacy_receipts_original_interpretations_and_owner_isolation(tmp_path):
    from prometheus_assistant.decision_history import DecisionHistory, _event
    app, target, original = setup_root(tmp_path)
    original_store = app.evidence_resolutions().path.read_bytes()
    pending = change(app, original, reason='Private owner lifecycle reason')
    choose(app, pending)
    native = next(r for r in app.history(owner=O) if r['id'] == target['id'])
    assert native['evidence_resolutions']['items'][0]['reason'] == original['payload']['reason']
    assert app.evidence_resolutions().path.read_bytes() == original_store
    rows = DecisionHistory(tmp_path/'history')._read_rows()
    assert all(_event(r) for r in rows)
    assert next(r for r in rows if r['id'] == pending['id'])['kind'] == 'other'
    with sqlite3.connect(app.decision_history.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('decisions',)]
    complete(app, owner='f'*64)
    foreign = app.history(owner='f'*64)
    assert pending['id'] not in json.dumps(foreign) and 'Private owner' not in json.dumps(foreign)
    assert all(r['interpretation_lifecycle']['total'] == 0 for r in foreign)


@pytest.mark.parametrize('mutation', ['schema', 'oversize', 'foreign', 'fingerprint', 'head', 'phase', 'completion', 'noncanonical'])
def test_corrupt_sidecar_fails_closed_and_does_not_mutate_native_history(tmp_path, mutation):
    app, _, original = setup_root(tmp_path)
    choose(app, change(app, original))
    path = app.interpretation_lifecycle().path
    with sqlite3.connect(path) as db:
        if mutation == 'schema': db.execute('PRAGMA user_version=2')
        elif mutation == 'oversize': db.execute("UPDATE changes SET payload=? WHERE phase='proposal'", ('x'*(128*1024+1),))
        elif mutation == 'phase': db.execute("UPDATE changes SET phase='unknown' WHERE phase='proposal'")
        else:
            phase = 'decision' if mutation == 'completion' else 'proposal'
            value = json.loads(db.execute('SELECT payload FROM changes WHERE phase=?', (phase,)).fetchone()[0])
            if mutation == 'foreign': value['owner'] = 'f'*64
            elif mutation == 'fingerprint': value['fingerprint'] = 'f'*64
            elif mutation == 'head': value['document']['payload']['expected_state_sha256'] = 'f'*64
            elif mutation == 'completion': value['decision'] = 'reject'
            raw = json.dumps(value, indent=2) if mutation == 'noncanonical' else canonical(value)
            db.execute('UPDATE changes SET payload=? WHERE phase=?', (raw, phase))
    before = files(tmp_path)
    with pytest.raises(ValueError): app.history(owner=O)
    assert files(tmp_path) == before


def test_full_store_refuses_without_eviction_or_consumption(tmp_path, monkeypatch):
    import prometheus_assistant.evidence_interpretation_lifecycle as module
    app, _, original = setup_root(tmp_path)
    monkeypatch.setattr(module, 'MAX_CHANGES', 1)
    choose(app, change(app, original), 'reject')
    pending = change(app, original)
    before = files(tmp_path)
    with pytest.raises(ValueError, match='capacity full'): choose(app, pending)
    assert files(tmp_path) == before and app.peek(owner=O, binding=B)['id'] == pending['id']


def test_linked_paths_and_tampered_pending_are_rejected_before_writes(tmp_path, monkeypatch):
    app, _, original = setup_root(tmp_path)
    pending = change(app, original)
    before = files(tmp_path)
    app._pending[O, B]['review']['summary'] = 'Changed after review'
    with pytest.raises(ValueError, match='fingerprint'): choose(app, pending)
    assert files(tmp_path) == before
    path = app.interpretation_lifecycle().path
    actual = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == path or actual(p))
    with pytest.raises(ValueError, match='Linked'): app.history(owner=O)
    assert files(tmp_path) == before


def test_approved_interpretation_can_be_explicitly_withdrawn(tmp_path):
    app = make_app(tmp_path)
    target = family(app, 'worker')
    original = decide(app, prepare(app, target))['approval']
    saved = next(r for r in app.history(owner=O) if r['id'] == original['id'])
    item = saved['interpretation_lifecycle']['items'][0]
    pending = app.prepare_interpretation_change(owner=O, binding=B, session=S,
        interpretation_id=original['id'], interpretation_fingerprint=original['fingerprint'],
        expected_state_sha256=item['state_sha256'], operation='withdraw',
        reason='Withdraw this explanation; retain all observations.', policy_revision='p')
    result = app.decide_interpretation_change(owner=O, binding=B, session=S,
        approval_id=pending['id'], fingerprint=pending['fingerprint'], decision='approve', policy_revision='p')
    assert result['interpretation_change']['state'] == 'withdrawn'
    assert result['interpretation_change']['effective_payload'] == original['payload']
    assert not app._pending and not app._application_starts
