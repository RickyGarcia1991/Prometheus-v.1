"""Curated Hugging Face research index for offline-first Prometheus.

Pinned public repositories, bounded local Ollama inventory, optional *explicit*
read-only metadata comparison. No checkpoint downloads or remote code execution.
"""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import json
import re
import urllib.request

_ALLOWED_LICENSES = {"apache-2.0", "cc-by-sa-4.0", "mit"}
_API = "https://huggingface.co/api/"
MAX_REPOS = 12


@lru_cache(maxsize=1)
def catalog() -> dict:
    raw = files("prometheus_assistant").joinpath("hf_curated_catalog.json").read_text("utf-8")
    if len(raw) > 120_000:
        raise ValueError("Hugging Face catalog exceeded its audited size limit.")
    data = json.loads(raw)
    sources = data.get("resources", [])
    if data.get("schema_version") != 1 or not 1 <= len(sources) <= MAX_REPOS:
        raise ValueError("Curated Hugging Face catalog schema is invalid.")
    if len({x.get("repo_id") for x in sources}) != len(sources):
        raise ValueError("Duplicate Hugging Face resource in pinned catalog.")
    for entry in sources:
        rid = entry.get("repo_id")
        revision = entry.get("pinned_revision")
        repo_type = entry.get("repo_type")
        prefix = "https://huggingface.co/" + ("datasets/" if repo_type == "dataset" else "")
        if (not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", rid) or
                repo_type not in {"model", "dataset"} or
                not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision) or
                entry.get("license") not in _ALLOWED_LICENSES or
                entry.get("url") != prefix + rid or
                entry.get("auto_load") is not False or
                entry.get("auto_download") is not False or
                entry.get("auto_train") is not False or
                entry.get("requires_trust_remote_code") is not False):
            raise ValueError("Unreviewed Hugging Face artifact rejected.")
    return data


def ollama_inventory() -> dict[str, str]:
    """Loopback only, with no proxies and no bearer tokens."""
    from .application_policy import require_dispatch
    require_dispatch('ollama.request')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:11434/api/tags", timeout=2) as response:
        if response.geturl() != "http://127.0.0.1:11434/api/tags":
            raise ValueError("Ollama inventory unexpectedly redirected.")
        data = json.loads(response.read(160_000))
    result = {}
    for row in data.get("models", []):
        if not isinstance(row, dict) or any(row.get(k) for k in ("remote_host", "remote_model", "cloud")):
            continue
        name, digest = row.get("name"), row.get("digest")
        if isinstance(name, str) and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
            result[name] = digest
    return result


def describe(*, query: str = "", role: str = "", limit=12, inspect_host=True) -> dict:
    if (not isinstance(query, str) or len(query) > 100 or
            not isinstance(role, str) or len(role) > 60 or
            type(limit) is not int or not 1 <= limit <= MAX_REPOS or
            type(inspect_host) is not bool):
        raise ValueError("Hugging Face resource filters exceed safe limits.")
    all_sources = catalog()["resources"]
    filtered = [entry for entry in all_sources
                if (not query or query.casefold() in (entry["repo_id"] + " " + entry["use"]).casefold())
                and (not role or role == entry["role"])]
    installed = {}
    host_checked = False
    if inspect_host:
        try:
            installed = ollama_inventory()
            host_checked = True
        except (OSError, ValueError, TimeoutError):
            pass
    rows = []
    for item in filtered[:limit]:
        model = item["ollama_tag"]
        local_onnx_verified = False
        local_voice_verified = False
        if inspect_host and item["repo_id"] == "cross-encoder/ms-marco-MiniLM-L6-v2":
            try:
                from .hf_reranker import verify_assets
                local_onnx_verified = verify_assets()["onnx_verified"] is True
            except (OSError, ValueError, ImportError):
                pass
        if inspect_host and item["repo_id"] == "Systran/faster-whisper-tiny.en":
            try:
                from .hf_voice_ubuntu import verify_assets as verify_voice
                local_voice_verified = verify_voice()["files_verified"] == 4
            except (OSError, ValueError, ImportError):
                pass
        is_installed = bool(model in installed) if model and host_checked else False
        is_installed = is_installed or local_onnx_verified or local_voice_verified
        rows.append({
            "repo_id": item["repo_id"],
            "repo_type": item["repo_type"],
            "license": item["license"],
            "pinned_revision": item["pinned_revision"],
            "role": item["role"],
            "url": item["url"],
            "surface_priority": item["surface_priority"],
            "dell_priority": item["dell_priority"],
            "local_ollama_tag": model,
            "installed_locally": is_installed if host_checked or local_onnx_verified or local_voice_verified else None,
            "local_ollama_digest": installed.get(model) if model and is_installed else None,
            "local_onnx_verified": local_onnx_verified,
            "local_voice_verified": local_voice_verified,
            "artifact_format": ("onnx-cpu" if local_onnx_verified else
                                "ctranslate2-cpu" if local_voice_verified else
                                "ollama" if model else None),
            "decision": item["status"],
            "decision_scope": "Historical catalog assessment, not current execution evidence.",
            "execution_verified_this_call": False,
            "use": item["use"],
            "review_caution": item["risk_notes"],
            "auto_download": False,
            "auto_execute_external_code": False,
        })
    return {
        "source": "Hugging Face pinned public model and dataset metadata",
        "primary_host": "Dell",
        "portable_host": "Ubuntu Surface Laptop Go",
        "known_repositories": len(all_sources),
        "matched": len(filtered),
        "rows": rows,
        "host_model_check_performed": host_checked,
        "requires_explicit_approval_for_download": True,
        "license_gate_passed_for_catalog": True,
        "all_future_revisions_autotrusted": False,
        "remote_model_code_executed": False,
    }


def compare_remote(repo_id: str) -> dict:
    """Opt-in metadata-only check against one of the curated Hugging Face repos."""
    from .application_policy import require_dispatch
    require_dispatch('hf.metadata')
    if not isinstance(repo_id, str) or len(repo_id) > 120:
        raise ValueError("Choose one pinned Hugging Face resource.")
    entry = next((r for r in catalog()["resources"] if r["repo_id"] == repo_id), None)
    if entry is None:
        raise ValueError("Unknown Hugging Face repository; no arbitrary URL requests.")
    endpoint = _API + ("datasets/" if entry["repo_type"] == "dataset" else "models/") + repo_id
    request = urllib.request.Request(endpoint, headers={"User-Agent": "Prometheus-HF-metadata-review/0.1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=12) as response:
        if response.geturl() != endpoint:
            raise ValueError("Unexpected Hugging Face metadata redirect.")
        metadata = json.loads(response.read(320_000))
    license_name = (metadata.get("cardData") or {}).get("license")
    if isinstance(license_name, list) and len(license_name) == 1:
        license_name = license_name[0]
    live_sha = metadata.get("sha")
    same_license = license_name == entry["license"]
    same_revision = live_sha == entry["pinned_revision"]
    return {
        "repo_id": repo_id,
        "source": entry["url"],
        "pinned_sha": entry["pinned_revision"],
        "observed_sha": live_sha,
        "license_observed": license_name,
        "revision_unchanged": same_revision,
        "license_unchanged": same_license,
        "access_gated": bool(metadata.get("gated") or metadata.get("private")),
        "safe_to_promote_without_manual_review": False,
        "metadata_only": True,
        "no_files_downloaded": True,
        "status": "pinned_revision_matches" if same_revision and same_license else "changed_needs_review",
    }
