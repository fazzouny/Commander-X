from __future__ import annotations

import re
from typing import Any


def build_project_alias_map(projects: dict[str, dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for project_id, project in projects.items():
        aliases[project_id.lower()] = project_id
        aliases[project_id.replace("-", " ").lower()] = project_id
        for alias in project.get("aliases", []):
            alias_text = str(alias).strip().lower()
            if alias_text:
                aliases[alias_text] = project_id
    return aliases


def resolve_project(
    projects: dict[str, dict[str, Any]],
    value: str | None,
    active_project: str | None = None,
) -> str | None:
    aliases = build_project_alias_map(projects)
    if value:
        normalized = value.strip().lower()
        if normalized in aliases:
            return aliases[normalized]
        hyphenated = re.sub(r"\s+", "-", normalized)
        if hyphenated in aliases:
            return aliases[hyphenated]
    if active_project and active_project in projects:
        return active_project
    return None


def mentioned_projects(projects: dict[str, dict[str, Any]], text: str) -> list[str]:
    aliases = sorted(build_project_alias_map(projects).items(), key=lambda item: len(item[0]), reverse=True)
    found: dict[str, int] = {}
    for alias, project_id in aliases:
        pattern = rf"(?<![\w-]){re.escape(alias)}(?![\w-])"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and (project_id not in found or match.start() < found[project_id]):
            found[project_id] = match.start()
    return [project_id for project_id, _index in sorted(found.items(), key=lambda item: item[1])]
