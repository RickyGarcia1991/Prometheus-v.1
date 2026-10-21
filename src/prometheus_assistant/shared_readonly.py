"""Narrow, model-free grounding shared by agent and interactive chat.

Matching a request is not authorization. Source reads always use the registered
tool and Core's existing authorization and execution checks.
"""
from dataclasses import dataclass
import json
import re


@dataclass(frozen=True)
class GroundedRead:
    core: object
    route: str


def source_definition_identifier(prompt):
    """Recognize a complete definition lookup, never a prefix of another task."""
    if len(prompt) > 500:
        return None
    identifier = r"(?P<tick>`?)(?P<identifier>[a-z_][a-z0-9_]{0,99})(?P=tick)"
    source = r"(?:the )?(?:(?:prometheus|project) )?source(?: code)?"
    patterns = (
        rf"search {source} for (?:the )?(?:definition|declaration) of {identifier}",
        rf"(?:find|show|locate) (?:me )?where {identifier} is (?:defined|declared|implemented) in {source}",
        rf"where is {identifier} (?:defined|declared|implemented) in {source}",
        rf"(?:find|show|locate) (?:the )?(?:definition|declaration) of {identifier} in {source}",
    )
    suffix = (r"(?:(?:\.\s+|\s+(?:and\s+)?)(?:report (?:its|the matching) file(?: and line)?|"
              r"show (?:the )?matching lines|show (?:its|the) file and line(?:s| numbers)?))?")
    text = ' '.join(prompt.split())
    for pattern in patterns:
        match = re.fullmatch(pattern + suffix + r"[?.]?", text, re.I)
        if match:
            return match['identifier']
    return None


def direct_source_request(registry, prompt):
    identifier = source_definition_identifier(prompt)
    if identifier is None:
        return None
    try:
        spec = registry.get('project', 'search')
    except ValueError:
        return None
    if spec.mutates_state or spec.external_network or spec.command_execution:
        return None
    return spec.request('Find the requested definition in the registered project.', {'query': identifier})


def hardware_status_question(prompt):
    """Complete local OS/RAM/thread questions only; no devices or extra actions."""
    if len(prompt) > 500:
        return False
    text = ' '.join(prompt.casefold().split())
    host = r"(?:this|my|your|the local) (?:computer|system|machine)"
    field = r"(?:operating system|os|ram|memory|cpu threads|processor threads|hardware resources)"
    separator = r"(?:,\s*(?:and\s+)?|\s+and\s+)"
    fields = field + rf"(?:{separator}{field}){{0,2}}"
    question = r"(?:(?:what|which) (?:operating system|os)|how much (?:ram|memory)|how many (?:cpu|processor) threads)"
    questions = question + rf"(?:{separator}{question}){{0,2}}"
    patterns = (
        rf"{questions} (?:does {host} have|is {host} using)",
        rf"(?:what|which) {fields} (?:does {host} have|is {host} using|are available on {host})",
        rf"(?:what is|what are) {host}'s {fields}",
        rf"(?:what is|what are|show|report|list) (?:me )?(?:the )?{fields} (?:on|of|in|for) {host}",
        rf"(?:show|report|list) (?:me )?{host}'s {fields}",
    )
    return any(re.fullmatch(pattern + r"[?.]?", text) for pattern in patterns)


def hardware_status_reply(evidence):
    complete = [item for item in evidence
                if item.worker == 'system' and item.tool == 'summary' and item.status == 'complete']
    if len(evidence) != 1 or len(complete) != 1:
        return 'The registered host summary did not complete; no measured status is available.'
    try:
        data = json.loads(complete[0].output)
        return f"Operating system: {data['system']}; RAM: {data['ram_gib']} GiB; CPU threads: {data['cpu_threads']}."
    except (TypeError, ValueError, KeyError):
        return 'Verified system evidence was returned in an unreadable format.'


def project_search_reply(evidence):
    """Describe retrieved text as matches, not proof that every hit defines it."""
    complete = [item for item in evidence
                if item.worker == 'project' and item.tool == 'search' and item.status == 'complete']
    if len(evidence) != 1 or len(complete) != 1:
        return 'The registered project lookup did not complete; no source result is available.'
    try:
        data = json.loads(complete[0].output)
        rows = data['results'][:5]
        if not rows:
            return 'No matching source lines were found in the bounded Prometheus project search.'
        rendered = '; '.join(f"{row['path']}:{row['line']} - {row['excerpt'].strip()}" for row in rows)
        return 'Verified project matches: ' + rendered
    except (TypeError, ValueError, KeyError, AttributeError):
        return 'Verified project-search evidence was returned in an unreadable format.'


def grounded_readonly(memory, prompt, *, registry=None, memory_items=None,
                      online_enabled=False, required_requests=(), trace=None):
    """Return a handled Core result or None; never invoke a model or approve tools."""
    if online_enabled or required_requests:
        return None
    from .core import retrieve_memory, run_core
    from .grounding import direct_memory_lookup
    items = retrieve_memory(memory, prompt) if memory_items is None else memory_items
    stored_reply = direct_memory_lookup(prompt, items)
    if stored_reply is not None:
        result = run_core(memory, prompt, responder=lambda plan, evidence: stored_reply, trace=trace)
        return GroundedRead(result, 'exact-stored-subject')
    host_question = hardware_status_question(prompt)
    # Avoid loading the built-in catalog at all for ordinary conversations.
    if not host_question and source_definition_identifier(prompt) is None:
        return None
    if registry is None:
        from .builtin_tools import build_builtin_registry
        registry = build_builtin_registry(memory)
    if host_question:
        try:
            spec = registry.get('system', 'summary')
        except ValueError:
            return None
        if spec.mutates_state or spec.external_network or spec.command_execution:
            return None
        request = spec.request('Read measured local operating system, RAM and CPU thread counts.', {})
        render, route = hardware_status_reply, 'registered-host-summary'
    else:
        request = direct_source_request(registry, prompt)
        render, route = project_search_reply, 'registered-definition-search'
    if request is None:
        return None
    result = run_core(memory, prompt, tool_requests=(request,), executors=registry.executors(),
                      responder=lambda plan, evidence: render(evidence), trace=trace)
    return GroundedRead(result, route)
