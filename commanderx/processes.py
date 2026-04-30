from __future__ import annotations

import os
import subprocess
from pathlib import Path


WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        creationflags=WINDOWS_NO_WINDOW,
    )


def codex_command_args(args: list[str]) -> list[str]:
    if os.name == "nt":
        return ["cmd.exe", "/d", "/c", "codex", *args]
    return ["codex", *args]


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = run_command(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=15)
        return result.returncode == 0 and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_pid(pid: int) -> tuple[bool, str]:
    if os.name == "nt":
        result = run_command(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=30)
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    try:
        os.kill(pid, 15)
        return True, "Sent SIGTERM."
    except OSError as exc:
        return False, str(exc)
