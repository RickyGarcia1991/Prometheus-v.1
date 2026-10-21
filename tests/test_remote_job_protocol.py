import copy
import hashlib
import json

import pytest

from prometheus_assistant.remote_job_protocol import normalize_request, assess_admission, validate_receipt


def request():
    return dict(schema_version=1, job_id='a'*32, device_id='surface-1', worker_digest='b'*64,
                operation='python-syntax-check', timeout_seconds=10,
                files=[dict(path='src/main.py', content='print("hi")\r\n')])


def facts():
    return dict(enabled=True, device_id='surface-1', worker_digest='b'*64, observed_at=100,
                available_ram_gib=.5, free_disk_gib=.1, cpu_percent=70, plugged_in=True)


def receipt(req):
    return dict(schema_version=1, job_id=req['job_id'], device_id=req['device_id'],
                worker_digest=req['worker_digest'], request_digest=req['request_digest'],
                state='completed', checks=[dict(path=f['path'], passed=True) for f in req['files']])


def test_digest_binds_exact_bytes_and_fields_without_mutation():
    raw = request()
    original = copy.deepcopy(raw)
    got = normalize_request(raw)
    expected = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                        allow_nan=False).encode('utf-8')).hexdigest()
    assert got['request_digest'] == expected
    assert normalize_request(got) == got
    assert raw == original
    raw['files'][0]['content'] = 'print("hi")\n'
    assert normalize_request(raw)['request_digest'] != expected
    got['files'][0]['content'] = 'changed'
    assert original == request()
    with pytest.raises(ValueError): normalize_request(got)


@pytest.mark.parametrize('field,value', [('schema_version',True), ('timeout_seconds',True),
    ('timeout_seconds',0), ('timeout_seconds',31), ('timeout_seconds',float('nan')),
    ('operation','shell'), ('job_id','A'*32), ('worker_digest','B'*64), ('device_id','../host'),
    ('extra','value'), ('files',[]), ('files',[{'path':'a.py','content':'\ud800'}])])
def test_bad_request_fields(field, value):
    raw = request(); raw[field] = value
    with pytest.raises(ValueError): normalize_request(raw)


@pytest.mark.parametrize('path', ['/a.py','C:/a.py','../a.py','x/../a.py','x//a.py',
    'x\\a.py','.git/a.py','.ssh/a.py','__pycache__/a.py','a.py:stream','CON.py','a.PY','a\x00.py'])
def test_private_or_nonportable_paths(path):
    raw = request(); raw['files'][0]['path'] = path
    with pytest.raises(ValueError): normalize_request(raw)


def test_file_count_collision_and_utf8_byte_limits():
    raw = request()
    raw['files'] *= 2
    with pytest.raises(ValueError): normalize_request(raw)
    raw['files'] = [dict(path='a.py',content=''),dict(path='A.py',content='')]
    with pytest.raises(ValueError): normalize_request(raw)
    raw['files'] = [dict(path=f'{n}.py',content='') for n in range(9)]
    with pytest.raises(ValueError): normalize_request(raw)
    raw['files'] = [dict(path='a.py',content='é'*128000)]
    normalize_request(raw)
    raw['files'][0]['content'] += 'é'
    with pytest.raises(ValueError): normalize_request(raw)
    raw['files'] = [dict(path=f'{n}.py',content='x'*180000) for n in range(3)]
    with pytest.raises(ValueError): normalize_request(raw)


def test_admission_exact_thresholds_and_local_veto():
    assert assess_admission(request(), facts(), now=130)['decision'] == 'ready'
    f = facts(); f['enabled'] = False
    assert assess_admission(request(), f, now=100)['decision'] == 'reject'
    for key in ('device_id','worker_digest'):
        f = facts(); f[key] = 'wrong'
        assert assess_admission(request(), f, now=100)['decision'] == 'reject'


@pytest.mark.parametrize('field,value', [('observed_at',69),('observed_at',101),
    ('observed_at',float('nan')), ('available_ram_gib',.49), ('free_disk_gib',.09),
    ('cpu_percent',70.01), ('cpu_percent',-1), ('cpu_percent',101),
    ('available_ram_gib',True), ('plugged_in',None), ('plugged_in',False),('enabled',None)])
