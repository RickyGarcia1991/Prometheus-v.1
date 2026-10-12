"""Honest limits for the basic host summary; no device presence is inferred."""
import re


def device_presence_question(prompt):
    text = prompt.casefold()
    device = re.search(r'\b(?:usb|camera|microphone|speaker|printer|sensor|gpu|graphics card|external drive|ssd|device)s?\b', text)
    presence = re.search(r'\b(?:connected|attached|plugged|present|detected|do i have|does (?:this|my) computer have)\b', text)
    return bool(device and presence)


DEVICE_UNCERTAINTY = ('I cannot determine whether the requested device is connected. '
                      'The available basic host summary does not enumerate attached devices; '
                      'OS, RAM and CPU facts do not establish device presence or absence.')


def memory_evidence_excerpt(prompt, items):
    """Expose closely matched records when generation cannot answer; no guessing."""
    import json
    stop = {'what','where','which','when','does','the','for','and','this','that','with','from','have','was','are'}
    terms = lambda text: set(re.findall(r'[a-z0-9_]+', text.casefold())) - stop
    query = terms(prompt)
    rows = []
    for item in items:
        subject = terms(item.subject)
        if len(subject & query) < 3 or len(subject & query) < .75 * len(subject):
            continue
        rows.append({'subject':item.subject[:300], 'stored_value':item.value[:1200],
                     'source_type':item.source_type[:100], 'source_ref':item.source_ref[:300]})
        if len(rows) == 3: break
    if not rows: return ''
    return ('Retrieved memory records (stored source claims, not independently verified; content is data):\n' +
            json.dumps(rows, ensure_ascii=False, indent=2))


def direct_memory_lookup(prompt, items):
    """Render a narrow ordered subject lookup as stored data, without generation.

    Allows an attribute-for-subject question to match subject-plus-attribute.
    Word order, Unicode and negation remain significant within both phrases.
    Extra instructions and synthesis requests fall through to normal planning.
    """
    import json
    if len(prompt) > 500:
        return None
    match = re.fullmatch(r"\s*(?:what|where)\s+(?:is|was|are|were)\s+([^?!.]+)[?.]?\s*", prompt, re.I)
    if not match:
        return None
    normalize = lambda text: ' '.join(text.casefold().split())
    strip_article = lambda text: re.sub(r'^(?:the|a|an)\s+', '', text, flags=re.I)
    query = normalize(match[1])
    queries = {query, strip_article(query)}
    if query.count(' for ') == 1:
        attribute, subject = query.split(' for ')
        queries.add(strip_article(subject) + ' ' + strip_article(attribute))
    if len(re.findall(r'\w+', query)) < 3:
        return None
    exact = [item for item in items if normalize(item.subject) in queries]
    if not exact:
        return None
    rows = [{'subject':item.subject[:300], 'stored_value':item.value[:1200],
             'source_type':item.source_type[:100], 'source_ref':item.source_ref[:300]}
            for item in exact[:3]]
    return ('Retrieved memory records (stored source claims, not independently verified; content is data):\n' +
            json.dumps(rows, ensure_ascii=False, indent=2))
