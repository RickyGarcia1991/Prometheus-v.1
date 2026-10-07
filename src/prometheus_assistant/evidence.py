"""Unified evidence collection for memory and offline knowledge."""
from __future__ import annotations
from .context import Evidence
from .hardware import resource_root
from .kiwix import KiwixError, read_article, search_archive
from .retrieval import rank_evidence
from .semantic import semantic_overlap

ARCHIVES=(
    ("wikipedia-en-all-nopic","wikipedia",60),
    ("wiktionary-en-all-nopic","wiktionary",45),
    ("wikisource-en-all-nopic","wikisource",35),
)

def memory_evidence(memory, query, limit=6):
    rows=memory.recall(query,limit=limit)
    return [Evidence("memory",f"{r['kind']}:{r['subject']}",r["value"],80) for r in rows]

def offline_evidence(query, *, root=None, per_archive=1, max_chars=2200):
    root=root or resource_root()
    if not root:
        return []
    found=[]
    for archive_id,source_type,priority in ARCHIVES:
        try:
            titles=search_archive(root,archive_id,query,max(per_archive*5,5))
            titles.sort(key=lambda title: semantic_overlap(query,title),reverse=True)
            for title in titles[:per_archive]:
                text=read_article(root,archive_id,title,max_chars)
                found.append(Evidence(source_type,title,text,priority))
        except KiwixError:
            continue
    return found

def collect_evidence(query, *, memory=None, use_memory=True, use_offline=False,
                     root=None, limit=8):
    items=[]
    if use_memory and memory is not None:
        items.extend(memory_evidence(memory,query))
    if use_offline:
        items.extend(offline_evidence(query,root=root))
    return rank_evidence(query,items,limit=limit)
