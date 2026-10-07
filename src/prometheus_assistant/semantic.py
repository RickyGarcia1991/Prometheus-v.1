"""Dependency-free local semantic expansion with deterministic fallback."""
from __future__ import annotations
import re

_GROUPS=(
 {"car","vehicle","automobile","truck"},
 {"robot","robotic","machine","android"},
 {"motor","actuator","servo","drive"},
 {"hydraulic","fluid","pressure"},
 {"electric","electrical","electronic"},
 {"memory","remember","recall","knowledge"},
 {"computer","system","machine","host"},
)
_WORD=re.compile(r"[a-z0-9]{3,}")

def semantic_terms(text):
    base=set(_WORD.findall(text.lower()))
    expanded=set(base)
    for group in _GROUPS:
        if base & group:
            expanded.update(group)
    return expanded

def semantic_overlap(query,text):
    wanted=semantic_terms(query)
    if not wanted:
        return 0.0
    found=semantic_terms(text)
    return len(wanted & found)/len(wanted)