def test_admission_defers_without_adequate_actual_facts(field,value):
    f = facts(); f[field] = value
    result = assess_admission(request(),f,now=100)
    assert result['decision'] == 'defer' and result['reasons']


def test_missing_facts_not_inferred():
    for key in facts():
        f = facts(); del f[key]
        assert assess_admission(request(),f,now=100)['decision'] != 'ready'


def test_admission_cannot_be_embedded_and_extreme_numbers_defer():
    raw = request(); raw['enabled'] = True
    with pytest.raises(ValueError): normalize_request(raw)
    f = facts(); f['available_ram_gib'] = 10**1000
    assert assess_admission(request(),f,now=100)['decision'] == 'defer'
    assert assess_admission(request(),facts(),now=float('nan'))['decision'] == 'defer'


def test_receipt_bound_to_changed_target_and_content():
    old = normalize_request(request()); answer = receipt(old)
    for field, value in [('path','other.py'), ('content','print(2)')]:
        changed = request(); changed['files'][0][field] = value
        assert validate_receipt(changed,answer)['status'] == 'incomplete'


def test_exact_multifile_coverage_can_arrive_out_of_order():
    raw = request(); raw['files'].append(dict(path='other.py',content='pass'))
    req = normalize_request(raw); answer = receipt(req)
    answer['checks'].reverse()
    assert validate_receipt(req,answer)['status'] == 'passed'
    answer['checks'][1] = dict(answer['checks'][0])
    assert validate_receipt(req,answer)['status'] == 'incomplete'


def test_exact_completed_receipt_is_supplemental_only():
    req = normalize_request(request())
    result = validate_receipt(req,receipt(req))
    assert result['status'] == 'passed'
    assert result['supplemental'] is True and result['authorized'] is False
    assert 'not cryptographic authentication' in result['binding_scope']
    assert 'not Windows' in result['acceptance_scope']


@pytest.mark.parametrize('change', ['digest','device','worker','job','missing','duplicate','changed',
    'unknown','false','nonbool','extra','running','unconfirmed','failed','deferred'])
def test_receipt_tamper_and_incomplete_never_green(change):
    req = normalize_request(request()); row = receipt(req)
    keys = dict(digest='request_digest',device='device_id',worker='worker_digest',job='job_id')
    if change in keys: row[keys[change]] = 'wrong'
    elif change == 'missing': row['checks'] = []
    elif change == 'duplicate': row['checks'] *= 2
    elif change == 'changed': row['checks'][0]['path'] = 'other.py'
    elif change == 'unknown': row['checks'][0]['extra'] = True
    elif change == 'false': row['checks'][0]['passed'] = False
    elif change == 'nonbool': row['checks'][0]['passed'] = 1
    elif change == 'extra': row['extra'] = True
    else: row['state'] = change
    assert validate_receipt(req,row)['status'] != 'passed'


def lint_request():
    return {**request(), 'schema_version': 2, 'operation': 'python-ruff-check-v1',
            'profile': 'ruff-python-v1', 'tool': {'sha256': 'c'*64, 'version': '0.14.0'},
            'files': [{'path': 'a.py', 'content': 'import os\nprint(missing)\n'},
                      {'path': 'b.py', 'content': 'answer = 42\n'}]}


def lint_receipt(req, *, findings=False):
    result = {**receipt(req), 'schema_version': 2, 'operation': req['operation'],
              'tool': dict(req['tool']), 'profile': req['profile'],
              'status': 'findings' if findings else 'passed', 'reason': '',
              'source_unchanged': True, 'diagnostics': []}
    if findings:
        result['checks'][0]['passed'] = False
        result['diagnostics'] = [dict(path='a.py', code='F401', message='Unused import: os',
                                     line=1, column=8, end_line=1, end_column=10)]
    return result


def test_lint_request_detaches_tool_and_binds_fixed_operation_profile_and_pin():
    raw = lint_request(); original = copy.deepcopy(raw)
    req = normalize_request(raw)
    assert raw == original and normalize_request(req) == req
    for field, value in [('tool', {'sha256': 'd'*64, 'version': '0.14.0'}),
                         ('tool', {'sha256': 'c'*64, 'version': '0.14.1'})]:
        changed = copy.deepcopy(raw); changed[field] = value
        assert normalize_request(changed)['request_digest'] != req['request_digest']
    req['tool']['version'] = '0.15.0'
    assert raw == original
    with pytest.raises(ValueError): normalize_request(req)


