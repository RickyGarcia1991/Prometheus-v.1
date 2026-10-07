"""Deterministic local evidence ranking."""
from __future__ import annotations
import re
from .context import Evidence

_WORD=re.compile(r"[a-z0-9]{3,}")
SOURCE_WEIGHT={"memory":30,"wikipedia":22,"wiktionary":16,"wikisource":12,"online":20}

def tokens(text):
    return set(_WORD.findall(text.lower()))

def rank_evidence(query, items, limit=6):
    if limit < 1:
        raise ValueError("limit must be positive")
    wanted=tokens(query)
    ranked=[]
    for index,item in enumerate(items):
        if not item or not item.text.strip():
            continue
        hay=tokens(item.source_ref+" "+item.text)
        overlap=len(wanted & hay)
        phrase=1 if query.strip().lower() in item.text.lower() else 0
        if wanted and overlap == 0:
            continue
        score=item.priority+SOURCE_WEIGHT.get(item.source_type,0)+(overlap*12)+(phrase*16)
        ranked.append((score,-index,item))
    ranked.sort(key=lambda row:(row[0],row[1]),reverse=True)
    return [item for _,_,item in ranked[:limit]]
