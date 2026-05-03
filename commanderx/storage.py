from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any


def read_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(default)
    for attempt in range(6):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.15 * (attempt + 1))
        except FileNotFoundError:
            return copy.deepcopy(default)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    return copy.deepcopy(default)


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        for attempt in range(6):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.15 * (attempt + 1))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
