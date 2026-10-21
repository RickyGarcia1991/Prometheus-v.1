"""Bounded output checks, not a factual-accuracy or general comprehension claim."""
import json
import math
import re

def _reject_constant(value):
    raise ValueError('Non-finite JSON value')

def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('JSON number exceeds the supported finite range')
    return number

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result

STRICT_JSON_DECODER = json.JSONDecoder(parse_constant=_reject_constant,
    parse_float=_finite_float, object_pairs_hook=_unique_object)

def strict_json(text):
    value = STRICT_JSON_DECODER.decode(text)
    # Escaped lone surrogates parse in Python but cannot be persisted as UTF-8.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return value

def requests_json(prompt):
    # Examples are data, not output instructions. Keep a boundary marker so a
    # real instruction after a quoted example can still be recognized.
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', '\x00', prompt)
    text = re.sub(r'(?m)^\s*>[^\n]*', '\x00', text)
    text = re.sub(r'`[^`\n]*`', '\x00', text)
    text = re.sub(r'"(?:\\.|[^"\\])*"|“[^”]*”|‘[^’]*’|(?<!\w)\'(?:\\.|[^\'\\])*\'(?!\w)', '\x00', text)
    return bool(re.search(
        r'(?:^|[.!?;\x00]\s*|\band\s+)(?:please\s+)?'
        r'(?:return|respond|answer|output)\s+(?:(?:with|in|as)\s+)?'
        r'(?:(?:only|valid|exactly|just|this|a|an)\s+){0,4}json\b',
        text, re.I | re.M))

def requested_json_keys(prompt):
    """Read a bounded, explicit top-level key list; do not infer a schema."""
    key = r'[A-Za-z_][A-Za-z0-9_]{0,63}'
    match = re.match(r'\s*(?:return|respond|answer|output)\s+(?:with\s+|in\s+)?'
                     r'(?:only\s+|valid\s+)?JSON\s+(?:object\s+)?with\s+keys?\s+'
                     r'(' + key + r'(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)' + key + r')*)'
                     r'(?=\s*(?:[.:](?:\s|$)|$)|\s+(?:containing|translating|describing|representing|identifying)\b)',
                     prompt, re.I)
    if not match:
        return ()
    keys = tuple(re.split(r'\s*,\s*(?:and\s+)?|\s+and\s+', match[1], flags=re.I))
    return keys if len(keys) <= 16 and len(set(keys)) == len(keys) else ()

def json_output_format(prompt):
    keys = requested_json_keys(prompt)
    if not keys:
        return True
    return {'type': 'object', 'properties': {key: {} for key in keys},
            'required': list(keys), 'additionalProperties': False}


def finalize_code_only(prompt, reply):
    """Remove one Markdown wrapper only when explicitly asked for source alone.

    This changes presentation, never code contents or correctness checks. Prose,
    multiple blocks, quoted instructions and ordinary explanations stay intact.
    """
    instructions = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', '\x00', prompt)
    instructions = re.sub(r'(?m)^\s*>[^\n]*', '\x00', instructions)
    instructions = re.sub(r'`[^`\n]*`', '\x00', instructions)
    instructions = re.sub(r'"(?:\\.|[^"\\])*"|“[^”]*”|‘[^’]*’|(?<!\w)\'(?:\\.|[^\'\\])*\'(?!\w)', '\x00', instructions)
    explicit = re.search(r'(?:^|[.!?;]\s*)(?:please\s+)?(?:write|return|output|provide)\s+'
        r'(?:only\s+(?:(?:a|the)\s+)?|(?:raw|plain)\s+)'
        r'(?:(?:python|javascript|typescript|java|c\+\+|c#|rust|go|sql|html|css|bash)\s+)?'
        r'(?:source|code|function)\b', instructions, re.I | re.M)
    no_markdown = re.search(r'\b(?:no|without)\s+(?:markdown|(?:code\s+)?fences)\b', instructions, re.I)
    if not explicit or not no_markdown or not isinstance(reply, str):
        return reply
    block = re.fullmatch(r'\s*```[A-Za-z0-9_+.#-]*[ \t]*\r?\n([\s\S]*?)\r?\n```\s*', reply)
    if block and block[1].strip() and '```' not in block[1]:
        return block[1] + '\n'
    return reply

def output_issues(prompt, reply):
    if not isinstance(reply, str) or not reply.strip():
        return ("no completed response",)
    if requests_json(prompt):
        try:
            value = strict_json(reply)
        except (ValueError, TypeError, RecursionError):
            return ("requested JSON is not valid JSON",)
        keys = requested_json_keys(prompt)
        if keys and (not isinstance(value, dict) or set(value) != set(keys)):
            return ('requested JSON must contain exactly these keys: ' + ', '.join(keys),)
        # JSON arrays/objects are structured data, not prose bullet lists.
        return ()
    issues=[]
    text=prompt.casefold()
    if re.search(r"\b(?:one[- ]sentence|a short|a brief) explanation\b",text):
        # Equations and repeated answers alone are not an explanation.
        words=re.findall(r"\b[a-zA-Z]{2,}\b",reply)
        if len(words)<3:
            issues.append("requested explanation is missing")
    count=re.search(r"\b(?:give me|list|provide)\s+(?:exactly\s+)?(two|three|four|five|[2-9])\s+(?:examples|ideas|tips|items|reasons|steps)\b",text)
    if count:
        requested={"two":2,"three":3,"four":4,"five":5}.get(count[1],int(count[1]) if count[1].isdigit() else 0)
        items=re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S",reply)
        if len(items)!=requested:
            issues.append(f"requested {requested} list items; found {len(items)} explicit list items")
    lines=[re.sub(r"\s+"," ",line).strip().casefold() for line in reply.splitlines() if line.strip()]
    if len(lines)>1 and len(set(lines))==1 and "repeat" not in text:
        issues.append("response repeats the same line without additional information")
    return tuple(issues)
