"""Server-owned advisory evidence through exact project preparation and approval.

The UI's single task executor owns model work. Advice is generated only during
preparation; approval and history recovery reuse the originally reviewed report.
This adapter never applies edits, executes checks or supplies authorization.
"""
from copy import deepcopy

from .advisory_review import prepare_review, validate_saved_advice
from .project_task_evidence import prepare_evidence


def proposed_files(engine, candidate):
    """Use the editor's actual BOM/newline-preserving bytes, without writing."""
    with engine.workspace.lock:
        prepare_evidence(engine, candidate)
        members = [candidate] if candidate['version'] == 1 else candidate['candidates']
        return {member['path']: engine.workspace._proposal(member['path'], member['content'],
                    member['before_sha256'])[2] for member in members}


def retain(candidate, files, advice):
    """Validate trusted server-retained advice before any write; never model input.

The receipt detects corruption, not authenticity. Only the server's runtime
adapter may originate this record; HTTP requests cannot supply these fields.
"""
    if advice is None:
        return None  # Backwards-compatible proposals without optional advice.
    advice = validate_saved_advice(advice)
    if advice['status'] != 'qualified_advisory':
        return advice
    context = advice['report']['context']
    expected = prepare_review(candidate, files, author=context.get('author'),
        reviewer=context.get('reviewer'), prompt_version=context.get('prompt_version'),
        prompt_sha256=context.get('prompt_sha256'))
    if context != expected:
        raise ValueError('Retained advisory review belongs to a different candidate.')
    return advice


def prepare(app, engine, candidate):
    from .advisory_review_runtime import run_review
    files = proposed_files(engine, candidate)
    advice = run_review(app, candidate, files, author=getattr(engine, 'author_identity', None))
    # A file edited externally while inference ran needs a fresh proposal.
    if proposed_files(engine, candidate) != files:
        raise ValueError('Project changed during advisory review.')
    return retain(candidate, files, advice)


def findings(advice):
    report = (advice or {}).get('report')
    return deepcopy(report['findings']) if report else []


def needs_exact_review(advice):
    return any(row['risk'] in {'high', 'critical'} for row in findings(advice))
