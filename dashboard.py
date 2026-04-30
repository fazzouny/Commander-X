#!/usr/bin/env python3
"""Local dashboard for Commander X."""

from __future__ import annotations

import json
import os
import platform
import shutil
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import commander
from commanderx.system_info import disk_summary


HOST = os.environ.get("COMMANDER_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("COMMANDER_DASHBOARD_PORT", "8787"))
WEB_DIR = Path(__file__).resolve().parent / "web"
MCP_CACHE_SECONDS = int(os.environ.get("COMMANDER_DASHBOARD_MCP_CACHE_SECONDS", "300"))
MCP_CACHE: dict[str, Any] = {"value": None, "at": 0.0}
DASHBOARD_CACHE_SECONDS = int(os.environ.get("COMMANDER_DASHBOARD_CACHE_SECONDS", "8"))
DASHBOARD_CACHE: dict[str, Any] = {"value": None, "at": 0.0}


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def cached_mcp_summary() -> str:
    now = time.monotonic()
    if MCP_CACHE["value"] and now - float(MCP_CACHE["at"]) < MCP_CACHE_SECONDS:
        return str(MCP_CACHE["value"])
    value = commander.codex_mcp_summary()
    MCP_CACHE["value"] = value
    MCP_CACHE["at"] = now
    return value


def invalidate_dashboard_cache() -> None:
    DASHBOARD_CACHE["value"] = None
    DASHBOARD_CACHE["at"] = 0.0


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


def dashboard_recommendations(user_id: str, changes: list[dict[str, Any]], snapshot: dict[str, Any], sessions: dict[str, Any]) -> list[str]:
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
    recommendations = dashboard_recommendations(user_id, changes, snapshot, sessions)
    doctor = dashboard_doctor_checks(changes, snapshot, projects)
    return {
        "status": commander.command_status(),
        "doctor": {
            "score": commander.doctor_score(doctor),
            "checks": doctor,
        },
        "projects": projects,
        "sessions": sessions,
        "tasks": tasks[-60:],
        "memory_count": len(memories),
        "approvals": commander.pending_approvals(),
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
    if DASHBOARD_CACHE["value"] and now - float(DASHBOARD_CACHE["at"]) < DASHBOARD_CACHE_SECONDS:
        return DASHBOARD_CACHE["value"]
    payload = build_dashboard_payload()
    DASHBOARD_CACHE["value"] = payload
    DASHBOARD_CACHE["at"] = now
    return payload


def require_dashboard_token(headers: Any) -> tuple[bool, str]:
    token = os.environ.get("COMMANDER_DASHBOARD_TOKEN", "")
    if not token:
        return True, ""
    provided = headers.get("X-Commander-Token", "")
    if provided == token:
        return True, ""
    return False, "Invalid dashboard token."


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
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{commander.utc_now()} dashboard {self.address_string()} {format % args}", flush=True)


def main() -> int:
    commander.load_env_file()
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Commander X dashboard listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Commander X dashboard.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
