"""Exact owner review before signing and dispatching one bounded syntax job.

Boundary callbacks are trusted application wiring, never request parameters.
dispatch must authenticate returned receipts; this orchestration does not do so.
No transport, key storage, UI or installation is implemented here.
"""
from contextlib import ExitStack
import hashlib
import math
import time

from .application_policy import require_dispatch, revision
from .remote_job_envelope import seal_request
from .remote_job_protocol import normalize_request, validate_receipt
from .capability_outcome_hooks import tracking, observe_worker, attach


class RemoteJobReview:
    def __init__(self, approvals, ledger, *, grant_owner, binding, target, signer, dispatch, admission,
                 clock=time.time, cancelled=lambda:False):
        self.approvals, self.ledger = approvals, ledger
        self.grant_owner, self.binding, self.target = grant_owner, binding, target
        self.signer, self.dispatch, self.clock = signer, dispatch, clock
        self.admission = admission
        self.cancelled = cancelled

    def _admission(self, req):
        report = self.admission(req)
        if (not isinstance(report,dict) or report.get('authenticated') is not True
                or report.get('decision') not in ('ready','defer','reject')
                or not isinstance(report.get('reasons'),list)
                or any(not isinstance(reason,str) for reason in report['reasons'])):
            raise ValueError('Authenticated resource admission is unavailable.')
        if not self._fresh(report):
            raise ValueError('Resource observations are missing or expired.')
        return report

    def _fresh(self, report):
        observed, expires, now = report.get('observed_at'), report.get('expires_at'), self.clock()
        return (all(type(value) in (int,float) and math.isfinite(value) for value in (observed,expires,now))
                and 0 < expires-observed <= 30 and observed <= now < expires)

    def _target(self, req):
        value = self.target()
        fields = {'account_id','device_id','device_name','worker_digest','online'}
        if isinstance(value,dict) and 'configuration_digest' in value:
            import re
            if not isinstance(value['configuration_digest'],str) or not re.fullmatch('[0-9a-f]{64}',value['configuration_digest']):
                raise ValueError('The reviewed worker configuration identity is invalid.')
            fields.add('configuration_digest')
        if not isinstance(value,dict) or set(value) != fields:
            raise ValueError('A complete trusted remote target is required.')
        if (value['online'] is not True
                or any(not isinstance(value[key],str) or not 1 <= len(value[key]) <= 256
                       for key in fields-{'online'})
                or value['device_id'] != req['device_id'] or value['worker_digest'] != req['worker_digest']):
            raise ValueError('The selected online target must match the exact device and worker.')
        return dict(value)

    def prepare(self, request, *, session):
        """Observe resources and create an exact review; never sign or claim work."""
        req = normalize_request(request)
        with self.grant_owner() as owner:
            require_dispatch('remote.action')
            target = self._target(req)
        resources = self._admission(req)
        with self.grant_owner() as current_owner:
            if current_owner!=owner or target!=self._target(req) or self.cancelled():
                raise ValueError('Owner, target or cancellation changed during resource inspection.')
            require_dispatch('remote.action')
            if not self._fresh(resources):
                raise ValueError('Worker resource observations expired; prepare a new review.')
            if resources['decision'] != 'ready':
                raise ValueError('Worker is not ready: '+'; '.join(resources['reasons']))
            review = dict(action='Run Python syntax checks on the selected computer',
                reason='Check the exact selected source without executing it.',
                benefit='Obtain supplemental Python syntax evidence from the other computer.',
                effects='Transfer the listed source text to the selected account and device. '
                        'Compile it in memory without executing source or writing project files. '
                        'This does not establish Windows or runtime acceptance.',
                destination=target, resource_admission=resources,
                operation=req['operation'], request_digest=req['request_digest'],
                files=[dict(path=row['path'],content=row['content'],
                    sha256=hashlib.sha256(row['content'].encode('utf-8')).hexdigest(),
                    utf8_bytes=len(row['content'].encode('utf-8'))) for row in req['files']],
                limits=dict(timeout_seconds=req['timeout_seconds'],max_files=8,
                    max_total_source_bytes=512000,child_memory_mib=256,child_cpu_seconds=5),
                duration='One claim only; uncertain execution is not automatically retried.')
            return self.approvals.review(owner=owner,binding=self.binding,session=session,
                kind='remote_syntax',payload=dict(request=req,target=target),review=review,
                policy_revision=revision())

    @staticmethod
    def _result(req, entry, approval_id, *, replayed=False):
        receipt = entry.get('receipt')
        return dict(state=entry['state'],receipt=receipt,replayed=replayed,
            evidence=validate_receipt(req,receipt) if receipt is not None else None,
            approval_resolved=approval_id,
            reasons=['The durable claim has no confirmed result; it will not be dispatched again.']
                    if entry['state']=='unconfirmed' else [])

    def decide(self, *, session, approval_id, fingerprint, decision):
        observation = {}
        with ExitStack() as stack:
            result = self._decide(session=session, approval_id=approval_id,
                fingerprint=fingerprint, decision=decision, stack=stack, observation=observation)
            if observation:
                attach(result, observe_worker(observation['outcome'], observation['request'], result))
            return result

    def _decide(self, *, session, approval_id, fingerprint, decision, stack, observation):
        """Consume exact owner approval, recheck target, then claim before signing.

        Signing/dispatch/persistence uncertainty leaves an unconfirmed claim.
        This method never resets the ledger or retries a signed dispatch.
        """
        with self.grant_owner() as owner:
            pending = self.approvals.pending(owner=owner,binding=self.binding)
            if not pending or pending['kind'] != 'remote_syntax':
                raise ValueError('No exact remote syntax review belongs to this owner session.')
            row = self.approvals.consume(owner=owner,binding=self.binding,session=session,
                approval_id=approval_id,fingerprint=fingerprint,policy_revision=revision(),decision=decision)
            req = row['payload']['request']
            if decision == 'reject':
                return self._result(req,dict(state='rejected',receipt=None),row['id'])
            outcome = stack.enter_context(tracking(self.approvals, owner, row, request=req))
            observation.update(outcome=outcome, request=req)
            require_dispatch('remote.action')
            target = self._target(req)
            if target != row['payload']['target']:
                raise ValueError('The selected account, device or worker changed; prepare a new review.')
            require_dispatch('remote.dispatch',scope=dict(account=target['account_id'],
                device=target['device_id'],tool='prometheus.syntax',request_digest=req['request_digest']))
            # Existing claims only recover their outcome. A changed resource
            # reading must never reset an uncertain job or cause a new dispatch.
            existing = self.ledger.lookup(req)
            if existing is not None:
                return self._result(req,existing,row['id'],replayed=True)
        # Resource queries carry no source. Release the owner lock so a normal
        # Stop or Lock request can take effect while this network read waits.
        try:
            resources = self._admission(req)
        except Exception:
            resources = dict(decision='defer',reasons=['Fresh authenticated worker resources are unavailable.'])
        with self.grant_owner() as current_owner:
            if current_owner!=owner or revision()!=row['policy_revision'] or self._target(req)!=target:
                raise ValueError('Owner, policy or reviewed target changed during resource inspection.')
            if self.cancelled():
                return self._result(req,dict(state='cancelled',receipt=None),row['id'])
            if resources['decision']=='ready' and not self._fresh(resources):
                resources = dict(decision='defer',reasons=['Worker resource observations expired before the claim.'])
            if resources['decision'] != 'ready':
                result = self._result(req,dict(state='deferred',receipt=None),row['id'])
                result.update(resource_admission=resources,reasons=resources['reasons'])
                return result
            claim = self.ledger.claim(req)
            if not claim['claimed']:
                return self._result(req,claim,row['id'],replayed=True)
            try:
                now = self.clock()
                if type(now) not in (int,float) or not math.isfinite(now):
                    raise ValueError('A trusted current clock is required.')
                issued = int(now)
                envelope = seal_request(req,self.signer,issued_at=issued,expires_at=issued+300)
                receipt = self.dispatch(envelope)
                checked = validate_receipt(req,receipt)
                if checked['status']=='incomplete' or receipt['state'] not in ('completed','failed'):
                    raise ValueError('No exact terminal remote receipt was returned.')
                finished = self.ledger.finish(req,receipt)
            except Exception:
                return self._result(req,dict(state='unconfirmed',receipt=None),row['id'])
            return self._result(req,finished,row['id'])
