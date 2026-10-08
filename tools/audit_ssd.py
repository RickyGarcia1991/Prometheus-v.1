"""Read-only audit of all Prometheus-owned SSD files; reports never contain file contents."""
import argparse,ast,hashlib,json,os,sqlite3,time
from pathlib import Path
def run(root,output,reuse=None):
    root=Path(root).resolve(); output=Path(output).resolve()
    cache={}
    if reuse and Path(reuse).exists():
        cache={r["path"]:r for r in json.loads(Path(reuse).read_text())["files"]}
    files=[]; errors=[]; started=time.monotonic(); last=0; hashed=0
    targets=[p for p in root.iterdir() if p.name.startswith(("Prometheus","PROMETHEUS","START-PROMETHEUS"))]
    for target in targets:
        if target.is_file():files.append(target)
        elif target.is_dir():
            for base,dirs,names in os.walk(target,followlinks=False,onerror=lambda e:errors.append({"path":str(e.filename),"error":str(e)})):
                dirs[:]=[d for d in dirs if not Path(base,d).is_symlink() and not Path(base,d).is_junction()]
                files.extend(Path(base,n) for n in names if not Path(base,n).is_symlink())
    total=sum(p.stat().st_size for p in files if p.exists())
    rows=[]
    for n,p in enumerate(sorted(files),1):
        rel=p.relative_to(root).as_posix()
        if p==output:continue
        try:
            before=p.stat(); prior=cache.get(rel)
            cached=bool(prior and prior.get("size")==before.st_size and prior.get("mtime_ns")==before.st_mtime_ns)
            if cached:digest=prior["sha256"]
            else:
                h=hashlib.sha256()
                with p.open("rb",buffering=8*1024*1024) as f:
                    while block:=f.read(8*1024*1024):
                        h.update(block);hashed+=len(block)
                        now=time.monotonic()
                        if now-last>=5:
                            print(json.dumps({"phase":"hashing","file":rel,"file_number":n,"file_count":len(files),"bytes_read":hashed,"total_bytes":total,"elapsed_seconds":round(now-started)}),flush=True);last=now
                digest=h.hexdigest()
            after=p.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError("File changed during audit")
            row={"path":rel,"size":after.st_size,"mtime_ns":after.st_mtime_ns,"sha256":digest,"cached":cached}
            if p.name.startswith("sha256-") and len(p.name)==71 and digest!=p.name[7:]:raise ValueError("Model blob does not match its SHA256 filename")
            if p.suffix.lower()==".json" and after.st_size<8*1024*1024:
                try:json.loads(p.read_text(encoding="utf-8-sig"))
                except Exception as e:row["json_error"]=str(e)
            if p.suffix==".py" and after.st_size<2*1024*1024:
                try:ast.parse(p.read_text(encoding="utf-8-sig"),filename=rel)
                except Exception as e:row["python_error"]=str(e)
            if p.suffix in {".sqlite3",".db"}:
                with p.open("rb") as f:header=f.read(16)
                if header==b"SQLite format 3\x00":
                    try:
                        db=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=3)
                        row["sqlite_integrity"]=db.execute("PRAGMA integrity_check").fetchone()[0]
                        row["sqlite_fk_errors"]=len(db.execute("PRAGMA foreign_key_check").fetchall());db.close()
                    except Exception as e:row["sqlite_error"]=str(e)
            rows.append(row)
        except Exception as e:errors.append({"path":rel,"error":str(e)})
    result={"root":str(root),"created":time.strftime("%Y-%m-%dT%H:%M:%S%z"),"file_count":len(rows),"total_bytes":sum(r["size"] for r in rows),"bytes_read":hashed,"elapsed_seconds":round(time.monotonic()-started,1),"errors":errors,"files":rows}
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix(".tmp");temp.write_text(json.dumps(result,indent=2));temp.replace(output)
    print(json.dumps({k:v for k,v in result.items() if k!="files"}),flush=True)
    return 1 if errors else 0
if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--root",required=True);parser.add_argument("--output",required=True);parser.add_argument("--reuse")
    a=parser.parse_args();raise SystemExit(run(a.root,a.output,a.reuse))
