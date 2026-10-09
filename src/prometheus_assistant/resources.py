from __future__ import annotations
from dataclasses import dataclass, asdict
import json, os
from pathlib import Path
from .offline_archive import ARCHIVES

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
    KnowledgeResource('public-reference','Public resource directory and U.S. federal court rules',('law','government','medicine','science','employment','judges'),'sqlite-reference','offline','https://www.uscourts.gov/forms-rules','Knowledge/Public-Reference/2026-10-09/public-2026-10-09.sqlite3','Five original federal rule PDFs, selected indexed pages, public directory and small API discovery snapshots; not complete global law or a live legal database.'),
    KnowledgeResource("engineering-sources","Raspberry Pi, Arduino, microcontrollers and PCB/chip-design references",("engineering","electronics","raspberry-pi","arduino","esp32","sensors","pcb","chips"),"sqlite-reference","offline","https://www.raspberrypi.com/documentation/","Knowledge/Engineering/2026-10-09/engineering-2026-10-09.sqlite3","Pinned official source snapshots; licenses accompany originals. CAD geometry is retained in ZIPs; searchable metadata is not an electrical-design validation."),
    KnowledgeResource("openai-public-sources","OpenAI public coding, agent, tokenizer and evaluation sources",("coding","ai","agents","evaluation","openai"),"sqlite-reference","offline","https://developers.openai.com/","Knowledge/OpenAI-Reference/2026-10-09/openai-2026-10-09.sqlite3","Public source only, with original licenses. Evals dataset licenses vary. No private model internals, API credentials or automatic execution."),
    KnowledgeResource("advanced-mathematics","Stacks Project and Lean mathlib mathematical references",("math","algebra","geometry","proofs","topology","analysis","number-theory"),"sqlite-reference","offline","https://github.com/stacks/stacks-project","Knowledge/Mathematics/2026-10-09/mathematics-2026-10-09.sqlite3","Stacks Project: GNU FDL; mathlib: Apache 2.0. Original sources, licenses and pinned revisions accompany the index. Proof compiler not installed."),
    KnowledgeResource("medlineplus-topics","MedlinePlus health topics — English and Spanish",("medicine","health","reference","medical-terms"),"sqlite-reference","offline","https://medlineplus.gov/xml.html","Knowledge/Medical/MedlinePlus-2026-10-08/medlineplus-2026-10-08.sqlite3","Source: MedlinePlus, National Library of Medicine. Public-domain health-topic summaries; excludes externally licensed linked contents."),
    KnowledgeResource("openai-gpt-oss","OpenAI gpt-oss source and license",("coding","ai","reasoning"),"source-reference","offline","https://github.com/openai/gpt-oss","Knowledge/OpenAI-Reference/gpt-oss/README.md","Apache 2.0; reference implementations have separate hardware requirements"),
    KnowledgeResource("openai-whisper","OpenAI Whisper source and license",("voice","speech","ai"),"source-reference","offline","https://github.com/openai/whisper","Knowledge/OpenAI-Reference/whisper/README.md","MIT; installed speech runtime uses whisper.cpp"),
    KnowledgeResource("openai-docs","Official OpenAI developer documentation",("coding","ai","api"),"online-library","online","https://developers.openai.com/",None,"Live reference; accessing links requires internet"),
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
    for item in ARCHIVES:
        path = root / "Knowledge" / "Kiwix" / item.project / item.filename if root else None
        rows.append({"id": item.id, "title": item.title, "subjects": list(item.subjects) or [item.project],
                     "kind": "kiwix-zim", "access": "offline", "source": item.source_url,
                     "relative_path": f"Knowledge/Kiwix/{item.project}/{item.filename}",
                     "license_note": item.license_note, "installed": bool(path and path.is_file()),
                     "path": str(path) if path else None,
                     "size_bytes": path.stat().st_size if path and path.is_file() else None,
                     "version": item.version})
    return rows

def write_catalog(destination: Path, resource_root: str | None = None) -> Path:
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps({"schema":1,"resources":inventory(resource_root)},indent=2),encoding="utf-8")
    return destination