@pytest.mark.parametrize('field,value', [
    ('schema_version', 1), ('schema_version', True), ('schema_version', 3),
    ('operation', 'python-syntax-check'), ('operation', 'shell'),
    ('profile', 'user-defined'), ('tool', {}),
    ('tool', {'sha256': 'C'*64, 'version': '0.14.0'}),
    ('tool', {'sha256': 'c'*64, 'version': 'ruff 0.14.0'}),
    ('tool', {'sha256': 'c'*64, 'version': '0.14.0', 'path': '/usr/bin/ruff'}),
    ('args', ['--fix']), ('config', 'pyproject.toml')])
def test_lint_request_has_no_arbitrary_tool_or_option_surface(field, value):
    raw = lint_request(); raw[field] = value
    with pytest.raises(ValueError): normalize_request(raw)


def test_schema1_does_not_accept_schema2_tool_or_operation():
    raw = request(); raw['tool'] = lint_request()['tool']
    with pytest.raises(ValueError): normalize_request(raw)
    raw = request(); raw['operation'] = 'python-ruff-check-v1'
    with pytest.raises(ValueError): normalize_request(raw)


def test_clean_lint_and_findings_are_distinct_and_never_authority():
    req = normalize_request(lint_request())
    for findings, expected in [(False, 'passed'), (True, 'failed')]:
        answer = lint_receipt(req, findings=findings)
        original = copy.deepcopy(answer)
        evidence = validate_receipt(req, answer)
        assert evidence['status'] == expected
        assert evidence['lint_status'] == ('findings' if findings else 'passed')
        assert evidence['supplemental'] is True and evidence['authorized'] is False
        assert 'not Windows or runtime' in evidence['acceptance_scope']
        assert answer == original


@pytest.mark.parametrize('status,state,expected', [('tool_failed','failed','failed'),
    ('unavailable','failed','failed'), ('unavailable','deferred','incomplete'),
    ('unavailable','unconfirmed','incomplete')])
def test_lint_tool_failure_and_unavailable_do_not_assert_source_defect(status, state, expected):
    req = normalize_request(lint_request()); answer = lint_receipt(req)
    answer.update(state=state, status=status, reason='Pinned tool is unavailable.', source_unchanged=False)
    for row in answer['checks']: row['passed'] = False
    evidence = validate_receipt(req, answer)
    assert evidence['status'] == expected and evidence['lint_status'] == status
    assert 'no clean source result' in evidence['reasons'][0]


@pytest.mark.parametrize('field,value', [
    ('schema_version', 1), ('schema_version', True), ('operation','python-syntax-check'),
    ('tool', {'sha256': 'd'*64, 'version':'0.14.0'}), ('profile','other'),
    ('status','passed'), ('state','failed'), ('source_unchanged',False),
    ('reason','message'), ('checks',[]), ('extra', True)])
def test_lint_mismatched_or_contradictory_findings_are_incomplete(field, value):
    req = normalize_request(lint_request()); answer = lint_receipt(req, findings=True)
    answer[field] = value
    assert validate_receipt(req, answer)['status'] == 'incomplete'


@pytest.mark.parametrize('field,value', [
    ('path','other.py'), ('code','S101'), ('code',None), ('message',''),
    ('message','é'*81), ('message','a\x1b[2J'), ('message','x\ud800'),
    ('line',0), ('line',True), ('line',4), ('column',11),
    ('end_line',0), ('end_line',4), ('end_column',7), ('end_column',11), ('extra',False)])
def test_lint_diagnostic_is_bounded_and_bound_to_exact_source(field, value):
    req = normalize_request(lint_request()); answer = lint_receipt(req, findings=True)
    answer['diagnostics'][0][field] = value
    assert validate_receipt(req, answer)['status'] == 'incomplete'


