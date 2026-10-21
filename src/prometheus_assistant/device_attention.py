"""Fixed-text device attention from a bounded cache; no provider or action access."""
import math

FIELDS = {'schema_version','phase','generation','freshness','observed_at','expires_at',
          'ttl_seconds','counts','selected_state'}
PHASES = {'disconnected','connecting','ready','working','error','unknown'}
SELECTIONS = {'online','offline','missing','unselected','unknown'}


def _stamp(value):
    return type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 253402300799


def project(value, now):
    """Return source metadata and allowlisted summaries, never raw provider data."""
    unavailable = dict(state='unavailable',freshness='unavailable',truncated=False,omitted=0)
    if type(value) is not dict or set(value) != FIELDS:
        return unavailable, []
    counts=value['counts']
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
        or type(value['generation']) is not int or not 0 <= value['generation'] <= 2**53-1
        or type(value['ttl_seconds']) is not int or value['ttl_seconds'] != 30
        or type(value['phase']) is not str or value['phase'] not in PHASES
        or type(value['freshness']) is not str or value['freshness'] not in {'current','stale','unavailable'}
        or type(value['selected_state']) is not str or value['selected_state'] not in SELECTIONS
        or type(counts) is not dict or set(counts) != {'online','offline','unknown','total'}
        or any(type(n) is not int or not 0 <= n <= 100 for n in counts.values())
        or counts['online']+counts['offline']+counts['unknown'] != counts['total']):
        return unavailable, []
    observed,expires=value['observed_at'],value['expires_at']
    if not ((observed is None and expires is None) or
            (_stamp(observed) and _stamp(expires) and expires-observed == 30)):
        return unavailable, []
    phase,freshness=value['phase'],value['freshness']
    if freshness=='current':
        if phase not in {'ready','working'} or observed is None or not _stamp(now) or now < observed:
            freshness='unavailable'
        elif now >= expires:
            freshness='stale'
    source=dict(state='available',freshness=freshness,truncated=False,omitted=0)
    if freshness=='current':
        source.update(observed_at=observed,expires_at=expires)
    items=[]
    def add(kind,label,severity,state,count=1):
        items.append(dict(kind=kind,label=label,severity=severity,state=state,count=count,freshness=freshness))
    if phase in {'disconnected','connecting','error','unknown'}:
        labels={'disconnected':'The remote account is disconnected; connect it in Connected computers.',
                'connecting':'The remote account connection is being established; device availability is unconfirmed.',
                'error':'The remote connection could not be verified; refresh or reconnect in Connected computers.',
                'unknown':'Remote connection state is unavailable; inspect Connected computers.'}
        add('connection',labels[phase],'warning' if phase in {'error','unknown'} else 'info',phase)
        return source,items
    if freshness!='current':
        add('connectivity','Cached device availability is stale or unavailable; refresh Connected computers before relying on it.',
            'warning',freshness)
        return source,items
    for state,label in [('online','Devices reported online by the most recent provider observation.'),
                        ('offline','Devices reported offline by the most recent provider observation.'),
                        ('unknown','Devices with unknown availability in the most recent provider observation.')]:
        if counts[state]:
            add('connectivity',label,'info' if state=='online' else 'warning',state,counts[state])
    if counts['total']==0:
        add('connectivity','The latest provider observation returned no paired computers.','info','empty')
    selected=value['selected_state']
    if (selected=='online' and counts['online']==0) or (selected=='offline' and counts['offline']==0):
        return unavailable, []
    labels={'online':'The selected computer was observed online; each action still verifies its exact destination.',
            'offline':'The selected computer was observed offline; work cannot assume it is reachable.',
            'missing':'The selected computer is absent from the latest provider observation.',
            'unselected':'No remote computer is selected.',
            'unknown':'The selected computer has no confirmed availability.'}
    add('selection',labels[selected],'info' if selected in {'online','unselected'} else 'warning','selected_'+selected)
    return source,items
