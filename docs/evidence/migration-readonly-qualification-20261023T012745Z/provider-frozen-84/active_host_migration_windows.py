"""Production migration policy under implementation; no activation entry.

Plan construction is pure. Native execution must revalidate these observations
under maintenance locks; a serialized observation never grants mutation authority.
The completed fixture/store implementations remain unchanged.
"""
import hashlib,json,re,ntpath,ctypes
import active_host_migration_core as core
import active_migration_preflight as preflight

TASK_NAME='Prometheus USB Reconnect Supervisor'
PRODUCTION_DPAPI_DOMAIN='prometheus-owner-active-host-migration-v1'
PRODUCTION_ACTIVATION_AVAILABLE=False


def canonical_identity(value):
    encoded=core.encode(value).decode()
    if len(encoded)>4096:raise ValueError('Core identity bound exceeded')
    return encoded


def process_identity(row, native):
    """Bind native capture evidence to the already checked collector row.

    The eventual provider retains the capture handle; this pure schema check is
    deliberately insufficient by itself to authorize termination.
    """
    validate_process_schema(native)
    for field in ('pid','parent_pid','creation_ticks','sid','session_id','image_sha256'):
        if type(native[field]) is not type(row[field]) or native[field]!=row[field]:raise ValueError('Native capture differs from fresh collector')
    if preflight.path(native['image'])!=preflight.path(row['image']):raise ValueError('Native image changed')
    return canonical_identity(native)


def validate_process_schema(native):
    preflight.exact(native,'pid parent_pid creation_ticks sid session_id image image_sha256 command_line file_identity')
    for name in ('pid','creation_ticks'):preflight.integer(native[name],1)
    for name in ('parent_pid','session_id'):preflight.integer(native[name])
    if not re.fullmatch(r'S-1-\d+(?:-\d+)+',preflight.string(native['sid'],256)):raise ValueError('Process SID')
    preflight.path(native['image']);preflight.hash_value(native['image_sha256'])
    preflight.string(native['command_line'],2048)
    preflight.exact(native['file_identity'],'volume_serial file_index')
    for value in native['file_identity'].values():preflight.integer(value)


def system_powershell():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=kernel.GetSystemDirectoryW;function.argtypes=[ctypes.c_wchar_p,ctypes.c_uint];function.restype=ctypes.c_uint
    buffer=ctypes.create_unicode_buffer(32768);length=function(buffer,len(buffer))
    if not length or length>=len(buffer):raise ValueError('Native system directory unavailable')
    return ntpath.join(buffer.value,'WindowsPowerShell','v1.0','powershell.exe')


def validate_pair_commands(root,wrapper,child):
    exe=ntpath.join(root,'Prometheus-QuietHost-v3.exe');shell=system_powershell()
    if wrapper['command_line'].casefold() not in {(exe+' reconnect').casefold(),('"'+exe+'" reconnect').casefold()}:raise ValueError('Exact wrapper command required')
    expected='"'+shell+'" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+ntpath.join(root,'Prometheus-USB-Reconnect-Supervisor.ps1')+'"'
    if preflight.path(child['image'])!=preflight.path(shell) or child['command_line'].casefold()!=expected.casefold():raise ValueError('Exact signed supervisor command/system image required')


