"""Production policy tests use synthetic observations; no live task operations."""
import copy,hashlib,json
import pytest
import active_host_migration_windows as p
from test_active_migration_preflight import observations


def inputs(observations):
    o,s=observations
    xml='<Task>synthetic-only</Task>'
    o['task']['config_sha256']=hashlib.sha256(xml.encode()).hexdigest()
    pair={}
    for row in o['workers']:
        pair[row['kind']]={k:v for k,v in row.items() if k not in ('kind','command_valid')}
        command=('"'+row['image']+'" reconnect') if row['kind']=='wrapper' else ('"'+p.system_powershell()+'" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+o['context']['root']+'\\Prometheus-USB-Reconnect-Supervisor.ps1"')
        pair[row['kind']].update(command_line=command,file_identity=dict(volume_serial=1,file_index=row['pid']))
    return dict(schema=1,observation=o,task_xml=xml,normalized_task_sha256='e'*64,host='f'*64),s,pair


def test_plan_binds_fresh_observations_without_mutation(observations):
    e,s,pair=inputs(observations);before=copy.deepcopy((e,s,pair))
    plan=p.build_plan(e,s,pair,'a'*32)
    context,binding=p.validate_production_plan(plan)
    assert binding['root']==context['root'] and binding['normalized_task_sha256']=='e'*64
    assert (e,s,pair)==before and not p.PRODUCTION_ACTIVATION_AVAILABLE


@pytest.mark.parametrize('enabled,state',[(True,'Ready'),(False,'Disabled')])
def test_stopped_plan_has_no_process_authority(observations,enabled,state):
    e,s,pair=inputs(observations);e['observation']['task'].update(enabled=enabled,state=state);e['observation']['workers']=[]
    plan=p.build_plan(e,s,{},'a'*32)
    assert plan['wrapper_identity']==plan['child_identity']=='absent'
    p.validate_production_plan(plan)


@pytest.mark.parametrize('case',['sid','creation','image','extra','task-xml','pending','redirected','missing-pair','command-extra','child-image'])
def test_plan_refuses_changed_or_incomplete_evidence(observations,case):
    e,s,pair=inputs(observations)
    if case=='sid':pair['wrapper']['sid']='S-1-5-99'
    elif case=='creation':pair['wrapper']['creation_ticks']+=1
    elif case=='image':pair['wrapper']['image']='C:\\other.exe'
    elif case=='extra':pair['wrapper']['extra']=True
    elif case=='task-xml':e['task_xml']+='changed'
    elif case=='pending':e['observation']['migration_evidence']=['foreign']
    elif case=='redirected':e['observation']['context']['classification']='redirected'
    elif case=='missing-pair':pair.pop('supervisor')
    elif case=='command-extra':pair['supervisor']['command_line']+=' -extra'
    elif case=='child-image':
        pair['supervisor']['image']='C:\\other.exe';e['observation']['workers'][1]['image']='C:\\other.exe'
    with pytest.raises(ValueError):p.build_plan(e,s,pair,'a'*32)


@pytest.mark.parametrize('role',['ReconnectEnrollment','UsbLifecycle','Arrival','HostAgent','Eject-A','Eject-Z'])
def test_literal_production_name_policy_does_not_acquire(role):
    assert p.production_mutex_name(role)=='Local\\Prometheus'+role


@pytest.mark.parametrize('role',['Eject-AA','Eject-a','Global\\anything','other',''])
def test_name_policy_refuses_arbitrary_names(role):
    with pytest.raises(ValueError):p.production_mutex_name(role)


@pytest.mark.parametrize('case',['extra','missing','wrong-parent','wrong-session','noncanonical','profile-layout'])
def test_reloaded_plan_revalidates_complete_identity_schema(observations,case):
    e,s,pair=inputs(observations);plan=p.build_plan(e,s,pair,'a'*32)
    row=json.loads(plan['child_identity'])
    if case=='extra':row['extra']=True
    elif case=='missing':row.pop('file_identity')
    elif case=='wrong-parent':row['parent_pid']=999
    elif case=='wrong-session':row['session_id']=2
    elif case=='noncanonical':plan['child_identity']=json.dumps(row)
    elif case=='profile-layout':
        context=json.loads(plan['profile']);context['physical_local']='C:\\other';plan['profile']=p.canonical_identity(context)
    if case not in ('noncanonical','profile-layout'):plan['child_identity']=p.canonical_identity(row)
    with pytest.raises(ValueError):p.validate_production_plan(plan)
