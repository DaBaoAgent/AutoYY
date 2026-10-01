from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True, slots=True)
class ResourcePlan:
    cpu_count: int
    memory_gb: float | None
    gpu_name: str | None
    gpu_memory_gb: float | None
    asr_device: str
    asr_workers: int
    download_workers: int
    package_workers: int


def _windows_memory_gb() -> float | None:
    if os.name != "nt":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return round(status.ullTotalPhys / (1024**3), 2)


def _nvidia_gpu() -> tuple[str | None, float | None]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None, None
    command = [
        executable,
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, None
    first = proc.stdout.splitlines()[0]
    parts = [part.strip() for part in first.split(",", 1)]
    if len(parts) != 2:
        return parts[0] if parts else None, None
    try:
        memory_gb = round(float(parts[1]) / 1024.0, 2)
    except ValueError:
        memory_gb = None
    return parts[0], memory_gb


@lru_cache(maxsize=1)
def detect_resources() -> ResourcePlan:
    cpu = max(1, os.cpu_count() or 1)
    memory_gb = _windows_memory_gb()
    gpu_name, gpu_memory_gb = _nvidia_gpu()
    if gpu_name and (gpu_memory_gb or 0) >= 6:
        asr_device = "cuda"
        if (gpu_memory_gb or 0) >= 20:
            asr_workers = 3
        elif (gpu_memory_gb or 0) >= 12:
            asr_workers = 2
        else:
            asr_workers = 1
    else:
        asr_device = "cpu"
        asr_workers = 1
    download_workers = 4
    if cpu >= 12 and (memory_gb or 0) >= 24:
        download_workers = 6
    elif cpu <= 4 or (memory_gb is not None and memory_gb < 8):
        download_workers = 2
    package_workers = min(4, max(1, cpu // 2))
    return ResourcePlan(
        cpu, memory_gb, gpu_name, gpu_memory_gb, asr_device,
        asr_workers, download_workers, package_workers,
    )


def resource_plan_dict() -> dict[str, Any]:
    plan = detect_resources()
    return {
        "cpu_count": plan.cpu_count,
        "memory_gb": plan.memory_gb,
        "gpu_name": plan.gpu_name,
        "gpu_memory_gb": plan.gpu_memory_gb,
        "asr": {"device": plan.asr_device, "workers": plan.asr_workers},
        "download_workers": plan.download_workers,
        "package_workers": plan.package_workers,
    }
