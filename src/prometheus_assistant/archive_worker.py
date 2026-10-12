"""Keep native ZIM search work outside the interface's Python interpreter."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile


def search(root, query, *, project='auto', limit=5):
    if not root or not isinstance(query,str) or not 1<=len(query)<=256:
        raise ValueError('Archive search requires a resource directory and a query of 1–256 characters.')
    payload=json.dumps({'root':str(root),'query':query,'project':project,'limit':limit}).encode('utf-8')
    # Native libzim calls can hold Python's interpreter lock long enough to block
    # HTTP status requests. A short-lived child keeps the interface responsive
    # and releases archive handles and native allocations on completion.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--search'],
                input=payload,stdout=output,stderr=errors,timeout=90,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired as error:
            raise ValueError('Offline archive search exceeded 90 seconds; try a shorter query.') from error
        output.seek(0);raw=output.read(256*1024+1)
        if len(raw)>256*1024:raise ValueError('Archive search response exceeded its size limit.')
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):raise ValueError('Offline archive search did not return a valid result.') from None
        if result.returncode or not isinstance(value,dict) or 'error' in value:
            raise ValueError(str(value.get('error','Offline archive search failed.') if isinstance(value,dict) else 'Invalid archive response.')[:600])
        if not isinstance(value.get('results'),list):raise ValueError('Archive search results are missing.')
        return value


def main():
    # The embedded Windows runtime ignores PYTHONPATH. Resolve this signed
    # package explicitly; do not add the resource directory to the import path.
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from prometheus_assistant.articles import search_articles
    try:
        raw=sys.stdin.buffer.read(4097)
        if len(raw)>4096:raise ValueError('Archive query payload is too large.')
        value=json.loads(raw)
        result=search_articles(value['root'],value['query'],project=value['project'],limit=value['limit'])
        print(json.dumps(result,ensure_ascii=True),flush=True)
        return 0
    except Exception as error:
        print(json.dumps({'error':str(error)[:600]},ensure_ascii=True),flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
