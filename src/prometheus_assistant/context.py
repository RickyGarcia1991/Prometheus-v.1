"""Bounded, provenance-aware context assembly."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Evidence:
    source_type: str
    source_ref: str
    text: str
    priority: int = 50

def assemble_context(items, max_chars=6000):
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    clean=[]
    for item in items:
        if not item or not item.text.strip():
            continue
        clean.append(item)
    clean.sort(key=lambda x:x.priority, reverse=True)
    blocks=[]; used=0; sources=[]
    for item in clean:
        header=f"[SOURCE {item.source_type}: {item.source_ref}]\n"
        remaining=max_chars-used-len(header)
        if remaining <= 0:
            break
        body=item.text.strip()[:remaining]
        if not body:
            continue
        block=header+body
        blocks.append(block); used+=len(block)
        sources.append({"type":item.source_type,"ref":item.source_ref,"chars":len(body)})
    return {"context":"\n\n".join(blocks),"sources":sources,"chars":used}
