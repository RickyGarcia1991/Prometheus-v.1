"""Source-grounded legal intake and a bounded FRCP 6 calendar calculator.

This module calculates from an explicitly supplied period; it does not infer a
limitation period, select a court, submit a filing, or change its own rules.
"""
from datetime import date,datetime,timedelta,timezone
import hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
from .public_http import fetch_public_bytes

RULES_URL='https://www.uscourts.gov/sites/default/files/document/federal-rules-of-civil-procedure.pdf'
RULE_CONFIG=Path(__file__).with_name('legal_rules.json')
REQUIRED_FACTS=('country','region','court','issue','event_date','case_stage')

def legal_plan(**facts):
    for key,value in facts.items():
        if key not in REQUIRED_FACTS or not isinstance(value,str) or len(value)>800:
            raise ValueError('Invalid legal intake field.')
    missing=[k for k in REQUIRED_FACTS if not facts.get(k,'').strip()]
    from .source_catalog import source_catalog
    sources=[s for s in source_catalog() if set(s['subjects']) & {'law','courts','lawyers','judges','employment'}]
    return dict(status='research_plan',facts=facts,missing_facts=missing,
        deadline=None,filing_submitted=False,complete_legal_coverage=False,
        steps=[
          'Identify country, state/province, exact court or agency, parties, claim and requested remedy.',
          'Preserve a dated chronology, original documents, notices, contracts and proof of service.',
          'Check jurisdiction, venue, claim elements, standing, limitation/repose periods and any pre-suit notice or agency exhaustion.',
          'Read the current statute, national/state procedure, local court rules, judge-specific orders and case scheduling orders together.',
          'Obtain current official forms and filing instructions; check fees or fee waiver, signatures, redactions, attachments and electronic-filing eligibility.',
          'Calculate each period from its verified triggering event and governing rule; verify service method, holidays, closures, time zone and exceptions.',
          'Prepare a draft with fact/source citations and an unresolved-items list; check service requirements and preserve filing/service receipts.',
          'For a rejected filing or changed rule, compare the official notice and rule version, explain the difference, then revise the draft and calendar; do not silently extend an expired deadline.'
        ],sources=sources,
        boundaries='A research and drafting workflow. A qualified local lawyer or court self-help service can review case-specific uncertainties. Court submissions require a separate explicit instruction.')

def check_rules(*,online=False,fetch=None,archive_root=None):
    if not online:raise ValueError('Checking current court rules requires explicit online permission.')
    config=json.loads(RULE_CONFIG.read_text(encoding='utf-8'))
    body=(fetch or (lambda:fetch_public_bytes(RULES_URL,{'www.uscourts.gov'},max_bytes=10_000_000,accept='application/pdf')))()
    if not isinstance(body,bytes) or len(body)>10_000_000 or not body.startswith(b'%PDF-'):
        raise ValueError('The court rule download is not a bounded PDF.')
    digest=hashlib.sha256(body).hexdigest();same=digest==config['sha256']
    result=dict(source=RULES_URL,checked_utc=datetime.now(timezone.utc).isoformat(),sha256=digest,
        installed_sha256=config['sha256'],installed_edition=config['edition'],matches_installed_rules=same,
        action='Use the installed rule with case-specific checks.' if same else 'The official document changed. Reconcile the revision before using this calculator.',
        automatic_rule_rewrite=False)
    if archive_root is not None:
        from .public_history import save_document_version
        result['saved_version']=save_document_version(archive_root,RULES_URL,body,dict(retrieved_utc=result['checked_utc'],edition=config['edition'] if same else 'New revision requiring review'))
    return result

def _day(value):
    if not isinstance(value,str) or len(value)!=10:raise ValueError('Dates must be YYYY-MM-DD.')
    try:result=date.fromisoformat(value)
    except ValueError as error:raise ValueError('Dates must be valid YYYY-MM-DD dates.') from error
    if not 2022<=result.year<=2100:raise ValueError('Calendar supports 2022–2100 only.')
    return result

def _nth(year,month,weekday,number):
    first=date(year,month,1)
    return first+timedelta(days=(weekday-first.weekday())%7+7*(number-1))

def federal_holidays(year):
    # Statutory observation dates; exceptional proclamations are caller inputs.
    days=set()
    for y in (year-1,year,year+1):
        for month,day in ((1,1),(6,19),(7,4),(11,11),(12,25)):
            d=date(y,month,day)
            days.add(d-timedelta(days=1) if d.weekday()==5 else d+timedelta(days=1) if d.weekday()==6 else d)
        days.update((_nth(y,1,0,3),_nth(y,2,0,3),_nth(y,9,0,1),_nth(y,10,0,2),_nth(y,11,3,4)))
        last=date(y,5,31);days.add(last-timedelta(days=last.weekday()))
    return days

