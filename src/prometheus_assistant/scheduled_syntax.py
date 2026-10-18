"""Select a destination before approval; retain bounded, once-only waiting work."""
import hashlib
import json
import math
import re
import time

from .application_policy import require_dispatch, revision
from .remote_job_protocol import normalize_request, validate_receipt, LINT_OPERATION, LINT_PROFILE, _lint_tool

WAIT_SECONDS = 3600
RECHECK_SECONDS = 30
PLACEMENT_STRATEGIES = {'available-headroom-v1', 'remote-lightweight-v1'}


def _identity(target):
    keys = {'account_id','device_id','device_name','worker_digest','configuration_digest','online'}
    if (not isinstance(target,dict) or set(target)!=keys or target['online'] is not True
            or any(not isinstance(target[k],str) or not 1<=len(target[k])<=256 for k in keys-{'online'})
            or any(not re.fullmatch('[0-9a-f]{64}',target[k]) for k in ('worker_digest','configuration_digest'))):
        raise ValueError('A complete installed destination identity is required.')
    return dict(target)


def _number(value):
    try: return type(value) in (int,float) and math.isfinite(value)
    except OverflowError: return False


def fresh(report, now):
    if not isinstance(report,dict): return False
    start,end = report.get('observed_at'),report.get('expires_at')
    return (report.get('authenticated') is True and all(_number(v) for v in (start,end,now))
        and 0<end-start<=30 and start<=now<end)


def assess(target, report, *, now, operation='python-syntax-check'):
    """Apply conservative estimate + reserves on top of destination admission."""
    facts = report.get('observations',{}) if isinstance(report,dict) else {}
    if not isinstance(facts,dict): facts={}
    reasons = list(report.get('reasons',[])) if isinstance(report,dict) and isinstance(report.get('reasons'),list) else []
    reasons = [r[:300] for r in reasons if isinstance(r,str) and r.strip()][:10]
    ram,disk,cpu = (facts.get(k) for k in ('available_ram_gib','free_disk_gib','cpu_percent'))
    reserve = 2 if target['account_id']=='local' else .5
    # Upper compiler allocation is 256 MiB; 1.5x initial uncertainty margin.
    cost = .75 if operation==LINT_OPERATION else .375
    headroom = ram-reserve-cost if _number(ram) and ram>=0 else None
    if not fresh(report,now): reasons.append('Current authenticated resource readings are unavailable.')
    if headroom is None or headroom<0: reasons.append('Available RAM is below the estimate and reserve.')
    if not _number(disk) or disk<.501: reasons.append('Free disk capacity is below the reserve.')
    if not _number(cpu) or not 0<=cpu<=70: reasons.append('CPU headroom is unavailable.')
    if facts.get('plugged_in') is not True: reasons.append('Confirmed external power is required.')
    decision = report.get('decision') if isinstance(report,dict) else None
    if decision!='ready' and not reasons: reasons.append('The destination is not currently ready.')
    return dict(decision='reject' if decision=='reject' else 'defer' if reasons else 'ready',
        reasons=reasons,headroom_gib=headroom,estimated_ram_gib=cost,confidence='conservative baseline',
        cpu_percent=cpu if _number(cpu) else None,
        authenticated=fresh(report,now),observed_at=report.get('observed_at') if isinstance(report,dict) else None,
        expires_at=report.get('expires_at') if isinstance(report,dict) else None,observations=facts)


