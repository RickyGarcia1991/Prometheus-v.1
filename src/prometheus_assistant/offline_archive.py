from __future__ import annotations
from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
import json

@dataclass(frozen=True)
class ArchiveItem:
    id: str
    project: str
    title: str
    language: str
    variant: str
    filename: str
    source_url: str
    sha256: str
    version: str
    license_note: str

ARCHIVES=(
 ArchiveItem("wikipedia-en-all-nopic","wikipedia","Wikipedia English — full text, no images","en","nopic","wikipedia_en_all_nopic_2026-06.zim","https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_2026-06.zim","441a56d9e05b2d98f8ae9acb7986a513ed47904d73852c92dc6b7d50baa122e5","2026-06","Wikimedia content licenses apply; see source content."),
 ArchiveItem("wiktionary-en-all-nopic","wiktionary","Wiktionary English — no images","en","nopic","wiktionary_en_all_nopic_2026-08.zim","https://download.kiwix.org/zim/wiktionary/wiktionary_en_all_nopic_2026-08.zim","5276f63a2e451518ec5b7c4452ca9a4f72fae48991da3fe7ebc47df74bae760a","2026-08","Wikimedia content licenses apply; see source content."),
 ArchiveItem("wikisource-en-all-nopic","wikisource","Wikisource English — no images","en","nopic","wikisource_en_all_nopic_2026-09.zim","https://download.kiwix.org/zim/wikisource/wikisource_en_all_nopic_2026-09.zim","6180cd199142862fb0d276861ccc5b48a40f7bae427b8427b2b6a573796e2f98","2026-09","Source texts have per-work rights/license status; preserve attribution."),
)

def archive_root(resource_root: str|Path)->Path:
    return Path(resource_root)/"Knowledge"/"Kiwix"

def verify_file(path:Path, expected:str, chunk:int=8*1024*1024)->bool:
    h=sha256()
    with path.open("rb") as f:
        while b:=f.read(chunk): h.update(b)
    return h.hexdigest().lower()==expected.lower()

def inventory(resource_root: str|Path, verify:bool=False)->list[dict]:
    root=archive_root(resource_root); rows=[]
    for item in ARCHIVES:
        p=root/item.project/item.filename
        row=asdict(item); row["path"]=str(p); row["installed"]=p.is_file()
        row["size_bytes"]=p.stat().st_size if p.is_file() else None
        row["verified_sha256"]=verify_file(p,item.sha256) if verify and p.is_file() else None
        rows.append(row)
    return rows

def write_manifest(resource_root: str|Path, destination: str|Path, verify:bool=False)->Path:
    out=Path(destination); out.parent.mkdir(parents=True,exist_ok=True)
    payload={"schema":1,"format":"kiwix-zim","update_policy":"versioned-replacement","resources":inventory(resource_root,verify)}
    tmp=out.with_suffix(out.suffix+".tmp")
    tmp.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    tmp.replace(out)
    return out
