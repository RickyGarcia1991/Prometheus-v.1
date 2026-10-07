"""Convert successful agent observations into bounded provenance-aware evidence."""
from __future__ import annotations
import json
from .context import Evidence
from .retrieval import rank_evidence

def observation_evidence(prompt, observations, limit=6):
    rows=[]
    for item in observations:
        if item.get("status")!="completed":
            continue
        tool=item.get("tool","unknown")
        result=item.get("result")
        if tool=="offline_knowledge" and isinstance(result,list):
            for entry in result:
                if not isinstance(entry,dict):
                    continue
                text=str(entry.get("text","")).strip()
                if text:
                    rows.append(Evidence(str(entry.get("source","tool")),
                                         str(entry.get("ref",tool)),text,65))
            continue
        if result is None:
            continue
        if isinstance(result,str):
            text=result
        else:
            text=json.dumps(result,sort_keys=True,default=str)
        if text.strip():
            rows.append(Evidence("tool",tool,text[:12000],55))
    return rank_evidence(prompt,rows,limit=limit)
