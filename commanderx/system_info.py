from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any

from commanderx.processes import run_command


def disk_summary(paths: list[Path] | None = None) -> list[dict[str, Any]]:
    targets = paths or [Path.cwd()]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for path in targets:
        root = path.anchor or str(path)
        if root in seen:
            continue
        seen.add(root)
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        rows.append(
            {
                "root": root,
                "total_gb": round(usage.total / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
            }
        )
    return rows


def windows_battery_summary() -> str:
    if os.name != "nt":
        return "not available"
    result = run_command(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_Battery | Select-Object -First 1 EstimatedChargeRemaining,BatteryStatus | ConvertTo-Json -Compress)",
        ],
        timeout=20,
    )
    text = (result.stdout or result.stderr).strip()
    if not text:
        return "no battery detected"
    return text


def windows_memory_summary() -> str:
    if os.name != "nt":
        return "not available"
    result = run_command(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$total=[math]::Round($os.TotalVisibleMemorySize/1MB,1); "
            "$free=[math]::Round($os.FreePhysicalMemory/1MB,1); "
            "$used=[math]::Round((($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/$os.TotalVisibleMemorySize)*100,1); "
            "[pscustomobject]@{total_gb=$total;free_gb=$free;used_percent=$used} | ConvertTo-Json -Compress",
        ],
        timeout=20,
    )
    return (result.stdout or result.stderr).strip() or "unknown"


def system_snapshot(extra_paths: list[Path] | None = None) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "disk": disk_summary(extra_paths),
        "memory": windows_memory_summary(),
        "battery": windows_battery_summary(),
    }


def format_system_snapshot(snapshot: dict[str, Any]) -> str:
    lines = [
        "System status",
        f"Platform: {snapshot.get('platform')}",
        f"Python: {snapshot.get('python')}",
        f"Machine: {snapshot.get('machine')}",
        f"Memory: {snapshot.get('memory')}",
        f"Battery: {snapshot.get('battery')}",
        "",
        "Disk:",
    ]
    for row in snapshot.get("disk", []):
        lines.append(
            f"- {row.get('root')}: {row.get('free_gb')} GB free / {row.get('total_gb')} GB total "
            f"({row.get('used_percent')}% used)"
        )
    return "\n".join(lines)
