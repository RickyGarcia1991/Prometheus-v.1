"""Bounded lexical relevance and exact source windows; no generated evidence."""
from collections import Counter
import math
import re

STOP = frozenset(('a an the how what where when why can could do does did i you we is are was were '
                 'to of for in on with please me my about use using explain show find and or from '
                 'that this these those it its as be been being have has had not if then than '
                 'will would should need want help give tell get also just into through which '
                 'only yet now our your their they them there here so at by any all more most '
                 'some such still before after next must may might').split())


def terms(query):
    # Keep the whole bounded question, including requirements at its end.
    words = re.findall(r'[^\W_]+', query.casefold())
    return list(dict.fromkeys(w for w in words if w not in STOP and (len(w)>1 or len(words)==1)))[:128]


def focused_terms(query):
    """Discount conversational scaffolding, without rewriting short subjects."""
    original = terms(query)
    if len(original) <= 5 or re.search(r'["\u201c\u201d]', query):
        return original
    # These are question/request words, not domain-specific synonyms. Do not
    # globally discard "step", "plan", "current" or other meaningful subjects.
    focused = re.sub(r'\bstep[\s-]+by[\s-]+step\b', ' ', query, flags=re.I)
    # Remove only a noun occurrence naming a source collection. The same word
    # later in the question may be the subject (voltage reference, software
    # library), so filter the bounded phrase before tokenizing, not a word set.
    focused = re.sub(
        r'(\b(?:use|using|consult|search|from)\s+(?:my|your|our)\s+'
        r'(?:[\w-]+\s+){0,5}?)(?:library|references)\b'
        r'(?=\s*(?:[,;.!?]|\b(?:to|for|help|please)\b|'
        r'and\s+(?:explain|show|give|tell|help|describe|compare|summarize)\b))',
        r'\1', focused, flags=re.I)
    filler = {'new', 'know', 'whether', 'propose', 'imported', 'provided'}
    selected = [term for term in terms(focused) if term not in filler]
    return selected if len(selected) >= 2 else original


def plural_variants(term, query):
    """One conservative English number variant; never stem codes or roots."""
    variants = [term]
    if not re.fullmatch(r'[a-z]{4,40}', term) or term in {'series', 'species', 'news'}:
        return variants
    originals = re.findall(r'[^\W_]+', query)
    if any(word.casefold() == term and sum(char.isupper() for char in word)>1 for word in originals):
        return variants
    if term.endswith(('us', 'is')):
        return variants
    if term.endswith('ies') and len(term)>4:
        alternate = term[:-3]+'y'
    elif term.endswith(('ches', 'shes', 'xes', 'zes', 'sses')):
        alternate = term[:-2]
    elif term.endswith(('ss', 'ch', 'sh', 'x', 'z')):
        alternate = term+'es'
    elif term.endswith('s'):
        alternate = term[:-1]
    elif term.endswith('y') and term[-2] not in 'aeiou':
        alternate = term[:-1]+'ies'
    else:
        alternate = term+'s'
    if len(alternate)>=3 and alternate != term:
        variants.append(alternate)
    return variants


def window(text, query, maximum):
    """Return a verbatim contiguous, preferably line-aligned relevant window."""
    if len(text) <= maximum:
        return text, 0, len(text)
    if maximum <= 0:
        return '', 0, 0
    wanted = set(terms(query))
    counts = Counter(re.findall(r'[^\W_]+', text.casefold()))
    weights = {w: 1/math.sqrt(max(1, counts[w])) for w in wanted}
    boundaries = [0] + [m.end() for m in re.finditer(r'\n|(?<=[.!?])(?<!\d\.)\s+', text)]
    anchors = {0}
    for match in re.finditer(r'[^\W_]+', text):
        if match.group().casefold() in wanted:
            anchors.update((max(0, match.start()-maximum//3), max(0, match.end()-maximum)))
    candidates = []
    for anchor in sorted(anchors):
        # Preserve the nearest full line/sentence where it fits.
        start = max((b for b in boundaries if b <= anchor), default=0)
        end = min(len(text), start+maximum)
        endings = [b for b in boundaries if start+maximum//2 <= b <= end]
        if end < len(text) and endings:
            end = max(endings)
        piece = text[start:end]
        found = set(re.findall(r'[^\W_]+', piece.casefold())) & wanted
        score = sum(weights[w] for w in found)
        candidates.append((score, end-start, -start, start, end))
    _, _, _, start, end = max(candidates)
    return text[start:end], start, end


def exact_designators(query):
    """Require literal part designators only for exact-value/pinout requests."""
    if not re.search(r'\bexact\b', query, re.I):
        return []
    return list(dict.fromkeys(re.findall(r'\b(?:R|C|L|D|Q|U|J|F)\d{1,6}\b', query, re.I)))
