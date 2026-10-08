"""Bounded built-in tools for Prometheus local agent workflows."""
import json,re
from pathlib import Path
from .articles import search_articles
from .hardware import detect_hardware,resource_root
from .resources import inventory
from .tool_registry import ArgSpec,ToolRegistry,ToolSpec
MAX_PROJECT_FILE=300_000
PROJECT_EXTENSIONS={".py",".md",".txt",".json",".toml",".ps1",".cs",".xaml",".cmd"}
def _project_root(): return Path(__file__).resolve().parents[2]
def _safe_project_path(relative):
    root=_project_root().resolve(); path=(root/relative).resolve()
    if path==root or not path.is_relative_to(root): raise ValueError("Project path escapes the Prometheus source root.")
    if path.is_symlink() or not path.is_file(): raise ValueError("Project file is unavailable.")
    if path.suffix.lower() not in PROJECT_EXTENSIONS: raise ValueError("Project file type is not readable by this tool.")
    if path.stat().st_size>MAX_PROJECT_FILE: raise ValueError("Project file exceeds the bounded read limit.")
    return root,path
def build_builtin_registry(memory):
    def memory_lookup(request):
        words={w.lower().strip(".,!?") for w in request.arguments["query"].split() if len(w)>2}; rows=[]
        for row in memory.knowledge():
            text=(row["subject"]+" "+row["value"]).lower(); score=sum(word in text for word in words)
            if score: rows.append((score,row))
        rows.sort(key=lambda x:(x[0],x[1]["confidence"],x[1]["id"]),reverse=True)
        return json.dumps([{"kind":r["kind"],"subject":r["subject"],"value":r["value"],"source_type":r["source_type"],"source_ref":r["source_ref"],"confidence":r["confidence"]} for _,r in rows[:8]],ensure_ascii=False)
    def offline_articles(request): return json.dumps(search_articles(resource_root(),request.arguments["query"],limit=5),ensure_ascii=False)
    def resource_status(request): return json.dumps(inventory(resource_root()),ensure_ascii=False)
    def host_summary(request):
        hw=detect_hardware(); return json.dumps({"ram_gib":hw.ram_gib,"cpu_threads":hw.cpu_threads,"system":hw.system},ensure_ascii=False)
    def project_read(request):
        root,path=_safe_project_path(request.arguments["path"]); text=path.read_text(encoding="utf-8-sig",errors="replace")
        return json.dumps({"path":path.relative_to(root).as_posix(),"content":text[:12000],"truncated":len(text)>12000},ensure_ascii=False)
    def project_search(request):
        query=request.arguments["query"]; terms=set(re.findall(r"[A-Za-z0-9_]+",query.casefold())); hits=[]; root=_project_root().resolve(); scanned=0
        for path in root.rglob("*"):
            if scanned>=300: break
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in PROJECT_EXTENSIONS: continue
            if any(part in {".git",".venv","bin","obj","__pycache__"} for part in path.parts): continue
            try:
                if path.stat().st_size>MAX_PROJECT_FILE: continue
                text=path.read_text(encoding="utf-8-sig",errors="replace"); scanned+=1
            except OSError: continue
            for number,line in enumerate(text.splitlines(),1):
                line_terms=set(re.findall(r"[A-Za-z0-9_]+",line.casefold())); score=len(terms & line_terms)
                if score: hits.append({"path":path.relative_to(root).as_posix(),"line":number,"excerpt":line[:500],"score":score})
        hits.sort(key=lambda x:(-x["score"],x["path"],x["line"])); return json.dumps({"query":query,"results":hits[:20],"files_scanned":scanned},ensure_ascii=False)
    def project_list(request):
        root=_project_root().resolve(); base=(root/request.arguments.get("path","")).resolve()
        if base!=root and not base.is_relative_to(root): raise ValueError("Project path escapes the Prometheus source root.")
        if not base.exists() or not base.is_dir(): raise ValueError("Project directory is unavailable.")
        rows=[]
        for item in sorted(base.iterdir(),key=lambda x:(not x.is_dir(),x.name.casefold()))[:100]:
            if item.name in {".git",".venv","bin","obj","__pycache__"}: continue
            rows.append({"path":item.relative_to(root).as_posix(),"type":"directory" if item.is_dir() else "file","size":None if item.is_dir() else item.stat().st_size})
        return json.dumps(rows,ensure_ascii=False)
    def project_replace(request):
        root,path=_safe_project_path(request.arguments["path"]); old=request.arguments["old"]; new=request.arguments["new"]; text=path.read_text(encoding="utf-8-sig",errors="strict")
        if text.count(old)!=1: raise ValueError("Replacement requires exactly one matching block.")
        temp=path.with_name(path.name+".prometheus-tmp"); temp.write_text(text.replace(old,new,1),encoding="utf-8",newline=""); temp.replace(path)
        return json.dumps({"path":path.relative_to(root).as_posix(),"changed":True,"old_chars":len(old),"new_chars":len(new)})
    return ToolRegistry([
        ToolSpec("memory","lookup","Search trusted local knowledge with provenance.",memory_lookup,{"query":ArgSpec()}),
        ToolSpec("knowledge","articles","Search installed offline Wikimedia archives.",offline_articles,{"query":ArgSpec()}),
        ToolSpec("knowledge","resources","Inspect the local portable knowledge inventory.",resource_status,{}),
        ToolSpec("system","summary","Read basic local hardware and OS information.",host_summary,{}),
        ToolSpec("project","search","Search bounded text/code inside the Prometheus source tree.",project_search,{"query":ArgSpec(max_length=200)}),
        ToolSpec("project","read","Read one bounded relative text/code file inside the Prometheus source tree.",project_read,{"path":ArgSpec(max_length=240)}),
        ToolSpec("project","list","List one bounded directory inside the Prometheus source tree.",project_list,{"path":ArgSpec(required=False,max_length=240)}),
        ToolSpec("project","replace","Atomically replace exactly one matching text block inside an approved Prometheus source file.",project_replace,{"path":ArgSpec(max_length=240),"old":ArgSpec(max_length=8000),"new":ArgSpec(max_length=8000)},mutates_state=True),
    ])
