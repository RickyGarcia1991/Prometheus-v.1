import copy,json,hashlib
from datetime import date
import pytest
from prometheus_assistant import legal

def case(**changes):
    result=dict(jurisdiction='US-federal-civil',court='Example U.S. District Court',trigger_date='2026-06-01',days=18,
        period_source=legal.RULES_URL,period_rule='Example supplied period; test only',service_rule='not-service-based')
    result.update(changes);return result

def test_missing_facts_never_invents_period_or_date():
    assert legal.deadline_preview({})['calculated_date'] is None
    result=legal.legal_plan(country='US',issue='contract dispute')
    assert 'court' in result['missing_facts'] and result['deadline'] is None

def test_juneteenth_and_weekend_roll_forward():
    result=legal.deadline_preview(case())
    assert result['raw_date']=='2026-06-19'
    assert result['calculated_date']=='2026-06-22'
    assert result['deadline_guaranteed'] is False
    assert 'live_official_rule_version_check' in result['pending_verifications']

def test_before_rolls_back_and_state_holiday_is_not_applied():
    result=legal.deadline_preview(case(trigger_date='2026-06-29',days=7,direction='before',state_holidays=['2026-06-22']))
    assert result['calculated_date']=='2026-06-22'
    result=legal.deadline_preview(case(trigger_date='2026-06-29',days=8,direction='before'))
    assert result['calculated_date']=='2026-06-18'

def test_mail_adds_three_after_adjusted_expiry_but_electronic_does_not():
    mail=legal.deadline_preview(case(service_rule='5(b)(2)(C)'))
    assert mail['base_expiry']=='2026-06-22' and mail['calculated_date']=='2026-06-25'
    assert legal.deadline_preview(case(service_rule='5(b)(2)(E)'))['calculated_date']=='2026-06-22'

def test_added_service_days_roll_across_weekend():
    assert legal.deadline_preview(case(days=16,service_rule='5(b)(2)(C)'))['calculated_date']=='2026-06-22'

def test_cross_year_observed_holiday_and_supplied_closure():
    assert date(2027,12,31) in legal.federal_holidays(2027)
    result=legal.deadline_preview(case(trigger_date='2027-12-30',days=1,court_closures=['2028-01-03']))
    assert result['calculated_date']=='2028-01-04'

@pytest.mark.parametrize('changes',[dict(jurisdiction='UK'),dict(days=True),dict(days=0),dict(days=367),dict(trigger_date='2026-02-30'),dict(service_rule='email'),dict(direction='before',service_rule='5(b)(2)(C)'),dict(state_holidays='2026-01-01'),dict(period_source='file:///rules')])
def test_unsupported_and_malformed_inputs_rejected(changes):
    with pytest.raises(ValueError):legal.deadline_preview(case(**changes))

def test_changed_official_rules_block_calculation(monkeypatch,tmp_path):
    config=tmp_path/'rules.json';config.write_text(json.dumps(dict(sha256='0'*64,edition='test')))
    monkeypatch.setattr(legal,'RULE_CONFIG',config)
    result=legal.deadline_preview(case(),online=True,fetch=lambda:b'%PDF- changed')
    assert result['status']=='rule_revision_requires_review' and result['calculated_date'] is None

def test_live_identical_rule_document_records_hash(monkeypatch,tmp_path):
    body=b'%PDF- original';config=tmp_path/'rules.json'
    config.write_text(json.dumps(dict(sha256=hashlib.sha256(body).hexdigest(),edition='test')))
    monkeypatch.setattr(legal,'RULE_CONFIG',config)
    result=legal.deadline_preview(case(),online=True,fetch=lambda:body)
    assert result['rule_check']['matches_installed_rules']
    assert result['status']=='provisional_calendar_calculation'

def test_no_network_without_online_permission():
    with pytest.raises(ValueError,match='online permission'):legal.check_rules(fetch=lambda:pytest.fail('network invoked'))
