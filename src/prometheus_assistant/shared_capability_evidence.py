"""Read-only provenance for already-linked same-owner worker results.

Call with validated CapabilityOutcomes.read() rows. A digest is not authority;
this module cannot load, adopt, recover, sign, dispatch or persist any evidence.
"""
import hashlib
import json

from .decision_history import _identity
from .capability_outcomes import MAX_EVENTS, validate, _sequence

MAX_RELATED = 16
RESULTS = frozenset({'checks_passed','checks_failed','tool_failed'})


def for_owner(owner, events):
    """Map exact decision identities to bounded owner-local provenance labels."""
    _identity(owner,64)
    if type(events) is not list or len(events)>MAX_EVENTS:
        raise ValueError('Invalid bounded capability evidence collection.')
    prior={};groups={};bindings={}
    for item in events:
        if type(item) not in (tuple,list) or len(item)!=4:
            raise ValueError('Invalid capability evidence link.')
        actual_owner,approval_id,fingerprint,event=item
        if actual_owner!=owner:continue
        _identity(approval_id,32);_identity(fingerprint,64)
        validate(event)
        decision=(approval_id,fingerprint)
        history=prior.setdefault(decision,[])
        _sequence(history,event);history.append(event)
        if event['status'] not in RESULTS or not event['job_id']:continue
        # The native job/request binding, not merely an evidence hash, identifies
        # the execution. Contradictory results must remain an explicit gap.
        binding=(event['job_id'],event['request_digest'])
        facts=json.dumps({key:event[key] for key in ('status','checks','evidence_sha256')},
                         sort_keys=True,separators=(',',':'),allow_nan=False)
        if binding in bindings and bindings[binding]!=facts:
            raise ValueError('Shared native capability evidence is contradictory.')
        bindings[binding]=facts
        group=groups.setdefault((binding,facts),set());group.add(decision)
    result={}
    for (binding,facts),members in groups.items():
        if len(members)<2:continue
        ordered=sorted(members)
        reference=hashlib.sha256(json.dumps(['owner-worker-evidence-v1',owner,*binding,facts],
                                           separators=(',',':')).encode()).hexdigest()
        status=json.loads(facts)['status']
        for decision in ordered:
            related=[key for key in ordered[:MAX_RELATED+1] if key!=decision][:MAX_RELATED]
            result.setdefault(decision,[]).append(dict(reference=reference,kind='worker_result',status=status,
                linked_decisions=len(ordered),related_decisions=[dict(id=ident,fingerprint=fp) for ident,fp in related],
                truncated=len(ordered)-1>MAX_RELATED,read_only=True))
    return result
