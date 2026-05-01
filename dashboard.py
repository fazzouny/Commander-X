#!/usr/bin/env python3
"""Local dashboard for Commander X."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import commander
from commanderx.system_info import disk_summary


commander.load_env_file()
HOST = os.environ.get("COMMANDER_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("COMMANDER_DASHBOARD_PORT", "8787"))
WEB_DIR = Path(__file__).resolve().parent / "web"
MCP_CACHE_SECONDS = int(os.environ.get("COMMANDER_DASHBOARD_MCP_CACHE_SECONDS", "300"))
MCP_TIMEOUT_SECONDS = int(os.environ.get("COMMANDER_DASHBOARD_MCP_TIMEOUT_SECONDS", "8"))
MCP_CACHE: dict[str, Any] = {"value": None, "at": 0.0}
DASHBOARD_CACHE_SECONDS = int(os.environ.get("COMMANDER_DASHBOARD_CACHE_SECONDS", "8"))
DASHBOARD_BACKGROUND_REFRESH_SECONDS = int(os.environ.get("COMMANDER_DASHBOARD_BACKGROUND_REFRESH_SECONDS", "45"))
DASHBOARD_REQUEST_REFRESH_SECONDS = int(
    os.environ.get(
        "COMMANDER_DASHBOARD_REQUEST_REFRESH_SECONDS",
        str(max(DASHBOARD_CACHE_SECONDS, DASHBOARD_BACKGROUND_REFRESH_SECONDS if DASHBOARD_BACKGROUND_REFRESH_SECONDS > 0 else DASHBOARD_CACHE_SECONDS)),
    )
)
DASHBOARD_WARM_CACHE_ON_START = commander.env_bool("COMMANDER_DASHBOARD_WARM_CACHE_ON_START", True)
DASHBOARD_CACHE: dict[str, Any] = {
    "value": None,
    "at": 0.0,
    "generated_at": "",
    "refreshing": False,
    "last_error": "",
    "last_error_at": "",
}
DASHBOARD_CACHE_LOCK = threading.Lock()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def cached_mcp_summary() -> str:
    now = time.monotonic()
    if MCP_CACHE["value"] and now - float(MCP_CACHE["at"]) < MCP_CACHE_SECONDS:
        return str(MCP_CACHE["value"])
    try:
        result = commander.run_command(commander.codex_command_args(["mcp", "list"]), timeout=MCP_TIMEOUT_SECONDS)
        if result.returncode == 0 and result.stdout.strip():
            value = result.stdout.strip()
        else:
            value = (result.stderr or result.stdout or "Could not read Codex MCP list.").strip()
    except subprocess.TimeoutExpired:
        value = f"Codex MCP list did not respond within {MCP_TIMEOUT_SECONDS}s. Use /mcp for a full live read."
    MCP_CACHE["value"] = value
    MCP_CACHE["at"] = now
    return value


def invalidate_dashboard_cache() -> None:
    with DASHBOARD_CACHE_LOCK:
        DASHBOARD_CACHE["at"] = 0.0
    refresh_dashboard_cache_async(force=True)


def dashboard_cache_metadata(now: float | None = None, stale: bool | None = None) -> dict[str, Any]:
    current = now or time.monotonic()
    with DASHBOARD_CACHE_LOCK:
        generated_at = str(DASHBOARD_CACHE.get("generated_at") or "")
        at = float(DASHBOARD_CACHE.get("at") or 0.0)
        refreshing = bool(DASHBOARD_CACHE.get("refreshing"))
        last_error = str(DASHBOARD_CACHE.get("last_error") or "")
        last_error_at = str(DASHBOARD_CACHE.get("last_error_at") or "")
    age = max(0.0, current - at) if at else 0.0
    is_stale = bool(stale) if stale is not None else bool(at and age >= DASHBOARD_CACHE_SECONDS)
    return {
        "generated_at": generated_at,
        "age_seconds": round(age, 1),
        "stale": is_stale,
        "refreshing": refreshing,
        "ttl_seconds": DASHBOARD_CACHE_SECONDS,
        "background_refresh_seconds": DASHBOARD_BACKGROUND_REFRESH_SECONDS,
        "request_refresh_seconds": DASHBOARD_REQUEST_REFRESH_SECONDS,
        "last_error": last_error,
        "last_error_at": last_error_at,
    }


def attach_dashboard_cache_metadata(payload: dict[str, Any], now: float | None = None, stale: bool | None = None) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["dashboard_cache"] = dashboard_cache_metadata(now=now, stale=stale)
    return enriched


def fallback_dashboard_payload(message: str) -> dict[str, Any]:
    return {
        "status": message,
        "doctor": {"score": "warming", "checks": []},
        "projects": {},
        "sessions": {},
        "session_briefs": [],
        "recent_images": [],
        "work_feed": [],
        "tasks": [],
        "memory_count": 0,
        "approvals": [],
        "action_center": [],
        "inbox": [{"kind": "system", "priority": "low", "title": "Dashboard warming up", "detail": message}],
        "changes": [],
        "recommendations": [message],
        "state": {"updated_at": "", "telegram_update_offset": None, "users": {}},
        "heartbeat": {},
        "tools": {
            "apps": sorted(commander.app_catalog(commander.computer_tools_config())),
            "mcp": "Dashboard snapshot is warming up.",
            "skills": commander.skill_catalog(limit=12),
            "plugins": commander.plugin_catalog(limit=12),
            "clickup_configured": commander.clickup_settings_from_env().configured,
        },
        "capabilities": capabilities_payload("checking"),
        "openclaw": {
            "state": "checking",
            "skills_count": 0,
            "plugin_cache": False,
            "legacy_checkout": False,
            "configured_launcher": "",
            "launcher_error": "",
            "available_launchers": [],
            "processes": [],
            "repo_configured": bool(os.environ.get("COMMANDER_OPENCLAW_REPO_URL")),
            "web_research": os.environ.get("COMMANDER_OPENCLAW_WEB_RESEARCH", "true"),
        },
        "env": commander.env_readiness(),
        "system": fast_system_snapshot([commander.BASE_DIR]),
        "logs": [],
    }


def store_dashboard_cache(payload: dict[str, Any]) -> None:
    with DASHBOARD_CACHE_LOCK:
        DASHBOARD_CACHE["value"] = payload
        DASHBOARD_CACHE["at"] = time.monotonic()
        DASHBOARD_CACHE["generated_at"] = commander.utc_now()
        DASHBOARD_CACHE["refreshing"] = False
        DASHBOARD_CACHE["last_error"] = ""
        DASHBOARD_CACHE["last_error_at"] = ""


def mark_dashboard_cache_error(error: Exception) -> None:
    with DASHBOARD_CACHE_LOCK:
        DASHBOARD_CACHE["refreshing"] = False
        DASHBOARD_CACHE["last_error"] = f"{type(error).__name__}: {error}"
        DASHBOARD_CACHE["last_error_at"] = commander.utc_now()


def refresh_dashboard_cache_worker() -> None:
    try:
        store_dashboard_cache(build_dashboard_payload())
    except Exception as exc:  # pragma: no cover - defensive background guard
        mark_dashboard_cache_error(exc)
        print(f"{commander.utc_now()} dashboard cache refresh failed: {type(exc).__name__}: {exc}", flush=True)


def refresh_dashboard_cache_async(force: bool = False) -> bool:
    now = time.monotonic()
    with DASHBOARD_CACHE_LOCK:
        at = float(DASHBOARD_CACHE.get("at") or 0.0)
        value = DASHBOARD_CACHE.get("value")
        if DASHBOARD_CACHE.get("refreshing"):
            return False
        if not force and value and at and now - at < DASHBOARD_CACHE_SECONDS:
            return False
        DASHBOARD_CACHE["refreshing"] = True
    thread = threading.Thread(target=refresh_dashboard_cache_worker, name="commander-dashboard-cache", daemon=True)
    thread.start()
    return True


def dashboard_cache_loop() -> None:
    interval = max(5, DASHBOARD_BACKGROUND_REFRESH_SECONDS)
    while True:
        time.sleep(interval)
        refresh_dashboard_cache_async(force=False)


def start_dashboard_cache_workers() -> None:
    if not DASHBOARD_WARM_CACHE_ON_START:
        return
    refresh_dashboard_cache_async(force=True)
    if DASHBOARD_BACKGROUND_REFRESH_SECONDS > 0:
        thread = threading.Thread(target=dashboard_cache_loop, name="commander-dashboard-cache-loop", daemon=True)
        thread.start()


def light_project_profile(project_id: str, project: dict[str, Any], path: Path) -> dict[str, Any]:
    package = commander.read_package_json(path)
    scripts_obj = package.get("scripts")
    scripts = scripts_obj if isinstance(scripts_obj, dict) else {}
    stored = commander.profiles_data().get("profiles", {}).get(project_id, {})
    verification = stored.get("verification_commands") or []
    if not verification:
        for name in ("typecheck", "lint", "test", "build", "smoke"):
            if name in scripts:
                verification.append(f"npm run {name}")
    return {
        "stack": stored.get("stack") or commander.detect_stack(path, package),
        "verification_commands": verification,
    }


def project_payload(project_id: str, project: dict[str, Any], change_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    path = commander.project_path(project)
    exists = path.exists()
    profile = light_project_profile(project_id, project, path)
    change = change_map.get(project_id, {})
    return {
        "id": project_id,
        "allowed": bool(project.get("allowed", False)),
        "exists": exists,
        "git": bool(change) or (path / ".git").exists(),
        "branch": change.get("branch"),
        "changed_count": int(change.get("changed_count") or 0),
        "changed_preview": [],
        "areas": change.get("areas") or "",
        "aliases": project.get("aliases", []),
        "stack": profile.get("stack", []),
        "verification_commands": profile.get("verification_commands", []),
    }


def fast_system_snapshot(paths: list[Path]) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "disk": disk_summary(paths),
        "memory": "Use /system for live memory details",
        "battery": "Use /system for live battery details",
    }


def openclaw_dashboard_payload() -> dict[str, Any]:
    snapshot = commander.openclaw_status_snapshot()
    launcher, launcher_error = commander.configured_openclaw_launcher()
    launchable = bool(snapshot["available_launchers"])
    running = bool(snapshot["process_rows"])
    has_traces = bool(snapshot["openclaw_home_exists"] or snapshot["claw_home_exists"] or snapshot["skills_count"])
    if running:
        state = "running"
    elif launcher:
        state = "startable"
    elif launchable:
        state = "launchable"
    elif has_traces:
        state = "traces"
    else:
        state = "not detected"
    return {
        "state": state,
        "skills_count": snapshot["skills_count"],
        "plugin_cache": bool(snapshot["claw_home_exists"]),
        "legacy_checkout": bool(snapshot["legacy_checkout_exists"]),
        "configured_launcher": commander.friendly_local_path(launcher) if launcher else "",
        "launcher_error": launcher_error,
        "available_launchers": [
            {"label": str(item.get("label", "")), "path": commander.friendly_local_path(str(item.get("path", "")))}
            for item in snapshot["available_launchers"][:4]
        ],
        "processes": commander.summarize_process_rows(snapshot["process_rows"]),
        "repo_configured": bool(os.environ.get("COMMANDER_OPENCLAW_REPO_URL")),
        "web_research": os.environ.get("COMMANDER_OPENCLAW_WEB_RESEARCH", "true"),
    }


def capabilities_payload(openclaw_status: str | None = None) -> dict[str, Any]:
    apps = sorted(commander.app_catalog(commander.computer_tools_config()))
    skills = commander.skill_catalog(limit=12)
    plugins = commander.plugin_catalog(limit=12)
    clickup_configured = commander.clickup_settings_from_env().configured
    openclaw = openclaw_status or commander.openclaw_brief_status()
    return {
        "highlights": [
            "Telegram text, buttons, and voice notes",
            "Codex CLI sessions with logs, watch view, and task plans",
            "Dashboard control room with approvals and task controls",
            "Git diff, commit, and push approval gates",
            "Safe computer broker for URLs, apps, files, volume, screenshots, and process checks",
            "Browser inspection for websites",
            "ClickUp campaign/task bridge" if clickup_configured else "ClickUp bridge ready after API keys are configured",
            f"OpenClaw status: {openclaw}",
        ],
        "commands": [
            "/tools",
            "/status",
            "/watch",
            "/queue",
            "/approvals",
            "/changes",
            "/openclaw",
            "/clickup recent campaigns",
            "/computer codex",
            "/browser inspect https://example.com",
        ],
        "counts": {
            "apps": len(apps),
            "skills": len(skills),
            "plugins": len(plugins),
        },
        "openclaw": openclaw,
        "clickup_configured": clickup_configured,
    }


def sessions_payload() -> dict[str, Any]:
    commander.refresh_session_states()
    return commander.sessions_data()


def visible_user_states(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    users = state.get("users", {})
    allowed = commander.allowed_user_ids()
    if allowed:
        return {user_id: user for user_id, user in users.items() if user_id in allowed}
    return {
        user_id: user
        for user_id, user in users.items()
        if user_id.isdigit() and (user.get("last_chat_id") or user.get("heartbeat_chat_id"))
    }


def dashboard_doctor_checks(changes: list[dict[str, Any]], snapshot: dict[str, Any], projects: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(status: str, label: str, detail: str) -> None:
        checks.append({"status": status, "label": label, "detail": detail})

    add("good" if shutil.which("codex") else "bad", "Codex CLI", shutil.which("codex") or "missing from PATH")
    add("good" if shutil.which("git") else "bad", "Git", shutil.which("git") or "missing from PATH")
    add("good" if os.environ.get("TELEGRAM_BOT_TOKEN") else "bad", "Telegram bot token", "configured" if os.environ.get("TELEGRAM_BOT_TOKEN") else "missing")
    add("good" if commander.allowed_user_ids() else "bad", "Telegram allowlist", f"{len(commander.allowed_user_ids())} allowed user ID(s)" if commander.allowed_user_ids() else "missing")
    add("good" if os.environ.get("OPENAI_API_KEY") else "warn", "OpenAI API key", "configured" if os.environ.get("OPENAI_API_KEY") else "missing; voice/NL routing will degrade")
    add("good" if commander.clickup_settings_from_env().configured else "warn", "ClickUp API bridge", "configured" if commander.clickup_settings_from_env().configured else "missing API token/workspace ID")
    add("good" if commander.automation_exists("commander-x-monster-build-loop") else "warn", "Monster build automation", "configured" if commander.automation_exists("commander-x-monster-build-loop") else "not found")

    enabled = {project_id: project for project_id, project in projects.items() if project.get("allowed")}
    missing_paths = [project_id for project_id, project in enabled.items() if not project.get("exists")]
    non_git = [project_id for project_id, project in enabled.items() if project.get("exists") and not project.get("git")]
    add("good" if enabled else "warn", "Registered projects", f"{len(enabled)} enabled project(s)")
    add("good" if not missing_paths else "bad", "Project paths", "all enabled project paths exist" if not missing_paths else ", ".join(missing_paths[:6]))
    add("good" if not non_git else "warn", "Project Git repos", "all enabled projects are Git repos" if not non_git else ", ".join(non_git[:6]))
    add("good" if not changes else "warn", "Dirty worktrees", f"{len(changes)} enabled project(s) have local changes")

    commander.refresh_session_states()
    sessions = commander.sessions_data().get("sessions", {})
    running = [project_id for project_id, session in sessions.items() if session.get("state") == "running"]
    failed = [project_id for project_id, session in sessions.items() if session.get("state") in {"failed", "finished_unknown"}]
    add("good" if not failed else "warn", "Session failures", "none" if not failed else ", ".join(failed[:6]))
    add("good", "Running sessions", ", ".join(running) if running else "none")

    worst_disk = max((float(row.get("used_percent") or 0) for row in snapshot.get("disk", [])), default=0.0)
    add("good" if worst_disk < 85 else "warn" if worst_disk < 93 else "bad", "Disk pressure", f"{worst_disk}% used")
    return checks


def dashboard_recommendations(
    user_id: str,
    changes: list[dict[str, Any]],
    snapshot: dict[str, Any],
    sessions: dict[str, Any],
    openclaw: dict[str, Any] | None = None,
) -> list[str]:
    items: list[str] = []
    for disk in snapshot.get("disk", []):
        used = float(disk.get("used_percent") or 0)
        if used >= 90:
            items.append(f"Run /cleanup and free disk space on {disk.get('root')}: {used}% used, {disk.get('free_gb')} GB free.")
            break
    if not commander.clickup_settings_from_env().configured:
        items.append("Add CLICKUP_API_TOKEN and CLICKUP_WORKSPACE_ID so Commander can answer campaign/task questions from Telegram.")
    if not os.environ.get("GITHUB_TOKEN"):
        items.append("Add GITHUB_TOKEN so Commander can prepare PR and issue workflows later.")
    if not os.environ.get("WHATSAPP_ACCESS_TOKEN"):
        items.append("Add WhatsApp Cloud API keys when you want WhatsApp control after Telegram.")
    running = [project_id for project_id, session in sessions.items() if session.get("state") == "running"]
    if running:
        items.append("Check running Codex sessions: " + ", ".join(sorted(running)) + ".")
    review = [project_id for project_id, session in sessions.items() if session.get("state") in {"finished_unknown", "failed"}]
    if review:
        items.append("Review completed/uncertain Codex sessions: " + ", ".join(sorted(review)) + ".")
    if changes:
        formatted = ", ".join(f"{row['project']} ({row['changed_count']})" for row in changes[:5])
        items.append(f"Review local diffs before starting more work: {formatted}.")
    state = commander.user_state(user_id)
    if not state.get("heartbeat_enabled"):
        items.append("Enable Commander heartbeat with /heartbeat on 30 for proactive updates.")
    if not commander.get_project(str(state.get("active_project") or "")) and commander.assistant_mode(user_id) == "focused":
        items.append("Set a focused project with /focus <project>, or switch to /free for general computer work.")
    openclaw = openclaw or openclaw_dashboard_payload()
    if openclaw["state"] == "traces":
        items.append("OpenClaw traces exist but no trusted launcher is configured. Use /openclaw recover or set COMMANDER_OPENCLAW_LAUNCHER.")
    elif openclaw["state"] in {"startable", "launchable"}:
        items.append("OpenClaw has a launcher candidate. Use /openclaw start when you want to start it with approval.")
    return items[:8]


def dashboard_inbox(user_id: str, recommendations: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for approval in commander.pending_approvals():
        items.append(
            {
                "kind": "approval",
                "priority": "high",
                "title": f"Approve {approval['type']} for {approval['project']}",
                "detail": f"/approve {approval['project']} {approval['id']} or /cancel {approval['project']} {approval['id']}",
            }
        )
    for project_id, session in sorted(commander.sessions_data().get("sessions", {}).items()):
        state = str(session.get("state", "unknown"))
        if state == "running":
            items.append(
                {
                    "kind": "session",
                    "priority": "medium",
                    "title": f"{project_id} is running",
                    "detail": f"Task: {session.get('task', '-')}; use /watch {project_id}",
                }
            )
        elif state in {"failed", "finished_unknown"}:
            items.append(
                {
                    "kind": "session",
                    "priority": "high",
                    "title": f"{project_id} needs review",
                    "detail": f"State: {state}; use /watch {project_id}",
                }
            )
    for task in commander.visible_task_records(commander.tasks_data().get("tasks", []), limit=8):
        status = str(task.get("status", "queued"))
        if status in {"queued", "review", "failed"}:
            project = str(task.get("project", "-"))
            items.append(
                {
                    "kind": "task",
                    "priority": "medium" if status != "failed" else "high",
                    "title": f"{status}: {project}",
                    "detail": str(task.get("title", "-")),
                }
            )
    for recommendation in recommendations[:6]:
        items.append(
            {
                "kind": "recommendation",
                "priority": "low",
                "title": "Recommended action",
                "detail": recommendation,
            }
        )
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(items, key=lambda item: order.get(item["priority"], 9))[:12]


def dashboard_action_center(
    approvals: list[dict[str, Any]],
    sessions: dict[str, Any],
    tasks: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    limit: int = 12,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for approval in approvals:
        project = str(approval.get("project") or "-")
        items.append(
            {
                "kind": "approval",
                "priority": "high",
                "project": project,
                "title": f"Approval needed: {approval.get('type', 'action')}",
                "detail": str(approval.get("message") or f"Branch: {approval.get('branch') or '-'}"),
                "approval_id": str(approval.get("id") or ""),
                "actions": [
                    {"label": "Approve", "type": "approval", "action": "approve", "style": "primary"},
                    {"label": "Cancel", "type": "approval", "action": "cancel", "style": "danger"},
                ],
            }
        )
        seen_projects.add(project)
    for project_id, session in sorted(sessions.items()):
        if not isinstance(session, dict):
            continue
        state = str(session.get("state") or "unknown")
        if state == "running":
            items.append(
                {
                    "kind": "session",
                    "priority": "medium",
                    "project": project_id,
                    "title": f"{project_id} is running",
                    "detail": str(session.get("task") or "Managed Codex session is active."),
                    "actions": [
                        {"label": "Watch", "type": "work", "action": "watch"},
                        {"label": "Areas", "type": "work", "action": "changes"},
                        {"label": "Stop", "type": "work", "action": "stop", "style": "danger"},
                    ],
                }
            )
            seen_projects.add(project_id)
        elif state in {"failed", "finished_unknown", "stop_failed"}:
            items.append(
                {
                    "kind": "session",
                    "priority": "high",
                    "project": project_id,
                    "title": f"{project_id} needs review",
                    "detail": f"Session state: {state}",
                    "actions": [
                        {"label": "Watch", "type": "work", "action": "watch"},
                        {"label": "Plan", "type": "work", "action": "plan"},
                        {"label": "Areas", "type": "work", "action": "changes"},
                    ],
                }
            )
            seen_projects.add(project_id)
    for task in commander.visible_task_records(tasks, limit=10):
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "queued")
        if status not in {"queued", "review", "failed"}:
            continue
        task_id = str(task.get("id") or "")
        project = str(task.get("project") or "-")
        items.append(
            {
                "kind": "task",
                "priority": "medium" if status == "queued" else "high",
                "project": project,
                "title": f"{status}: {project}",
                "detail": str(task.get("title") or "-"),
                "task_id": task_id,
                "actions": [
                    {"label": "Start", "type": "task", "action": "start"} if status == "queued" else {"label": "Done", "type": "task", "action": "done"},
                    {"label": "Cancel", "type": "task", "action": "cancel", "style": "danger"},
                ],
            }
        )
        seen_projects.add(project)
    for change in changes:
        project = str(change.get("project") or "")
        if not project or project in seen_projects:
            continue
        items.append(
            {
                "kind": "changes",
                "priority": "low",
                "project": project,
                "title": f"Review local changes: {project}",
                "detail": f"{change.get('changed_count', 0)} changed; {change.get('areas') or 'areas unavailable'}",
                "actions": [
                    {"label": "Areas", "type": "work", "action": "changes"},
                    {"label": "Watch", "type": "work", "action": "watch"},
                ],
            }
        )
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (order.get(str(item.get("priority")), 9), str(item.get("project") or "")))
    return items[:limit]


def dashboard_recent_images(users: dict[str, dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for user_id, user in users.items():
        image = user.get("last_image") if isinstance(user.get("last_image"), dict) else {}
        if not image:
            continue
        suggested = image.get("suggested_commands") if isinstance(image.get("suggested_commands"), list) else []
        safe_commands: list[str] = []
        for command in suggested[:4]:
            if not isinstance(command, str) or not command.startswith("/"):
                continue
            try:
                safe_commands.append(commander.validate_generated_command(command))
            except Exception:
                continue
        suffix = str(user_id)[-4:] if user_id else "user"
        items.append(
            {
                "user": f"Telegram user ...{suffix}",
                "at": str(image.get("at") or ""),
                "kind": commander.safe_brief_text(image.get("kind") or "image"),
                "summary": commander.safe_brief_text(image.get("summary") or "-"),
                "visible_text": commander.safe_brief_text(image.get("visible_text") or "-"),
                "likely_intent": commander.safe_brief_text(image.get("likely_intent") or "-"),
                "risk": commander.safe_brief_text(image.get("risk") or "-"),
                "suggested_commands": safe_commands,
            }
        )
    items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return items[:limit]


def build_dashboard_payload() -> dict[str, Any]:
    commander.refresh_session_states()
    commander.sync_tasks_with_sessions()
    cfg = commander.projects_config()
    changes = commander.changed_project_details(limit=12, max_files=0)
    change_map = {str(row["project"]): row for row in changes}
    projects = {
        project_id: project_payload(project_id, project, change_map)
        for project_id, project in sorted(cfg.get("projects", {}).items())
    }
    state = commander.state_data()
    users = visible_user_states(state)
    tasks = commander.tasks_data().get("tasks", [])
    memories = commander.memory_data().get("memories", [])
    user_id = commander.active_user_id()
    snapshot = fast_system_snapshot([commander.BASE_DIR])
    sessions = sessions_payload().get("sessions", {})
    work_feed = commander.work_feed_items(user_id=user_id, limit=10, sessions=sessions, changes=changes, tasks=tasks)
    session_briefs = commander.session_brief_items(user_id=user_id, limit=8, sessions=sessions, changes=changes, tasks=tasks)
    openclaw = openclaw_dashboard_payload()
    recommendations = dashboard_recommendations(user_id, changes, snapshot, sessions, openclaw=openclaw)
    doctor = dashboard_doctor_checks(changes, snapshot, projects)
    approvals = commander.pending_approvals()
    return {
        "status": commander.command_status(),
        "doctor": {
            "score": commander.doctor_score(doctor),
            "checks": doctor,
        },
        "projects": projects,
        "sessions": sessions,
        "session_briefs": session_briefs,
        "recent_images": dashboard_recent_images(users),
        "work_feed": work_feed,
        "tasks": tasks[-60:],
        "memory_count": len(memories),
        "approvals": approvals,
        "action_center": dashboard_action_center(approvals, sessions, tasks, changes),
        "inbox": dashboard_inbox(user_id=user_id, recommendations=recommendations),
        "changes": changes,
        "recommendations": recommendations,
        "state": {
            "updated_at": state.get("updated_at"),
            "telegram_update_offset": state.get("telegram_update_offset"),
            "users": users,
        },
        "heartbeat": {
            user_id: {
                "enabled": user.get("heartbeat_enabled"),
                "interval_minutes": user.get("heartbeat_interval_minutes"),
                "quiet": commander.quiet_window_status(user),
                "next_at": user.get("heartbeat_next_at"),
                "active_project": user.get("active_project"),
                "assistant_mode": commander.assistant_mode(user_id),
            }
            for user_id, user in users.items()
        },
        "tools": {
            "apps": sorted(commander.app_catalog(commander.computer_tools_config())),
            "mcp": cached_mcp_summary(),
            "skills": commander.skill_catalog(limit=24),
            "plugins": commander.plugin_catalog(limit=24),
            "clickup_configured": commander.clickup_settings_from_env().configured,
        },
        "capabilities": capabilities_payload(str(openclaw.get("state") or "")),
        "openclaw": openclaw,
        "env": commander.env_readiness(),
        "system": snapshot,
        "logs": [
            {
                "name": item.name,
                "size": item.stat().st_size,
                "modified": item.stat().st_mtime,
            }
            for item in sorted(commander.LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
        ],
    }


def dashboard_payload() -> dict[str, Any]:
    now = time.monotonic()
    with DASHBOARD_CACHE_LOCK:
        payload = DASHBOARD_CACHE.get("value")
        at = float(DASHBOARD_CACHE.get("at") or 0.0)
    if isinstance(payload, dict) and at:
        stale = now - at >= DASHBOARD_CACHE_SECONDS
        if stale and now - at >= DASHBOARD_REQUEST_REFRESH_SECONDS:
            refresh_dashboard_cache_async(force=True)
        return attach_dashboard_cache_metadata(payload, now=now, stale=stale)
    refresh_dashboard_cache_async(force=True)
    return attach_dashboard_cache_metadata(
        fallback_dashboard_payload("Dashboard snapshot is warming up. Refresh again in a few seconds."),
        now=now,
        stale=True,
    )


def require_dashboard_token(headers: Any) -> tuple[bool, str]:
    token = os.environ.get("COMMANDER_DASHBOARD_TOKEN", "")
    if not token:
        return True, ""
    provided = headers.get("X-Commander-Token", "")
    if provided == token:
        return True, ""
    return False, "Invalid dashboard token."


def dashboard_approval_action(payload: dict[str, Any], action: str) -> tuple[dict[str, Any], int]:
    project_id = str(payload.get("project", "")).strip()
    approval_id = str(payload.get("approval_id", "")).strip()
    if not project_id or not approval_id:
        return {"ok": False, "error": "project and approval_id are required"}, 400
    if action == "approve":
        result = commander.execute_pending(project_id, approval_id)
    elif action == "cancel":
        result = commander.command_cancel(project_id, approval_id)
    else:
        return {"ok": False, "error": "Unknown approval action"}, 400
    return {"ok": True, "text": result}, 200


def dashboard_task_action(payload: dict[str, Any], action: str) -> tuple[dict[str, Any], int]:
    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        return {"ok": False, "error": "task_id is required"}, 400
    allowed = {"start": "start", "done": "done", "cancel": "cancel"}
    command_action = allowed.get(action)
    if not command_action:
        return {"ok": False, "error": "Unknown task action"}, 400
    result = commander.command_queue([command_action, task_id], user_id="dashboard")
    return {"ok": True, "text": result}, 200


def dashboard_project_read_action(project_id: str, action: str) -> tuple[dict[str, Any], int]:
    project_id = project_id.strip()
    if not project_id:
        return {"ok": False, "error": "project is required"}, 400
    if not commander.get_project(project_id):
        return {"ok": False, "error": "unknown or disabled project"}, 404
    if action == "watch":
        text = commander.command_watch(project_id, user_id="dashboard")
    elif action == "plan":
        text = commander.command_plan(project_id, user_id="dashboard")
    elif action == "feed":
        text = commander.command_feed([project_id], user_id="dashboard")
    elif action == "brief":
        text = commander.command_briefs([project_id], user_id="dashboard")
    elif action == "changes":
        rows = [
            row
            for row in commander.changed_project_details(limit=30, max_files=0)
            if str(row.get("project")) == project_id
        ]
        if not rows:
            text = f"No local Git changes found for {project_id}."
        else:
            row = rows[0]
            text = "\n".join(
                [
                    f"Changed work areas: {project_id}",
                    f"- Files changed: {row.get('changed_count', 0)}",
                    f"- Branch: {row.get('branch') or '-'}",
                    f"- Areas: {row.get('areas') or 'changed areas unavailable'}",
                    "",
                    "Technical filenames are hidden here. Use the Diff button only when you want code-level detail.",
                ]
            )
    else:
        return {"ok": False, "error": "Unknown work feed action"}, 400
    return {"ok": True, "project": project_id, "action": action, "text": text}, 200


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CommanderXDashboard/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self.send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self.send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if path == "/api/dashboard":
            self.send_json(dashboard_payload())
            return
        if path.startswith("/api/diff/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/diff/"))
            self.send_json({"project": project_id, "text": commander.command_diff(project_id)})
            return
        if path.startswith("/api/log/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/log/"))
            self.send_json({"project": project_id, "text": commander.command_log(project_id, 120)})
            return
        if path.startswith("/api/profile/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/profile/"))
            self.send_json({"project": project_id, "text": commander.command_profile(project_id, user_id="dashboard")})
            return
        if path.startswith("/api/evidence/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/evidence/"))
            self.send_json({"project": project_id, "text": commander.session_evidence(project_id)})
            return
        if path.startswith("/api/work/"):
            parts = path.removeprefix("/api/work/").split("/", 1)
            action = urllib.parse.unquote(parts[0]) if parts else ""
            project_id = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
            result, status = dashboard_project_read_action(project_id, action)
            self.send_json(result, status=status)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        ok, error = require_dashboard_token(self.headers)
        if not ok:
            self.send_json({"ok": False, "error": error}, status=403)
            return
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return

        if parsed.path == "/api/start":
            project_id = str(payload.get("project", "")).strip()
            task = str(payload.get("task", "")).strip()
            if not project_id or not task:
                self.send_json({"ok": False, "error": "project and task are required"}, status=400)
                return
            result = commander.start_codex(project_id, task, user_id="dashboard")
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "text": result})
            return
        if parsed.path == "/api/queue":
            project_id = str(payload.get("project", "")).strip()
            task = str(payload.get("task", "")).strip()
            if not project_id or not task:
                self.send_json({"ok": False, "error": "project and task are required"}, status=400)
                return
            created = commander.add_task(project_id, task, user_id="dashboard", status="queued", source="dashboard")
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "task": created})
            return
        if parsed.path == "/api/remember":
            note = str(payload.get("note", "")).strip()
            project_id = str(payload.get("project", "")).strip() or None
            scope = "project" if project_id else str(payload.get("scope", "global")).strip() or "global"
            if not note:
                self.send_json({"ok": False, "error": "note is required"}, status=400)
                return
            item = commander.add_memory(note, user_id="dashboard", scope=scope, project_id=project_id, source="dashboard")
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "memory": item})
            return
        if parsed.path == "/api/stop":
            project_id = str(payload.get("project", "")).strip()
            if not project_id:
                self.send_json({"ok": False, "error": "project is required"}, status=400)
                return
            result = commander.command_stop(project_id)
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "text": result})
            return
        if parsed.path == "/api/focus":
            project_id = str(payload.get("project", "")).strip()
            user_id = str(payload.get("user_id", "dashboard")).strip()
            result = commander.command_focus(project_id, user_id=user_id)
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "text": result})
            return
        if parsed.path == "/api/openclaw/recover":
            result = commander.command_openclaw(["recover"])
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "text": result})
            return
        if parsed.path == "/api/openclaw/start":
            result = commander.command_openclaw(["start"])
            invalidate_dashboard_cache()
            self.send_json({"ok": True, "text": result})
            return
        if parsed.path == "/api/approval/approve":
            result, status = dashboard_approval_action(payload, "approve")
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/approval/cancel":
            result, status = dashboard_approval_action(payload, "cancel")
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/task/start":
            result, status = dashboard_task_action(payload, "start")
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/task/done":
            result, status = dashboard_task_action(payload, "done")
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/task/cancel":
            result, status = dashboard_task_action(payload, "cancel")
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        self.send_json({"ok": False, "error": "Unknown endpoint"}, status=404)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404, "Not found")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{commander.utc_now()} dashboard {self.address_string()} {format % args}", flush=True)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Commander X dashboard listening on http://{HOST}:{PORT}", flush=True)
    start_dashboard_cache_workers()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Commander X dashboard.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
