"""Safe read-only tools exposed to the bounded agent loop."""
from __future__ import annotations
from pathlib import Path
from .agent import ToolRegistry,ToolSpec
from .hardware import detect_hardware,resource_root
from .resources import inventory
from .evidence import offline_evidence

def _safe_status():
    hw=detect_hardware()
    return {"system":hw.system,"ram_gib":hw.ram_gib,"cpu_threads":hw.cpu_threads,
            "resource_root":resource_root()}

def _safe_resources():
    return inventory(resource_root())

def _safe_knowledge(query,limit=3):
    rows=offline_evidence(query,root=resource_root())
    return [{"source":r.source_type,"ref":r.source_ref,"text":r.text} for r in rows[:limit]]

def _safe_read_text(path,roots=()):
    target=Path(path).expanduser().resolve()
    allowed=[Path(r).expanduser().resolve() for r in roots]
    if not allowed or not any(target==root or root in target.parents for root in allowed):
        raise PermissionError("Path is outside approved read roots.")
    if not target.is_file():
        raise FileNotFoundError(str(target))
    return target.read_text(encoding="utf-8")[:12000]

def default_local_registry(*,read_roots=()):
    registry=ToolRegistry()
    registry.register(ToolSpec("system_status",_safe_status))
    registry.register(ToolSpec("resource_inventory",_safe_resources))
    registry.register(ToolSpec("offline_knowledge",_safe_knowledge))
    registry.register(ToolSpec("read_text",lambda path:_safe_read_text(path,read_roots)))
    return registry