def test_lint_requires_canonical_complete_coverage_and_diagnostic_order():
    req = normalize_request(lint_request()); answer = lint_receipt(req, findings=True)
    answer['checks'].reverse()
    assert validate_receipt(req, answer)['status'] == 'incomplete'
    answer = lint_receipt(req, findings=True); answer['checks'][0]['passed'] = True
    assert validate_receipt(req, answer)['status'] == 'incomplete'
    answer = lint_receipt(req, findings=True)
    second = dict(path='a.py',code='F821',message='Undefined name: missing',
                  line=2,column=7,end_line=2,end_column=14)
    answer['diagnostics'].append(second)
    assert validate_receipt(req, answer)['status'] == 'failed'
    answer['diagnostics'].reverse()
    assert validate_receipt(req, answer)['status'] == 'incomplete'
    answer['diagnostics'] = [second, second]
    assert validate_receipt(req, answer)['status'] == 'incomplete'
    answer['diagnostics'] *= 9
    assert validate_receipt(req, answer)['status'] == 'incomplete'


def test_lint_unicode_positions_empty_source_and_utf8_message_limits():
    raw = lint_request(); raw['files'] = [dict(path='a.py', content='é = missing\r\n')]
    req = normalize_request(raw); answer = lint_receipt(req, findings=True)
    row = answer['diagnostics'][0]
    row.update(code='F821', message='é'*80, column=5, end_column=12)
    assert validate_receipt(req, answer)['status'] == 'failed'
    row['end_column'] = 13
    assert validate_receipt(req, answer)['status'] == 'incomplete'
    raw['files'][0]['content'] = ''
    req = normalize_request(raw); answer = lint_receipt(req)
    assert validate_receipt(req, answer)['status'] == 'passed'


def test_lint_receipt_total_byte_limit_cannot_be_hidden_by_individual_limits():
    raw = lint_request(); raw['files'] = [dict(path='a'*236+'.py', content='x'*200)]
    req = normalize_request(raw); answer = lint_receipt(req, findings=True)
    answer['diagnostics'] = [dict(path=raw['files'][0]['path'], code='F821', message='m'*160,
        line=1,column=n,end_line=1,end_column=n+1) for n in range(1,17)]
    assert len(json.dumps(answer, separators=(',',':')).encode()) >= 8192
    evidence = validate_receipt(req, answer)
    assert evidence['status'] == 'incomplete' and 'byte limit' in evidence['reasons'][0]


def test_lint_receipt_cannot_replay_against_another_tool_source_or_schema():
    req = normalize_request(lint_request()); answer = lint_receipt(req)
    raw = lint_request(); raw['files'][0]['content'] += '# changed\n'
    assert validate_receipt(raw, answer)['status'] == 'incomplete'
    assert validate_receipt(request(), answer)['status'] == 'incomplete'


def test_lint_admission_has_its_own_conservative_cost_without_changing_syntax():
    f = facts()
    assert assess_admission(request(), f, now=100)['decision'] == 'ready'
    assert assess_admission(lint_request(), f, now=100)['decision'] == 'defer'
    f.update(available_ram_gib=1.25, free_disk_gib=.5)
    assert assess_admission(lint_request(), f, now=100)['decision'] == 'ready'
    for key, value in [('available_ram_gib',1.249), ('free_disk_gib',.499),
                       ('available_ram_gib',True), ('free_disk_gib',None)]:
        changed = {**f, key: value}
        assert assess_admission(lint_request(), changed, now=100)['decision'] == 'defer'
    f.update(enabled=False, available_ram_gib=0)
    assert assess_admission(lint_request(), f, now=100)['decision'] == 'reject'


@pytest.mark.parametrize('paths',[('A/x.py','a/y.py'), ('parent/Sub/x.py','parent/sub/y.py'),
                                 ('a.py','a.py/child.py'), ('A.py','a.py/child.py'),
                                 ('venv/x.py','b.py'), ('module.py/child.py','b.py')])
def test_lint_rejects_directory_case_aliases_and_file_directory_collisions(paths):
    raw=lint_request(); raw['files']=[dict(path=path,content='pass') for path in paths]
    with pytest.raises(ValueError): normalize_request(raw)


def test_lint_staged_tree_bound_rejected_before_execution():
    raw=lint_request()
    raw['files']=[dict(path=str(index)+'/'+'d/'*20+'x.py',content='pass') for index in range(8)]
    with pytest.raises(ValueError, match='tree'): normalize_request(raw)
