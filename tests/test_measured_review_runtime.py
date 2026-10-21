"""Post-check adapter tests use fake transport, never model or test execution."""
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import measured_review_runtime as runtime
from prometheus_assistant.project_task_evidence import candidate_digest
from prometheus_assistant.reviewer_check_evidence import measured_checks
from prometheus_assistant.ollama import LocalModelError


@pytest.fixture
def rig(monkeypatch,tmp_path):
    source = b'value = 1\n'
    candidate = dict(version=1, path='example.py', content=source.decode(),
                     after_sha256=hashlib.sha256(source).hexdigest(), checks=[])
    digest = candidate_digest(candidate)
    native = dict(kind='process_outcome', run_id='a'*32, candidate_sha256=digest,
                  preset='python-compile', target='example.py')
    packet = dict(schema_version=1, kind='native_process_outcomes', run_id='a'*32,
        candidate_sha256=digest, files={'example.py': source}, issues=[], complete=True,
        semantic_evidence_available=False, authorized=False, production_accepted=False,
        evidence={hashlib.sha256(source).hexdigest(): dict(path='example.py', checks=[dict(
            input=native, expected=dict(exit_code=0,cancelled=False,timed_out=False),
            actual=dict(exit_code=1,cancelled=False,timed_out=False),passed=False)])})
    state = SimpleNamespace(candidate=candidate, packet=packet, response='{"findings":[]}',
        calls=0, inventory_calls=0, identity_change=False, after=None, error=None, messages=None,
        app=SimpleNamespace(cancel_event=threading.Event()), policy=True, fit=True)
    from prometheus_assistant.project_resources import ProjectResources
    state.app.lock=threading.RLock()
    state.app.project_resources=ProjectResources(tmp_path,probe=SimpleNamespace(snapshot=lambda **_:dict(
        total_ram_gib=16,available_ram_gib=8,free_disk_gib=20,cpu_percent=10,
        battery_percent=100,plugged_in=True)))
    class Client:
        def __init__(self, **kwargs):
            self.options=kwargs
        def chat_stream(self,messages,**options):
            state.calls+=1
            state.messages=deepcopy(messages)
            assert self.options['base_url']=='http://127.0.0.1:11434'
            state.schema=deepcopy(options['json_format'])
            if state.error:
                raise state.error
            options['on_token'](state.response)
            if state.after:
                state.after()
            return state.response,1
    def inventory(client):
        state.inventory_calls+=1
        return {'local':dict(provider='ollama-local',model='local',artifact_sha256=(
            'c' if state.identity_change and state.inventory_calls==3 else 'b')*64)}
    class Signal:
        def __init__(self,*args):
            self.timed_out=False
            self.memory=SimpleNamespace(pressure=False)
        def is_set(self):
            return False
    monkeypatch.setattr(runtime.guards,'OllamaClient',Client)
    monkeypatch.setattr(runtime.guards,'_inventory',inventory)
    monkeypatch.setattr(runtime.guards,'_profiles',lambda *args: [SimpleNamespace(
        model='local',context=32768,cpu_threads=2)] if state.fit else [])
    monkeypatch.setattr(runtime.guards,'_allowed',lambda:state.policy)
    monkeypatch.setattr(runtime.guards,'_output_tokens',lambda p:768)
    monkeypatch.setattr(runtime.guards,'_Signal',Signal)
    return state


def run(rig,**kwargs):
    return runtime.run_review(rig.app,rig.candidate,rig.packet,**kwargs)


def test_empty_report_does_not_hide_native_failure(rig):
    result=run(rig)
    assert result['status']=='completed' and rig.calls==1
    assert result['report']['status']=='checks_failed'
    assert len(result['report']['reviewer_omissions'])==1
    assert result['production_accepted'] is False and result['authorized'] is False
    assert result['semantic_evidence_available'] is False
    assert 'native process outcomes' in rig.messages[0]['content']
    assert json.loads(rig.messages[1]['content'])['files'][0]['source']=='value = 1\n'
    json.dumps(result)  # No raw byte snapshot leaks into saved UI report.


