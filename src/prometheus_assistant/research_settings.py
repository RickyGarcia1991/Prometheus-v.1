"""Explicit machine-local research settings; no secrets required."""
import json
import os
from pathlib import Path
from .web_research import PROVIDERS

def config_path():
    explicit = os.environ.get("PROMETHEUS_RESEARCH_CONFIG")
    if explicit: return Path(explicit)
    root = os.environ.get("PROMETHEUS_RESOURCE_ROOT")
    if root: return Path(root) / "research.json"
    return None

def load_research_settings():
    result = {"online_enabled": False, "provider": "federated", "retain_research": False}
    path = config_path()
    if path is None or not path.exists(): return result
    if path.stat().st_size > 16_384: raise ValueError("Research configuration is too large.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid research configuration JSON.") from error
    if not isinstance(data, dict) or set(data) - set(result):
        raise ValueError("Unknown research configuration fields.")
    for name in ("online_enabled", "retain_research"):
        if name in data and not isinstance(data[name], bool):
            raise ValueError(f"Research setting {name} must be a boolean.")
    if data.get("provider", "federated") not in PROVIDERS:
        raise ValueError("Unknown research provider in configuration.")
    result.update(data)
    return result
