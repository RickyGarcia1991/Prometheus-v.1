"""Construct JSON only from a complete, explicit declaration of supplied data.

This is a bounded formatter, not inference, memory lookup or tool planning.
Ambiguous references, missing values and additional instructions do not match.
"""
import json
import re
from decimal import Decimal
from .response_checks import STRICT_JSON_DECODER, strict_json

_KEY = r'[A-Za-z_][A-Za-z0-9_]{0,63}'
_HEAD = re.compile(r'\s*(?:return|output)\s+(?:only\s+|valid\s+)?JSON\s+with\s+keys?\s+'
                   r'(.{1,1100}?)\.\s*Set\s+', re.I)
_ASSIGN = re.compile(r'(' + _KEY + r')\s+to\s+', re.I)
_NEXT = re.compile(r'(?:\s+and\s+|\s*,\s*(?:and\s+)?|\s*;\s*)(?:set\s+)?', re.I)
_NUMBER = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
_SORT = re.compile(r'\s*(?:return|output)\s+(?:only\s+|valid\s+)?JSON\s+with\s+key\s+'
                   r'(' + _KEY + r')\s+containing\s+(?:the\s+)?numbers\s+'
                   r'(' + _NUMBER + r'(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)' + _NUMBER + r')*)'
                   r'\s+sorted\s+(?:in\s+)?(ascending|descending)\s+order\s*\.?\s*', re.I)


_AMBIGUOUS = {'it', 'this', 'that', 'these', 'those', 'same', 'above', 'below',
              'previous', 'current', 'nan', 'infinity', 'inf', 'undefined'}


def explicit_json_reply(prompt):
    if not isinstance(prompt, str) or len(prompt) > 4000:
        return None
    sort = _SORT.fullmatch(prompt)
    if sort:
        # Exact supplied numbers only. Preserve numeric tokens and duplicates;
        # Decimal comparison avoids rounding close decimal values before sorting.
        tokens = re.split(r'\s*,\s*(?:and\s+)?|\s+and\s+', sort[2], flags=re.I)
        if len(tokens) > 128:
            return None
        try:
            strict_json('[' + ','.join(tokens) + ']')
            ordered = sorted(tokens, key=Decimal, reverse=sort[3].casefold() == 'descending')
            return '{' + json.dumps(sort[1]) + ': [' + ', '.join(ordered) + ']}'
        except (ValueError, ArithmeticError, RecursionError):
            return None
    head = _HEAD.match(prompt)
    if not head:
        return None
    keys = re.split(r',\s*(?:and\s+)?|\s+and\s+', head[1].strip(), flags=re.I)
    if not 1 <= len(keys) <= 16 or len(set(keys)) != len(keys):
        return None
    if any(not re.fullmatch(_KEY, key) for key in keys):
        return None
    remaining = prompt[head.end():]
    values = {}
    for _ in keys:
        assignment = _ASSIGN.match(remaining)
        if not assignment or assignment[1] not in keys or assignment[1] in values:
            return None
        name = assignment[1]
        tail = remaining[assignment.end():]
        literal = re.match(r'(?:the\s+)?literal\s+string\s+', tail, re.I)
        if literal:
            tail = tail[literal.end():]
        try:
            value, end = STRICT_JSON_DECODER.raw_decode(tail)
            encoded = tail[:end]  # Preserve supplied numeric tokens exactly.
            if literal and not isinstance(value, str):
                encoded = json.dumps(encoded, ensure_ascii=False)
        except (ValueError, RecursionError):
            # Only a single explicit token, never an inferred multiword value.
            token = re.match(r'[^\W\d][\w-]*|\[[0-9]+\]', tail, re.UNICODE)
            if not token or (not literal and token[0].casefold() in _AMBIGUOUS):
                return None
            value, end = token[0], token.end()
            # Invalid structured JSON must not degrade into a string token.
            if not literal and tail[:1] in '[{"':
                return None
            encoded = json.dumps(value, ensure_ascii=False)
        values[name] = encoded
        remaining = tail[end:]
        if len(values) == len(keys):
            if not re.fullmatch(r'\s*\.?\s*', remaining):
                return None
            try:
                reply = '{' + ', '.join(json.dumps(key) + ': ' + values[key] for key in keys) + '}'
                strict_json(reply)
                return reply
            except (ValueError, RecursionError):
                return None
        delimiter = _NEXT.match(remaining)
        if not delimiter:
            return None
        remaining = remaining[delimiter.end():]
    return None
