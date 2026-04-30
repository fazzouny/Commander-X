from __future__ import annotations

from typing import Any


SESSION_TO_TASK_STATUS = {
    "running": "running",
    "completed": "done",
    "failed": "failed",
    "stopped": "stopped",
    "stop_failed": "failed",
    "finished_unknown": "review",
}


def sync_task_records(
    tasks: list[dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    updated_at: str,
) -> bool:
    changed = False
    for task in tasks:
        task_id = task.get("id")
        for session in sessions.values():
            if session.get("task_id") != task_id:
                continue
            mapped = SESSION_TO_TASK_STATUS.get(str(session.get("state")), task.get("status"))
            if task.get("status") != mapped:
                task["status"] = mapped
                task["updated_at"] = updated_at
                changed = True
    return changed


def visible_task_records(tasks: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    active_statuses = {"queued", "running", "review", "failed", "stopped"}
    visible = [task for task in tasks if task.get("status") in active_statuses]
    if not visible:
        visible = tasks[-limit:]
    return visible[-limit:]
