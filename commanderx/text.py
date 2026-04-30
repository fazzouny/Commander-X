from __future__ import annotations

import re
import shlex


def parse_message(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def slugify(value: str, limit: int = 44) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    slug = slug[:limit].strip("-")
    return slug or "task"
