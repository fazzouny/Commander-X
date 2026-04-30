from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


API_BASE = "https://api.clickup.com/api/v2"


@dataclass
class ClickUpSettings:
    token: str
    workspace_id: str

    @property
    def configured(self) -> bool:
        return bool(self.token and self.workspace_id)


def settings_from_env(env: dict[str, str] | None = None) -> ClickUpSettings:
    source = env or os.environ
    return ClickUpSettings(
        token=source.get("CLICKUP_API_TOKEN", "").strip(),
        workspace_id=(source.get("CLICKUP_WORKSPACE_ID") or source.get("CLICKUP_TEAM_ID") or "").strip(),
    )


def clickup_request(settings: ClickUpSettings, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.configured:
        raise RuntimeError("CLICKUP_API_TOKEN and CLICKUP_WORKSPACE_ID are required.")
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{API_BASE}{path}"
    if query:
        url += f"?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": settings.token,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def filtered_team_tasks(settings: ClickUpSettings, page: int = 0, include_closed: bool = False) -> dict[str, Any]:
    return clickup_request(
        settings,
        f"/team/{urllib.parse.quote(settings.workspace_id)}/task",
        {
            "page": page,
            "order_by": "updated",
            "reverse": "true",
            "subtasks": "true",
            "include_closed": "true" if include_closed else "false",
        },
    )


def task_text(task: dict[str, Any]) -> str:
    fields = [
        task.get("name"),
        task.get("text_content"),
        task.get("description"),
        task.get("markdown_description"),
        task.get("custom_id"),
        task.get("id"),
    ]
    return " ".join(str(item) for item in fields if item).lower()


def filter_tasks(tasks: list[dict[str, Any]], query: str | None = None) -> list[dict[str, Any]]:
    if not query:
        return tasks
    terms = [term.lower() for term in query.split() if term.strip()]
    if not terms:
        return tasks
    return [task for task in tasks if all(term in task_text(task) for term in terms)]


def format_tasks(tasks: list[dict[str, Any]], limit: int = 8) -> str:
    if not tasks:
        return "No matching ClickUp tasks found."
    lines = ["ClickUp tasks"]
    for task in tasks[:limit]:
        status = (task.get("status") or {}).get("status") if isinstance(task.get("status"), dict) else task.get("status")
        assignees = task.get("assignees") or []
        assignee_names = []
        for assignee in assignees[:3]:
            if isinstance(assignee, dict):
                assignee_names.append(str(assignee.get("username") or assignee.get("email") or assignee.get("id")))
        url = task.get("url") or ""
        task_id = task.get("custom_id") or task.get("id") or "-"
        lines.append(f"- {task_id}: {task.get('name', '-')}")
        lines.append(f"  Status: {status or '-'}; Assignees: {', '.join(assignee_names) or '-'}")
        if url:
            lines.append(f"  URL: {url}")
    if len(tasks) > limit:
        lines.append(f"...and {len(tasks) - limit} more.")
    return "\n".join(lines)