def test_valid_reference_remains_unverified_location(rig):
    row=measured_checks(rig.packet['evidence'],rig.packet['files'])[0]
    rig.response=json.dumps({'findings':[dict(evidence_id=row['evidence_id'],file='example.py',line=1)]})
    result=run(rig)
    assert result['report']['reviewer_omissions']==[]
    assert result['report']['reviewer_report']['findings'][0]['line_verified'] is False


@pytest.mark.parametrize('response',['bad','{"findings":[],"claim":"fake"}','x'*65537],ids=['malformed','claim','oversized'])
def test_invalid_model_output_preserves_failures(rig,response):
    rig.response=response
    result=run(rig)
    assert result['status']=='invalid_response'
    assert result['report']['status']=='checks_failed'
    assert result['report']['reviewer_report'] is None


@pytest.mark.parametrize('scenario',['policy','cancel','memory','runtime','identity','incomplete'])
def test_unavailable_model_preserves_failures(rig,scenario):
    if scenario=='policy':rig.policy=False
    if scenario=='cancel':rig.app.cancel_event.set()
    if scenario=='memory':rig.fit=False
    if scenario=='runtime':rig.error=LocalModelError('private raw message')
    if scenario=='identity':rig.identity_change=True
    if scenario=='incomplete':rig.packet['complete']=False
    result=run(rig)
    assert result['status']!='completed'
    assert len(result['report']['failed_checks'])==1
    assert len(result['report']['reviewer_omissions'])==1
    assert 'private raw message' not in json.dumps(result)
    assert rig.calls==int(scenario in {'runtime','identity'})


@pytest.mark.parametrize('field,value',[('run_id','bad'),('candidate_sha256','0'*64),
    ('semantic_evidence_available',True),('complete',1),('authorized',True)])
def test_wrong_packet_not_sent_to_model(rig,field,value):
    rig.packet[field]=value
    assert run(rig)['status']=='invalid_input'
    assert rig.calls==0


def test_wrong_source_and_measurement_run_rejected(rig):
    rig.packet['files']['example.py']=b'changed\n'
    assert run(rig)['status']=='invalid_input'
    assert rig.calls==0


def test_cancel_or_policy_change_after_call_discards_model_claim(rig):
    rig.after=rig.app.cancel_event.set
    result=run(rig)
    assert result['status']=='cancelled'
    assert result['report']['reviewer_report'] is None


def test_caller_mutation_during_inference_cannot_change_assessed_snapshot(rig):
    rig.after=lambda:rig.packet['evidence'].clear()
    result=run(rig)
    assert len(result['report']['failed_checks'])==1


def test_timeout_parameter_is_bounded_without_losing_failures(rig):
    result=run(rig,timeout_seconds=True)
    assert result['status']=='invalid_input' and rig.calls==0
    assert len(result['report']['failed_checks'])==1


def test_transport_timeout_is_explicit_and_keeps_failures(rig):
    rig.error=TimeoutError('do not expose raw text')
    result=run(rig)
    assert result['status']=='timed_out' and rig.calls==1
    assert len(result['report']['failed_checks'])==1


def test_context_overflow_does_not_truncate_source(rig,monkeypatch):
    monkeypatch.setattr(runtime.guards,'_profiles',lambda *args:[SimpleNamespace(
        model='local',context=100,cpu_threads=2)])
    result=run(rig)
    assert result['status']=='context_unavailable' and rig.calls==0


def test_passing_native_check_cannot_be_claimed_as_defect(rig):
    check=next(iter(rig.packet['evidence'].values()))['checks'][0]
    check['actual']=deepcopy(check['expected'])
    check['passed']=True
    row=measured_checks(rig.packet['evidence'],rig.packet['files'])[0]
    rig.response=json.dumps({'findings':[dict(evidence_id=row['evidence_id'],file='example.py',line=1)]})
    result=run(rig)
    assert result['status']=='invalid_response'
    assert not result['report']['may_present_as_checks_passed']


