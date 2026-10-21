"""Native capability observations, independent of rendering and execution authority."""
from contextlib import contextmanager

from .capability_outcomes import CapabilityOutcome
from .remote_job_protocol import normalize_request, validate_receipt


@contextmanager
def tracking(approvals, owner, row, *, request=None):
    binding = {} if request is None else dict(job_id=request['job_id'],
                                              request_digest=request['request_digest'])
    outcome = CapabilityOutcome(approvals, owner, row, **binding)
    outcome.start()  # Intent must be durable before any new effect.
    try:
        yield outcome
    except BaseException:
        outcome.unknown()  # An exception after submission cannot establish no effect.
        raise


def attach(result, observation):
    if observation is not None:
        result['decision_outcome'] = observation
    return result


def gap_notice(observation):
    if observation is not None and observation.get('recorded') is False:
        return ' Decision history could not save this outcome; inspect the native result. Do not repeat the action to repair history.'
    return ''


def observe_worker(outcome, request, result):
    """Only call after the trusted worker transport/ledger authenticated receipts.

    This validates the exact request again; digest matching alone does not
    authenticate a remote worker. No transport, signing or execution occurs here.
    """
    try:
        return _observe_worker(outcome, request, result)
    except Exception:
        return dict(recorded=False, error='Capability history unavailable; native job result is retained.')


def _observe_worker(outcome, request, result):
    state = result.get('state')
    receipt = result.get('receipt')
    if receipt is not None:
        checked = validate_receipt(request, receipt)
        if checked['status'] not in {'passed', 'failed'} or receipt['state'] not in {'completed', 'failed'}:
            return outcome.observe('unconfirmed', evidence={'confirmed': False})
        passed = sum(row['passed'] for row in receipt['checks'])
        failed = len(receipt['checks']) - passed
        if checked.get('lint_status') in {'tool_failed', 'unavailable'} or (receipt['state']=='failed' and not failed):
            return outcome.observe('tool_failed', evidence={'confirmed': True, 'tool_failed': True})
        counts = dict(planned=len(request['files']), executed=len(receipt['checks']),
                      passed=passed, failed=failed, cancelled=0, timed_out=0)
        return outcome.observe('checks_passed' if checked['status']=='passed' else 'checks_failed',
            checks=counts, evidence={'confirmed': True, **counts})
    if state in {'waiting', 'deferred', 'cancelled', 'expired'}:
        return outcome.observe(state, evidence={state: True})
    return outcome.observe('unconfirmed', evidence={'confirmed': False})


def reconcile_worker(approvals, owner, request, result):
    """Append to existing exact links only. Historic unlinked jobs stay unlinked."""
    if approvals.decision_history is None:
        return None
    try:
        req = normalize_request(request)
        links = approvals.capability_outcomes().links_for_job(owner=owner,
            job_id=req['job_id'], request_digest=req['request_digest'])
        observations = []
        for link in links:
            row = dict(id=link['approval_id'], fingerprint=link['fingerprint'])
            outcome = CapabilityOutcome(approvals, owner, row,
                job_id=req['job_id'], request_digest=req['request_digest'])
            observations.append(observe_worker(outcome, req, result))
        if not observations:
            return None
        return dict(recorded=all(item and item['recorded'] for item in observations),
                    linked_decisions=len(observations),
                    error='' if all(item and item['recorded'] for item in observations) else
                        'Native capability outcome could not be saved. History remains unconfirmed.')
    except Exception:
        return dict(recorded=False, error='Capability history unavailable; native job result is retained.')


def observe_queued(approvals, owner, row):
    if row is None:
        return None
    result = row.get('result') or {'state': row['state'], 'receipt': None}
    return reconcile_worker(approvals, owner, row['request'], result)