def build_plan(envelope,sessions,native_pair,transaction):
    """Build exact core plan from validated initial R5 observations only."""
    preflight.exact(envelope,'schema observation task_xml normalized_task_sha256 host')
    if type(envelope['schema']) is not int or envelope['schema']!=1:raise ValueError('Native observation version')
    o=envelope['observation'];result=preflight.evaluate(o,sessions)
    if result['status']!='preflight_observations_valid':raise ValueError('Initial production preflight refused: '+repr(result['reasons']))
    preflight.hash_value(envelope['host']);preflight.hash_value(envelope['normalized_task_sha256'])
    preflight.string(envelope['task_xml'],131072)
    if hashlib.sha256(envelope['task_xml'].encode()).hexdigest()!=o['task']['config_sha256']:raise ValueError('Actual task XML hash changed')
    if type(transaction) is not str or not re.fullmatch('[a-f0-9]{32}',transaction):raise ValueError('Transaction')
    c=o['context'];task=o['task'];running=task['state']=='Running'
    rows={row['kind']:row for row in o['workers'] if row['kind'] in ('wrapper','supervisor')}
    if type(native_pair) is not dict or set(native_pair)!=(set(rows) if running else set()):raise ValueError('Exact captured pair required')
    binding=dict(name=TASK_NAME,path='\\',sid=c['sid'],session_id=c['session_id'],root=c['root'],enrollment_id=o['receipt']['id'],normalized_task_sha256=envelope['normalized_task_sha256'],auth_sha256=o['auth']['sha256'],enrollment_sha256=o['receipt']['sha256'],host=envelope['host'])
    payload=next(r for r in o['runtime'] if r['name']=='Prometheus-Host-Agent.ps1')
    plan=dict(transaction=transaction,host=envelope['host'],profile=canonical_identity(c),auth_sha256=o['auth']['sha256'],enrollment_sha256=o['receipt']['sha256'],task_config_sha256=task['config_sha256'],task_identity=canonical_identity(binding),original_enabled=task['enabled'],original_running=running,old_manifest=core.R5,new_manifest=core.R7,old_file_sha256=payload['r5_sha256'],new_file_sha256=payload['r7_sha256'],payload='Prometheus-Host-Agent.ps1',wrapper_identity=process_identity(rows['wrapper'],native_pair['wrapper']) if running else 'absent',child_identity=process_identity(rows['supervisor'],native_pair['supervisor']) if running else 'absent')
    core.validate(plan)
    validate_production_plan(plan)
    return plan


def validate_production_plan(plan):
    core.validate(plan)
    context=json.loads(plan['profile']);binding=json.loads(plan['task_identity'])
    preflight.exact(context,'sid session_id profile_root physical_local reported_local root classification')
    preflight.exact(binding,'name path sid session_id root enrollment_id normalized_task_sha256 auth_sha256 enrollment_sha256 host')
    if canonical_identity(context)!=plan['profile'] or canonical_identity(binding)!=plan['task_identity']:raise ValueError('Noncanonical production identities')
    if binding['name']!=TASK_NAME or binding['path']!='\\' or context['classification']!='physical-profile':raise ValueError('Production task/profile scope')
    preflight.integer(context['session_id'],1)
    if not re.fullmatch(r'S-1-\d+(?:-\d+)+',preflight.string(context['sid'],256)):raise ValueError('Profile SID')
    profile,physical,reported,root=[preflight.path(context[name]) for name in ('profile_root','physical_local','reported_local','root')]
    if physical!=ntpath.join(profile,'appdata','local') or reported!=physical or root!=ntpath.join(physical,'prometheus'):raise ValueError('Physical profile layout')
    for name in ('sid','session_id','root'):
        if binding[name]!=context[name]:raise ValueError('Task/profile binding')
    for name in ('host','auth_sha256','enrollment_sha256'):
        if binding[name]!=plan[name]:raise ValueError('Plan authority binding')
    preflight.hash_value(binding['normalized_task_sha256'])
    if not re.fullmatch('[a-f0-9]{32}',binding['enrollment_id']):raise ValueError('Enrollment identity')
    if not plan['original_running'] and (plan['wrapper_identity']!='absent' or plan['child_identity']!='absent'):raise ValueError('Stopped plan process identity')
    if plan['original_running']:
        pair=[json.loads(plan[name]) for name in ('wrapper_identity','child_identity')]
        for name,row in zip(('wrapper_identity','child_identity'),pair):
            validate_process_schema(row)
            if canonical_identity(row)!=plan[name]:raise ValueError('Noncanonical process identity')
            if row['sid']!=context['sid'] or row['session_id']!=context['session_id']:raise ValueError('Process owner/session scope')
        if pair[0]['pid']==pair[1]['pid'] or pair[1]['parent_pid']!=pair[0]['pid'] or pair[1]['creation_ticks']<pair[0]['creation_ticks']:raise ValueError('Immediate child relation')
        if preflight.path(pair[0]['image'])!=preflight.path(context['root']+'\\Prometheus-QuietHost-v3.exe'):raise ValueError('Wrapper scope')
        validate_pair_commands(context['root'],pair[0],pair[1])
    return context,binding


def production_mutex_name(role):
    """Literal policy only; this function never opens/acquires a mutex."""
    if role in ('ReconnectEnrollment','UsbLifecycle','Arrival','HostAgent') or re.fullmatch('Eject-[A-Z]',role):
        return 'Local\\Prometheus'+role
    raise ValueError('Unknown production mutex role')
