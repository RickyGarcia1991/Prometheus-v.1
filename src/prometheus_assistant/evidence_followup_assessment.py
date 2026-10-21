"""Explicit read-only comparison of a saved follow-up with current inputs.

Matching inputs are a point-in-time observation, not execution authority,
runtime acceptance or conflict resolution. Source correspondence compares the
reviewed UTF-8 text, as the request does; original file BOM bytes were not bound
by that request. Current raw hashes only detect changes during this assessment.
The caller holds the current owner grant and supplies the currently open project.
"""
from pathlib import Path
import hashlib
import time

from .decision_history import _identity
from .evidence_resolution import digest, stamp
from .evidence_interpretation_lifecycle import completed_roots, initial_state, advance


def _snapshot(store, job_id):
    # The canonical receipt snapshot is shared by all bounded history readers.
    # Their normal validators remain authoritative; corruption must propagate.
    app=store.approvals
    decisions,conflicts=app._evidence_conflict_inputs(store.owner)
    links=store.read(decisions=decisions)
    link=next((r for r in links if r['owner']==store.owner
               and r['document']['payload']['request']['job_id']==job_id),None)
    jobs=store.public_jobs(decisions=decisions)
    public=next((r for r in jobs if r['job_id']==job_id),None)
    if link is None or public is None:
        raise ValueError('No owned saved follow-up has that identity.')
    if (public['approval_id']!=link['approval_id'] or public['fingerprint']!=link['fingerprint']
            or public['provenance']['request_digest']!=link['document']['payload']['request']['request_digest']):
        raise ValueError('Saved follow-up association changed during assessment.')
    frozen=link['document']['payload']['followup']
    resolutions=app.evidence_resolutions().read(decisions=decisions)
    records=app.interpretation_lifecycle().read(decisions=decisions,resolutions=resolutions)
    root=completed_roots(resolutions).get((store.owner,frozen['interpretation_id']))
    state=None
    if root and root[0]['fingerprint']==frozen['interpretation_fingerprint']:
        state=initial_state(root);selected=set()
        # Fold the complete validated history rather than the bounded UI view.
        for phase,value in records:
            if value['owner']!=store.owner:continue
            if phase=='proposal' and value['document']['payload']['interpretation_id']==frozen['interpretation_id']:
                selected.add(value['approval_id']);advance(state,phase,value)
            elif phase=='decision' and value['approval_id'] in selected:
                advance(state,phase,value)
        state={k:state[k] for k in ('head','state','payload','review_id','review_fingerprint')}
    effective=frozen['interpretation']
    evidence=conflicts.get((effective['target_id'],effective['target_fingerprint']))
    return dict(link=link,public=public,interpretation=state,evidence=evidence)


def _source(project, files, workspace_factory):
    try:workspace=workspace_factory(project)
    except (OSError,ValueError):return dict(available=False,matching=False,files=None)
    if str(workspace.root)!=project:
        raise ValueError('The currently open project differs from the reviewed project.')
    try:
        observed=[];matching=True
        with workspace.lock:
            for row in files:
                found=workspace.read_file(row['path'])
                # Keep source and paths internal; only fixed public reason codes
                # leave this module. The trusted workspace enforces read limits.
                observed.append(dict(path=found['path'],text_sha256=hashlib.sha256(found['content'].encode('utf-8')).hexdigest(),raw_sha256=found['sha256']))
                matching=matching and all(found[k]==row[k] for k in ('path','content'))
        return dict(available=True,matching=matching,files=observed)
    except (OSError,ValueError):return dict(available=False,matching=False,files=None)


def _dimension(status,reason):return dict(status=status,reason=reason)


def assess(store, job_id, workspace_factory, *, project):
    """Inspect one owned saved result; never refresh, claim, recover or dispatch.

Before/after observations detect changes across the separate stores and project
read. They are not a transaction freezing future writes. A normal caller's owner
grant and project scope must remain held for this entire explicit operation.
"""
    _identity(job_id,32);_identity(store.owner,64)
    if type(project) is not str or not project or len(project)>4096 or '\x00' in project or not Path(project).is_absolute():
        raise ValueError('The currently open resolved project is required.')
    observed_at=time.time();stamp(observed_at)
    before=_snapshot(store,job_id);payload=before['link']['document']['payload'];frozen=payload['followup']
    if project!=frozen['project']:
        raise ValueError('The currently open project differs from the reviewed project.')
    source_before=_source(project,payload['request']['files'],workspace_factory)
    source_after=_source(project,payload['request']['files'],workspace_factory)
    after=_snapshot(store,job_id)
    public=before['public'];outcome=public['result'];state=before['interpretation']
    evidence=outcome['evidence'] if outcome else None
    terminal=bool(public['state'] in {'completed','failed'} and outcome
                  and outcome['state']==public['state'] and outcome['receipt'] is not None
                  and evidence and evidence['status'] in {'passed','failed'})
    historical=dict(state=public['state'],result_state=outcome['state'] if outcome else None,
        check_status=evidence['status'] if terminal else 'unavailable',terminal=terminal)
    if state is None:interpretation=_dimension('unavailable','interpretation_unavailable')
    elif state['state']=='withdrawn':interpretation=_dimension('stale','interpretation_withdrawn')
    elif (state['head']!=frozen['state_sha256'] or state['payload']!=frozen['interpretation']
            or state['review_id']!=frozen['interpretation_review_id']
            or state['review_fingerprint']!=frozen['interpretation_review_fingerprint']):
        interpretation=_dimension('stale','interpretation_changed')
    else:interpretation=_dimension('matching_inputs','matching_inputs')
    if before['evidence'] is None:native=_dimension('unavailable','evidence_unavailable')
    elif before['evidence']!=frozen['evidence_snapshot']:native=_dimension('stale','evidence_changed')
    else:native=_dimension('matching_inputs','matching_inputs')
    if not source_before['available']:source=_dimension('unavailable','source_unavailable')
    elif not source_before['matching']:source=_dimension('stale','source_changed')
    else:source=_dimension('matching_inputs','matching_inputs')
    checks=dict(interpretation=interpretation,evidence=native,source=source)
    if before!=after or source_before!=source_after:
        checks={k:_dimension('unavailable','inputs_changed_during_assessment') for k in checks}
    statuses={v['status'] for v in checks.values()}
    applicability=('unavailable' if not terminal or 'unavailable' in statuses
                   else 'stale' if 'stale' in statuses else 'matching_inputs')
    return dict(schema_version=1,read_only=True,scope='point_in_time_inputs_only',observed_at=observed_at,
        job_id=job_id,approval_id=public['approval_id'],fingerprint=public['fingerprint'],
        request_digest=payload['request']['request_digest'],result_sha256=digest(outcome) if outcome else None,
        historical=historical,applicability=applicability,checks=checks,
        authorizes_execution=False,resolves_conflicts=False,verifies_runtime_correctness=False)
