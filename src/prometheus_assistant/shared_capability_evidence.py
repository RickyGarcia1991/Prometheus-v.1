"""Read-only same-owner provenance for exact native project and worker results."""
from .decision_history import _identity
from .evidence_lifecycle import MAX_RELATED, _events, _native_groups


def for_owner(owner, events, *, project_events=None):
    """Return consistent result groups; contradictory families remain unshared.

    The separate lifecycle projection exposes every affected decision as
    conflicting. Malformed native sequences still fail closed. No raw outcome is
    removed, selected as a winner or changed by this projection.
    """
    _identity(owner, 64)
    projects = _events(owner, [] if project_events is None else project_events, project=True)
    capabilities = _events(owner, events, project=False)
    if projects.keys() & capabilities.keys():
        raise ValueError('A decision cannot mix native evidence namespaces.')
    groups, _ = _native_groups(owner, projects, capabilities)
    result = {}
    for binding, group in groups.items():
        members = sorted(group['results'])
        if group['conflicting'] or len(members) < 2:
            continue
        for decision in members:
            related = [dict(id=i, fingerprint=f) for i, f in members[:MAX_RELATED + 1] if (i, f) != decision][:MAX_RELATED]
            result.setdefault(decision, []).append(dict(
                reference=group['reference'], kind=binding[0] + '_result',
                status=group['results'][decision]['status'], linked_decisions=len(members),
                related_decisions=related, truncated=len(members) > MAX_RELATED + 1, read_only=True))
    return result
