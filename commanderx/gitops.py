from __future__ import annotations

import subprocess
from pathlib import Path

from commanderx.processes import run_command


def git_safe_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def git_args(path: Path, *args: str) -> list[str]:
    return ["git", "-c", f"safe.directory={git_safe_path(path)}", "-C", str(path), *args]


def git_run(path: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return run_command(git_args(path, *args), timeout=timeout)


def is_git_repo(path: Path) -> bool:
    result = git_run(path, "rev-parse", "--is-inside-work-tree", timeout=15)
    return result.returncode == 0 and result.stdout.strip() == "true"


def current_branch(path: Path) -> str:
    result = git_run(path, "symbolic-ref", "--short", "HEAD", timeout=15)
    if result.returncode == 0:
        return result.stdout.strip()
    result = git_run(path, "rev-parse", "--abbrev-ref", "HEAD", timeout=15)
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def changed_files(path: Path) -> list[str]:
    result = git_run(path, "status", "--porcelain=v1", timeout=30)
    if result.returncode != 0:
        return []
    files: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        name = line[3:].strip()
        if " -> " in name:
            name = name.split(" -> ", 1)[1].strip()
        if name:
            files.append(name)
    return files


def has_changes(path: Path) -> bool:
    return bool(changed_files(path))
