"""Pure investigation of conflicting historical inputs; no resolution authority."""
import copy
import hashlib
import json

from .evidence_lifecycle import for_owner as lifecycle_for_owner, WORKER_RESULTS

MAX_VARIANTS = 8
MAX_WITNESSES = 4
ACTIONS = {'project':'review_project_check', 'worker':'review_worker_check',
           'application':'inspect_application_recovery'}
APPLICATION_TERMINAL = frozenset({'active','failed','rolled_back','recovery_required'})


def _digest(value):
    encoded=json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _incompatible(source, a, b):
    if source != 'application':return a['fact_sha256'] != b['fact_sha256']
    pair={a['status'],b['status']}
    return ('active' in pair and bool(pair & {'failed','rolled_back','recovery_required'})
            or pair == {'rolled_back','recovery_required'})


def _facts(source,event):
    return dict(status=event['status'],checks=copy.deepcopy(event.get('checks')),
                rollback=event.get('rollback'),evidence_sha256=event['evidence_sha256'])


def for_owner(owner, *, decisions, project_events, capability_events, application_events=None):
    """Bind the complete collected family, before display limits; never perform I/O.

    These inputs may come from sequential store reads. The digest identifies
    exactly those inputs, not an atomic database snapshot or current live state.
    """
    application_events=[] if application_events is None else application_events
    lifecycle=lifecycle_for_owner(owner,decisions=decisions,project_events=project_events,
        capability_events=capability_events,application_events=application_events)
    owned={(row['id'],row['fingerprint']):row for row in decisions if row['owner']==owner}
    families={}
    memberships={}
    for key,value in lifecycle.items():
        if value['state'] != 'conflicting':continue
        family=(value['source'],value['reference'])
        families.setdefault(family,{})[key]=[]
        memberships[key]=family
    for source,events in [('project',project_events),('worker',capability_events),('application',application_events)]:
        for actual_owner,ident,fingerprint,event in events:
            key=(ident,fingerprint)
            family=memberships.get(key)
            if actual_owner==owner and family is not None and family[0]==source:
                families[family][key].append(event)
    result={}
    for (source,reference),streams in families.items():
        variants={}
        snapshot=[]
        for key,events in sorted(streams.items()):
            snapshot.append({'decision':owned[key], 'events':events})
            if source=='project':selected=[e for e in events if e['phase']=='result']
            elif source=='worker':selected=[e for e in events if e['status'] in WORKER_RESULTS]
            else:selected=[e for e in events if e['status'] in APPLICATION_TERMINAL]
            for event in selected:
                facts=_facts(source,event);digest=_digest(facts)
                variant=variants.setdefault(digest,dict(fact_sha256=digest,**facts,witnesses=[],observation_count=0))
                variant['observation_count']+=1
                if len(variant['witnesses'])<MAX_WITNESSES:
                    variant['witnesses'].append(dict(id=key[0],fingerprint=key[1],event_id=event['event_id']))
        ordered=sorted(variants.values(),key=lambda v:(v['status'],v['fact_sha256']))
        # Prefer status-disagreeing witnesses. For application histories a
        # legitimate failed -> rolled_back sequence is never a conflict pair.
        representatives={}
        for variant in ordered:
            representatives.setdefault(variant['status'],variant)
        candidates=list(representatives.values())
        pair=next(((a,b) for a in candidates for b in candidates
                   if a['status']!=b['status'] and _incompatible(source,a,b)),None)
        if pair is None and source != 'application' and len(ordered)>1:
            pair=(ordered[0],ordered[1])
        if pair is None:raise ValueError('Conflicting lifecycle lacks incompatible native witnesses.')
        selected=list(pair)+[v for v in ordered if v not in pair]
        for value in selected:
            value['witnesses_truncated']=value['observation_count']>MAX_WITNESSES
        value=dict(schema_version=1,state='unresolved',source=source,reference=reference,
            snapshot_sha256=_digest(['owner-conflict-inputs-v1',owner,source,reference,snapshot]),
            snapshot_scope='collected_history_inputs',freshness='historical',read_only=True,
            requires_new_review=True,suggested_action=ACTIONS[source],
            variant_count=len(ordered),observation_count=sum(v['observation_count'] for v in ordered),
            variants=selected[:MAX_VARIANTS],variants_truncated=len(ordered)>MAX_VARIANTS)
        for key in streams:result[key]=value
    return result
