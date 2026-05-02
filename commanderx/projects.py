from __future__ import annotations

import re
from typing import Any


def normalized_project_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[_-]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def project_alias_values(project_id: str, project: dict[str, Any]) -> list[str]:
    values = [
        project_id,
        project_id.replace("-", " "),
        project.get("display_name", ""),
        project.get("name", ""),
        project.get("short_name", ""),
    ]
    values.extend(project.get("aliases", []))
    return [str(value).strip() for value in values if str(value or "").strip()]


def build_project_alias_map(projects: dict[str, dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for project_id, project in projects.items():
        for alias in project_alias_values(project_id, project):
            raw_text = str(alias).strip().lower()
            if raw_text:
                aliases[raw_text] = project_id
            normalized_text = normalized_project_text(alias)
            if normalized_text:
                aliases[normalized_text] = project_id
    return aliases


def resolve_project(
    projects: dict[str, dict[str, Any]],
    value: str | None,
    active_project: str | None = None,
) -> str | None:
    aliases = build_project_alias_map(projects)
    if value:
        normalized = normalized_project_text(value)
        if normalized in aliases:
            return aliases[normalized]
        hyphenated = re.sub(r"\s+", "-", normalized)
        if hyphenated in aliases:
            return aliases[hyphenated]
        mentions = mentioned_projects(projects, normalized)
        if len(mentions) == 1:
            return mentions[0]
    if active_project and active_project in projects:
        return active_project
    return None


def mentioned_projects(projects: dict[str, dict[str, Any]], text: str) -> list[str]:
    aliases = sorted(build_project_alias_map(projects).items(), key=lambda item: len(item[0]), reverse=True)
    normalized = normalized_project_text(text)
    found: dict[str, int] = {}
    for alias, project_id in aliases:
        pattern = rf"(?<![\w-]){re.escape(alias)}(?![\w-])"
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match and (project_id not in found or match.start() < found[project_id]):
            found[project_id] = match.start()
    return [project_id for project_id, _index in sorted(found.items(), key=lambda item: item[1])]
