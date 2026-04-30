from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def bytes_to_mb(value: int) -> float:
    return round(value / (1024 * 1024), 1)


def estimate_dir_size(path: Path, max_files: int = 20000, max_seconds: float = 2.0) -> dict[str, Any]:
    started = time.perf_counter()
    total = 0
    files = 0
    dirs = 0
    errors = 0
    truncated = False
    stack = [path]
    while stack:
        if files >= max_files or (time.perf_counter() - started) > max_seconds:
            truncated = True
            break
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs += 1
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            total += int(stat.st_size)
                            files += 1
                            if files >= max_files:
                                truncated = True
                                break
                    except OSError:
                        errors += 1
                if truncated:
                    break
        except OSError:
            errors += 1
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": total,
        "size_mb": bytes_to_mb(total),
        "files": files,
        "dirs": dirs,
        "errors": errors,
        "truncated": truncated,
    }


def cleanup_targets(base_dir: Path) -> list[dict[str, Any]]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    temp = Path(os.environ.get("TEMP", local / "Temp"))
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    targets = [
        {
            "id": "commander-archive-logs",
            "label": "Commander archived logs",
            "path": base_dir / "logs" / "archive",
            "risk": "low",
            "action": "Delete old archived service logs after reviewing them.",
        },
        {
            "id": "commander-voice",
            "label": "Commander downloaded voice notes",
            "path": base_dir / "logs" / "voice",
            "risk": "medium",
            "action": "Delete old voice-note downloads if transcripts are no longer needed.",
        },
        {
            "id": "commander-screenshots",
            "label": "Commander screenshots",
            "path": base_dir / "logs" / "screenshots",
            "risk": "medium",
            "action": "Delete screenshots after checking they do not contain needed evidence.",
        },
        {
            "id": "windows-temp",
            "label": "Windows user temp",
            "path": temp,
            "risk": "medium",
            "action": "Delete old temp files that are not in use.",
        },
        {
            "id": "npm-npx-cache",
            "label": "NPX temporary package cache",
            "path": local / "npm-cache" / "_npx",
            "risk": "low",
            "action": "Delete stale NPX folders; they are recreated on demand.",
        },
        {
            "id": "pip-cache",
            "label": "Python pip cache",
            "path": local / "pip" / "Cache",
            "risk": "low",
            "action": "Clear pip cache if disk pressure is high.",
        },
        {
            "id": "playwright-cache",
            "label": "Playwright browser cache",
            "path": local / "ms-playwright",
            "risk": "medium",
            "action": "Remove unused browser builds only if projects can reinstall them.",
        },
        {
            "id": "windows-update-downloads",
            "label": "Windows Update downloads",
            "path": windir / "SoftwareDistribution" / "Download",
            "risk": "medium",
            "action": "Clean only with Windows Update services handled safely.",
        },
    ]
    return targets


def cleanup_scan(base_dir: Path, max_files: int = 20000, max_seconds_per_target: float = 2.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in cleanup_targets(base_dir):
        path = Path(target["path"])
        estimate = estimate_dir_size(path, max_files=max_files, max_seconds=max_seconds_per_target) if path.exists() else {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "size_mb": 0.0,
            "files": 0,
            "dirs": 0,
            "errors": 0,
            "truncated": False,
        }
        rows.append({**target, **estimate, "path": str(path)})
    rows.sort(key=lambda item: float(item.get("size_mb", 0)), reverse=True)
    return rows


def format_cleanup_scan(rows: list[dict[str, Any]], limit: int = 8) -> str:
    if not rows:
        return "No cleanup targets found."
    lines = ["Cleanup advisor"]
    lines.append("No files were deleted. This is a planning scan only.")
    lines.append("")
    for row in rows[:limit]:
        suffix = " partial scan" if row.get("truncated") else ""
        exists = "exists" if row.get("exists") else "missing"
        lines.append(f"- {row['label']}: {row['size_mb']} MB, {row['files']} files, {exists}{suffix}")
        lines.append(f"  Risk: {row['risk']}; action: {row['action']}")
    lines.append("")
    lines.append("Deletion is intentionally not exposed from Telegram yet. Use this as a review plan.")
    return "\n".join(lines)
