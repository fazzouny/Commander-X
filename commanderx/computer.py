from __future__ import annotations

import ctypes
import csv
import datetime as dt
import io
import json
import os
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from commanderx.processes import run_command


DEFAULT_APPS: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
}

VOLUME_KEYS = {
    "mute": 0xAD,
    "down": 0xAE,
    "up": 0xAF,
}


def normalize_url(url: str) -> str:
    clean = url.strip()
    if not clean:
        return ""
    if not clean.startswith(("http://", "https://")):
        clean = "https://" + clean
    return clean


def open_url(url: str) -> tuple[bool, str]:
    clean = normalize_url(url)
    if not clean:
        return False, "URL is required."
    opened = webbrowser.open(clean)
    return opened, f"Opened URL: {clean}" if opened else f"Could not open URL: {clean}"


def app_catalog(config: dict[str, Any] | None = None) -> dict[str, list[str]]:
    apps = dict(DEFAULT_APPS)
    for name, value in (config or {}).get("apps", {}).items():
        if isinstance(value, str):
            apps[str(name).lower()] = [value]
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            apps[str(name).lower()] = value
    return apps


def open_app(name: str, config: dict[str, Any] | None = None) -> tuple[bool, str]:
    apps = app_catalog(config)
    key = name.strip().lower()
    if not key:
        return False, "App name is required."
    command = apps.get(key)
    if not command:
        return False, f"App is not allowlisted: {name}. Available: {', '.join(sorted(apps))}"
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
    except OSError as exc:
        return False, f"Could not open {name}: {exc}"
    return True, f"Opened app: {name}"


def press_volume_key(action: str, steps: int = 1) -> tuple[bool, str]:
    key = VOLUME_KEYS.get(action.lower())
    if not key:
        return False, "Volume action must be up, down, or mute."
    steps = max(1, min(25, steps))
    user32 = ctypes.windll.user32 if os.name == "nt" else None
    if user32 is None:
        return False, "Volume key control is currently implemented for Windows only."
    for _ in range(steps):
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)
    detail = f"{action} x{steps}" if action.lower() in {"up", "down"} else action
    return True, f"Volume command sent: {detail}"


def capture_screenshot(destination_dir: Path) -> tuple[bool, str]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output = destination_dir / f"screenshot-{timestamp}.png"
    script = rf"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save('{str(output).replace("'", "''")}', [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
"""
    result = run_command(["powershell", "-NoProfile", "-Command", script], timeout=30)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "Screenshot failed.").strip()
    return True, str(output)


def process_lines(names: list[str], timeout: int = 30) -> list[str]:
    cleaned = [name.strip() for name in names if name.strip()]
    if not cleaned:
        cleaned = ["python.exe", "codex.exe", "node.exe"]
    if os.name == "nt":
        exact = [name for name in cleaned if name.lower().endswith(".exe")]
        terms = [name for name in cleaned if not name.lower().endswith(".exe")]
        wmic_lines = wmic_process_lines(exact, terms, timeout=max(3, min(timeout, 8)))
        if wmic_lines is not None:
            return wmic_lines
        quoted_exact = ", ".join("'" + name.replace("'", "''") + "'" for name in exact)
        quoted_terms = ", ".join("'" + name.replace("'", "''") + "'" for name in terms)
        script = (
            f"$exact = @({quoted_exact}); $terms = @({quoted_terms}); "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $proc = $_; ($exact -contains $proc.Name) -or "
            "[bool]($terms | Where-Object { $null -ne $proc.CommandLine -and $proc.CommandLine -like ('*' + $_ + '*') }) } | "
            "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Depth 3"
        )
        result = run_command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                script,
            ],
            timeout=timeout,
        )
        text = (result.stdout or result.stderr).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            rows = parsed if isinstance(parsed, list) else [parsed]
            lines: list[str] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pid = row.get("ProcessId", "-")
                name = row.get("Name", "-")
                command = str(row.get("CommandLine") or "").replace("\r", " ").replace("\n", " ")
                if len(command) > 180:
                    command = command[:177].rstrip() + "..."
                lines.append(f"{pid} {name} {command}".strip())
            return lines
        except json.JSONDecodeError:
            return text.splitlines()
    result = run_command(["ps", "aux"], timeout=timeout)
    lines = []
    for line in result.stdout.splitlines():
        if any(name.lower() in line.lower() for name in cleaned):
            lines.append(line)
    return lines


def wmic_process_lines(exact: list[str], terms: list[str], timeout: int = 8) -> list[str] | None:
    """Fast Windows process scan fallback; returns None when WMIC is unavailable."""
    try:
        result = run_command(
            ["wmic", "process", "get", "ProcessId,Name,CommandLine", "/format:csv"],
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or "").strip()
    if not output:
        return []
    exact_lower = {item.lower() for item in exact}
    terms_lower = [item.lower() for item in terms]
    lines: list[str] = []
    reader = csv.DictReader(io.StringIO(output))
    for row in reader:
        name = str(row.get("Name") or "").strip()
        command = str(row.get("CommandLine") or "").replace("\r", " ").replace("\n", " ").strip()
        pid = str(row.get("ProcessId") or "-").strip()
        if not name:
            continue
        haystack = f"{name} {command}".lower()
        if name.lower() not in exact_lower and not any(term in haystack for term in terms_lower):
            continue
        if len(command) > 180:
            command = command[:177].rstrip() + "..."
        lines.append(f"{pid} {name} {command}".strip())
    return lines
