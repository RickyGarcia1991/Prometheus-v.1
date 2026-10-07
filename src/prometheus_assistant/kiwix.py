"""Portable offline ZIM search and article retrieval using official Kiwix tools."""
from __future__ import annotations
from html.parser import HTMLParser
import os, shutil, socket, subprocess, time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from .offline_archive import ARCHIVES

class KiwixError(RuntimeError): pass

class _Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","svg"}: self.skip+=1
    def handle_endtag(self,tag):
        if tag in {"script","style","svg"} and self.skip: self.skip-=1
    def handle_data(self,data):
        if not self.skip and data.strip(): self.parts.append(data.strip())

def _tool(root:Path,name:str)->Path:
    override=os.environ.get("PROMETHEUS_KIWIX_BIN")
    for p in ([Path(override)/name] if override else [])+[root/"Tools"/"Kiwix"/"bin"/name]:
        if p.is_file(): return p
    found=shutil.which(name)
    if found: return Path(found)
    raise KiwixError(f"{name} was not found in the Prometheus resource root or PATH")

def _item(archive_id:str):
    item=next((x for x in ARCHIVES if x.id==archive_id),None)
    if item is None: raise KiwixError(f"Unknown archive: {archive_id}")
    return item

def archive_path(resource_root:str|Path,archive_id:str)->Path:
    item=_item(archive_id); p=Path(resource_root)/"Knowledge"/"Kiwix"/item.project/item.filename
    if not p.is_file(): raise KiwixError(f"Archive is not installed: {archive_id}")
    return p

def search_archive(resource_root:str|Path,archive_id:str,query:str,limit:int=10)->list[str]:
    query=query.strip()
    if not query: raise ValueError("query must not be empty")
    if limit<1: raise ValueError("limit must be at least 1")
    root=Path(resource_root); exe="kiwix-search.exe" if os.name=="nt" else "kiwix-search"
    try: done=subprocess.run([str(_tool(root,exe)),str(archive_path(root,archive_id)),query],
        capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=120,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc: raise KiwixError(f"Kiwix search failed: {exc}") from exc
    if done.returncode: raise KiwixError((done.stderr or done.stdout).strip() or f"kiwix-search exited with {done.returncode}")
    return [x.strip() for x in done.stdout.splitlines() if x.strip()][:limit]

def read_article(resource_root:str|Path,archive_id:str,title:str,max_chars:int=12000)->str:
    if not title.strip(): raise ValueError("title must not be empty")
    if max_chars<1: raise ValueError("max_chars must be at least 1")
    root=Path(resource_root); item=_item(archive_id); archive=archive_path(root,archive_id)
    exe="kiwix-serve.exe" if os.name=="nt" else "kiwix-serve"
    with socket.socket() as s:
        s.bind(("127.0.0.1",0)); port=s.getsockname()[1]
    proc=subprocess.Popen([str(_tool(root,exe)),"--address=127.0.0.1",f"--port={port}","--blockexternal",str(archive)],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        url=f"http://127.0.0.1:{port}/content/{archive.stem}/{quote(title.replace(' ','_'),safe='/()_-')}"
        last=None
        for _ in range(40):
            try:
                with urlopen(url,timeout=2) as response: raw=response.read().decode("utf-8","replace")
                break
            except Exception as exc: last=exc; time.sleep(.1)
        else: raise KiwixError(f"Unable to read offline article: {last}")
        parser=_Text(); parser.feed(raw)
        return " ".join(parser.parts)[:max_chars]
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill()