def deadline_preview(case,*,online=False,fetch=None):
    if not isinstance(case,dict):raise ValueError('Deadline input must be a JSON object.')
    allowed={'jurisdiction','court','trigger_date','days','direction','period_source','period_rule','service_rule','state_holidays','additional_federal_holidays','court_closures','court_time_zone','filing_method','verified_checks'}
    if set(case)-allowed:raise ValueError('Unknown deadline fields: '+', '.join(sorted(set(case)-allowed)))
    missing=[k for k in ('jurisdiction','court','trigger_date','days','period_source','period_rule','service_rule') if case.get(k) in (None,'')]
    if missing:return dict(status='needs_facts',missing_facts=missing,calculated_date=None,filing_submitted=False)
    if case['jurisdiction']!='US-federal-civil':raise ValueError('This calculator implements FRCP 6 only; use the legal research plan for other jurisdictions.')
    for key in ('court','period_source','period_rule','service_rule','court_time_zone','filing_method'):
        if key in case and (not isinstance(case[key],str) or len(case[key])>1000):raise ValueError('Invalid '+key)
    source=urlsplit(case['period_source'])
    if source.scheme!='https' or not source.hostname or source.username or source.password:raise ValueError('Provide the HTTPS source for the period you are calculating.')
    days=case['days']
    if type(days) is not int or not 1<=days<=366:raise ValueError('Supply an explicit period of 1–366 days. Hours, months and years are unsupported.')
    trigger=_day(case['trigger_date']);direction=case.get('direction','after')
    if direction not in ('before','after'):raise ValueError('Direction must be before or after.')
    service=case['service_rule']
    if service not in ('not-service-based','5(b)(2)(A)','5(b)(2)(B)','5(b)(2)(C)','5(b)(2)(D)','5(b)(2)(E)','5(b)(2)(F)'):
        raise ValueError('Specify the actual Rule 5 service provision or not-service-based; do not infer service from a delivery label.')
    extra=service in ('5(b)(2)(C)','5(b)(2)(D)','5(b)(2)(F)')
    if direction=='before' and service!='not-service-based':raise ValueError('Service-based periods are supported only after service.')
    supplied={}
    for name in ('state_holidays','additional_federal_holidays','court_closures'):
        values=case.get(name,[])
        if not isinstance(values,list) or len(values)>366:raise ValueError('Invalid holiday/closure list.')
        supplied[name]={_day(v) for v in values}
    checks=case.get('verified_checks',[])
    if not isinstance(checks,list) or any(not isinstance(v,str) or len(v)>100 for v in checks):raise ValueError('Invalid verification list.')
    rule_check=check_rules(online=True,fetch=fetch) if online else None
    if rule_check and not rule_check['matches_installed_rules']:
        return dict(status='rule_revision_requires_review',calculated_date=None,rule_check=rule_check,filing_submitted=False)
    sign=1 if direction=='after' else -1
    raw=trigger+timedelta(days=sign*days);trace=[]
    def adjust(d,stage):
        for _ in range(370):
            reasons=[]
            if d.weekday()>=5:reasons.append('weekend')
            if d in federal_holidays(d.year) or d in supplied['additional_federal_holidays']:reasons.append('federal legal holiday')
            if direction=='after' and d in supplied['state_holidays']:reasons.append('state legal holiday')
            if d in supplied['court_closures']:reasons.append('supplied court inaccessibility')
            if not reasons:return d
            trace.append(dict(date=d.isoformat(),stage=stage,reasons=reasons));d+=timedelta(days=sign)
        raise ValueError('Calendar adjustments exceeded their bound.')
    base=adjust(raw,'Rule 6(a)');result=adjust(base+timedelta(days=3),'Rule 6(d)') if extra else base
    required={'period_and_trigger','current_rules','local_rules_and_orders','state_and_proclaimed_holidays','court_accessibility','service_provision','filing_cutoff_and_time_zone'}
    pending=sorted(required-set(checks))
    if not online:pending.append('live_official_rule_version_check')
    return dict(status='provisional_calendar_calculation',calculated_date=result.isoformat(),raw_date=raw.isoformat(),
        base_expiry=base.isoformat(),service_days_added=3 if extra else 0,adjustments=trace,
        period_source=case['period_source'],period_rule=case['period_rule'],rule_source=RULES_URL,
        rule_sections=['6(a)(1)','6(a)(3)–(6)']+(['6(d)'] if extra else []),rule_check=rule_check,
        court=case['court'],court_time_zone=case.get('court_time_zone'),filing_method=case.get('filing_method'),
        pending_verifications=pending,checks_are_user_attestations=True,deadline_guaranteed=False,filing_submitted=False,
        assumptions=['Trigger day excluded; intermediate days counted.','Supplied period and Rule 5 provision apply to this matter.','No contrary statute, local rule, scheduling order, tolling provision or exception.','Non-electronic filing ends at the clerk closing time; electronic filing ordinarily ends at court-local midnight unless another time controls.'],
        unsupported=['Selecting limitation or repose periods','Hour/month/year periods','Other jurisdictions','Automatic extensions or actual court filings'])
