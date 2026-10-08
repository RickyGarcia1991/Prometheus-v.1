"""Safe built-in tools exposed to the Prometheus agent."""
import json
from .articles import search_articles
from .hardware import detect_hardware, resource_root
from .resources import inventory
from .tool_registry import ArgSpec, ToolRegistry, ToolSpec

def build_builtin_registry(memory):
    def memory_lookup(request):
        query=request.arguments["query"]
        words={w.lower().strip(".,!?") for w in query.split() if len(w)>2}
        rows=[]
        for row in memory.knowledge():
            text=(row["subject"]+" "+row["value"]).lower()
            score=sum(word in text for word in words)
            if score: rows.append((score,row))
        rows.sort(key=lambda x:(x[0],x[1]["confidence"],x[1]["id"]),reverse=True)
        return json.dumps([{"kind":r["kind"],"subject":r["subject"],"value":r["value"],
            "source_type":r["source_type"],"source_ref":r["source_ref"],
            "confidence":r["confidence"]} for _,r in rows[:8]],ensure_ascii=False)

    def offline_articles(request):
        return json.dumps(search_articles(resource_root(),request.arguments["query"],limit=5),ensure_ascii=False)

    def resource_status(request):
        return json.dumps(inventory(resource_root()),ensure_ascii=False)

    def host_summary(request):
        hw=detect_hardware()
        return json.dumps({"ram_gib":hw.ram_gib,"cpu_threads":hw.cpu_threads,
                           "system":hw.system},ensure_ascii=False)

    return ToolRegistry([
        ToolSpec("memory","lookup","Search trusted local knowledge with provenance.",memory_lookup,{"query":ArgSpec()}),
        ToolSpec("knowledge","articles","Search installed offline Wikimedia archives.",offline_articles,{"query":ArgSpec()}),
        ToolSpec("knowledge","resources","Inspect the local portable knowledge inventory.",resource_status,{}),
        ToolSpec("system","summary","Read basic local hardware and OS information.",host_summary,{}),
    ])
