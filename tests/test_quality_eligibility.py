from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.compact_chat import candidates
from prometheus_assistant.hardware import HardwareProfile
from prometheus_assistant import ui_server


def record(evidence, model, role, outcomes):
    for n, passed in enumerate(outcomes):
        evidence.record_benchmark(model, role, str(n), passed=passed, seconds=.2)


def test_poor_recorded_role_is_paused_without_disabling_other_roles(tmp_path):
    e=ModelEvidence(tmp_path/'evidence.sqlite3')
    record(e,'gemma3:270m','general',[True,False,True,False])
    record(e,'gemma3:270m','translation',[True]*4)
    assert e.order(['gemma3:270m'],'general')==[]
    assert e.order(['gemma3:270m'],'translation')==['gemma3:270m']
    assert e.summary()['paused_roles'][0]['role']=='general'
    assert candidates('Name a planet',{'gemma3:270m'},HardwareProfile(8,8,'Windows',1.2),e)[1]==[]


def test_exact_threshold_and_rechecked_cases_can_restore_eligibility(tmp_path):
    e=ModelEvidence(tmp_path/'evidence.sqlite3')
    record(e,'tiny','general',[True,True,False,False])
    assert e.order(['tiny'],'general')==[]
    e.record_benchmark('tiny','general','2',passed=True,seconds=.3)
    assert e.order(['tiny'],'general')==['tiny']
    assert e.summary()['paused_roles']==[]


def test_small_sample_is_ranked_but_not_paused_and_old_evidence_expires(tmp_path):
    e=ModelEvidence(tmp_path/'evidence.sqlite3')
    record(e,'tiny','general',[False]*3)
    assert e.order(['tiny'],'general')==['tiny']
    e.record_benchmark('tiny','general','3',passed=False,seconds=.1)
    assert e.order(['tiny'],'general')==[]
    with e.connect() as db: db.execute('UPDATE benchmarks SET checked=0')
    assert e.order(['tiny'],'general')==['tiny']


def test_interface_uses_cited_evidence_when_only_fitting_role_failed(tmp_path,monkeypatch):
    app=ui_server.Interface(tmp_path/'memory.sqlite3')
    record(app.model_evidence,'gemma3:270m','general',[True,False,False,False])
    monkeypatch.setattr(ui_server,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',1.2))
    monkeypatch.setattr(ui_server,'installed_models',lambda:{'gemma3:270m'})
    monkeypatch.setattr(ui_server.OllamaClient,'resident_memory_gib',lambda self:0)
    monkeypatch.setattr(app,'ensure_runtime',lambda:(_ for _ in ()).throw(AssertionError('Rejected helper must not start')))
    monkeypatch.setattr(ui_server,'reference_query',lambda *args:{'results':[{'title':'Official source','excerpt':'Referenced evidence','url':'https://www.uscourts.gov/'}]})
    try:
        result=app.execute({'mode':'chat','prompt':'court service summons'})
        assert result['label']=='Source evidence · helper quality and memory'
        assert 'uscourts.gov' in result['reply']
        assert result['details']['paused_helpers'][0]['model']=='gemma3:270m'
        assert 'not a generated answer' in result['details']['memory_note']
    finally: app.close()
