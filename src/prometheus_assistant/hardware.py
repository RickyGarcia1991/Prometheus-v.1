from __future__ import annotations

from dataclasses import dataclass, replace
import os
import platform
import shutil
from pathlib import Path


@dataclass(frozen=True)
class HardwareProfile:
    ram_gib: float
    cpu_threads: int
    system: str
    available_ram_gib: float | None = None


@dataclass(frozen=True)
class ModelProfile:
    model: str
    min_ram_gib: float
    context: int
    tier: str
    cpu_threads: int = 1


MODEL_PROFILES = (
    ModelProfile("gpt-oss:20b", 24.0, 8192, "reasoning"),
    ModelProfile("gemma4:26b", 30.0, 4096, "heavy"),
    ModelProfile("hermes3:3b", 6.0, 4096, "standard"),
    ModelProfile("llama3.2:1b-instruct-q4_K_M", 6.0, 3072, "balanced"),
    ModelProfile("llama3.2:1b", 4.0, 2048, "light"),
    ModelProfile("qwen3:1.7b", 6.0, 3072, "balanced"),
    ModelProfile("qwen3:0.6b", 4.0, 2048, "light"),
    ModelProfile("qwen3.5:0.8b", 4.0, 1536, "compact-multimodal"),
    ModelProfile("gemma3:270m", 2.0, 1024, "ultralight"),
    ModelProfile("qwen2.5-coder:0.5b", 4.0, 1024, "coding-light"),
)


def detect_hardware() -> HardwareProfile:
    ram = 0
    available = None
    if os.name == "nt":
        try:
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended_virtual", ctypes.c_ulonglong)]
            status = MemoryStatus(); status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                ram = status.total_phys
                available = status.avail_phys
        except (AttributeError, OSError):
            pass
    if not ram and hasattr(os, "sysconf"):
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            pass
    if available is None and platform.system() == "Linux":
        try:
            from pathlib import Path
            values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            available = int(values["MemAvailable"].split()[0]) * 1024
        except (OSError, ValueError, KeyError):
            pass
    return HardwareProfile(round(ram / 1024**3, 2), os.cpu_count() or 1, platform.system(),
                           round(available / 1024**3, 2) if available is not None else None)


def select_model(installed: set[str], hardware: HardwareProfile,
                 resident_model_gib: float = 0, *, task: str = "general") -> ModelProfile | None:
    """Choose only installed models, reserving RAM for the OS and other applications.

    Resident Ollama weights can be reused/replaced (the portable server permits one
    loaded model). These are conservative CPU-memory estimates, not speed guarantees.
    """
    reserve = 1.0 if hardware.ram_gib < 12 else 2.0 if hardware.ram_gib < 48 else 3.0
    available = hardware.available_ram_gib
    if available is None:
        # Unknown pressure must never trigger a large-model load.
        available = min(hardware.ram_gib * .5, 6.0)
    budget = max(0, min(hardware.ram_gib, available + max(0, resident_model_gib)) - reserve)
    threads = max(1, min(16, hardware.cpu_threads - (2 if hardware.cpu_threads > 4 else 1)))
    profiles = MODEL_PROFILES
    if task == "coding":
        profiles = (MODEL_PROFILES[0], ModelProfile("qwen2.5-coder:1.5b", 4.0, 4096, "coding"),
                    ModelProfile("qwen2.5-coder:0.5b", 4.0, 2048, "coding-light"), *MODEL_PROFILES[1:])
    elif task == "vision":
        profiles = (ModelProfile("qwen3-vl:2b", 6.0, 4096, "vision"),
                    ModelProfile("qwen3.5:0.8b", 4.0, 1536, "compact-vision"))
    for profile in profiles:
        context = profile.context
        if profile.tier == "reasoning":
            required = 15.0
        elif profile.tier == "vision":
            required = 3.5
        elif profile.tier == "compact-vision":
            required = 2.5
        elif profile.tier == "compact-multimodal":
            required = 2.0
        elif profile.tier == "coding-light":
            required = 1.25
            if budget < required and hardware.available_ram_gib is not None and available + max(0, resident_model_gib) >= 1.05:
                # SSD measurements: 0.380 GiB resident, context1024/batch32.
                # 0.55 GiB estimate plus 0.5 GiB system headroom.
                if profile.model in installed and hardware.ram_gib >= profile.min_ram_gib:
                    return replace(profile, context=1024, cpu_threads=min(4, threads))
        elif profile.tier == "coding":
            context = 8192 if hardware.ram_gib >= 12 and budget >= 3 else 4096
            required = 3.0 if context == 8192 else 2.0
        elif profile.tier == "heavy":
            required = 22.0
            if hardware.ram_gib >= 48 and budget >= 28:
                context = 8192
                required = 28.0
        elif profile.tier == "standard":
            context = 8192 if hardware.ram_gib >= 12 and budget >= 4.5 else 4096
            required = 4.5 if context == 8192 else 3.0
        elif profile.tier == "balanced":
            context = 8192 if hardware.ram_gib >= 30 and budget >= 5 else 4096 if hardware.ram_gib >= 12 and budget >= 3 else 3072
            required = {3072: 2.0, 4096: 3.0, 8192: 5.0}[context]
        else:
            required = 1.5
        if profile.tier == 'ultralight':
            # Measured 0.279 GiB resident with batch32/context1024 on the SSD.
            # Add runtime headroom and keep 0.5 GiB free; no estimate grants unknown-pressure loads.
            if hardware.available_ram_gib is None:
                continue
            required = .35
            if profile.model in installed and hardware.ram_gib >= 2 and available + max(0, resident_model_gib) >= .85:
                return replace(profile, context=1024, cpu_threads=min(4, threads))
            continue
        if profile.model in installed and hardware.ram_gib >= profile.min_ram_gib and budget >= required:
            return replace(profile, context=context, cpu_threads=threads)
    return None


def coding_agents() -> dict[str, str]:
    commands = ("opencode", "qwen", "cline", "claude", "codex", "hermes")
    found = {name: path for name in commands if (path := shutil.which(name))}
    root = resource_root()
    if root:
        for name, relative in PORTABLE_AGENTS.items():
            executable = Path(root) / relative
            if executable.is_file():
                found[name] = str(executable)
    return found


PORTABLE_AGENTS = {
    "codex": "Coding/Codex-0.162.0/bin/codex.exe",
    "hermes": "Coding/Hermes-0.21.6/bin/hermes.exe",
}


def resource_root() -> str | None:
    configured = os.environ.get("PROMETHEUS_RESOURCE_ROOT")
    if configured and os.path.isdir(configured):
        return os.path.abspath(configured)
    return None