def choose_destination(adapters, *, now, clock=None, strategy='available-headroom-v1',operation='python-syntax-check'):
    if not isinstance(strategy,str) or strategy not in PLACEMENT_STRATEGIES:
        raise ValueError('Choose a supported placement strategy.')
    if operation not in ('python-syntax-check',LINT_OPERATION):
        raise ValueError('Choose a supported verification operation.')
    if operation==LINT_OPERATION: strategy='remote-lightweight-v1'
    choices=[]; reports=[]; lint_tools={}
    for adapter in adapters:
        try: target=_identity(adapter.target())
        except (ValueError,OSError): continue
        if strategy=='remote-lightweight-v1' and target['account_id']=='local':
            continue
        if operation==LINT_OPERATION:
            try: tool=adapter.lint_tool()
            except (AttributeError,ValueError,OSError): continue
            if not _lint_tool(tool): continue
            lint_tools[target['device_id']]=dict(tool)
        try: report=adapter.admission()
        except Exception: report={}
        reports.append((target,report))
    observed_now=clock() if clock else now
    for target,report in reports:
        verdict=assess(target,report,now=observed_now,operation=operation)
        if verdict['decision']=='reject': continue
        choices.append((target,verdict))
    if not choices: raise ValueError('No eligible installed destination is available for review.')
    if len({target['device_id'] for target,_ in choices})!=len(choices):
        raise ValueError('Duplicate destination identities are not eligible.')
    choices.sort(key=lambda item:(item[1]['decision']!='ready',
        -(item[1]['headroom_gib'] if item[1]['headroom_gib'] is not None else -1e30),
        item[1]['cpu_percent'] if item[1]['cpu_percent'] is not None else 100,item[0]['device_id']))
    target,report=choices[0]
    reason=('Selected the ready destination with the most RAM headroom after reserves and the compiler estimate.'
            if report['decision']=='ready' else 'All eligible destinations need to wait; selected the best available headroom for bounded waiting.')
    if strategy=='remote-lightweight-v1':
        reason=('Reserve this computer for heavier work; use the enrolled remote worker for this lightweight check. '
                'If it is busy, wait for this exact destination without falling back to this computer.')
    placement=dict(strategy=strategy,reason=reason,selected=target['device_id'],
        candidates=[dict(device_id=t['device_id'],device_name=t['device_name'],
            **{k:r[k] for k in ('decision','reasons','headroom_gib','estimated_ram_gib','confidence')}) for t,r in choices])
    result=dict(target=target,resource_admission=report,placement=placement)
    if operation==LINT_OPERATION: result['lint_tool']=lint_tools[target['device_id']]
    return result


def public_job(row):
    """No source, signing material, local paths or session tokens in queue views."""
    return dict(job_id=row['request']['job_id'],state=row['state'],destination=row['target']['device_name'],
        device_id=row['target']['device_id'],approved_at=row['approved_at'],expires_at=row['expires_at'],
        next_attempt_at=row['next_attempt_at'],reasons=row['reasons'],result=row['result'],
        files=[f['path'] for f in row['request']['files']],placement=row['placement'],operation=row['request']['operation'])


