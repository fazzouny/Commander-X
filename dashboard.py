#!/usr/bin/env python3
"""Local dashboard for Commander X."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import datetime as dt
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
    is_stale = bool(stale) if stale is not None else bool(at and age >= DASHBOARD_REQUEST_REFRESH_SECONDS)
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
        "conversation": {"summary": message, "counts": {}, "items": []},
        "audit_trail": {"summary": message, "counts": {}, "items": []},
        "decision_suggestions": [],
        "mission_timeline": [],
        "session_evidence": [],
        "session_replay": [],
        "operator_playback": [],
        "project_completion": [],
        "owner_reviews": [],
        "autopilot": [],
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
    process_error = str(snapshot.get("process_error") or "")
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
        "launcher_error": launcher_error or process_error,
        "available_launchers": [
            {"label": str(item.get("label", "")), "path": commander.friendly_local_path(str(item.get("path", "")))}
            for item in snapshot["available_launchers"][:4]
        ],
        "processes": commander.summarize_process_rows(snapshot["process_rows"]),
        "repo_configured": bool(os.environ.get("COMMANDER_OPENCLAW_REPO_URL")),
        "web_research": os.environ.get("COMMANDER_OPENCLAW_WEB_RESEARCH", "true"),
    }


def safe_openclaw_dashboard_payload() -> dict[str, Any]:
    try:
        return openclaw_dashboard_payload()
    except Exception as exc:  # pragma: no cover - protects live dashboard from local process timeouts
        return {
            "state": "unavailable",
            "skills_count": 0,
            "plugin_cache": False,
            "legacy_checkout": False,
            "configured_launcher": "",
            "launcher_error": commander.safe_brief_text(commander.redact(str(exc))),
            "available_launchers": [],
            "processes": [],
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
            "/mission",
            "/evidence",
            "/replay",
            "/playback",
            "/objective",
            "/done",
            "/watch",
            "/queue",
            "/approvals",
            "/audit",
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
        visible = {user_id: user for user_id, user in users.items() if user_id in allowed}
    else:
        visible = {
            user_id: user
            for user_id, user in users.items()
            if user_id.isdigit() and (user.get("last_chat_id") or user.get("heartbeat_chat_id"))
        }
    dashboard_user = users.get("dashboard")
    if isinstance(dashboard_user, dict) and dashboard_user.get("last_image"):
        visible["dashboard"] = dashboard_user
    return visible


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
    openclaw = openclaw or safe_openclaw_dashboard_payload()
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


def dashboard_audit_trail(limit: int = 12) -> dict[str, Any]:
    events = commander.audit_data().get("events", [])
    clean: list[dict[str, Any]] = []
    for event in reversed(events[-limit:]):
        if not isinstance(event, dict):
            continue
        clean.append(
            {
                "at": str(event.get("at") or ""),
                "project": commander.audit_clean(event.get("project") or "-", limit=120),
                "approval_id": commander.audit_clean(event.get("approval_id") or "-", limit=80),
                "type": commander.audit_clean(event.get("type") or "action", limit=80),
                "status": commander.audit_clean(event.get("status") or "recorded", limit=80),
                "branch": commander.audit_clean(event.get("branch") or "-", limit=160),
                "summary": commander.audit_clean(event.get("summary") or "-", limit=500),
                "result": commander.audit_clean(event.get("result") or "", limit=500) if event.get("result") else "",
            }
        )
    counts: dict[str, int] = {}
    for item in clean:
        status = str(item.get("status") or "recorded")
        counts[status] = counts.get(status, 0) + 1
    if clean:
        latest = clean[0]
        summary = f"{len(clean)} recent approval events. Latest: {latest['status']} {latest['type']} for {latest['project']}."
    else:
        summary = "No approval audit events recorded yet."
    return {"summary": commander.safe_brief_text(summary), "counts": counts, "items": clean}


def dashboard_conversation_log_files(limit: int = 8) -> list[Path]:
    files: list[Path] = []
    current = commander.LOG_DIR / "commander-service.out.log"
    if current.exists():
        files.append(current)
    archive = commander.LOG_DIR / "archive"
    if archive.exists():
        files.extend(
            sorted(
                archive.glob("commander-service.out-*.log"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[: max(0, limit - len(files))]
        )
    return files[:limit]


def dashboard_conversation_item_from_line(line: str) -> dict[str, Any] | None:
    match = re.match(r"^(?P<at>\d{4}-\d{2}-\d{2}T\S+)\s+(?P<user>\S+)\s+(?P<body>.*)$", line.strip())
    if not match:
        return None
    raw_user = match.group("user")
    body = match.group("body").strip()
    if not raw_user.isdigit() or not body:
        return None
    kind = "user"
    direction = "User asked"
    actor = f"Telegram user ...{raw_user[-4:]}"
    status = "good"
    if body.startswith("[reply]"):
        kind = "reply"
        direction = "Commander replied"
        actor = "Commander X"
        body = body.removeprefix("[reply]").strip()
    elif body.startswith("[button]"):
        kind = "button"
        direction = "Button pressed"
        body = body.removeprefix("[button]").strip()
    elif body.startswith("[voice/audio message]"):
        kind = "voice"
        direction = "Voice note received"
        body = "Voice note received for transcription and routing."
    elif body.startswith("[image message]"):
        kind = "image"
        direction = "Image received"
        body = "Image received for safe visual context."
    elif body.startswith("[unsupported media]"):
        kind = "media"
        direction = "Unsupported media"
        body = "Unsupported media received."
        status = "warn"
    lower = body.lower()
    if any(token in lower for token in ("failed", "error", "blocked", "not wired", "approval", "access is denied")):
        status = "warn"
    return {
        "at": match.group("at"),
        "actor": actor,
        "kind": kind,
        "direction": direction,
        "summary": commander.safe_brief_text(commander.compact(body, limit=420)),
        "status": status,
    }


def dashboard_conversation_items_from_lines(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in lines:
        item = dashboard_conversation_item_from_line(line)
        if item:
            items.append(item)
            continue
        continuation = line.strip()
        if continuation and items and items[-1].get("kind") == "user":
            items[-1]["summary"] = commander.safe_brief_text(
                commander.compact(f"{items[-1].get('summary', '')} {continuation}", limit=420)
            )
    return items


def dashboard_conversation(limit: int = 12) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for path in dashboard_conversation_log_files():
        text = commander.read_recent_log_text(path, max_bytes=80_000)
        if text:
            items.extend(dashboard_conversation_items_from_lines(text.splitlines()))
    items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    latest = items[:limit]
    counts: dict[str, int] = {}
    for item in latest:
        kind = str(item.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    if latest:
        summary = f"{len(latest)} recent conversation events. Latest: {latest[0]['direction']} - {latest[0]['summary']}"
    else:
        summary = "No recent Telegram conversation events found in Commander logs."
    return {"summary": commander.safe_brief_text(summary), "counts": counts, "items": latest}


def dashboard_decision_suggestion_exists(note: str, memories: list[dict[str, Any]]) -> bool:
    key = re.sub(r"\W+", " ", note.lower()).strip()
    if not key:
        return True
    key_terms = [term for term in key.split() if len(term) >= 5][:8]
    for memory in memories:
        memory_note = re.sub(r"\W+", " ", str(memory.get("note") or "").lower()).strip()
        if not memory_note:
            continue
        if key in memory_note or memory_note in key:
            return True
        if key_terms and sum(1 for term in key_terms if term in memory_note) >= min(4, len(key_terms)):
            return True
    return False


def dashboard_decision_suggestions(
    conversation: dict[str, Any],
    memories: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    items = conversation.get("items") if isinstance(conversation.get("items"), list) else []
    candidates = [
        {
            "id": "hide-technical-names",
            "title": "Keep updates non-technical by default",
            "note": "Keep routine Telegram and heartbeat updates plain-English: hide folder paths and filenames unless I explicitly ask for technical details.",
            "terms": ("file names", "folder", "technical path", "technical file", "useless"),
            "scope": "user",
        },
        {
            "id": "image-context-first",
            "title": "Treat screenshots as context",
            "note": "When I send a screenshot or image, analyze it as context first and explain what is visible before asking me to describe it again.",
            "terms": ("image", "screenshot", "unsupported media", "image received"),
            "scope": "user",
        },
        {
            "id": "act-on-proceed",
            "title": "Proceed means execute",
            "note": "When I say proceed, make it work, or build it, take reasonable safe assumptions, execute the next useful step, verify it, and report briefly.",
            "terms": ("proceed", "make it work", "start working", "build"),
            "scope": "user",
        },
        {
            "id": "heartbeat-respect",
            "title": "Respect heartbeat preference",
            "note": "Respect heartbeat quiet/disabled state and avoid proactive Telegram messages unless a decision, approval, blocker, or important result needs attention.",
            "terms": ("heartbeat off", "heartbeat disabled", "quiet", "sleep"),
            "scope": "user",
        },
    ]
    suggestions: list[dict[str, Any]] = []
    for candidate in candidates:
        matches: list[dict[str, Any]] = []
        terms = tuple(str(term).lower() for term in candidate["terms"])
        for item in items:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(
                [
                    str(item.get("direction") or ""),
                    str(item.get("kind") or ""),
                    str(item.get("summary") or ""),
                ]
            ).lower()
            if any(term in haystack for term in terms):
                matches.append(item)
        if not matches:
            continue
        note = commander.safe_brief_text(candidate["note"])
        if dashboard_decision_suggestion_exists(note, memories):
            continue
        evidence = commander.safe_brief_text(str(matches[0].get("summary") or matches[0].get("direction") or "conversation signal"))
        suggestions.append(
            {
                "id": candidate["id"],
                "title": candidate["title"],
                "note": note,
                "scope": candidate["scope"],
                "evidence": evidence,
                "confidence": "medium" if len(matches) == 1 else "high",
                "matches": len(matches),
            }
        )
        if len(suggestions) >= limit:
            break
    return suggestions


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
        kind = commander.safe_brief_text(image.get("kind") or "image")
        user_label = "Dashboard upload" if str(user_id) == "dashboard" or kind == "dashboard upload" else f"Telegram user ...{str(user_id)[-4:] if user_id else 'user'}"
        items.append(
            {
                "user": user_label,
                "at": str(image.get("at") or ""),
                "kind": kind,
                "summary": commander.safe_brief_text(image.get("summary") or "-"),
                "visible_text": commander.safe_brief_text(image.get("visible_text") or "-"),
                "likely_intent": commander.safe_brief_text(image.get("likely_intent") or "-"),
                "risk": commander.safe_brief_text(image.get("risk") or "-"),
                "suggested_commands": safe_commands,
            }
        )
    items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return items[:limit]


def dashboard_blocker_from_session(session: dict[str, Any], mission_item: dict[str, Any] | None = None) -> str:
    signals = session.get("progress_signals") if isinstance(session.get("progress_signals"), list) else []
    for signal in reversed(signals):
        if isinstance(signal, dict) and str(signal.get("status") or "") == "warn":
            return f"{commander.safe_brief_text(signal.get('title'))}: {commander.safe_brief_text(signal.get('detail'))}"
    if mission_item and mission_item.get("blocker"):
        return commander.safe_brief_text(mission_item.get("blocker"))
    return "none reported"


def dashboard_session_evidence_cards(
    sessions: dict[str, Any],
    changes: list[dict[str, Any]],
    mission_timeline: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    change_map = {str(row.get("project")): row for row in changes}
    mission_map = {str(row.get("project")): row for row in mission_timeline}
    projects: list[str] = []
    for project_id in sessions:
        if project_id not in projects:
            projects.append(project_id)
    for item in mission_timeline:
        project_id = str(item.get("project") or "")
        if project_id and project_id not in projects:
            projects.append(project_id)

    cards: list[dict[str, Any]] = []
    for project_id in projects[:limit]:
        session = sessions.get(project_id) if isinstance(sessions.get(project_id), dict) else {}
        plan = session.get("work_plan") if isinstance(session.get("work_plan"), dict) else {}
        change = change_map.get(project_id, {})
        mission_item = mission_map.get(project_id)
        checks = commander.verification_results_as_checks(session.get("verification_results"))
        expected = plan.get("expected_checks") if isinstance(plan.get("expected_checks"), list) else []
        timeline = commander.timeline_lines(session, limit=5) if session else []
        cards.append(
            {
                "project": commander.audit_clean(project_id, limit=120),
                "state": commander.audit_clean(session.get("state") if session else "no session", limit=80),
                "process": "running" if str(session.get("state") or "") == "running" else "not running",
                "task": commander.audit_clean(session.get("task") if session else "No Commander session recorded.", limit=500),
                "task_id": commander.audit_clean(session.get("task_id") if session else "-", limit=100),
                "risk": commander.audit_clean(plan.get("risk") or "unknown", limit=80),
                "approach": [commander.audit_clean(item, limit=260) for item in (plan.get("approach") if isinstance(plan.get("approach"), list) else [])[:4]],
                "checks": [commander.audit_clean(item, limit=220) for item in checks[:6]],
                "expected_checks": [commander.audit_clean(item, limit=220) for item in expected[:5]],
                "changed_count": int(change.get("changed_count") or 0),
                "areas": commander.audit_clean(change.get("areas") or "no local changes tracked", limit=260),
                "branch": commander.audit_clean(session.get("branch") or "-", limit=160),
                "blocker": dashboard_blocker_from_session(session, mission_item),
                "timeline": [commander.audit_clean(line.lstrip("- "), limit=320) for line in timeline[:5]],
                "approvals": [],
                "log_age_minutes": mission_item.get("last_activity_minutes") if isinstance(mission_item, dict) else None,
            }
        )
    return cards


def dashboard_session_replay_cards(
    evidence_cards: list[dict[str, Any]],
    mission_timeline: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    mission_map = {str(row.get("project")): row for row in mission_timeline}
    cards: list[dict[str, Any]] = []
    for card in evidence_cards[:limit]:
        project_id = str(card.get("project") or "")
        mission_item = mission_map.get(project_id)
        checks = card.get("checks") if isinstance(card.get("checks"), list) else []
        timeline = card.get("timeline") if isinstance(card.get("timeline"), list) else []
        cards.append(
            {
                "project": commander.audit_clean(project_id, limit=120),
                "state": commander.audit_clean(card.get("state"), limit=80),
                "task": commander.audit_clean(card.get("task"), limit=420),
                "story": commander.audit_clean(commander.replay_story_from_card(card), limit=1000),
                "outcome": commander.audit_clean(commander.replay_outcome_from_card(card), limit=320),
                "work_areas": commander.audit_clean(card.get("areas"), limit=260),
                "changed_count": int(card.get("changed_count") or 0),
                "blocker": commander.audit_clean(card.get("blocker") or "none reported", limit=260),
                "checks": [commander.audit_clean(item, limit=180) for item in checks[:5]],
                "decisions": [],
                "timeline": [commander.audit_clean(item, limit=260) for item in timeline[:5]],
                "next_step": commander.replay_next_step_from_card(card, mission_item),
                "freshness": commander.audit_clean(mission_item.get("freshness") if mission_item else "unknown", limit=80),
                "last_activity_minutes": mission_item.get("last_activity_minutes") if mission_item else card.get("log_age_minutes"),
            }
        )
    return cards


def dashboard_operator_playback_cards(
    replay_cards: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    user_id: str | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    approvals_by_project: dict[str, list[dict[str, Any]]] = {}
    for item in approvals:
        project_id = str(item.get("project") or "")
        if project_id:
            approvals_by_project.setdefault(project_id, []).append(item)
    image_summary = "No recent image context."
    if user_id:
        image_summary = commander.audit_clean(commander.last_image_context_summary(user_id), limit=360)
    cards: list[dict[str, Any]] = []
    for replay in replay_cards[:limit]:
        project_id = str(replay.get("project") or "")
        project_approvals = approvals_by_project.get(project_id, [])
        checks = replay.get("checks") if isinstance(replay.get("checks"), list) else []
        card = {
            "project": commander.audit_clean(project_id, limit=120),
            "state": commander.audit_clean(replay.get("state"), limit=80),
            "confidence": commander.playback_confidence(replay, project_approvals),
            "story": commander.audit_clean(replay.get("story"), limit=900),
            "outcome": commander.audit_clean(replay.get("outcome"), limit=320),
            "blocker": commander.audit_clean(replay.get("blocker") or "none reported", limit=260),
            "work_areas": commander.audit_clean(replay.get("work_areas"), limit=260),
            "changed_count": int(replay.get("changed_count") or 0),
            "checks": [commander.audit_clean(item, limit=180) for item in checks[:4]],
            "decisions": [],
            "pending_approvals": project_approvals[:4],
            "visual_context": image_summary,
            "next_step": commander.audit_clean(replay.get("next_step"), limit=260),
            "primary_action": commander.playback_primary_action(replay, project_approvals),
            "commands": [f"/playback {project_id}", f"/watch {project_id}", f"/evidence {project_id}", f"/replay {project_id}"],
            "log_age_minutes": replay.get("last_activity_minutes"),
        }
        cards.append(card)
    return cards


def dashboard_project_completion_cards(operator_playback: list[dict[str, Any]], user_id: str | None = None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for playback in operator_playback:
        project_id = str(playback.get("project") or "")
        if not project_id:
            continue
        profile = commander.project_profile(project_id)
        criteria = commander.normalize_done_criteria(profile.get("done_criteria") or [])
        objective = commander.audit_clean(profile.get("objective") or "", limit=500) if profile.get("objective") else ""
        playback_checks = playback.get("checks") if isinstance(playback.get("checks"), list) else []
        approvals = playback.get("pending_approvals") if isinstance(playback.get("pending_approvals"), list) else []
        open_criteria = [item for item in criteria if item.get("status") == "open"]
        blocked_criteria = [item for item in criteria if item.get("status") == "blocked"]
        done_criteria = [item for item in criteria if item.get("status") in {"done", "waived"}]
        checks = commander.merge_verification_signals(playback_checks, commander.done_criteria_evidence_signals(done_criteria))
        confidence = str(playback.get("confidence") or "")
        state = str(playback.get("state") or "")
        changed_count = int(playback.get("changed_count") or 0)
        no_hazards = not approvals and confidence != "blocked" and state != "running"
        strict_done = bool(objective) and bool(criteria) and not open_criteria and not blocked_criteria and bool(checks) and no_hazards and changed_count == 0
        if strict_done:
            verdict = "100% done candidate"
        elif not objective:
            verdict = "objective missing"
        elif not criteria:
            verdict = "definition of done missing"
        elif state == "running":
            verdict = "in progress"
        elif confidence == "blocked" or blocked_criteria:
            verdict = "blocked"
        elif approvals:
            verdict = "waiting for approval"
        elif open_criteria:
            verdict = "not done"
        elif not checks:
            verdict = "needs verification"
        elif changed_count:
            verdict = "reviewable, not final"
        else:
            verdict = "done candidate"
        percent = (20 if objective else 0) + int((len(done_criteria) / len(criteria)) * 50) if criteria else (20 if objective else 0)
        if checks:
            percent += 15
        if no_hazards:
            percent += 15
        if not strict_done:
            percent = min(percent, 99)
        cards.append(
            {
                "project": commander.audit_clean(project_id, limit=120),
                "objective": objective,
                "verdict": verdict,
                "completion_percent": percent,
                "state": commander.audit_clean(state, limit=80),
                "confidence": commander.audit_clean(confidence, limit=120),
                "criteria": criteria,
                "done_criteria": len(done_criteria),
                "total_criteria": len(criteria),
                "checks": [commander.audit_clean(item, limit=180) for item in checks[:5]],
                "pending_approvals": approvals,
                "changed_count": changed_count,
                "blocker": commander.audit_clean(playback.get("blocker") or "none reported", limit=260),
            }
        )
    return cards


def dashboard_owner_review_packs(limit: int = 8) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in commander.saved_owner_review_packs(limit=limit):
        project = commander.safe_brief_text(record.get("project") or "-")
        items.append(
            {
                "project": project,
                "saved_at": commander.safe_brief_text(record.get("saved_at") or "-"),
                "size": commander.safe_brief_text(record.get("size") or "-"),
                "summary": f"{project} has a saved owner review pack ready for non-technical review.",
                "command": "/reviews",
            }
        )
    return items


def dashboard_autopilot_status(sessions: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    profiles = commander.profiles_data().get("profiles", {})
    now = dt.datetime.now(dt.timezone.utc)
    for project_id, profile in sorted(profiles.items()):
        if not isinstance(profile, dict):
            continue
        autopilot = profile.get("autopilot")
        if not isinstance(autopilot, dict):
            continue
        criteria = commander.normalize_done_criteria(profile.get("done_criteria") or [])
        open_criteria = [item for item in criteria if item.get("status") == "open"]
        done_criteria = [item for item in criteria if item.get("status") in {"done", "waived"}]
        blocked_criteria = [item for item in criteria if item.get("status") == "blocked"]
        session = sessions.get(project_id) if isinstance(sessions.get(project_id), dict) else {}
        pending = session.get("pending_actions") if isinstance(session.get("pending_actions"), dict) else {}
        enabled = bool(autopilot.get("enabled"))
        try:
            interval = max(1, int(autopilot.get("interval_minutes") or 5))
        except (TypeError, ValueError):
            interval = 5
        reason = "ready"
        can_start = enabled
        last_started = commander.parse_iso_datetime(str(autopilot.get("last_started_at") or ""))
        if not enabled:
            can_start = False
            reason = "off"
        elif session.get("state") == "running":
            can_start = False
            reason = "session already running"
        elif pending:
            can_start = False
            reason = "pending approval exists"
        elif blocked_criteria:
            can_start = False
            reason = "blocked criteria need review"
        elif not open_criteria:
            can_start = False
            reason = "no open criteria"
        elif last_started and last_started + dt.timedelta(minutes=interval) > now:
            can_start = False
            reason = "cooldown active"
        next_action = commander.autopilot_next_action(project_id, reason, can_start=can_start)
        next_criterion = open_criteria[0] if open_criteria else {}
        rows.append(
            {
                "project": commander.safe_brief_text(commander.project_label(project_id, include_id=False)),
                "project_id": commander.safe_brief_text(project_id),
                "enabled": enabled,
                "can_start": can_start,
                "reason": commander.safe_brief_text(reason),
                "interval_minutes": interval,
                "done_criteria": len(done_criteria),
                "total_criteria": len(criteria),
                "open_criteria": len(open_criteria),
                "blocked_criteria": len(blocked_criteria),
                "next_criterion": commander.safe_brief_text(next_criterion.get("text") or reason),
                "next_action": commander.safe_brief_text(next_action),
                "last_started_at": commander.safe_brief_text(autopilot.get("last_started_at") or "-"),
                "command": f"/autopilot {'run' if can_start else 'status'}",
            }
        )
    return rows[:limit]


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
    mission_timeline = commander.mission_timeline_items(user_id=user_id, limit=10, sessions=sessions, changes=changes, tasks=tasks)
    approvals = commander.pending_approvals()
    session_evidence = dashboard_session_evidence_cards(sessions, changes, mission_timeline, limit=8)
    session_replay = dashboard_session_replay_cards(session_evidence, mission_timeline, limit=6)
    operator_playback = dashboard_operator_playback_cards(session_replay, approvals, user_id=user_id, limit=6)
    project_completion = dashboard_project_completion_cards(operator_playback, user_id=user_id)
    openclaw = safe_openclaw_dashboard_payload()
    recommendations = dashboard_recommendations(user_id, changes, snapshot, sessions, openclaw=openclaw)
    doctor = dashboard_doctor_checks(changes, snapshot, projects)
    conversation = dashboard_conversation()
    audit_trail = dashboard_audit_trail()
    return {
        "status": commander.command_status(),
        "doctor": {
            "score": commander.doctor_score(doctor),
            "checks": doctor,
        },
        "projects": projects,
        "sessions": sessions,
        "conversation": conversation,
        "audit_trail": audit_trail,
        "decision_suggestions": dashboard_decision_suggestions(conversation, memories),
        "mission_timeline": mission_timeline,
        "session_evidence": session_evidence,
        "session_replay": session_replay,
        "operator_playback": operator_playback,
        "project_completion": project_completion,
        "owner_reviews": dashboard_owner_review_packs(limit=8),
        "autopilot": dashboard_autopilot_status(sessions, limit=10),
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
        needs_refresh = now - at >= DASHBOARD_REQUEST_REFRESH_SECONDS
        if needs_refresh:
            refresh_dashboard_cache_async(force=True)
        return attach_dashboard_cache_metadata(payload, now=now, stale=needs_refresh)
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


def dashboard_image_analyze_action(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    data_url = str(payload.get("data_url") or "").strip()
    caption = commander.compact(str(payload.get("caption") or ""), limit=1000)
    if not data_url:
        return {"ok": False, "error": "image data is required"}, 400
    try:
        mime_type, raw = commander.parse_image_data_url(data_url)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}, 400
    commander.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = commander.utc_now().replace(":", "").replace("-", "").replace("+", "Z")
    image_path = commander.IMAGE_DIR / f"dashboard-{stamp}{commander.image_suffix_for_mime_type(mime_type)}"
    image_path.write_bytes(raw)
    try:
        analysis = commander.openai_image_analysis(image_path, caption=caption, telegram_mime_type=mime_type)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}, 502
    user_id = commander.active_user_id()
    record = commander.save_user_image_context(user_id, "dashboard upload", analysis, caption=caption)
    image = dashboard_recent_images({user_id: {"last_image": record}}, limit=1)
    return {
        "ok": True,
        "text": commander.format_image_analysis(analysis, caption=caption),
        "image": image[0] if image else {},
    }, 200


def dashboard_decision_memory_action(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    note = commander.safe_brief_text(commander.compact(str(payload.get("note") or ""), limit=900))
    scope = str(payload.get("scope") or "user").strip().lower()
    if scope not in {"user", "global"}:
        return {"ok": False, "error": "scope must be user or global"}, 400
    if len(note) < 20:
        return {"ok": False, "error": "decision memory note is too short"}, 400
    memories = commander.memory_data().get("memories", [])
    if dashboard_decision_suggestion_exists(note, memories):
        return {"ok": True, "duplicate": True, "text": "A similar Commander memory already exists."}, 200
    item = commander.add_memory(note, user_id=commander.active_user_id(), scope=scope, source="dashboard-decision")
    return {"ok": True, "memory": item, "text": f"Saved decision memory {item['id']}."}, 200


def dashboard_report_action(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    save = bool(payload.get("save"))
    snapshot = dashboard_payload()
    markdown = commander.format_operator_report(snapshot, source="dashboard")
    response: dict[str, Any] = {"ok": True, "text": markdown, "saved": False}
    if save:
        path = commander.save_operator_report(markdown)
        response.update(
            {
                "saved": True,
                "report_id": path.stem.removeprefix("commander-x-report-"),
                "text": "Saved Commander X operator report.\n\n" + markdown,
            }
        )
    return response, 200


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
    elif action == "evidence":
        text = commander.session_evidence(project_id)
    elif action == "replay":
        text = commander.session_replay(project_id)
    elif action == "playback":
        text = commander.operator_playback(project_id, user_id="dashboard")
    elif action == "done":
        text = commander.project_completion(project_id, user_id="dashboard")
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
        if path.startswith("/api/replay/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/replay/"))
            self.send_json({"project": project_id, "text": commander.session_replay(project_id)})
            return
        if path.startswith("/api/playback/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/playback/"))
            self.send_json({"project": project_id, "text": commander.operator_playback(project_id, user_id="dashboard")})
            return
        if path.startswith("/api/done/"):
            project_id = urllib.parse.unquote(path.removeprefix("/api/done/"))
            self.send_json({"project": project_id, "text": commander.project_completion(project_id, user_id="dashboard")})
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
        if parsed.path == "/api/image/analyze" and length > int(commander.max_openai_image_bytes() * 1.45) + 20_000:
            self.send_json({"ok": False, "error": "Image upload is too large."}, status=413)
            return
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
        if parsed.path == "/api/image/analyze":
            result, status = dashboard_image_analyze_action(payload)
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/decision-memory":
            result, status = dashboard_decision_memory_action(payload)
            invalidate_dashboard_cache()
            self.send_json(result, status=status)
            return
        if parsed.path == "/api/report":
            result, status = dashboard_report_action(payload)
            self.send_json(result, status=status)
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
