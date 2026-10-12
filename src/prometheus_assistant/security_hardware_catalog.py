"""Read-only, bounded, evidence-graded hardware guide lookup for Prometheus.

Package-name existence in Kali is not proof of maintenance, compatibility,
hardware necessity, or executable integration. Never runs tool binaries.
"""
from __future__ import annotations
from functools import lru_cache
import json
import os
from pathlib import Path
import re

REPORT = Path(__file__).resolve().parent / "hardware_814_reference.json"
MAX_BYTES = 3_000_000


@lru_cache(maxsize=1)
def _report() -> dict:
    if REPORT.is_symlink() or not REPORT.is_file() or REPORT.stat().st_size > MAX_BYTES:
        raise ValueError("Sourced hardware report is unavailable or oversized.")
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    if (not isinstance(data, dict)
        or data.get("schema_version") != 1
        or data.get("package_count") != 814
        or data.get("inventory_quality") != "PRELIMINARY_PURCHASE_MAPPING_NOT_INDIVIDUAL_CERTIFICATION"
        or not isinstance(data.get("packages"), list)
        or not isinstance(data.get("hardware_source_catalog"), list)
        or len(data["packages"]) != 814):
        raise ValueError("Hardware source report is not verified for this checkout.")
    return data


def hardware_sources(*, package: str = "", limit: int = 12) -> dict:
    if not isinstance(package, str) or len(package) > 105:
        raise ValueError("Provide a bounded exact Kali package name.")
    if type(limit) is not int or not 1 <= limit <= 35:
        raise ValueError("Hardware source limit must be between 1 and 35.")
    data = _report()
    parts = {part["id"]: part for part in data["hardware_source_catalog"]}
    info = {
        "host": "Dell Windows primary workstation" if os.name == "nt" else data["host"],
        "source_audit_host": data["host"],
        "primary_host": "Dell",
        "package_references": data["package_count"],
        "hardware_classes_sourced": data["hardware_classes"],
        "review_quality": data["inventory_quality"],
        "purchases_performed": False,
        "every_package_functionally_tested": False,
        "automatic_installation": False,
        "source_updated": data["generated_at"],
    }
    if not package:
        info["hardware_classes"] = [
            {k: part[k] for k in ("id", "name", "priority", "source", "choice")}
            for part in data["hardware_source_catalog"][:limit]
        ]
        info["returned"] = len(info["hardware_classes"])
        info["note"] = "Use --package PACKAGE for hardware candidates for one catalog entry."
        return info
    name = package.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,100}", name):
        raise ValueError("Provide a literal Kali package name (not flags or commands).")
    row = next((r for r in data["packages"] if r["package"] == name), None)
    if row is None:
        raise ValueError("Package does not appear in the pinned 814-reference catalog.")
    requirement = row["specialized_hardware"]
    components = []
    for link in requirement["hardware_class_candidates"]:
        part = parts.get(link["hardware_id"])
        if not part:
            raise ValueError("Hardware source catalog has a missing dependency.")
        components.append({
            "hardware_id": part["id"],
            "name": part["name"],
            "priority": part["priority"],
            "source": part["source"],
            "selection": part["choice"],
            "hardware_need_basis": link["dependency_basis"],
            "device_required_for_all_commands": link["mandatory_for_all_commands"],
            "functionality_verified_on_this_host": False,
        })
    info.update({
        "package": row["package"],
        "ubuntu_package_state_at_audit": row["ubuntu_package_state"],
        "maintenance_status": row["maintenance_status"],
        "hardware_evidence_level": requirement["hardware_specificity"],
        "per_package_hardware_certified": False,
        "component_candidates": components,
        "notes": [
            "No external hardware purchase is required without a specific validated operation.",
            "Category inference is not a verified driver/device dependency.",
            "The Bluetooth availability observation comes from the Ubuntu source audit, not a Dell hardware test.",
            "Exact driver, antenna, voltage, firmware and legal permissions require validation.",
        ],
    })
    return info