def test_changed_run_inside_measurement_rejected(rig):
    next(iter(rig.packet['evidence'].values()))['checks'][0]['input']['run_id']='b'*32
    assert run(rig)['status']=='invalid_input' and rig.calls==0


def test_multibyte_stream_is_byte_bounded(rig):
    rig.response='\u20ac'*22000
    result=run(rig)
    assert result['status']=='invalid_response'
    assert len(result['report']['failed_checks'])==1


def test_saved_review_is_detached_and_json_safe(rig):
    original=run(rig)
    saved=runtime.validate_saved_review(original)
    assert saved==original
    saved['report']['measured_checks'].clear()
    assert len(original['report']['measured_checks'])==1


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(source='private source'),
    lambda r:r.update(status='arbitrary model prose'),
    lambda r:r.update(authorized=True),
    lambda r:r['report'].update(status='checks_passed'),
    lambda r:r['report'].update(failed_checks=[]),
    lambda r:r['report'].update(reviewer_omissions=[]),
    lambda r:r['report']['measured_checks'][0]['input'].update(stdout='private stdout'),
    lambda r:r['report']['measured_checks'][0]['actual'].update(exit_code=False),
    lambda r:r.update(status=[]),
],ids=['source','status','auth','falsegreen','missingfailure','missingomission','stdout','booleanexit','malformed'])
def test_saved_history_rejects_false_or_unbounded_fields(rig,mutation):
    value=run(rig)
    mutation(value)
    with pytest.raises(ValueError):runtime.validate_saved_review(value)


def test_large_valid_history_preserves_counts_in_explicit_nonpassing_summary(rig):
    value=run(rig)
    report=value['report']
    first=report['measured_checks'][0]
    checks=[]
    for index in range(100):
        row=deepcopy(first)
        row['evidence_id']=format(index,'016x')
        checks.append(row)
    report.update(measured_checks=checks,failed_checks=deepcopy(checks),
                  reviewer_omissions=[r['evidence_id'] for r in checks])
    saved=runtime.validate_saved_review(value)
    assert saved['report'] is None
    assert saved['history_summary']['failed_check_count']==100
    assert saved['history_summary']['reviewer_omission_count']==100
    assert saved['history_summary']['may_present_as_checks_passed'] is False
    assert len(json.dumps(saved))<48*1024
    assert runtime.validate_saved_review(saved)==saved


def test_saved_unavailable_result_has_no_invented_report(rig):
    rig.policy=False
    value=runtime.validate_saved_review(run(rig))
    assert value['status']=='policy_denied'
    assert len(value['report']['failed_checks'])==1


def test_generation_constrains_exact_failed_evidence_id(rig):
    run(rig)
    row=measured_checks(rig.packet['evidence'],rig.packet['files'])[0]
    assert rig.schema['properties']['findings']['items']['properties']['evidence_id']['enum']==[row['evidence_id']]
    assert 'enum' not in runtime.SCHEMA['properties']['findings']['items']['properties']['evidence_id']


def test_truncated_id_still_rejected_if_backend_ignores_schema(rig):
    row=measured_checks(rig.packet['evidence'],rig.packet['files'])[0]
    rig.response=json.dumps({'findings':[dict(evidence_id=row['evidence_id'][:7]+'_',file='example.py',line=1)]})
    result=run(rig)
    assert result['status']=='invalid_response'
    assert result['report']['status']=='checks_failed'
    assert result['report']['reviewer_omissions']==[row['evidence_id']]


def test_clean_request_disallows_findings_without_mutating_next_request(rig):
    check=next(iter(rig.packet['evidence'].values()))['checks'][0]
    check['actual']['exit_code']=0;check['passed']=True
    run(rig)
    assert rig.schema['properties']['findings']['maxItems']==0
    check['actual']['exit_code']=1;check['passed']=False
    run(rig)
    assert rig.schema['properties']['findings']['maxItems']==32
    assert len(rig.schema['properties']['findings']['items']['properties']['evidence_id']['enum'])==1


