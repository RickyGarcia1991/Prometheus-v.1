from __future__ import annotations
from dataclasses import dataclass, asdict
import json, os
from pathlib import Path

@dataclass(frozen=True)
class KnowledgeResource:
    id: str
    title: str
    subjects: tuple[str, ...]
    kind: str
    access: str
    source: str
    relative_path: str | None = None
    license_note: str = ""

DEFAULT_RESOURCES = (
    KnowledgeResource("wikipedia-top","Wikipedia English Top",("general","history","science","math","language","geography"),"kiwix-zim","offline","https://library.kiwix.org","Knowledge/Kiwix/wikipedia_en_top_mini_2026-09.zim","Wikimedia content licenses apply"),
    KnowledgeResource("wikipedia-astronomy","Wikipedia English Astronomy",("astronomy","physics","space","science"),"kiwix-zim","offline","https://library.kiwix.org","Knowledge/Kiwix/wikipedia_en_astronomy_nopic_2026-08.zim","Wikimedia content licenses apply"),
    KnowledgeResource("openstax","OpenStax",("math","algebra","calculus","statistics","science","physics","chemistry","biology","astronomy","history","humanities"),"online-library","online","https://openstax.org/subjects",None,"Openly licensed textbooks; license varies by title"),
    KnowledgeResource("gutenberg","Project Gutenberg",("literature","history","philosophy","reference","books"),"online-library","online","https://www.gutenberg.org/",None,"Primarily works not restricted by U.S. copyright; check each title"),
    KnowledgeResource("wikisource","Wikisource English",("books","primary-sources","history","literature"),"kiwix-catalog","catalog","https://library.kiwix.org",None,"Wikimedia content licenses apply"),
    KnowledgeResource("wiktionary","Wiktionary",("dictionary","language","etymology","vocabulary"),"kiwix-catalog","catalog","https://library.kiwix.org",None,"Wikimedia content licenses apply"),
)

def knowledge_root(resource_root: str | None = None) -> Path | None:
    root = resource_root or os.environ.get("PROMETHEUS_RESOURCE_ROOT")
    return Path(root) if root else None

def inventory(resource_root: str | None = None) -> list[dict]:
    root = knowledge_root(resource_root)
    rows=[]
    for item in DEFAULT_RESOURCES:
        row=asdict(item); path=(root/item.relative_path) if root and item.relative_path else None
        row["installed"]=bool(path and path.is_file())
        row["path"]=str(path) if path else None
        row["size_bytes"]=path.stat().st_size if path and path.is_file() else None
        rows.append(row)
    return rows

def write_catalog(destination: Path, resource_root: str | None = None) -> Path:
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps({"schema":1,"resources":inventory(resource_root)},indent=2),encoding="utf-8")
    return destination

