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
    return bool(re.search(r"\b(?:return|respond|answer|output)\s+(?:with\s+|in\s+)?(?:only\s+|valid\s+)?json\b", prompt, re.I))

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