def test_historical_v1_saved_review_remains_readable(rig):
    result=run(rig);result['prompt_version']='measured-evidence-reference-v1'
    assert runtime.validate_saved_review(result)['prompt_version']=='measured-evidence-reference-v1'


def test_dynamic_schema_is_bound_into_request_hash(rig):
    result=run(rig)
    wire=runtime._json_bytes(dict(messages=rig.messages,format=rig.schema))
    assert result['prompt_sha256']==hashlib.sha256(wire).hexdigest()


def test_dynamic_enum_cost_is_included_in_context_admission(rig,monkeypatch):
    run(rig)
    old_size=len(runtime._json_bytes(dict(messages=rig.messages,format=runtime.SCHEMA)))
    rig.calls=0
    monkeypatch.setattr(runtime.guards,'_profiles',lambda *args:[SimpleNamespace(
        model='local',context=old_size+256+768,cpu_threads=2)])
    assert run(rig)['status']=='context_unavailable'
    assert rig.calls==0


@pytest.mark.parametrize('mode',['Idle','Battery Saver','Critical Recovery'])
def test_restrictive_mode_preserves_failed_checks_without_inventory_or_inference(rig,mode):
    rig.app.project_resources.set_mode_preference(mode)
    result=run(rig)
    assert result['status']=='resource_deferred' and result['attempted'] is False
    assert rig.calls==rig.inventory_calls==0
    assert result['report']['status']=='checks_failed'
    assert result['report']['may_present_as_checks_passed'] is False
    assert runtime.validate_saved_review(result)==result


def test_mode_change_during_preparation_prevents_chat(rig,monkeypatch):
    original=runtime.guards._profiles
    calls=[]
    def profiles(*args):
        calls.append(True)
        result=original(*args)
        if len(calls)==2:rig.app.project_resources.set_mode_preference('Idle')
        return result
    monkeypatch.setattr(runtime.guards,'_profiles',profiles)
    result=run(rig)
    assert result['status']=='resource_deferred' and not result['attempted']
    assert rig.inventory_calls==2 and rig.calls==0
    assert result['resource_admission']['mode']['requested']=='Idle'
    assert runtime.validate_saved_review(result)==result


def test_automatic_idle_dwell_remains_foreground_eligible(rig):
    assert rig.app.project_resources.status()['mode']=='Idle'
    result=run(rig)
    assert result['status']=='completed' and rig.calls==1
    assert result['resource_admission']['mode']['requested'] is None


def test_unknown_admission_is_safe_and_legacy_history_remains_valid(rig,monkeypatch):
    legacy=run(rig);legacy.pop('resource_admission')
    assert runtime.validate_saved_review(legacy)==legacy
    def fail(*args,**kwargs):raise OSError('private path')
    monkeypatch.setattr(runtime.specialist_resources,'admit',fail)
    before=rig.calls;result=run(rig)
    assert result['status']=='resource_deferred' and result['resource_admission'] is None
    assert rig.calls==before and 'private path' not in str(result)
    assert runtime.validate_saved_review(result)==result


@pytest.mark.parametrize('mutate',[
    lambda r:r.update(attempted=True),
    lambda r:r.update(status='completed'),
    lambda r:r.pop('resource_admission'),
    lambda r:r['resource_admission'].update(decision='run_local'),
    lambda r:r['resource_admission']['mode'].update(dwell_pending=1),
])
def test_deferred_receipt_rejects_forged_contradictions(rig,mutate):
    rig.app.project_resources.set_mode_preference('Idle');result=run(rig)
    mutate(result)
    with pytest.raises(ValueError):runtime.validate_saved_review(result)


def test_clean_native_checks_remain_available_when_model_deferred(rig):
    for group in rig.packet['evidence'].values():
        for check in group['checks']:
            check['actual']['exit_code']=0;check['passed']=True
    rig.app.project_resources.set_mode_preference('Battery Saver')
    result=run(rig)
    assert result['status']=='resource_deferred' and result['attempted'] is False
    assert result['report']['measured_checks'][0]['passed'] is True
    assert result['report']['reviewer_report'] is None