class ScheduledSyntax:
    def __init__(self, approvals, queue, *, adapters, grant_owner, binding,
                 cancelled=lambda:False, clock=time.time):
        self.approvals,self.queue,self.adapters=approvals,queue,adapters
        self.grant_owner,self.binding,self.cancelled,self.clock=grant_owner,binding,cancelled,clock

    def _adapter(self,target):
        for adapter in self.adapters:
            try: current=_identity(adapter.target())
            except (ValueError,OSError): continue
            if current['device_id']==target['device_id']:
                if current!=target: raise ValueError('The approved destination identity changed; prepare a new review.')
                return adapter
        return None

    def prepare(self, files, job_id, *, session, strategy='available-headroom-v1',operation='python-syntax-check'):
        with self.grant_owner() as owner:
            if owner!=self.queue.owner: raise ValueError('Queue belongs to another owner.')
            require_dispatch('ui.scheduled_syntax_prepare')
            policy=revision()
        selected=choose_destination(self.adapters,now=self.clock(),clock=self.clock,strategy=strategy,operation=operation)
        with self.grant_owner() as current:
            if current!=owner or revision()!=policy or self.cancelled() or self._adapter(selected['target']) is None:
                raise ValueError('Owner, policy, destination or cancellation changed before review.')
            target=selected['target']
            lint=operation==LINT_OPERATION
            req=normalize_request(dict(schema_version=2 if lint else 1,job_id=job_id,operation=operation,
                device_id=target['device_id'],worker_digest=target['worker_digest'],timeout_seconds=10,files=files,
                **(dict(tool=selected['lint_tool'],profile=LINT_PROFILE) if lint else {})))
            payload=dict(request=req,target=target,placement=selected['placement'])
            if len(json.dumps(payload,ensure_ascii=False).encode())>58000:
                raise ValueError('Select a smaller source snapshot for bounded waiting.')
            review=dict(action='Approve automatic placement and bounded waiting for this syntax check',
                reason=selected['placement']['reason'],benefit='Use eligible headroom while keeping work responsive.',
                effects=('Retain this exact source snapshot locally for recovery. '+
                    ('Compile on this computer; no source transfer. ' if target['account_id']=='local' else
                     'Transfer the listed source only to this exact enrolled account and device when ready. ')+
                    'Compile without executing source or changing project files.'),
                destination=target,resource_admission=selected['resource_admission'],placement=selected['placement'],
                files=[dict(**f,sha256=hashlib.sha256(f['content'].encode()).hexdigest(),utf8_bytes=len(f['content'].encode())) for f in files],
                duration='One execution claim within one hour of approval. Waiting survives restart; unlock to resume. '
                         'No destination switching after approval. Uncertain jobs are never rerun.')
            if lint:
                review.update(action='Approve fixed-profile Ruff lint checks and bounded remote waiting',
                    operation=operation,tool=req['tool'],profile=req['profile'],
                    benefit='Reserve this computer for heavier work and obtain deterministic lint findings.',
                    effects='Retain the exact source locally for recovery and transfer it only to this reviewed remote worker. '
                        'Run the pinned Ruff tool with isolated configuration and no fixes. Source is not executed. '
                        'Lint findings do not establish runtime correctness or qualify a model reviewer.',
                    limits=dict(child_memory_mib=512,child_cpu_seconds=5,timeout_seconds=10,max_diagnostics=16))
            return self.approvals.review(owner=owner,binding=self.binding,session=session,kind='scheduled_syntax',
                payload=payload,review=review,policy_revision=policy)

    def decide(self, *, session, approval_id, fingerprint, decision):
        with self.grant_owner() as owner:
            if owner!=self.queue.owner: raise ValueError('Queue belongs to another owner.')
            require_dispatch('ui.approval_decision')
            pending=self.approvals.pending(owner=self.queue.owner,binding=self.binding)
            if not pending or pending['kind']!='scheduled_syntax': raise ValueError('No automatic syntax review is pending.')
            row=self.approvals.consume(owner=self.queue.owner,binding=self.binding,session=session,
                approval_id=approval_id,fingerprint=fingerprint,decision=decision,policy_revision=revision())
            if decision=='reject': return dict(state='rejected',approval_resolved=row['id'],job_id=row['payload']['request']['job_id'])
            if self.cancelled(): return dict(state='cancelled',approval_resolved=row['id'],job_id=row['payload']['request']['job_id'])
            adapter=self._adapter(row['payload']['target']) # An offline destination can wait.
            if adapter is not None: getattr(adapter,'authorize',lambda req:None)(row['payload']['request'])
            now=self.clock()
            record=dict(**row['payload'],policy_revision=row['policy_revision'],approval_id=row['id'],
                fingerprint=row['fingerprint'],approved_at=now,expires_at=now+WAIT_SECONDS)
            saved=self.queue.enqueue(record,now=now)
            return {**public_job(saved),'approval_resolved':row['id']}

    def _cancel(self, row, reason):
        now=self.clock()
        result=self.queue.cancel(row['request']['job_id'],now=now,reason=reason)
        return public_job(result)

    def run_one(self, job_id=None, *, recover=False):
        with self.grant_owner() as owner:
            if owner!=self.queue.owner: raise ValueError('Queue belongs to another owner.')
            require_dispatch('ui.scheduled_syntax_recover' if recover else 'ui.scheduled_syntax_tick')
            self.queue.expire(now=self.clock())
            rows=[self.queue.get(job_id)] if job_id else self.queue.list()
            if recover and job_id and rows[0] and rows[0]['state'] in {'completed','failed'}:
                return public_job(rows[0])
            rows=[r for r in rows if r and (r['state']=='unconfirmed' if recover else r['state']=='waiting' and r['next_attempt_at']<=self.clock())]
            if not rows: return None
            row=rows[0];job_id=row['request']['job_id']
            if self.cancelled(): return public_job(row) if recover else self._cancel(row,'Waiting work was stopped before execution.')
            if revision()!=row['policy_revision']:
                return public_job(row) if recover else self._cancel(row,'Application policy changed; prepare a new approval.')
            try: adapter=self._adapter(row['target'])
            except ValueError:
                return public_job(row) if recover else self._cancel(row,'Approved destination identity changed; prepare a new approval.')
            if adapter is None:
                if recover: return public_job(row)
                return public_job(self.queue.update_wait(job_id,['The approved destination is offline or unavailable.'],now=self.clock(),next_attempt_at=self.clock()+RECHECK_SECONDS))
            if recover:
                try: result=adapter.recover(row['request'])
                except (ValueError,OSError,RuntimeError): return public_job(row)
                if result and result.get('receipt'):
                    return public_job(self.queue.finish(job_id,result,now=self.clock()))
                return public_job(row)
        try: report=adapter.admission(row['request'])
        except Exception: report={}
        with self.grant_owner() as current:
            if current!=owner: raise ValueError('Authenticated owner changed.')
            if self.cancelled(): return self._cancel(row,'Waiting work was stopped before execution.')
            if revision()!=row['policy_revision']:
                return self._cancel(row,'Application policy changed; prepare a new approval.')
            try:
                if self._adapter(row['target']) is None: report={}
            except ValueError: return self._cancel(row,'Approved destination identity changed; prepare a new approval.')
            assessment=assess(row['target'],report,now=self.clock(),operation=row['request']['operation'])
            if assessment['decision']=='reject': return self._cancel(row,'The destination rejected this work; prepare a new approval.')
            if assessment['decision']!='ready':
                return public_job(self.queue.update_wait(job_id,assessment['reasons'],now=self.clock(),next_attempt_at=self.clock()+RECHECK_SECONDS))
            getattr(adapter,'authorize',lambda req:None)(row['request'])
            if self.cancelled(): return self._cancel(row,'Waiting work was stopped before execution.')
            if revision()!=row['policy_revision']:
                return self._cancel(row,'Application policy changed; prepare a new approval.')
            try: current_adapter=self._adapter(row['target'])
            except ValueError: return self._cancel(row,'Approved destination identity changed; prepare a new approval.')
            if current_adapter is None or not fresh(report,self.clock()):
                return public_job(self.queue.update_wait(job_id,['Destination or resource reading changed before claim.'],
                    now=self.clock(),next_attempt_at=self.clock()+RECHECK_SECONDS))
            claim=self.queue.claim(job_id,now=self.clock())
            if not claim['claimed']: return public_job(claim)
            try:
                # A busy database can block claim. Never dispatch based on a
                # reading or permission that expired while recording intent.
                if (self.cancelled() or revision()!=row['policy_revision'] or self._adapter(row['target']) is None
                        or not fresh(report,self.clock()) or self.clock()>=row['expires_at']):
                    raise ValueError('Authority or admission changed after the durable claim.')
                receipt=adapter.execute(row['request'])
                evidence=validate_receipt(row['request'],receipt)
                if evidence['status']=='incomplete' or receipt['state'] not in ('completed','failed'):
                    raise ValueError('No exact terminal result.')
                result=dict(state=receipt['state'],receipt=receipt,evidence=evidence)
            except Exception:
                result=dict(state='unconfirmed',receipt=None,evidence=None)
            return public_job(self.queue.finish(job_id,result,now=self.clock()))
