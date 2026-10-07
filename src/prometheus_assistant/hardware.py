from __future__ import annotations

from dataclasses import dataclass
import os
import platform
import shutil


@dataclass(frozen=True)
class HardwareProfile:
    ram_gib: float
    cpu_threads: int
    system: str


@dataclass(frozen=True)
class ModelProfile:
    model: str
    min_ram_gib: float
    context: int
    tier: str


MODEL_PROFILES = (
    ModelProfile("gemma4:26b", 24.0, 4096, "heavy"),
    ModelProfile("llama3.2:1b-instruct-q4_K_M", 6.0, 3072, "balanced"),
    ModelProfile("llama3.2:1b", 4.0, 2048, "light"),
)


def detect_hardware() -> HardwareProfile:
    ram = 0
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
        except (AttributeError, OSError):
            pass
    if not ram and hasattr(os, "sysconf"):
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            pass
    return HardwareProfile(round(ram / 1024**3, 2), os.cpu_count() or 1, platform.system())


def select_model(installed: set[str], hardware: HardwareProfile) -> ModelProfile | None:
    for profile in MODEL_PROFILES:
        if profile.model in installed and hardware.ram_gib >= profile.min_ram_gib:
            return profile
    return None


def coding_agents() -> dict[str, str]:
    commands = {"opencode": "opencode", "qwen": "qwen", "cline": "cline", "claude": "claude", "codex": "codex"}
    return {name: path for name, command in commands.items() if (path := shutil.which(command))}


def resource_root() -> str | None:
    configured = os.environ.get("PROMETHEUS_RESOURCE_ROOT")
    if configured and os.path.isdir(configured):
        return os.path.abspath(configured)
    return None
