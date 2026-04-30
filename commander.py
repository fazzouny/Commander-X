#!/usr/bin/env python3
"""
Codex Commander: Telegram-controlled local orchestration for Codex CLI.

This service intentionally exposes a small command surface:
- registered projects only
- no raw shell commands from chat
- approval-gated commit and push
- local logs with basic secret redaction
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from commanderx.browser import format_inspection as format_browser_inspection
from commanderx.browser import inspect_url as browser_inspect_url
from commanderx.clickup_api import filtered_team_tasks as clickup_filtered_team_tasks
from commanderx.clickup_api import filter_tasks as clickup_filter_tasks
from commanderx.clickup_api import format_tasks as clickup_format_tasks
from commanderx.clickup_api import settings_from_env as clickup_settings_from_env
from commanderx.cleanup import cleanup_scan
from commanderx.cleanup import format_cleanup_scan
from commanderx.memory import relevant_memories as rank_relevant_memories
from commanderx.computer import app_catalog
from commanderx.computer import capture_screenshot
from commanderx.computer import open_app as computer_open_app
from commanderx.computer import open_url as computer_open_url
from commanderx.computer import press_volume_key
from commanderx.computer import process_lines as computer_process_lines
from commanderx.gitops import changed_files as git_changed_files
from commanderx.gitops import current_branch as git_current_branch
from commanderx.gitops import git_args as build_git_args
from commanderx.gitops import git_run as run_git
from commanderx.gitops import git_safe_path as safe_git_path
from commanderx.gitops import has_changes as git_has_changes
from commanderx.gitops import is_git_repo as path_is_git_repo
from commanderx.processes import codex_command_args as build_codex_command_args
from commanderx.processes import pid_running as is_process_running
from commanderx.processes import run_command as run_process_command
from commanderx.processes import stop_pid as stop_process_tree
from commanderx.projects import build_project_alias_map, mentioned_projects as detect_mentioned_projects, resolve_project
from commanderx.storage import read_json_file, write_json_file
from commanderx.system_info import format_system_snapshot
from commanderx.system_info import system_snapshot
from commanderx.tasks import sync_task_records, visible_task_records
from commanderx.telegram import TelegramTransport
from commanderx.text import parse_message as parse_commander_message, slugify as slugify_text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
ALLOWLIST_FILE = BASE_DIR / "allowlist.json"
PROJECTS_FILE = BASE_DIR / "projects.json"
SESSIONS_FILE = BASE_DIR / "sessions.json"
STATE_FILE = BASE_DIR / "commander_state.json"
MEMORY_FILE = BASE_DIR / "memory.json"
TASKS_FILE = BASE_DIR / "tasks.json"
PROFILES_FILE = BASE_DIR / "project_profiles.json"
COMPUTER_TOOLS_FILE = BASE_DIR / "computer_tools.json"
SYSTEM_PROMPT_FILE = BASE_DIR / "system_prompt.md"
LOG_DIR = BASE_DIR / "logs"
VOICE_DIR = LOG_DIR / "voice"
SCREENSHOT_DIR = LOG_DIR / "screenshots"
ENV_FILE = BASE_DIR / ".env"
SESSION_LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen[str]] = {}

WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

MAX_TELEGRAM_MESSAGE = 3900
DEFAULT_LOG_LINES = 80
MAX_OPENAI_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_HEARTBEAT_MINUTES = 30
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "08:00"
NL_ALLOWED_COMMANDS = {
    "/whoami",
    "/help",
    "/projects",
    "/status",
    "/doctor",
    "/inbox",
    "/approvals",
    "/changes",
    "/watch",
    "/timeline",
    "/plan",
    "/brief",
    "/morning",
    "/next",
    "/updates",
    "/mode",
    "/free",
    "/tools",
    "/computer",
    "/browser",
    "/clickup",
    "/skills",
    "/plugins",
    "/mcp",
    "/system",
    "/env",
    "/clipboard",
    "/cleanup",
    "/open",
    "/file",
    "/volume",
    "/start",
    "/log",
    "/diff",
    "/stop",
    "/commit",
    "/push",
    "/approve",
    "/cancel",
    "/check",
    "/focus",
    "/context",
    "/heartbeat",
    "/remember",
    "/memory",
    "/forget",
    "/profile",
    "/queue",
}
TELEGRAM_COMMANDS = [
    ("help", "Show Commander commands"),
    ("projects", "List registered projects"),
    ("status", "Show active Codex sessions"),
    ("doctor", "Run Commander health check"),
    ("inbox", "Show decision inbox"),
    ("approvals", "List pending approvals"),
    ("changes", "Show changed projects"),
    ("watch", "Show live project work view"),
    ("timeline", "Show session timeline"),
    ("plan", "Show pre-work plan"),
    ("brief", "Summarize active project"),
    ("morning", "Show wake-up operating brief"),
    ("next", "Show recommended next actions"),
    ("updates", "Show latest project updates"),
    ("mode", "Show or change assistant mode"),
    ("free", "Use free assistant mode"),
    ("tools", "Show Commander tool access"),
    ("computer", "Run safe computer tools"),
    ("browser", "Inspect or open websites"),
    ("clickup", "Show ClickUp bridge status/tasks"),
    ("skills", "List local Commander-visible skills"),
    ("plugins", "List local plugin cache"),
    ("mcp", "Research or prepare MCP setup"),
    ("system", "Show device/system status"),
    ("env", "Show setup readiness"),
    ("clipboard", "Use guarded clipboard tools"),
    ("cleanup", "Show safe disk cleanup plan"),
    ("open", "Open URL or allowlisted app"),
    ("file", "Read project file"),
    ("volume", "Control system volume"),
    ("focus", "Set the active project"),
    ("context", "Show active project context"),
    ("start", "Start Codex on a project task"),
    ("log", "Show latest Codex output"),
    ("diff", "Show Git diff summary"),
    ("stop", "Stop a running session"),
    ("commit", "Prepare commit for approval"),
    ("push", "Prepare push for approval"),
    ("approve", "Approve a pending action"),
    ("cancel", "Cancel a pending action"),
    ("heartbeat", "Manage automatic status updates"),
    ("remember", "Save a Commander memory"),
    ("memory", "Show saved Commander memories"),
    ("forget", "Delete a Commander memory"),
    ("profile", "Show project profile"),
    ("queue", "Show or manage task queue"),
    ("check", "Check Commander config"),
    ("whoami", "Show your Telegram user ID"),
]
DEFAULT_BUTTON_ROWS = [
    [("Status", "cmd:/status"), ("Projects", "cmd:/projects")],
    [("Mode", "cmd:/mode"), ("Free Mode", "cmd:/free")],
    [("Morning", "cmd:/morning"), ("Next", "cmd:/next")],
    [("Inbox", "cmd:/inbox"), ("Approvals", "cmd:/approvals")],
    [("Changes", "cmd:/changes"), ("Log", "cmd:/log"), ("Diff", "cmd:/diff")],
    [("Context", "cmd:/context")],
    [("Queue", "cmd:/queue"), ("Profile", "cmd:/profile")],
    [("Heartbeat Now", "cmd:/heartbeat now"), ("Heartbeat Off", "cmd:/heartbeat off")],
]

SENSITIVE_FILE_PATTERNS = (
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def local_now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def load_env_file(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    return read_json_file(path, default)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_file(path, payload)


def redact(text: str) -> str:
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b", "[REDACTED_TELEGRAM_TOKEN]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{20,}\b", "[REDACTED_OPENAI_KEY]", redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password|private[_-]?key|access[_-]?token|refresh[_-]?token)\b"
        r"(\s*[:=]\s*)"
        r"([^\s'\"`]+)",
        r"\1\2[REDACTED]",
        redacted,
    )
    return redacted


def compact(text: str, limit: int = MAX_TELEGRAM_MESSAGE) -> str:
    text = redact(text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 120].rstrip() + "\n\n...[truncated by Commander]"


def split_for_telegram(text: str) -> list[str]:
    text = redact(text)
    if len(text) <= MAX_TELEGRAM_MESSAGE:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        chunk = remaining[:MAX_TELEGRAM_MESSAGE]
        split_at = chunk.rfind("\n")
        if split_at > 1000:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk) :].lstrip()
    return chunks


def telegram_html(text: str) -> str:
    text = redact(text or "").strip() or "(empty)"
    lines = text.splitlines()
    formatted: list[str] = []
    first_text_line_done = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted.append("")
            continue
        if stripped.startswith("Command: "):
            command = stripped.removeprefix("Command: ").strip()
            formatted.append(f"Command: <code>{html.escape(command)}</code>")
            continue
        if stripped.startswith("/") and len(stripped.split()) <= 6:
            formatted.append(f"<code>{html.escape(stripped)}</code>")
            continue
        escaped = html.escape(line)
        if not first_text_line_done:
            formatted.append(f"<b>{escaped}</b>")
            first_text_line_done = True
        else:
            formatted.append(escaped)
    return "\n".join(formatted)


def should_attach_buttons(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped) > 1800:
        return False
    first_line = stripped.splitlines()[0].lower()
    noisy_prefixes = (
        "transcript:",
        "context file:",
        "=== ",
        "provider:",
    )
    if first_line.startswith(noisy_prefixes):
        return False
    if "Context file:" in stripped and len(stripped) > 900:
        return False
    return True


def telegram_button(label: str, command: str) -> dict[str, str]:
    callback_data = f"cmd:{command}"
    return {"text": label[:48], "callback_data": callback_data}


def dedupe_button_rows(rows: list[list[dict[str, str]]]) -> list[list[dict[str, str]]]:
    seen: set[str] = set()
    clean_rows: list[list[dict[str, str]]] = []
    for row in rows:
        clean_row: list[dict[str, str]] = []
        for button in row:
            data = button.get("callback_data", "")
            if not data or data in seen:
                continue
            seen.add(data)
            clean_row.append(button)
        if clean_row:
            clean_rows.append(clean_row)
    return clean_rows


def response_project_hint(text: str) -> str | None:
    stripped = (text or "").strip()
    patterns = [
        r"^Started\s+([A-Za-z0-9_.-]+)\.",
        r"^Watch:\s+([A-Za-z0-9_.-]+)",
        r"^Plan before work\s*\nProject:\s+([A-Za-z0-9_.-]+)",
        r"^(?:Commit|Push) prepared for\s+([A-Za-z0-9_.-]+)",
        r"^Active project set to\s+([A-Za-z0-9_.-]+)\.",
    ]
    for pattern in patterns:
        match = re.search(pattern, stripped, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1)
    return None


def response_pending_hint(text: str) -> tuple[str, str, str] | None:
    stripped = (text or "").strip()
    match = re.search(
        r"^(Commit|Push) prepared for\s+([A-Za-z0-9_.-]+).*?Pending approval ID:\s*([A-Za-z0-9]+)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).lower(), match.group(2), match.group(3)
    match = re.search(
        r"^MCP install prepared for\s+([A-Za-z0-9_.-]+).*?Pending approval ID:\s*([A-Za-z0-9]+)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return "mcp add", "commander", match.group(2)
    return None


def contextual_button_rows(text: str, user_id: str | None = None) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    pending = response_pending_hint(text)
    if pending:
        action_type, project_id, pending_id = pending
        rows.append(
            [
                telegram_button(f"Approve {action_type}", f"/approve {project_id} {pending_id}"),
                telegram_button("Cancel", f"/cancel {project_id} {pending_id}"),
            ]
        )
        if project_id == "commander":
            rows.append([telegram_button("MCP status", "/mcp"), telegram_button("MCP help", "/mcp help")])
        else:
            rows.append(
                [
                    telegram_button("Show diff", f"/diff {project_id}"),
                    telegram_button("Watch", f"/watch {project_id}"),
                ]
            )

    project_id = response_project_hint(text)
    if project_id:
        rows.append(
            [
                telegram_button("Watch", f"/watch {project_id}"),
                telegram_button("Plan", f"/plan {project_id}"),
            ]
        )
        session = sessions_data().get("sessions", {}).get(project_id, {})
        if session.get("state") == "running":
            rows.append([telegram_button("Stop session", f"/stop {project_id}")])

    if user_id:
        state = user_state(user_id)
        active = state.get("active_project")
        session = sessions_data().get("sessions", {}).get(str(active), {}) if active else {}
        pending_actions = session.get("pending_actions") or {}
        for pending_id, action in list(pending_actions.items())[:2]:
            action_type = str(action.get("type", "action"))
            rows.append(
                [
                    telegram_button(f"Approve {action_type}", f"/approve {active} {pending_id}"),
                    telegram_button("Cancel", f"/cancel {active} {pending_id}"),
                ]
            )
        if active and session.get("state") == "running":
            rows.append(
                [
                    telegram_button(f"Watch {active}", f"/watch {active}"),
                    telegram_button("Stop", f"/stop {active}"),
                ]
            )
    return dedupe_button_rows(rows)


def parse_env_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]


def env_bool(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def allowlist_config() -> dict[str, Any]:
    return read_json(
        ALLOWLIST_FILE,
        {
            "allowed_telegram_user_ids": [],
            "allow_whoami_for_unauthorized": True,
            "dangerous_commands_require_manual_approval": True,
        },
    )


def projects_config() -> dict[str, Any]:
    return read_json(PROJECTS_FILE, {"projects": {}, "codex": {}})


def computer_tools_config() -> dict[str, Any]:
    return read_json(COMPUTER_TOOLS_FILE, {"apps": {}, "safe_roots": []})


def env_readiness() -> dict[str, dict[str, str]]:
    groups: dict[str, list[str]] = {
        "core": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "OPENAI_API_KEY"],
        "models": ["OPENAI_COMMAND_MODEL", "OPENAI_TRANSCRIBE_MODEL", "OPENAI_VOICE_MODEL", "OPENAI_VOICE"],
        "dashboard": ["COMMANDER_DASHBOARD_HOST", "COMMANDER_DASHBOARD_PORT", "COMMANDER_DASHBOARD_TOKEN"],
        "clickup": ["CLICKUP_API_TOKEN", "CLICKUP_WORKSPACE_ID"],
        "meta_ads": ["META_APP_ID", "META_APP_SECRET", "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_BUSINESS_ID"],
        "whatsapp": ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET"],
        "github": ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_DEFAULT_REPO"],
        "browser": ["COMMANDER_BROWSER_HEADLESS", "COMMANDER_BROWSER_TIMEOUT_SECONDS"],
        "cloud": ["NETLIFY_AUTH_TOKEN", "NETLIFY_SITE_ID", "RENDER_API_KEY", "CLOUDINARY_URL"],
        "data": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ACCESS_TOKEN"],
        "business": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_ACCOUNT_ID"],
        "google": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID", "GMAIL_LABEL_PREFIX"],
        "microsoft": ["MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_REFRESH_TOKEN"],
        "notifications": ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "DISCORD_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "PUSHOVER_USER_KEY", "PUSHOVER_API_TOKEN"],
        "safety": ["COMMANDER_REQUIRE_APPROVAL_FOR_PUSH", "COMMANDER_REQUIRE_APPROVAL_FOR_DEPLOY", "COMMANDER_REQUIRE_APPROVAL_FOR_EXTERNAL_SEND", "COMMANDER_REQUIRE_APPROVAL_FOR_PACKAGE_INSTALL"],
    }
    readiness: dict[str, dict[str, str]] = {}
    for group, keys in groups.items():
        readiness[group] = {key: ("configured" if os.environ.get(key) else "missing") for key in keys}
    return readiness


def openai_config() -> dict[str, str]:
    return {
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "command_model": os.environ.get("OPENAI_COMMAND_MODEL", "gpt-4o-mini"),
        "transcribe_model": os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        "transcribe_prompt": os.environ.get(
            "OPENAI_TRANSCRIBE_PROMPT",
            (
                "Transcribe this as a command for Codex Commander. Preserve project IDs, "
                "approval IDs, branch names, command words, and technical terms exactly."
            ),
        ),
    }


def sessions_data() -> dict[str, Any]:
    return read_json(SESSIONS_FILE, {"sessions": {}})


def save_sessions(data: dict[str, Any]) -> None:
    write_json(SESSIONS_FILE, data)


def memory_data() -> dict[str, Any]:
    return read_json(MEMORY_FILE, {"memories": []})


def save_memory(data: dict[str, Any]) -> None:
    write_json(MEMORY_FILE, data)


def tasks_data() -> dict[str, Any]:
    return read_json(TASKS_FILE, {"tasks": []})


def save_tasks(data: dict[str, Any]) -> None:
    write_json(TASKS_FILE, data)


def profiles_data() -> dict[str, Any]:
    return read_json(PROFILES_FILE, {"profiles": {}})


def save_profiles(data: dict[str, Any]) -> None:
    write_json(PROFILES_FILE, data)


def state_data() -> dict[str, Any]:
    return read_json(STATE_FILE, {"users": {}, "updated_at": None})


def save_state(data: dict[str, Any]) -> None:
    data["updated_at"] = utc_now()
    write_json(STATE_FILE, data)


def telegram_update_offset() -> int | None:
    offset = state_data().get("telegram_update_offset")
    return int(offset) if offset is not None else None


def save_telegram_update_offset(offset: int) -> None:
    data = state_data()
    data["telegram_update_offset"] = offset
    save_state(data)


def user_state(user_id: str) -> dict[str, Any]:
    data = state_data()
    return data.setdefault("users", {}).setdefault(str(user_id), {})


def update_user_state(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    data = state_data()
    user = data.setdefault("users", {}).setdefault(str(user_id), {})
    user.update(updates)
    save_state(data)
    return user


def assistant_mode(user_id: str) -> str:
    mode = str(user_state(user_id).get("assistant_mode") or "").lower()
    if mode in {"focused", "free"}:
        return mode
    return "focused" if user_state(user_id).get("active_project") else "free"


def allows_active_project_fallback(user_id: str) -> bool:
    return assistant_mode(user_id) == "focused"


def allowed_user_ids() -> set[str]:
    cfg = allowlist_config()
    ids = {str(item) for item in cfg.get("allowed_telegram_user_ids", [])}
    ids.update(parse_env_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS")))
    return {item for item in ids if item}


def is_authorized(user_id: str) -> bool:
    return str(user_id) in allowed_user_ids()


def get_project(project_id: str) -> dict[str, Any] | None:
    projects = projects_config().get("projects", {})
    project = projects.get(project_id)
    if not project or not project.get("allowed", False):
        return None
    return project


def project_path(project: dict[str, Any]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(project["path"])))).resolve()


def project_alias_map() -> dict[str, str]:
    return build_project_alias_map(projects_config().get("projects", {}))


def mentioned_projects(text: str) -> list[str]:
    return detect_mentioned_projects(projects_config().get("projects", {}), text)


def resolve_project_id(value: str | None, user_id: str | None = None) -> str | None:
    active = None
    if user_id:
        active = user_state(user_id).get("active_project")
    return resolve_project(projects_config().get("projects", {}), value, active_project=str(active) if active else None)


def read_context_file(path: Path, limit: int = 1400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    text = redact(text)
    if len(text) > limit:
        return text[:limit].rstrip() + "\n...[truncated]"
    return text


def project_context_summary(project_id: str, max_files: int = 4, show_path: bool = False) -> str:
    project = get_project(project_id)
    if not project:
        return f"No enabled project found for {project_id}."
    path = project_path(project)
    lines = [
        f"Project: {project_id}",
        f"Allowed: {project.get('allowed', False)}",
    ]
    if show_path:
        lines.insert(1, f"Path: {path}")
    if path.exists() and is_git_repo(path):
        lines.append(f"Git branch: {current_branch(path)}")
        changed = changed_files(path)
        lines.append(f"Changed files: {len(changed)}")
        if changed[:12]:
            lines.extend(f"- {item}" for item in changed[:12])
    candidates = [Path(item) for item in project.get("context_files", [])]
    if not candidates:
        candidates = [Path("AGENTS.md"), Path("README.md"), Path("START_HERE.md"), Path("QUICK_START.md")]
    added = 0
    for rel in candidates:
        full = (path / rel).resolve()
        try:
            full.relative_to(path)
        except ValueError:
            continue
        if not full.exists() or not full.is_file():
            continue
        content = read_context_file(full)
        if not content:
            continue
        lines.extend(["", f"Context file: {rel}", content])
        added += 1
        if added >= max_files:
            break
    if not show_path:
        lines.append("")
        lines.append("Use /context full to show local paths.")
    return compact("\n".join(lines), limit=8000)


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return run_process_command(args, cwd=cwd, timeout=timeout)


def codex_command_args(args: list[str]) -> list[str]:
    return build_codex_command_args(args)


def git_safe_path(path: Path) -> str:
    return safe_git_path(path)


def git_args(path: Path, *args: str) -> list[str]:
    return build_git_args(path, *args)


def git_run(path: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return run_git(path, *args, timeout=timeout)


def is_git_repo(path: Path) -> bool:
    return path_is_git_repo(path)


def current_branch(path: Path) -> str:
    return git_current_branch(path)


def changed_files(path: Path) -> list[str]:
    return git_changed_files(path)


def has_changes(path: Path) -> bool:
    return git_has_changes(path)


def sensitive_changed_files(path: Path) -> list[str]:
    return sensitive_file_paths(changed_files(path))


def sensitive_file_paths(files: list[str]) -> list[str]:
    sensitive: list[str] = []
    for rel in files:
        lower = rel.lower().replace("\\", "/")
        basename = lower.rsplit("/", 1)[-1]
        if basename in SENSITIVE_FILE_PATTERNS or basename.startswith(".env."):
            sensitive.append(rel)
            continue
        if lower.endswith(SENSITIVE_SUFFIXES):
            sensitive.append(rel)
    return sensitive


def slugify(value: str, limit: int = 44) -> str:
    return slugify_text(value, limit=limit)


def create_task_branch(project_id: str, path: Path, task: str) -> tuple[str | None, str | None]:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"codex/{project_id}/{slugify(task)}-{timestamp}"
    result = git_run(path, "checkout", "-b", branch, timeout=45)
    if result.returncode != 0:
        return None, redact((result.stderr or result.stdout).strip())
    return branch, None


def pid_running(pid: int) -> bool:
    return is_process_running(pid)


def stop_pid(pid: int) -> tuple[bool, str]:
    ok, output = stop_process_tree(pid)
    return ok, redact(output)


def read_package_json(path: Path) -> dict[str, Any]:
    package_file = path / "package.json"
    if not package_file.exists():
        return {}
    try:
        return json.loads(package_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def detect_stack(path: Path, package: dict[str, Any]) -> list[str]:
    stack: list[str] = []
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    deps = {
        **dependencies,
        **dev_dependencies,
    }
    markers = {
        "next": "Next.js",
        "react": "React",
        "vite": "Vite",
        "typescript": "TypeScript",
        "tailwindcss": "Tailwind CSS",
        "@supabase/supabase-js": "Supabase",
        "playwright": "Playwright",
        "vitest": "Vitest",
        "jest": "Jest",
        "eslint": "ESLint",
    }
    for dep, label in markers.items():
        if dep in deps and label not in stack:
            stack.append(label)
    if (path / "pyproject.toml").exists() and "Python" not in stack:
        stack.append("Python")
    if (path / "supabase").exists() and "Supabase" not in stack:
        stack.append("Supabase")
    if (path / "netlify.toml").exists():
        stack.append("Netlify")
    if (path / "render.yaml").exists():
        stack.append("Render")
    return stack


def project_profile(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    if not project:
        return {"project": project_id, "error": "Unknown or disabled project."}
    path = project_path(project)
    package = read_package_json(path)
    scripts_obj = package.get("scripts")
    scripts = scripts_obj if isinstance(scripts_obj, dict) else {}
    stored = profiles_data().get("profiles", {}).get(project_id, {})
    context_files = project.get("context_files") or ["AGENTS.md", "README.md", "START_HERE.md", "QUICK_START.md"]
    verification = stored.get("verification_commands") or []
    if not verification:
        for name in ("typecheck", "lint", "test", "build", "smoke"):
            if name in scripts:
                verification.append(f"npm run {name}")
    is_repo = path.exists() and is_git_repo(path)
    changed = changed_files(path) if is_repo else []
    return {
        "project": project_id,
        "allowed": bool(project.get("allowed", False)),
        "exists": path.exists(),
        "git": is_repo,
        "branch": current_branch(path) if is_repo else None,
        "changed_count": len(changed),
        "changed_preview": changed[:10],
        "stack": stored.get("stack") or detect_stack(path, package),
        "scripts": {key: scripts[key] for key in sorted(scripts) if key in {"dev", "build", "test", "lint", "typecheck", "smoke", "preview"}},
        "verification_commands": verification,
        "context_files": [str(item) for item in context_files],
        "notes": stored.get("notes", []),
        "risk_rules": stored.get("risk_rules", []),
    }


def format_project_profile(profile: dict[str, Any]) -> str:
    if profile.get("error"):
        return str(profile["error"])
    lines = [
        f"Project profile: {profile['project']}",
        f"Status: {'enabled' if profile.get('allowed') else 'disabled'}, branch {profile.get('branch') or '-'}",
        f"Changed files: {profile.get('changed_count', 0)}",
        "Stack: " + (", ".join(profile.get("stack") or []) or "not detected"),
    ]
    scripts = profile.get("scripts") or {}
    if scripts:
        lines.extend(["", "Useful scripts:"])
        lines.extend(f"- {name}: {command}" for name, command in scripts.items())
    verification = profile.get("verification_commands") or []
    if verification:
        lines.extend(["", "Verification Commander should prefer:"])
        lines.extend(f"- {command}" for command in verification)
    notes = profile.get("notes") or []
    if notes:
        lines.extend(["", "Project notes:"])
        lines.extend(f"- {note}" for note in notes[:8])
    risk_rules = profile.get("risk_rules") or []
    if risk_rules:
        lines.extend(["", "Risk rules:"])
        lines.extend(f"- {rule}" for rule in risk_rules[:8])
    return compact("\n".join(lines), limit=3000)


def add_memory(note: str, user_id: str, scope: str = "user", project_id: str | None = None, source: str = "telegram") -> dict[str, Any]:
    data = memory_data()
    item = {
        "id": secrets.token_hex(3),
        "scope": scope,
        "project": project_id,
        "user_id": str(user_id),
        "note": note.strip(),
        "source": source,
        "created_at": utc_now(),
    }
    data.setdefault("memories", []).append(item)
    save_memory(data)
    return item


def relevant_memories(user_id: str, project_id: str | None = None, query: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    return rank_relevant_memories(memory_data().get("memories", []), user_id=user_id, project_id=project_id, query=query, limit=limit)


def memory_brief(user_id: str, project_id: str | None = None, query: str | None = None, limit: int = 8) -> str:
    memories = relevant_memories(user_id, project_id=project_id, query=query, limit=limit)
    if not memories:
        return "No relevant Commander memories."
    return "\n".join(f"- [{item['id']}] {item.get('note', '')}" for item in memories)


def add_task(project_id: str, title: str, user_id: str = "system", status: str = "queued", source: str = "telegram") -> dict[str, Any]:
    data = tasks_data()
    task = {
        "id": secrets.token_hex(3),
        "project": project_id,
        "title": title.strip(),
        "status": status,
        "source": source,
        "user_id": str(user_id),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    data.setdefault("tasks", []).append(task)
    save_tasks(data)
    return task


def update_task(task_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    data = tasks_data()
    for task in data.get("tasks", []):
        if task.get("id") == task_id:
            task.update(updates)
            task["updated_at"] = utc_now()
            save_tasks(data)
            return task
    return None


def get_task(task_id: str) -> dict[str, Any] | None:
    for task in tasks_data().get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


def sync_tasks_with_sessions() -> None:
    data = tasks_data()
    sessions = sessions_data().get("sessions", {})
    if sync_task_records(data.get("tasks", []), sessions, updated_at=utc_now()):
        save_tasks(data)


def tasks_summary(limit: int = 12) -> str:
    refresh_session_states()
    sync_tasks_with_sessions()
    tasks = tasks_data().get("tasks", [])
    if not tasks:
        return "Task queue is empty."
    visible = visible_task_records(tasks, limit=limit)
    lines = ["Commander task queue:"]
    for task in visible:
        lines.append(f"- [{task.get('id')}] {task.get('project')} - {task.get('status')}: {task.get('title')}")
    return compact("\n".join(lines))


def session_evidence(project_id: str) -> str:
    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(project_id) or {}
    project = get_project(project_id)
    path = project_path(project) if project else None
    lines = [f"Evidence for {project_id}:"]
    if session:
        pid = int(session.get("pid", 0) or 0)
        lines.append(f"- Session state: {session.get('state', 'unknown')}")
        lines.append(f"- PID: {pid or '-'} ({'running' if pid_running(pid) else 'not running'})")
        lines.append(f"- Branch: {session.get('branch') or '-'}")
        lines.append(f"- Task ID: {session.get('task_id') or '-'}")
        log_file = Path(str(session.get("log_file", "")))
        if log_file.exists():
            age = dt.datetime.now().timestamp() - log_file.stat().st_mtime
            lines.append(f"- Log: {log_file.name}, updated {int(age // 60)} min ago")
        else:
            lines.append("- Log: missing")
    else:
        lines.append("- No Commander session recorded.")
    if path and path.exists() and is_git_repo(path):
        changed = changed_files(path)
        lines.append(f"- Git branch: {current_branch(path)}")
        lines.append(f"- Changed files: {len(changed)}")
        if changed[:6]:
            lines.extend(f"  - {item}" for item in changed[:6])
    return "\n".join(lines)


def load_system_prompt() -> str:
    if not SYSTEM_PROMPT_FILE.exists():
        return "You are Codex Commander. Work only inside the selected project."
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()


def work_plan_risk(task: str) -> str:
    lowered = task.lower()
    high_patterns = r"\b(push|deploy|publish|launch|delete|remove|drop|production|prod|billing|payment|stripe|credential|secret|env|api key|spend|campaign budget|send message|email users?)\b"
    if re.search(high_patterns, lowered):
        return "high"
    medium_patterns = r"\b(fix|audit|security|auth|database|migration|refactor|install|package|dependency|webhook|integration|onboarding|checkout)\b"
    if re.search(medium_patterns, lowered):
        return "medium"
    return "low"


def build_work_plan(project_id: str, task: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or project_profile(project_id)
    task_text = task.strip() or "Work on the requested project task."
    risk = work_plan_risk(task_text)
    lowered = task_text.lower()
    approach = ["Understand the current state and identify the real blocker."]
    if re.search(r"\b(summary|summarize|brief|updates?|campaign|linkedin|meta|ads)\b", lowered):
        approach.extend(
            [
                "Read the latest project signals and changed work areas.",
                "Separate completed work, blockers, and decisions needed from the operator.",
                "Return a non-technical operating summary first.",
            ]
        )
    elif re.search(r"\b(audit|security|auth|permission|privacy|secret|credential)\b", lowered):
        approach.extend(
            [
                "Inspect the relevant workflow and trust boundaries.",
                "Make the smallest behavior-preserving hardening change.",
                "Call out any remaining risk that needs explicit approval.",
            ]
        )
    elif re.search(r"\b(fix|bug|broken|issue|error|not working|production ready)\b", lowered):
        approach.extend(
            [
                "Reproduce or narrow the likely failure path.",
                "Apply the smallest useful fix.",
                "Verify the fix with the narrowest reliable check.",
            ]
        )
    else:
        approach.extend(
            [
                "Inspect the project context before editing.",
                "Make only changes that directly support the request.",
                "Report what changed, what was verified, and what remains.",
            ]
        )

    verification = list(profile.get("verification_commands") or [])[:5]
    if not verification:
        stack = " ".join(str(item) for item in profile.get("stack", []))
        if "Python" in stack:
            verification = ["python -m py_compile <changed Python files>", "python -m unittest discover"]
        elif "Node" in stack or "React" in stack or "Vite" in stack or "Next.js" in stack:
            verification = ["npm test", "npm run build"]
        else:
            verification = ["Review local changes", "Run the narrowest available project check"]

    approval_boundaries = [
        "Commit, push, deploy, publish, launch, or spend money only after explicit approval.",
        "Ask before package installs, credential/env changes, destructive actions, or external messages.",
    ]
    if risk == "high":
        approval_boundaries.insert(0, "Treat this as high-impact until proven otherwise.")

    return {
        "project": project_id,
        "goal": task_text,
        "risk": risk,
        "approach": approach[:4],
        "expected_checks": verification,
        "approval_boundaries": approval_boundaries,
    }


def format_work_plan(plan: dict[str, Any]) -> str:
    lines = [
        "Plan before work",
        f"Project: {plan.get('project', '-')}",
        f"Goal: {plan.get('goal', '-')}",
        f"Risk: {plan.get('risk', 'unknown')}",
        "",
        "Approach:",
    ]
    for index, item in enumerate(plan.get("approach") or [], start=1):
        lines.append(f"{index}. {item}")
    checks = plan.get("expected_checks") or []
    if checks:
        lines.extend(["", "Expected checks:"])
        lines.extend(f"- {item}" for item in checks[:5])
    boundaries = plan.get("approval_boundaries") or []
    if boundaries:
        lines.extend(["", "Approval boundaries:"])
        lines.extend(f"- {item}" for item in boundaries[:4])
    return "\n".join(lines)


def build_codex_prompt(
    project_id: str,
    path: Path,
    task: str,
    user_id: str = "system",
    profile: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
) -> str:
    profile = profile or project_profile(project_id)
    plan = plan or build_work_plan(project_id, task, profile)
    return f"""{load_system_prompt()}

Commander dispatch:
- Project ID: {project_id}
- Project path: {path}
- Requested task: {task}

Commander work plan:
{format_work_plan(plan)}

Project profile:
{format_project_profile(profile)}

Commander memory:
{memory_brief(user_id, project_id=project_id, query=task, limit=8)}

Project context:
{project_context_summary(project_id, max_files=3, show_path=True)}

Execution constraints:
- Stay inside this project unless the task explicitly requires reading a registered dependency path.
- Do not push, publish, deploy, spend money, send external messages, change credentials, or modify billing/legal/identity settings.
- Do not delete production data.
- Do not reveal secrets or print .env values.
- Return evidence before saying work is complete: files changed, checks run, current blocker, and next step.
- If you need a high-impact action, stop and state exactly what approval is needed.
- Before finishing, run the narrowest useful verification available and report any checks you could not run.
"""


def timeline_event(phase: str, title: str, detail: str = "", status: str = "done") -> dict[str, str]:
    return {
        "at": utc_now(),
        "phase": phase,
        "title": title,
        "detail": detail,
        "status": status,
    }


def append_timeline_event(session: dict[str, Any], phase: str, title: str, detail: str = "", status: str = "done") -> None:
    timeline = session.setdefault("timeline", [])
    if isinstance(timeline, list) and timeline:
        last = timeline[-1]
        if isinstance(last, dict) and last.get("phase") == phase and last.get("title") == title:
            last["at"] = utc_now()
            last["detail"] = detail
            last["status"] = status
            session["current_phase"] = phase
            return
    if not isinstance(timeline, list):
        timeline = []
        session["timeline"] = timeline
    timeline.append(timeline_event(phase, title, detail=detail, status=status))
    session["timeline"] = timeline[-20:]
    session["current_phase"] = phase


def initial_session_timeline(
    task: str,
    branch_created: bool = False,
    branch_warning: str | None = None,
    plan: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    risk = str((plan or {}).get("risk") or "unknown")
    events = [
        timeline_event("requested", "Task received", task.strip() or "Project work requested."),
        timeline_event("planned", "Plan prepared", f"Risk: {risk}. Commander will inspect, make a narrow change, verify, then report."),
    ]
    if branch_created:
        events.append(timeline_event("prepared", "Work branch prepared", "A separate task branch was created for safer review."))
    elif branch_warning:
        events.append(timeline_event("prepared", "Branch preparation needs review", branch_warning, status="warn"))
    events.append(timeline_event("running", "Codex session launched", "Commander is watching the managed local Codex run.", status="active"))
    return events


def timeline_lines(session: dict[str, Any], limit: int = 7) -> list[str]:
    timeline = session.get("timeline") if isinstance(session, dict) else []
    if not isinstance(timeline, list) or not timeline:
        state = str(session.get("state", "unknown")) if isinstance(session, dict) else "unknown"
        return [f"- {state}: timeline not available for this older session."]
    rows: list[str] = []
    for item in timeline[-limit:]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("phase") or "Update")
        detail = str(item.get("detail") or "").strip()
        status = str(item.get("status") or "done")
        prefix = "In progress" if status == "active" else "Needs review" if status == "warn" else "Done"
        rows.append(f"- {prefix}: {title}" + (f" - {detail}" if detail else ""))
    return rows or ["- Timeline not available for this older session."]


def watch_process(project_id: str, proc: subprocess.Popen[str]) -> None:
    exit_code = proc.wait()
    task_id = None
    with SESSION_LOCK:
        data = sessions_data()
        session = data.get("sessions", {}).get(project_id)
        if session and int(session.get("pid", -1)) == proc.pid:
            session["state"] = "completed" if exit_code == 0 else "failed"
            session["exit_code"] = exit_code
            session["completed_at"] = utc_now()
            session["updated_at"] = utc_now()
            append_timeline_event(
                session,
                "completed" if exit_code == 0 else "failed",
                "Codex run finished" if exit_code == 0 else "Codex run failed",
                "Review the summary and checks before committing." if exit_code == 0 else "Use /log for raw details or /watch for the safe summary.",
                status="done" if exit_code == 0 else "warn",
            )
            task_id = session.get("task_id")
            save_sessions(data)
    if task_id:
        update_task(str(task_id), {"status": "done" if exit_code == 0 else "failed", "completed_at": utc_now(), "exit_code": exit_code})
    PROCESSES.pop(project_id, None)


def refresh_session_states() -> None:
    with SESSION_LOCK:
        data = sessions_data()
        changed = False
        for project_id, session in data.get("sessions", {}).items():
            if session.get("state") == "running":
                pid = int(session.get("pid", 0))
                if not pid_running(pid):
                    session["state"] = "finished_unknown"
                    session["updated_at"] = utc_now()
                    append_timeline_event(
                        session,
                        "review",
                        "Session stopped outside Commander",
                        "Commander could not confirm the final Codex exit status. Review before continuing.",
                        status="warn",
                    )
                    if session.get("task_id"):
                        update_task(str(session["task_id"]), {"status": "review"})
                    changed = True
        if changed:
            save_sessions(data)


def start_codex(project_id: str, task: str, user_id: str = "system", task_id: str | None = None) -> str:
    project = get_project(project_id)
    if not project:
        return f"Unknown or disabled project: {project_id}"

    path = project_path(project)
    if not path.exists():
        return f"Project path does not exist: {path}"

    refresh_session_states()
    data = sessions_data()
    existing = data.get("sessions", {}).get(project_id)
    if existing and existing.get("state") == "running":
        return f"{project_id} already has a running session (PID {existing.get('pid')}). Use /log or /stop first."

    if shutil.which("codex") is None:
        return "Codex CLI was not found in PATH."

    branch = None
    branch_warning = None
    if project.get("create_branch_on_start", True) and is_git_repo(path):
        branch, branch_warning = create_task_branch(project_id, path, task)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = LOG_DIR / f"{project_id}-{timestamp}.log"
    last_message_file = LOG_DIR / f"{project_id}-{timestamp}-last-message.txt"

    codex_cfg = projects_config().get("codex", {})
    sandbox = codex_cfg.get("sandbox", "workspace-write")
    extra_args = [str(item) for item in codex_cfg.get("extra_args", [])]
    profile = project_profile(project_id)
    plan = build_work_plan(project_id, task, profile)
    prompt = build_codex_prompt(project_id, path, task, user_id=user_id, profile=profile, plan=plan)
    args = codex_command_args([
        "exec",
        "-C",
        str(path),
        "-s",
        sandbox,
        "--skip-git-repo-check",
        "--color",
        "never",
        "-o",
        str(last_message_file),
        *extra_args,
        "-",
    ])

    with log_file.open("a", encoding="utf-8", errors="replace") as log_handle:
        log_handle.write(f"=== Codex Commander session started {utc_now()} ===\n")
        log_handle.write(f"Project: {project_id}\nPath: {path}\nBranch: {branch or current_branch(path) if is_git_repo(path) else 'no-git'}\n")
        log_handle.write(f"Task: {task}\n\n")
        log_handle.flush()
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(path),
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=WINDOWS_NEW_PROCESS_GROUP,
            )
        except OSError as exc:
            log_handle.write(f"\nFailed to launch Codex CLI: {exc}\n")
            if branch and is_git_repo(path) and not has_changes(path):
                original = project.get("default_branch") or "main"
                git_run(path, "checkout", str(original), timeout=45)
                git_run(path, "branch", "-D", branch, timeout=45)
                log_handle.write(f"Rolled back empty task branch: {branch}\n")
            raise RuntimeError(f"Failed to launch Codex CLI: {exc}") from exc
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

    if task_id and get_task(task_id):
        update_task(str(task_id), {"status": "running", "session_project": project_id, "started_at": utc_now()})
    else:
        task_record = add_task(project_id, task, user_id=user_id, status="running", source="start")
        task_id = str(task_record["id"])

    session = {
        "project": project_id,
        "state": "running",
        "pid": proc.pid,
        "task": task,
        "task_id": task_id,
        "path": str(path),
        "branch": branch or (current_branch(path) if is_git_repo(path) else None),
        "log_file": str(log_file),
        "last_message_file": str(last_message_file),
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "pending_actions": {},
        "work_plan": plan,
        "current_phase": "running",
        "timeline": initial_session_timeline(task, branch_created=bool(branch), branch_warning=branch_warning, plan=plan),
    }
    data.setdefault("sessions", {})[project_id] = session
    save_sessions(data)
    PROCESSES[project_id] = proc
    threading.Thread(target=watch_process, args=(project_id, proc), daemon=True).start()

    msg = f"Started {project_id}."
    msg += "\n\n" + format_work_plan(plan)
    msg += f"\n\nSession: running, PID {proc.pid}"
    if branch:
        msg += "\nWork branch: created for this task."
    if branch_warning:
        msg += f"\nBranch warning: {branch_warning}"
    msg += f"\nTask ID: {task_id}"
    msg += "\nLog: saved locally for Commander."
    msg += "\n\nUse /watch " + project_id + " for the human-readable live view."
    msg += "\nUse /log " + project_id + " only if you want raw Codex output."
    return msg


def tail_file(path: Path, lines: int = DEFAULT_LOG_LINES) -> str:
    if not path.exists():
        return f"Log file does not exist: {path}"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Could not read log: {exc}"
    return "\n".join(content[-lines:])


def command_status() -> str:
    refresh_session_states()
    data = sessions_data()
    sessions = data.get("sessions", {})
    if not sessions:
        return "No Codex Commander sessions yet."
    lines = ["Active Codex sessions:"]
    for project_id in sorted(sessions):
        session = sessions[project_id]
        state = session.get("state", "unknown")
        pid = session.get("pid", "-")
        phase = session.get("current_phase") or state
        updated = session.get("updated_at") or session.get("started_at") or "-"
        pending = session.get("pending_actions") or {}
        pending_note = f", pending approvals: {len(pending)}" if pending else ""
        lines.append(f"- {project_id}: {state}, phase {phase}, PID {pid}, updated {updated}{pending_note}")
    return "\n".join(lines)


def project_from_assistant_query(project_id: str | None, user_id: str, query: str | None = None) -> str | None:
    resolved = resolve_project_id(project_id, user_id=None)
    if resolved:
        return resolved
    if query:
        projects = mentioned_projects(query)
        if len(projects) == 1:
            return projects[0]
        if re.search(r"\b(campaign|campaigns|linkedin|meta ads|ads|paid social)\b", query, flags=re.IGNORECASE):
            if get_project("taalam-campaigns"):
                return "taalam-campaigns"
    if allows_active_project_fallback(user_id):
        return resolve_project_id(None, user_id=user_id)
    return None


def local_timestamp_from_path(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "-"


def is_sensitive_path(path: Path) -> bool:
    lower = path.name.lower()
    if lower in SENSITIVE_FILE_PATTERNS or lower.startswith(".env"):
        return True
    return lower.endswith(SENSITIVE_SUFFIXES)


def markdown_signal(path: Path, max_lines: int = 90) -> tuple[str, list[str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:max_lines]
    except OSError:
        return path.stem.replace("_", " ").replace("-", " "), []
    title = path.stem.replace("_", " ").replace("-", " ")
    signals: list[str] = []
    signal_pattern = re.compile(
        r"\b(status|update|result|recommend|next|blocker|decision|budget|launch|rejected|approved|performance|objective|todo|action)\b",
        flags=re.IGNORECASE,
    )
    for raw in lines:
        line = raw.strip().strip("|").strip()
        if not line:
            continue
        if line.startswith("#") and title == path.stem.replace("_", " ").replace("-", " "):
            title = line.lstrip("#").strip() or title
            continue
        cleaned = re.sub(r"^[\-*>\d.\s\[\]xX]+", "", line).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if 20 <= len(cleaned) <= 180 and signal_pattern.search(cleaned):
            signals.append(cleaned)
        if len(signals) >= 2:
            break
    return title, signals


def recent_project_documents(project_id: str, limit: int = 7) -> list[dict[str, Any]]:
    project = get_project(project_id)
    if not project:
        return []
    root = project_path(project)
    if not root.exists():
        return []
    docs: list[Path] = []
    skip_parts = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
    for path in root.rglob("*.md"):
        if any(part in skip_parts for part in path.parts):
            continue
        if is_sensitive_path(path):
            continue
        docs.append(path)
    docs.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    result: list[dict[str, Any]] = []
    for path in docs[:limit]:
        title, signals = markdown_signal(path)
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        result.append(
            {
                "path": rel,
                "title": title,
                "modified": local_timestamp_from_path(path),
                "signals": signals,
            }
        )
    return result


def project_session_line(project_id: str) -> str:
    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(project_id)
    if not session:
        return "No Commander-started Codex session is running or recorded for this project."
    pid = int(session.get("pid", 0) or 0)
    running = "running" if pid_running(pid) else "not running"
    return f"{session.get('state', 'unknown')} - PID {pid or '-'} ({running}) - task: {session.get('task', '-')}"


def project_queue_lines(project_id: str, limit: int = 4) -> list[str]:
    sync_tasks_with_sessions()
    tasks = [
        task
        for task in tasks_data().get("tasks", [])
        if task.get("project") == project_id and task.get("status") in {"queued", "running", "review", "failed", "stopped"}
    ]
    if not tasks:
        return ["No active queued Commander tasks for this project."]
    return [f"[{task.get('id')}] {task.get('status')}: {task.get('title')}" for task in tasks[-limit:]]


def command_updates(project_id: str | None, user_id: str, query: str | None = None) -> str:
    resolved = project_from_assistant_query(project_id, user_id=user_id, query=query)
    if not resolved or not get_project(resolved):
        return command_overview(user_id=user_id)

    project = get_project(resolved)
    assert project is not None
    path = project_path(project)
    changed = changed_files(path) if path.exists() and is_git_repo(path) else []
    recent_docs = recent_project_documents(resolved)

    lines = [f"Latest updates: {resolved}"]
    lines.append("")
    lines.append("Codex:")
    lines.append(f"- {project_session_line(resolved)}")
    lines.append("")
    lines.append("Queue:")
    lines.extend(f"- {line}" for line in project_queue_lines(resolved))
    lines.append("")
    lines.append("Local work:")
    if path.exists() and is_git_repo(path):
        lines.append(f"- Branch: {current_branch(path)}")
        lines.append(f"- Changed files: {len(changed)}")
        areas = change_bucket_summary(changed)
        if areas:
            lines.append(f"- Areas: {areas}")
            lines.append("- Technical filenames are hidden by default. Use /diff only when you want code-level detail.")
    else:
        lines.append("- Not a Git repository or path is missing.")

    if recent_docs:
        lines.append("")
        lines.append("Recent project docs:")
        for item in recent_docs[:5]:
            lines.append(f"- {item['title']} ({item['modified']})")
            for signal in item.get("signals", [])[:1]:
                lines.append(f"  {signal}")

    lines.append("")
    if not sessions_data().get("sessions", {}).get(resolved):
        lines.append("Read: there are local project updates, but Commander X is not currently running a Codex session for this project.")
    elif changed:
        lines.append("Read: there is active/local work to review. Use /log, /diff, or the dashboard for evidence.")
    else:
        lines.append("Read: no local Git changes were detected for this project.")
    return compact("\n".join(lines), limit=3600)


def command_overview(user_id: str) -> str:
    refresh_session_states()
    sync_tasks_with_sessions()
    lines = [f"Commander overview - {assistant_mode(user_id)} mode"]
    active = user_state(user_id).get("active_project")
    lines.append(f"Focused project: {active or 'none'}")
    lines.append("")
    lines.append("Sessions:")
    lines.append(command_status())
    lines.append("")
    lines.append("Projects with local changes:")
    any_changed = False
    for project_id, project in sorted(projects_config().get("projects", {}).items()):
        if not project.get("allowed", False):
            continue
        path = project_path(project)
        if not path.exists() or not is_git_repo(path):
            continue
        changed = changed_files(path)
        if not changed:
            continue
        any_changed = True
        lines.append(f"- {project_id}: {len(changed)} changed files, branch {current_branch(path)}")
    if not any_changed:
        lines.append("- No changed files detected in enabled Git projects.")
    lines.append("")
    lines.append("Queue:")
    lines.append(tasks_summary(limit=8))
    return compact("\n".join(lines), limit=3600)


def command_brief(project_id: str | None, user_id: str) -> str:
    return command_updates(project_id, user_id=user_id)




def command_projects(show_details: bool = False) -> str:
    cfg = projects_config()
    projects = cfg.get("projects", {})
    if not projects:
        return "No projects registered in projects.json."
    lines = ["Registered projects:"]
    for project_id, project in sorted(projects.items()):
        path = project_path(project)
        allowed = "enabled" if project.get("allowed", False) else "disabled"
        exists = "exists" if path.exists() else "missing"
        git = "git" if path.exists() and is_git_repo(path) else "no-git"
        branch = current_branch(path) if path.exists() and is_git_repo(path) else "-"
        if show_details:
            lines.append(f"- {project_id}: {allowed}, {exists}, {git}, branch {branch}, path {path}")
        else:
            lines.append(f"- {project_id}: {allowed}, branch {branch}")
    if not show_details:
        lines.append("")
        lines.append("Use /projects full to show local paths.")
    return "\n".join(lines)


def command_focus(project_id: str | None, user_id: str, chat_id: int | str | None = None) -> str:
    resolved = resolve_project_id(project_id, user_id=None)
    if not resolved or not get_project(resolved):
        return "Unknown or disabled project. Use /projects to see valid project IDs."
    updates: dict[str, Any] = {
        "active_project": resolved,
        "active_project_set_at": utc_now(),
        "assistant_mode": "focused",
        "assistant_mode_updated_at": utc_now(),
    }
    if chat_id is not None:
        updates["last_chat_id"] = chat_id
    update_user_state(user_id, updates)
    return f"Active project set to {resolved}.\n\n{project_context_summary(resolved, max_files=2)}"


def command_mode(args: list[str], user_id: str, chat_id: int | str | None = None) -> str:
    action = args[0].lower() if args else "status"
    if action in {"free", "general", "computer"}:
        updates: dict[str, Any] = {
            "assistant_mode": "free",
            "assistant_mode_updated_at": utc_now(),
        }
        if chat_id is not None:
            updates["last_chat_id"] = chat_id
        update_user_state(user_id, updates)
        return (
            "Mode set to free.\n"
            "I will not assume the focused project for ambiguous requests. "
            "Mention a project when you want project work, or ask general computer/system questions."
        )
    if action in {"focused", "focus"}:
        project_id = " ".join(args[1:]).strip() if len(args) > 1 else None
        if project_id:
            return command_focus(project_id, user_id=user_id, chat_id=chat_id)
        active = user_state(user_id).get("active_project")
        if not active:
            return "No focused project is set. Use /mode focused <project> or /focus <project>."
        update_user_state(user_id, {"assistant_mode": "focused", "assistant_mode_updated_at": utc_now()})
        return f"Mode set to focused.\nFocused project: {active}"
    active = user_state(user_id).get("active_project")
    return (
        f"Mode: {assistant_mode(user_id)}\n"
        f"Focused project: {active or 'none'}\n\n"
        "Use /mode free for general computer/system work.\n"
        "Use /mode focused <project> or /focus <project> for project work."
    )


def command_tools() -> str:
    lines = ["Commander tools"]
    lines.append("")
    lines.append("Native:")
    lines.extend(
        [
            "- Telegram text, buttons, and voice transcription",
            "- Codex CLI session start/stop/log/status",
            "- Git diff, commit approval, push approval",
            "- Local dashboard",
            "- Memory, project profiles, task queue, heartbeats",
            "- Safe computer broker: URLs, allowlisted apps, files, volume, screenshots, process checks",
            "- Browser broker: open and inspect websites",
            "- ClickUp API bridge when CLICKUP_API_TOKEN and CLICKUP_WORKSPACE_ID are configured",
        ]
    )
    lines.append("")
    lines.append("Computer tool apps:")
    apps = app_catalog(computer_tools_config())
    lines.append(", ".join(sorted(apps)) or "none")
    lines.append("")
    lines.append("Codex CLI MCPs:")
    lines.append(codex_mcp_summary())
    lines.append("")
    skills = skill_catalog(limit=12)
    lines.append(f"Local skills visible to Commander: {len(skills)}")
    if skills:
        lines.append(", ".join(skills[:12]))
    plugins = plugin_catalog(limit=12)
    lines.append(f"Local plugins/cache visible to Commander: {len(plugins)}")
    if plugins:
        lines.append(", ".join(plugins[:12]))
    lines.append("")
    clickup_settings = clickup_settings_from_env()
    lines.append(f"ClickUp API bridge: {'configured' if clickup_settings.configured else 'not configured'}")
    lines.append("")
    lines.append("Not yet wired directly:")
    lines.extend(
        [
            "- Codex Desktop connector passthrough",
            "- This Codex Desktop thread's skills/plugins as direct Commander tools",
            "- Unrestricted mouse/keyboard control",
        ]
    )
    return compact("\n".join(lines), limit=3000)


def codex_mcp_summary() -> str:
    result = run_command(codex_command_args(["mcp", "list"]), timeout=60)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return (result.stderr or result.stdout or "Could not read Codex MCP list.").strip()


def mcp_usage() -> str:
    return "\n".join(
        [
            "MCP setup",
            "",
            "Commands:",
            "- /mcp",
            "- /mcp help",
            "- /mcp request <docs URL, package search, or install command>",
            "- /mcp find <package or connector name>",
            "- /mcp add <server-name> npx -y <package> [args...]",
            "- /mcp add <server-name> uvx <package> [args...]",
            "",
            "Guardrails:",
            "- Web pages are researched for explicit MCP install commands before any approval is prepared.",
            "- Package registry search is treated as a lead, not proof that a package is official.",
            "- Adding a server prepares an approval; it does not run from Telegram immediately.",
            "- Raw shell, pipes, redirects, chained commands, and unknown runners are blocked.",
        ]
    )


def normalize_mcp_server_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return name[:48]


def mcp_package_hint(args: list[str]) -> str | None:
    if not args:
        return None
    if args[0].lower() == "npx" and len(args) >= 3 and args[1] in {"-y", "--yes"}:
        return args[2]
    if args[0].lower() == "uvx" and len(args) >= 2:
        return args[1]
    return None


def is_safe_mcp_package(value: str) -> bool:
    return bool(re.fullmatch(r"(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+", value.strip()))


def trusted_mcp_npm_scopes() -> set[str]:
    raw = os.environ.get(
        "COMMANDER_MCP_TRUSTED_NPM_SCOPES",
        "@modelcontextprotocol,@upstash,@supabase,@cloudflare,@github,@vercel,@netlify",
    )
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def mcp_package_trust_label(package: str) -> str:
    package = package.strip().lower()
    if package.startswith("@") and "/" in package:
        scope = package.split("/", 1)[0]
        if scope in trusted_mcp_npm_scopes():
            return "known vendor scope"
        return "scoped community package"
    return "unscoped community package"


def validate_mcp_command(command: list[str]) -> tuple[bool, str]:
    if not command:
        return False, "MCP command is required."
    blocked = {";", "&&", "||", "|", ">", "<", "`", "$(", "${"}
    for token in command:
        if token in blocked or any(marker in token for marker in {"&&", "||", "|", ">", "<", "`", "$("}):
            return False, "Blocked: MCP command contains shell control syntax."
    runner = command[0].lower()
    if runner == "npx":
        if len(command) < 3 or command[1] not in {"-y", "--yes"}:
            return False, "Use npx only as: npx -y <package> [args...]"
    elif runner == "uvx":
        if len(command) < 2:
            return False, "Use uvx only as: uvx <package> [args...]"
    else:
        return False, "Allowed MCP runners are currently npx -y and uvx."
    package = mcp_package_hint(command)
    if not package or not is_safe_mcp_package(package):
        return False, "MCP package name is missing or unsafe."
    return True, ""


class MCPTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_stack.append(tag)
        if tag in {"br", "p", "div", "li", "tr", "pre", "code", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_stack and self.skip_stack[-1] == tag:
            self.skip_stack.pop()
        if tag in {"p", "div", "li", "tr", "pre", "code", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        return html.unescape(" ".join(self.parts))


def html_to_text(source: str) -> str:
    parser = MCPTextExtractor()
    try:
        parser.feed(source)
        return parser.text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html.unescape(source))


def mcp_research_timeout() -> int:
    raw = os.environ.get("COMMANDER_MCP_RESEARCH_TIMEOUT_SECONDS", "12")
    try:
        return max(3, min(45, int(raw)))
    except ValueError:
        return 12


def fetch_mcp_url_text(url: str, max_bytes: int = 250_000) -> tuple[bool, str, str]:
    if not env_bool("COMMANDER_MCP_WEB_RESEARCH", True):
        return False, "", "MCP web research is disabled by COMMANDER_MCP_WEB_RESEARCH."
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, "", "Unsupported URL."
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CommanderX/1.0 (+https://github.com/fazzouny/Commander-X)",
            "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=mcp_research_timeout()) as response:
            content_type = response.headers.get("content-type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return False, "", f"HTTP {exc.code} while reading the page."
    except urllib.error.URLError as exc:
        return False, "", f"Could not reach the page: {redact(str(exc.reason))}."
    except TimeoutError:
        return False, "", "Timed out while reading the page."
    except Exception as exc:
        return False, "", f"Could not inspect the page: {redact(str(exc))}."
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode(charset, errors="replace")
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        text = html_to_text(text)
    detail = f"Read {len(raw):,} bytes"
    if truncated:
        detail += " (truncated)"
    return True, text, detail


def mcp_command_from_codex_tokens(tokens: list[str]) -> tuple[str | None, list[str] | None]:
    lowered = [item.lower() for item in tokens]
    for index in range(0, max(0, len(tokens) - 3)):
        if lowered[index : index + 3] != ["codex", "mcp", "add"]:
            continue
        name = tokens[index + 3] if len(tokens) > index + 3 else ""
        try:
            sep = tokens.index("--", index + 4)
        except ValueError:
            return normalize_mcp_server_name(name), None
        return normalize_mcp_server_name(name), tokens[sep + 1 :]
    return None, None


def mcp_candidate_name(package: str) -> str:
    base = package.split("/")[-1]
    base = re.sub(r"^(mcp[-_.]?server[-_.]?|server[-_.]?mcp[-_.]?|mcp[-_.]?)", "", base, flags=re.IGNORECASE)
    base = re.sub(r"[-_.]?(mcp[-_.]?server|server[-_.]?mcp)$", "", base, flags=re.IGNORECASE)
    return normalize_mcp_server_name(base or package)


def mcp_candidate_from_tokens(command: list[str], name_hint: str | None = None, source: str = "") -> dict[str, Any] | None:
    ok, _error = validate_mcp_command(command)
    if not ok:
        return None
    package = mcp_package_hint(command)
    if not package:
        return None
    return {
        "name": normalize_mcp_server_name(name_hint or mcp_candidate_name(package)),
        "command": command,
        "package": package,
        "trust": mcp_package_trust_label(package),
        "source": source,
    }


MCP_RUNNER_PATTERN = re.compile(
    r"(?P<command>\b(?:npx\s+(?:-y|--yes)\s+(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+|uvx\s+(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+)(?:\s+(?![;&|<>`$])[A-Za-z0-9_@./:=,+-]+){0,16})",
    flags=re.IGNORECASE,
)


def mcp_install_candidates_from_text(text: str, source: str = "") -> list[dict[str, Any]]:
    cleaned = html.unescape(text or "")
    cleaned = cleaned.replace("\u2013", "-").replace("\u2014", "-")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    for line in re.split(r"[\r\n]+", cleaned):
        line = line.strip().strip("$").strip()
        if not line:
            continue
        tokens = parse_message(line)
        name_hint, command = mcp_command_from_codex_tokens(tokens)
        if command:
            candidate = mcp_candidate_from_tokens(command, name_hint=name_hint, source=source or "explicit codex mcp command")
            if candidate:
                key = tuple(candidate["command"])
                if key not in seen:
                    seen.add(key)
                    candidates.append(candidate)
        for match in MCP_RUNNER_PATTERN.finditer(line):
            command_tokens = parse_message(match.group("command"))
            candidate = mcp_candidate_from_tokens(command_tokens, source=source or "explicit runner command")
            if candidate:
                key = tuple(candidate["command"])
                if key not in seen:
                    seen.add(key)
                    candidates.append(candidate)
        if len(candidates) >= 5:
            break
    return candidates[:5]


def mcp_research_terms(request: str, fetched_text: str = "") -> str:
    words: list[str] = []
    for url in re.findall(r"https?://\S+", request):
        parsed = urllib.parse.urlparse(url.rstrip(".,)"))
        words.extend(re.split(r"[^A-Za-z0-9]+", parsed.netloc + " " + parsed.path))
    without_urls = re.sub(r"https?://\S+", " ", request)
    words.extend(re.split(r"[^A-Za-z0-9]+", without_urls))
    title_match = re.search(r"\b([A-Z][A-Za-z0-9 ]{5,80})\b", fetched_text[:1000])
    if title_match:
        words.extend(title_match.group(1).split())
    stop = {
        "www",
        "com",
        "org",
        "net",
        "https",
        "http",
        "business",
        "news",
        "docs",
        "documentation",
        "install",
        "connect",
        "connector",
        "connectors",
        "setup",
        "this",
        "that",
        "can",
        "you",
        "the",
        "and",
        "for",
        "with",
        "please",
        "introducing",
        "mcp",
        "ai",
    }
    selected: list[str] = []
    for word in words:
        word = word.lower()
        if len(word) < 3 or word in stop or word in selected:
            continue
        selected.append(word)
        if len(selected) >= 8:
            break
    return " ".join(selected)


def npm_search_mcp_packages(query: str, limit: int = 5) -> tuple[list[dict[str, Any]], str]:
    query = " ".join((query or "").split())
    if not query:
        return [], "No package search terms were available."
    encoded = urllib.parse.urlencode({"text": f"{query} mcp", "size": str(max(1, min(10, limit)))})
    url = f"https://registry.npmjs.org/-/v1/search?{encoded}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CommanderX/1.0 (+https://github.com/fazzouny/Commander-X)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=mcp_research_timeout()) as response:
            data = json.loads(response.read(250_000).decode("utf-8", errors="replace"))
    except Exception as exc:
        return [], f"NPM registry search failed: {redact(str(exc))}."

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data.get("objects", []):
        package = (item.get("package") or {}).get("name", "")
        description = ((item.get("package") or {}).get("description") or "").strip()
        haystack = f"{package} {description}".lower()
        if not is_safe_mcp_package(package) or "mcp" not in haystack:
            continue
        if package in seen:
            continue
        seen.add(package)
        candidate = mcp_candidate_from_tokens(["npx", "-y", package], source="npm registry search")
        if candidate:
            candidate["description"] = description[:160]
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates, f"Searched npm for: {query} mcp"


def format_mcp_candidate(candidate: dict[str, Any], index: int) -> str:
    name = str(candidate.get("name", "mcp-server"))
    command = " ".join(str(item) for item in candidate.get("command", []))
    package = str(candidate.get("package", "unknown"))
    line = f"{index}. {package}\n   Prepare: /mcp add {name} {command}"
    trust = str(candidate.get("trust") or "").strip()
    if trust:
        line += f"\n   Trust: {trust}"
    description = str(candidate.get("description") or "").strip()
    if description:
        line += f"\n   Note: {description}"
    return line


def prepare_mcp_add_response(server_name: str, command: list[str], source: str = "") -> str:
    server_name = normalize_mcp_server_name(server_name)
    ok, error = validate_mcp_command(command)
    if not server_name:
        return "MCP server name is required."
    if not ok:
        return error
    pending_action: dict[str, Any] = {
        "type": "mcp_add",
        "name": server_name,
        "command": command,
        "message": f"Add MCP server {server_name}: {' '.join(command)}",
    }
    if source:
        pending_action["source"] = source
    pending_id = add_pending_action("commander", pending_action)
    lines = [
        f"MCP install prepared for {server_name}.",
        f"Pending approval ID: {pending_id}",
        "",
        f"Command: codex mcp add {server_name} -- {' '.join(command)}",
    ]
    if source:
        lines.extend(["", f"Source: {source}"])
    lines.extend(
        [
            "",
            "This changes your local Codex MCP configuration.",
            f"Approve with /approve commander {pending_id}",
            f"Cancel with /cancel commander {pending_id}",
        ]
    )
    return "\n".join(lines)


def mcp_request_response(request: str) -> str:
    request = request.strip()
    if not request:
        return mcp_usage()
    url_match = re.search(r"https?://\S+", request)
    if url_match:
        url = url_match.group(0).rstrip(".,)")
        ok, text, detail = fetch_mcp_url_text(url)
        lines = ["MCP research result", "", f"URL: {url}", ""]
        if ok:
            candidates = mcp_install_candidates_from_text(text, source=url)
            if len(candidates) == 1:
                candidate = candidates[0]
                return prepare_mcp_add_response(
                    str(candidate["name"]),
                    [str(item) for item in candidate["command"]],
                    source=url,
                )
            if len(candidates) > 1:
                lines.extend(
                    [
                        f"{detail}. I found multiple possible install commands.",
                        "Choose the one you trust and send its Prepare command:",
                        "",
                    ]
                )
                lines.extend(format_mcp_candidate(candidate, index + 1) for index, candidate in enumerate(candidates))
                lines.extend(["", "Nothing was installed."])
                return "\n".join(lines)
            terms = mcp_research_terms(request, text)
            registry_candidates, registry_detail = npm_search_mcp_packages(terms)
            lines.append(f"{detail}. I did not find an explicit npx/uvx/codex MCP install command on the page.")
            if registry_candidates:
                lines.extend(
                    [
                        "",
                        registry_detail,
                        "These are registry leads, not proof of an official Meta/OpenAI/vendor connector. Review before preparing approval:",
                        "",
                    ]
                )
                lines.extend(format_mcp_candidate(candidate, index + 1) for index, candidate in enumerate(registry_candidates))
            else:
                lines.extend(["", registry_detail, "I did not find a safe package candidate automatically.", "Nothing was installed."])
        else:
            lines.extend(
                [
                    f"I tried to inspect the page but could not fetch it: {detail}",
                    "Nothing was installed.",
                    "",
                    "You can also ask me to search a package name directly:",
                    "/mcp find meta ads",
                ]
            )
        if "facebook.com" in url.lower() or "meta" in url.lower():
            lines.extend(
                [
                    "",
                    "For a Meta Ads connector, expect to add Meta app/ad-account credentials in .env after an official package is identified.",
                ]
            )
        return "\n".join(lines)

    inline_candidates = mcp_install_candidates_from_text(request, source="operator message")
    if len(inline_candidates) == 1:
        candidate = inline_candidates[0]
        return prepare_mcp_add_response(
            str(candidate["name"]),
            [str(item) for item in candidate["command"]],
            source="operator-provided install command",
        )
    if len(inline_candidates) > 1:
        lines = [
            "MCP install commands detected",
            "",
            "I found multiple possible install commands. Choose the one you trust and send its Prepare command:",
            "",
        ]
        lines.extend(format_mcp_candidate(candidate, index + 1) for index, candidate in enumerate(inline_candidates))
        lines.extend(["", "Nothing was installed."])
        return "\n".join(lines)

    tokens = parse_message(request)
    package = mcp_package_hint(tokens)
    if package:
        suggested_name = mcp_candidate_name(package)
        return prepare_mcp_add_response(suggested_name, tokens, source="operator-provided install command")

    candidates, detail = npm_search_mcp_packages(request)
    if candidates:
        lines = [
            "MCP package research",
            "",
            detail,
            "These are registry leads, not proof that a package is official. Choose one only if you trust the publisher/source.",
            "",
        ]
        lines.extend(format_mcp_candidate(candidate, index + 1) for index, candidate in enumerate(candidates))
        return "\n".join(lines)
    return (
        "MCP request received, but I could not find a safe MCP package/command automatically.\n\n"
        + mcp_usage()
    )


def command_mcp(args: list[str] | None = None) -> str:
    args = args or []
    action = args[0].lower() if args else "list"
    if action in {"list", "status"}:
        return "Codex CLI MCPs\n\n" + codex_mcp_summary() + "\n\n" + "Use /mcp help for setup commands."
    if action in {"help", "usage"}:
        return mcp_usage()
    if action in {"request", "connect", "install", "setup", "find", "search"}:
        return compact(mcp_request_response(" ".join(args[1:])), limit=3200)
    if action == "add":
        if len(args) < 3:
            return "Usage: /mcp add <server-name> npx -y <package> [args...]\nUse /mcp help for details."
        server_name = normalize_mcp_server_name(args[1])
        if not server_name:
            return "MCP server name is required."
        command = args[2:]
        ok, error = validate_mcp_command(command)
        if not ok:
            return error
        return prepare_mcp_add_response(server_name, command, source="operator /mcp add command")
    return mcp_request_response(" ".join(args))


def skill_catalog(limit: int = 20) -> list[str]:
    names = [label for label, _path in skill_entries(limit=limit)]
    return sorted(names)


def skill_entries(limit: int = 100) -> list[tuple[str, Path]]:
    roots = [
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills",
        Path.home() / ".agents" / "skills",
    ]
    entries: list[tuple[str, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for skill_file in root.rglob("SKILL.md"):
            try:
                rel = skill_file.parent.relative_to(root)
            except ValueError:
                rel = skill_file.parent
            label = str(rel).replace("\\", "/")
            if label and not any(existing == label for existing, _path in entries):
                entries.append((label, skill_file))
            if len(entries) >= limit:
                return sorted(entries, key=lambda item: item[0])
    return sorted(entries, key=lambda item: item[0])


def plugin_catalog(limit: int = 20) -> list[str]:
    cache = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "plugins" / "cache"
    if not cache.exists():
        return []
    names: list[str] = []
    for child in cache.iterdir():
        if child.is_dir() and child.name not in names:
            names.append(child.name)
        if len(names) >= limit:
            break
    return sorted(names)


def command_plugins() -> str:
    plugins = plugin_catalog(limit=50)
    if not plugins:
        return "No local plugin cache found."
    return "Local plugins/cache visible to Commander:\n" + "\n".join(f"- {name}" for name in plugins)


def command_skills(args: list[str]) -> str:
    entries = skill_entries(limit=200)
    query = " ".join(arg for arg in args if arg.lower() not in {"full", "details", "path", "paths"}).strip().lower()
    show_details = bool(args and any(arg.lower() in {"full", "details"} for arg in args))
    show_paths = bool(args and any(arg.lower() in {"path", "paths"} for arg in args))
    if query:
        matches = [(name, path) for name, path in entries if query in name.lower()]
        if not matches:
            return f"No matching local skill found for: {query}"
        name, path = matches[0]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Could not read skill {name}: {exc}"
        lines = [f"Skill: {name}"]
        if show_paths:
            lines.append(f"Path: {path}")
        if show_details:
            lines.extend(["", text[:2400].strip()])
        else:
            for raw in text.splitlines()[:40]:
                line = raw.strip()
                if line.startswith("description:"):
                    lines.append(line.replace("description:", "Description:", 1).strip())
                    break
            lines.append("Use /skills <name> details for more.")
        return compact("\n".join(lines), limit=3200)
    if not entries:
        return "No local skills found."
    lines = [f"Local skills visible to Commander: {len(entries)}"]
    lines.extend(f"- {name}" for name, _path in entries[:40])
    if len(entries) > 40:
        lines.append(f"...and {len(entries) - 40} more.")
    lines.append("")
    lines.append("Use /skills <name> for a short description.")
    return compact("\n".join(lines), limit=3200)


def command_env() -> str:
    readiness = env_readiness()
    lines = ["Commander environment readiness"]
    for group, keys in readiness.items():
        configured = sum(1 for status in keys.values() if status == "configured")
        lines.append("")
        lines.append(f"{group}: {configured}/{len(keys)} configured")
        for key, status in keys.items():
            lines.append(f"- {key}: {status}")
    lines.append("")
    lines.append("Secrets are never printed. Fill missing values in .env, then restart Commander.")
    return "\n".join(lines)


def command_system() -> str:
    paths = [BASE_DIR]
    for project in projects_config().get("projects", {}).values():
        try:
            paths.append(project_path(project))
        except Exception:
            continue
    return compact(format_system_snapshot(system_snapshot(paths)), limit=3200)


def automation_exists(automation_id: str) -> bool:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return (codex_home / "automations" / automation_id / "automation.toml").exists()


def doctor_checks(user_id: str | None = None) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(status: str, label: str, detail: str) -> None:
        checks.append({"status": status, "label": label, "detail": detail})

    add("good" if shutil.which("codex") else "bad", "Codex CLI", shutil.which("codex") or "missing from PATH")
    add("good" if shutil.which("git") else "bad", "Git", shutil.which("git") or "missing from PATH")
    add("good" if os.environ.get("TELEGRAM_BOT_TOKEN") else "bad", "Telegram bot token", "configured" if os.environ.get("TELEGRAM_BOT_TOKEN") else "missing")
    add("good" if allowed_user_ids() else "bad", "Telegram allowlist", f"{len(allowed_user_ids())} allowed user ID(s)" if allowed_user_ids() else "missing")
    add("good" if os.environ.get("OPENAI_API_KEY") else "warn", "OpenAI API key", "configured" if os.environ.get("OPENAI_API_KEY") else "missing; voice/NL routing will degrade")
    add("good" if clickup_settings_from_env().configured else "warn", "ClickUp API bridge", "configured" if clickup_settings_from_env().configured else "missing API token/workspace ID")
    add("good" if automation_exists("commander-x-monster-build-loop") else "warn", "Monster build automation", "configured" if automation_exists("commander-x-monster-build-loop") else "not found")

    projects = projects_config().get("projects", {})
    enabled = {project_id: project for project_id, project in projects.items() if project.get("allowed", False)}
    missing_paths: list[str] = []
    non_git: list[str] = []
    changed_count = 0
    for project_id, project in enabled.items():
        try:
            path = project_path(project)
        except Exception:
            missing_paths.append(project_id)
            continue
        if not path.exists():
            missing_paths.append(project_id)
            continue
        if not is_git_repo(path):
            non_git.append(project_id)
            continue
        if changed_files(path):
            changed_count += 1
    add("good" if enabled else "warn", "Registered projects", f"{len(enabled)} enabled project(s)")
    add("good" if not missing_paths else "bad", "Project paths", "all enabled project paths exist" if not missing_paths else ", ".join(missing_paths[:6]))
    add("good" if not non_git else "warn", "Project Git repos", "all enabled projects are Git repos" if not non_git else ", ".join(non_git[:6]))
    add("good" if changed_count == 0 else "warn", "Dirty worktrees", f"{changed_count} enabled project(s) have local changes")

    refresh_session_states()
    sessions = sessions_data().get("sessions", {})
    running = [project_id for project_id, session in sessions.items() if session.get("state") == "running"]
    failed = [project_id for project_id, session in sessions.items() if session.get("state") in {"failed", "finished_unknown"}]
    add("good" if not failed else "warn", "Session failures", "none" if not failed else ", ".join(failed[:6]))
    add("good", "Running sessions", ", ".join(running) if running else "none")

    snapshot = system_snapshot([BASE_DIR])
    worst_disk = max((float(row.get("used_percent") or 0) for row in snapshot.get("disk", [])), default=0.0)
    add("good" if worst_disk < 85 else "warn" if worst_disk < 93 else "bad", "Disk pressure", f"{worst_disk}% used")
    return checks


def doctor_score(checks: list[dict[str, str]]) -> int:
    if not checks:
        return 0
    weights = {"good": 1.0, "warn": 0.55, "bad": 0.0}
    score = sum(weights.get(check["status"], 0.0) for check in checks) / len(checks)
    return int(round(score * 100))


def command_doctor(user_id: str | None = None) -> str:
    checks = doctor_checks(user_id=user_id)
    score = doctor_score(checks)
    lines = [
        "Commander doctor",
        f"Health score: {score}/100",
        "",
        "Checks:",
    ]
    symbol = {"good": "OK", "warn": "WARN", "bad": "BAD"}
    for check in checks:
        lines.append(f"- {symbol.get(check['status'], check['status'].upper())}: {check['label']} - {check['detail']}")
    problems = [check for check in checks if check["status"] in {"warn", "bad"}]
    lines.extend(["", "Top fixes:"])
    if problems:
        for check in problems[:6]:
            lines.append(f"- {check['label']}: {check['detail']}")
    else:
        lines.append("- No urgent fixes.")
    lines.append("")
    lines.append("No secrets were printed.")
    return compact("\n".join(lines), limit=3600)


def active_user_id(default: str = "dashboard") -> str:
    allowed = sorted(allowed_user_ids())
    if allowed:
        return allowed[0]
    users = state_data().get("users", {})
    for user_id, state in users.items():
        if state.get("last_chat_id") or state.get("heartbeat_chat_id"):
            return str(user_id)
    return default


def pending_approvals() -> list[dict[str, Any]]:
    refresh_session_states()
    approvals: list[dict[str, Any]] = []
    for project_id, session in sorted(sessions_data().get("sessions", {}).items()):
        pending = session.get("pending_actions") or {}
        if not isinstance(pending, dict):
            continue
        for pending_id, action in pending.items():
            if not isinstance(action, dict):
                continue
            approvals.append(
                {
                    "project": project_id,
                    "id": str(pending_id),
                    "type": str(action.get("type", "action")),
                    "branch": str(action.get("branch") or session.get("branch") or "-"),
                    "message": str(action.get("message") or action.get("description") or ""),
                    "created_at": str(action.get("created_at") or session.get("updated_at") or ""),
                }
            )
    return approvals


def command_approvals() -> str:
    approvals = pending_approvals()
    if not approvals:
        return "No pending approvals."
    lines = ["Pending approvals:"]
    for item in approvals:
        detail = f"{item['type']} on {item['branch']}"
        if item.get("message"):
            detail += f" - {item['message']}"
        lines.append(f"- {item['project']} [{item['id']}]: {detail}")
        lines.append(f"  Approve: /approve {item['project']} {item['id']}")
        lines.append(f"  Cancel: /cancel {item['project']} {item['id']}")
    return compact("\n".join(lines), limit=3600)


def inbox_items(user_id: str | None = None, limit: int = 12) -> list[dict[str, str]]:
    user_id = user_id or active_user_id()
    items: list[dict[str, str]] = []
    for approval in pending_approvals():
        items.append(
            {
                "kind": "approval",
                "priority": "high",
                "title": f"Approve {approval['type']} for {approval['project']}",
                "detail": f"/approve {approval['project']} {approval['id']} or /cancel {approval['project']} {approval['id']}",
            }
        )
    sessions = sessions_data().get("sessions", {})
    for project_id, session in sorted(sessions.items()):
        state = str(session.get("state", "unknown"))
        if state == "running":
            items.append(
                {
                    "kind": "session",
                    "priority": "medium",
                    "title": f"{project_id} is running",
                    "detail": f"Task: {session.get('task', '-')}; use /log {project_id} or /stop {project_id}",
                }
            )
        elif state in {"failed", "finished_unknown"}:
            items.append(
                {
                    "kind": "session",
                    "priority": "high",
                    "title": f"{project_id} needs review",
                    "detail": f"State: {state}; use /log {project_id} and /diff {project_id}",
                }
            )
    for task in visible_task_records(tasks_data().get("tasks", []), limit=8):
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
    for recommendation in recommendation_items(user_id=user_id, limit=6):
        items.append(
            {
                "kind": "recommendation",
                "priority": "low",
                "title": "Recommended action",
                "detail": recommendation,
            }
        )
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: order.get(item["priority"], 9))
    return items[:limit]


def command_inbox(user_id: str) -> str:
    items = inbox_items(user_id=user_id, limit=14)
    if not items:
        return "Commander inbox is empty."
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    summary = ", ".join(f"{kind}: {count}" for kind, count in sorted(counts.items()))
    lines = ["Commander inbox", f"Items: {len(items)} ({summary})", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. [{item['priority']}] {item['title']}")
        lines.append(f"   {item['detail']}")
    return compact("\n".join(lines), limit=3600)


def changed_projects(limit: int = 8) -> list[tuple[str, int, str]]:
    result: list[tuple[str, int, str]] = []
    for project_id, project in sorted(projects_config().get("projects", {}).items()):
        if not project.get("allowed", False):
            continue
        try:
            path = project_path(project)
        except Exception:
            continue
        if not path.exists() or not is_git_repo(path):
            continue
        changed = changed_files(path)
        if changed:
            result.append((project_id, len(changed), current_branch(path)))
    result.sort(key=lambda item: item[1], reverse=True)
    return result[:limit]


def changed_project_details(limit: int = 8, max_files: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project_id, project in sorted(projects_config().get("projects", {}).items()):
        if not project.get("allowed", False):
            continue
        try:
            path = project_path(project)
        except Exception:
            continue
        if not path.exists() or not is_git_repo(path):
            continue
        changed = changed_files(path)
        if not changed:
            continue
        sensitive = sensitive_file_paths(changed)
        rows.append(
            {
                "project": project_id,
                "branch": current_branch(path),
                "changed_count": len(changed),
                "changed_preview": changed[:max_files],
                "areas": change_bucket_summary(changed),
                "sensitive_count": len(sensitive),
                "sensitive_preview": sensitive[:max_files],
            }
        )
    rows.sort(key=lambda item: int(item["changed_count"]), reverse=True)
    return rows[:limit]


def human_change_bucket(file_path: str) -> str:
    lower = file_path.lower().replace("\\", "/")
    if lower.startswith(".github/") or "/workflows/" in lower:
        return "automation"
    if "test" in lower or "spec" in lower:
        return "tests"
    if lower.endswith((".md", ".mdx", ".txt")):
        return "docs/content"
    if "package.json" in lower or "package-lock" in lower or "requirements" in lower or "pyproject" in lower:
        return "dependencies/config"
    if lower.startswith(("supabase/", "migrations/", "db/")):
        return "database"
    if lower.startswith(("public/", "assets/", "images/")):
        return "assets"
    if "/api/" in lower or "server" in lower or "backend" in lower:
        return "backend"
    if lower.endswith((".tsx", ".jsx", ".css", ".scss", ".html")) or "components" in lower or "pages" in lower or "app/" in lower:
        return "app/user interface"
    if lower.endswith((".ts", ".js", ".py", ".mjs", ".cjs")):
        return "logic/scripts"
    return "other"


def change_bucket_summary(files: list[str]) -> str:
    buckets: dict[str, int] = {}
    for file in files:
        bucket = human_change_bucket(file)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    if not buckets:
        return "no changed areas"
    ordered = sorted(buckets.items(), key=lambda item: item[1], reverse=True)
    return ", ".join(f"{name} ({count})" for name, count in ordered[:4])


def command_changes(args: list[str], user_id: str) -> str:
    show_files = bool(args and any(arg.lower() in {"files", "details", "full", "technical"} for arg in args))
    filtered_args = [arg for arg in args if arg.lower() not in {"files", "details", "full", "technical"}]
    if filtered_args:
        first = filtered_args[0].lower()
        if first not in {"all", "global", "overview", "summary"}:
            project_id, _rest = project_and_rest(filtered_args, user_id=user_id)
            if project_id:
                return command_diff(project_id)
    rows = changed_project_details(limit=10, max_files=5)
    if not rows:
        return "No local Git changes found in enabled projects."
    total = sum(int(row["changed_count"]) for row in rows)
    lines = [f"Changed projects: {len(rows)} projects, {total} files"]
    for row in rows:
        sensitive_note = f", sensitive-looking: {row['sensitive_count']}" if row["sensitive_count"] else ""
        lines.append("")
        lines.append(f"- {row['project']}: {row['changed_count']} files on {row['branch']}{sensitive_note}")
        project = get_project(str(row["project"]))
        path = project_path(project) if project else None
        all_files = changed_files(path) if path and path.exists() and is_git_repo(path) else []
        lines.append(f"  Areas: {change_bucket_summary(all_files)}")
        if show_files:
            for file in row["changed_preview"]:
                lines.append(f"  - {file}")
            if int(row["changed_count"]) > len(row["changed_preview"]):
                lines.append(f"  ...and {int(row['changed_count']) - len(row['changed_preview'])} more")
            if row["sensitive_preview"]:
                lines.append("  Sensitive-looking changed files:")
                lines.extend(f"  - {file}" for file in row["sensitive_preview"])
    lines.append("")
    lines.append("Use /changes files or /diff <project> only when you want technical filenames.")
    return compact("\n".join(lines), limit=3600)


def recommendation_items(user_id: str | None = None, limit: int = 8) -> list[str]:
    user_id = user_id or active_user_id()
    items: list[str] = []
    snapshot = system_snapshot([BASE_DIR])
    for disk in snapshot.get("disk", []):
        used = float(disk.get("used_percent") or 0)
        if used >= 90:
            items.append(f"Run /cleanup and free disk space on {disk.get('root')}: {used}% used, {disk.get('free_gb')} GB free.")
            break
    settings = clickup_settings_from_env()
    if not settings.configured:
        items.append("Add CLICKUP_API_TOKEN and CLICKUP_WORKSPACE_ID so Commander can answer campaign/task questions from Telegram.")
    if not os.environ.get("GITHUB_TOKEN"):
        items.append("Add GITHUB_TOKEN so Commander can prepare PR and issue workflows later.")
    if not os.environ.get("WHATSAPP_ACCESS_TOKEN"):
        items.append("Add WhatsApp Cloud API keys when you want WhatsApp control after Telegram.")
    sessions = sessions_data().get("sessions", {})
    running = [project_id for project_id, session in sessions.items() if session.get("state") == "running"]
    if running:
        items.append("Check running Codex sessions: " + ", ".join(sorted(running)) + ".")
    review = [project_id for project_id, session in sessions.items() if session.get("state") in {"finished_unknown", "failed"}]
    if review:
        items.append("Review completed/uncertain Codex sessions: " + ", ".join(sorted(review)) + ".")
    changed = changed_projects(limit=5)
    if changed:
        formatted = ", ".join(f"{project_id} ({count})" for project_id, count, _branch in changed)
        items.append(f"Review local diffs before starting more work: {formatted}.")
    state = user_state(user_id)
    if not state.get("heartbeat_enabled"):
        items.append("Enable Commander heartbeat with /heartbeat on 30 for proactive updates.")
    if not get_project(str(state.get("active_project") or "")) and assistant_mode(user_id) == "focused":
        items.append("Set a focused project with /focus <project>, or switch to /free for general computer work.")
    return items[:limit]


def command_next(user_id: str) -> str:
    items = recommendation_items(user_id=user_id, limit=10)
    if not items:
        return "No urgent Commander recommendations right now."
    return "Recommended next actions:\n" + "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def task_direction_lines(task: str) -> list[str]:
    task_text = task.strip() or "Work on the requested project task."
    return [
        f"Goal: {task_text}",
        "Direction:",
        "1. Understand the current workflow and find the real blocker.",
        "2. Make the smallest useful change that moves the project forward.",
        "3. Run the relevant checks instead of guessing.",
        "4. Report outcome, risks, and what needs your approval.",
    ]


def command_plan(project_id: str | None, user_id: str, task: str | None = None) -> str:
    resolved = resolve_project_id(project_id, user_id=user_id) if project_id else resolve_project_id(None, user_id=user_id)
    if not resolved:
        return "No project selected. Use /plan <project> [task] or /focus <project>."
    session = sessions_data().get("sessions", {}).get(resolved) or {}
    if not task and session.get("work_plan"):
        return format_work_plan(session["work_plan"])
    task_text = task or str(session.get("task") or "Work on the requested project task.")
    return format_work_plan(build_work_plan(resolved, task_text))


def command_watch(project_id: str | None, user_id: str) -> str:
    resolved = resolve_project_id(project_id, user_id=user_id) if project_id else resolve_project_id(None, user_id=user_id)
    if not resolved:
        return "No project selected. Use /watch <project> or /focus <project>."
    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(resolved) or {}
    profile = project_profile(resolved)
    lines = [f"Watch: {resolved}"]
    if session:
        plan = session.get("work_plan")
        lines.extend(
            [
                f"Status: {session.get('state', 'unknown')}",
                f"Phase: {session.get('current_phase') or session.get('state', 'unknown')}",
                f"Task: {session.get('task', '-')}",
                "",
                format_work_plan(plan) if isinstance(plan, dict) else "\n".join(task_direction_lines(str(session.get("task") or ""))),
                "",
                "Timeline:",
                *timeline_lines(session),
            ]
        )
        log_file = Path(str(session.get("log_file", "")))
        if log_file.exists():
            age_seconds = int(time.time() - log_file.stat().st_mtime)
            lines.extend(["", f"Latest activity: {max(0, age_seconds // 60)} minutes ago"])
        pending = session.get("pending_actions") or {}
        if pending:
            lines.extend(["", f"Needs approval: {len(pending)} pending action(s). Use /approvals."])
    else:
        lines.extend(
            [
                "Status: no active Commander-started Codex session",
                "",
                "What Commander can show now:",
                f"- Changed files count: {profile.get('changed_count', 0)}",
                "- Use /changes for a plain-English worktree summary.",
                "- Use /start <project> \"task\" to launch a managed Codex run.",
            ]
        )
    verification = profile.get("verification_commands") or []
    if verification:
        lines.extend(["", "Likely verification path:"])
        lines.extend(f"- {command}" for command in verification[:5])
    lines.extend(["", "Technical filenames are hidden by default. Use /diff only when you want code-level detail."])
    return compact("\n".join(lines), limit=3400)


def command_morning(user_id: str) -> str:
    refresh_session_states()
    state = user_state(user_id)
    active = state.get("active_project") or "none"
    heartbeat = "on" if state.get("heartbeat_enabled") else "off"
    quiet = quiet_window_status(state)
    sessions = sessions_data().get("sessions", {})
    running = [project_id for project_id, session in sessions.items() if session.get("state") == "running"]
    changed = changed_projects(limit=5)
    snapshot = system_snapshot([BASE_DIR])
    disk_lines = []
    for disk in snapshot.get("disk", []):
        disk_lines.append(f"{disk.get('root')}: {disk.get('free_gb')} GB free, {disk.get('used_percent')}% used")
    lines = [
        "Commander X morning brief",
        f"Mode: {assistant_mode(user_id)}",
        f"Focused project: {active}",
        f"Heartbeat: {heartbeat}; quiet hours: {quiet}",
        "",
        "Sessions:",
        "- Running: " + (", ".join(sorted(running)) if running else "none"),
        f"- Tracked: {len(sessions)}",
        "",
        "Device:",
        f"- Memory: {snapshot.get('memory')}",
        f"- Battery: {snapshot.get('battery')}",
    ]
    lines.extend(f"- Disk {line}" for line in disk_lines)
    lines.extend(["", "Projects with local changes:"])
    if changed:
        lines.extend(f"- {project_id}: {count} files changed on {branch}" for project_id, count, branch in changed)
    else:
        lines.append("- none")
    recommendations = recommendation_items(user_id=user_id, limit=5)
    lines.extend(["", "Recommended next actions:"])
    if recommendations:
        lines.extend(f"{index}. {item}" for index, item in enumerate(recommendations, start=1))
    else:
        lines.append("No urgent recommendations.")
    return compact("\n".join(lines), limit=3600)


def command_clipboard(args: list[str]) -> str:
    action = args[0].lower() if args else "status"
    if action in {"status", "show", "peek"}:
        if not env_bool("COMMANDER_ALLOW_CLIPBOARD_READ", True):
            return "Clipboard read is disabled by COMMANDER_ALLOW_CLIPBOARD_READ."
        result = run_command(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], timeout=15)
        text = redact((result.stdout or "").strip())
        if result.returncode != 0:
            return "Clipboard read failed:\n" + compact(result.stderr or result.stdout)
        if not text:
            return "Clipboard is empty."
        preview = text[:500].rstrip()
        if len(text) > 500:
            preview += "\n...[truncated]"
        return f"Clipboard preview ({len(text)} chars):\n{preview}"
    if action in {"set", "copy"}:
        value = " ".join(args[1:]).strip()
        if not value:
            return "Usage: /clipboard set <text>"
        if re.search(r"(?i)\b(api[_-]?key|token|secret|password|private[_-]?key)\b", value):
            return "Clipboard set blocked because the text looks like a secret."
        ps = "$input | Set-Clipboard"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            input=value,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if result.returncode != 0:
            return "Clipboard set failed:\n" + compact(result.stderr or result.stdout)
        return f"Clipboard updated with {len(value)} chars."
    if action == "clear":
        result = run_command(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ''"], timeout=15)
        if result.returncode != 0:
            return "Clipboard clear failed:\n" + compact(result.stderr or result.stdout)
        return "Clipboard cleared."
    return "Usage: /clipboard show, /clipboard set <text>, or /clipboard clear"


def command_cleanup(args: list[str]) -> str:
    max_files = int(os.environ.get("COMMANDER_CLEANUP_MAX_FILES", "20000") or "20000")
    max_seconds = float(os.environ.get("COMMANDER_CLEANUP_SECONDS_PER_TARGET", "2") or "2")
    if args and args[0].isdigit():
        max_seconds = max(0.5, min(10.0, float(args[0])))
    rows = cleanup_scan(BASE_DIR, max_files=max_files, max_seconds_per_target=max_seconds)
    return compact(format_cleanup_scan(rows), limit=3600)


def is_sensitive_relative_path(rel: Path) -> bool:
    for part in rel.parts:
        lower = part.lower()
        if lower in SENSITIVE_FILE_PATTERNS or lower.startswith(".env"):
            return True
        if lower.endswith(SENSITIVE_SUFFIXES):
            return True
    return False


def safe_project_file(project_id: str, rel_path: str) -> tuple[Path | None, str]:
    project = get_project(project_id)
    if not project:
        return None, f"Unknown or disabled project: {project_id}"
    rel = Path(rel_path.strip().strip('"'))
    if not str(rel).strip():
        return None, "File path is required."
    if rel.is_absolute() or re.match(r"^[A-Za-z]:", str(rel)):
        return None, "Use a relative path inside the registered project, not an absolute path."
    if is_sensitive_relative_path(rel):
        return None, "Blocked: Commander does not read secret or credential-like files."
    root = project_path(project)
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "Blocked: path escapes the registered project folder."
    if not candidate.exists():
        return None, f"File not found in {project_id}: {rel}"
    if not candidate.is_file():
        return None, f"Not a file in {project_id}: {rel}"
    if is_sensitive_path(candidate):
        return None, "Blocked: Commander does not read secret or credential-like files."
    return candidate, ""


def command_file(args: list[str], user_id: str) -> str:
    project_id, rest = project_and_rest(args, user_id=user_id)
    if not project_id or not rest:
        return "Usage: /file <project> <relative_path> [lines]\nIn focused mode, /file <relative_path> also works."
    lines = 80
    path_args = rest
    if rest[-1].isdigit():
        lines = max(10, min(250, int(rest[-1])))
        path_args = rest[:-1]
    rel_path = " ".join(path_args).strip()
    candidate, error = safe_project_file(project_id, rel_path)
    if error or candidate is None:
        return error
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        return f"Could not read file: {exc}"
    if b"\x00" in raw[:4096]:
        return f"Blocked: {Path(rel_path).as_posix()} looks like a binary file."
    text = raw.decode("utf-8", errors="replace")
    file_lines = text.splitlines()
    shown = file_lines[:lines]
    try:
        rel = candidate.relative_to(project_path(get_project(project_id) or {})).as_posix()
    except (ValueError, KeyError):
        rel = Path(rel_path).as_posix()
    header = [
        f"File: {project_id}/{rel}",
        f"Showing {len(shown)} of {len(file_lines)} lines.",
    ]
    if len(raw) > 180_000:
        header.append("Large file: response is truncated.")
    return compact("\n".join(header + ["", *shown]), limit=3600)


def command_open(args: list[str]) -> str:
    if not args:
        apps = ", ".join(sorted(app_catalog(computer_tools_config())))
        return f"Usage: /open url <url> or /open app <name>\nAllowlisted apps: {apps}"
    first = args[0].lower()
    if first in {"url", "site", "website", "web"}:
        if len(args) < 2:
            return "Usage: /open url <url>"
        ok, message = computer_open_url(" ".join(args[1:]))
        return message
    if first in {"app", "application"}:
        if len(args) < 2:
            return "Usage: /open app <allowlisted_app>"
        ok, message = computer_open_app(" ".join(args[1:]), computer_tools_config())
        return message
    apps = app_catalog(computer_tools_config())
    joined = " ".join(args)
    if re.search(r"^(https?://|www\.)", joined, flags=re.IGNORECASE) or re.search(r"\.[A-Za-z]{2,}(/|$)", joined):
        ok, message = computer_open_url(joined)
        return message
    if first in apps:
        ok, message = computer_open_app(first, computer_tools_config())
        return message
    return f"I could not tell whether that is a URL or app.\nUsage: /open url <url> or /open app <name>"


def command_volume(args: list[str]) -> str:
    if not env_bool("COMMANDER_ALLOW_VOLUME_KEYS", True):
        return "Volume control is disabled by COMMANDER_ALLOW_VOLUME_KEYS."
    if not args:
        return "Usage: /volume up [steps], /volume down [steps], or /volume mute"
    action = args[0].lower()
    if action in {"lower", "decrease", "quieter"}:
        action = "down"
    if action in {"raise", "increase", "louder"}:
        action = "up"
    steps = 1
    if len(args) > 1 and args[1].isdigit():
        steps = int(args[1])
    ok, message = press_volume_key(action, steps)
    return message


def command_computer(args: list[str], user_id: str) -> str:
    action = args[0].lower() if args else "status"
    if action in {"status", "tools", "capabilities"}:
        apps = ", ".join(sorted(app_catalog(computer_tools_config())))
        lines = [
            "Computer broker",
            f"Mode: {assistant_mode(user_id)}",
            f"Allowlisted apps: {apps}",
            "",
            "Commands:",
            "- /open url <url>",
            "- /open app <name>",
            "- /file <project> <relative_path> [lines]",
            "- /volume up|down|mute [steps]",
            "- /computer codex",
            "- /computer processes [name...]",
            "- /computer screenshot",
            "",
            "Guardrails: no raw shell from Telegram; file reads stay inside registered projects; secret-like files are blocked.",
        ]
        return "\n".join(lines)
    if action == "codex":
        lines = ["Codex computer status", "", command_status(), "", "Codex CLI MCPs:", codex_mcp_summary(), "", "Related processes:"]
        process_output = computer_process_lines(["codex.exe"])
        lines.extend(process_output[:25] or ["No related processes found."])
        return compact("\n".join(lines), limit=3600)
    if action in {"process", "processes", "ps"}:
        names = args[1:] or ["codex.exe", "python.exe", "node.exe"]
        lines = [f"Process check: {', '.join(names)}"]
        lines.extend(computer_process_lines(names)[:80] or ["No matching processes found."])
        return compact("\n".join(lines), limit=3600)
    if action in {"screenshot", "screen"}:
        if not env_bool("COMMANDER_ALLOW_SCREENSHOT", True):
            return "Screenshot capture is disabled by COMMANDER_ALLOW_SCREENSHOT."
        ok, message = capture_screenshot(SCREENSHOT_DIR)
        if ok:
            return f"Screenshot captured.\nPath: {message}"
        return f"Screenshot failed: {message}"
    if action == "open":
        return command_open(args[1:])
    if action == "volume":
        return command_volume(args[1:])
    if action == "file":
        return command_file(args[1:], user_id=user_id)
    return "Unknown computer action.\nUse /computer to see available safe computer tools."


def command_browser(args: list[str]) -> str:
    if not args:
        return "Usage: /browser inspect <url>, /browser open <url>, or /browser screenshot"
    action = args[0].lower()
    if action in {"open", "visit", "go"}:
        if len(args) < 2:
            return "Usage: /browser open <url>"
        return command_open(["url", *args[1:]])
    if action in {"inspect", "check", "summarize", "summary"}:
        if len(args) < 2:
            return "Usage: /browser inspect <url>"
        result = browser_inspect_url(" ".join(args[1:]))
        return compact(format_browser_inspection(result), limit=3200)
    if action in {"screenshot", "screen"}:
        return command_computer(["screenshot"], user_id="browser")
    return "Unknown browser action.\nUsage: /browser inspect <url>, /browser open <url>, or /browser screenshot"


def command_clickup(args: list[str]) -> str:
    settings = clickup_settings_from_env()
    action = args[0].lower() if args else "status"
    if action == "status":
        lines = [
            "ClickUp bridge",
            f"API token: {'configured' if settings.token else 'missing'}",
            f"Workspace ID: {'configured' if settings.workspace_id else 'missing'}",
            "",
            "Commands:",
            "- /clickup recent [query]",
            "- /clickup tasks [query]",
            "",
            "Note: the Codex Desktop ClickUp connector is available to this Codex chat, but the always-on Commander service needs ClickUp API credentials to work from Telegram while this chat is closed.",
        ]
        return "\n".join(lines)
    if action in {"recent", "tasks", "task"}:
        if not settings.configured:
            return (
                "ClickUp API bridge is not configured.\n"
                "Add CLICKUP_API_TOKEN and CLICKUP_WORKSPACE_ID to .env, then restart Commander.\n"
                "I will not use Telegram as a raw proxy into the Codex Desktop connector."
            )
        query = " ".join(args[1:]).strip() or None
        try:
            payload = clickup_filtered_team_tasks(settings)
        except Exception as exc:
            return f"ClickUp request failed: {redact(str(exc))}"
        tasks = payload.get("tasks", [])
        if not isinstance(tasks, list):
            return "ClickUp returned an unexpected task payload."
        filtered = clickup_filter_tasks(tasks, query=query)
        header = f"Recent ClickUp tasks" + (f" matching: {query}" if query else "")
        return compact(header + "\n\n" + clickup_format_tasks(filtered, limit=10), limit=3600)
    return "Unknown ClickUp action.\nUse /clickup status or /clickup recent [query]."


def command_context(project_id: str | None, user_id: str, show_details: bool = False) -> str:
    resolved = resolve_project_id(project_id, user_id=user_id)
    if not resolved:
        return "No active project yet. Use /focus <project> or mention a project."
    return project_context_summary(resolved, max_files=4, show_path=show_details)


def command_profile(project_id: str | None, user_id: str) -> str:
    resolved = resolve_project_id(project_id, user_id=user_id)
    if not resolved:
        return "No active project yet. Use /focus <project> or /profile <project>."
    return format_project_profile(project_profile(resolved))


def command_remember(args: list[str], user_id: str) -> str:
    if not args:
        return "Usage: /remember [global|project] <note>"
    scope = "user"
    project_id = None
    note_args = args
    first = args[0].lower()
    if first in {"global", "user", "project"}:
        scope = first
        note_args = args[1:]
    if scope == "project":
        maybe_project, rest = project_and_rest(note_args, user_id=user_id)
        if maybe_project and rest:
            project_id = maybe_project
            note_args = rest
        else:
            project_id = resolve_project_id(None, user_id=user_id)
    if not note_args:
        return "Usage: /remember [global|project] <note>"
    item = add_memory(" ".join(note_args), user_id=user_id, scope=scope, project_id=project_id)
    target = f"project {project_id}" if project_id else scope
    return f"Memory saved for {target}.\nID: {item['id']}"


def command_memory(args: list[str], user_id: str) -> str:
    scope = args[0].lower() if args else "relevant"
    project_id = resolve_project_id(None, user_id=user_id)
    query_args = args
    if scope in {"all", "global", "user", "project", "relevant"}:
        query_args = args[1:]
    else:
        scope = "relevant"
    query = " ".join(query_args).strip() or None
    memories = memory_data().get("memories", [])
    if scope != "all":
        filtered: list[dict[str, Any]] = []
        for item in memories:
            if scope == "global" and item.get("scope") != "global":
                continue
            if scope == "user" and str(item.get("user_id")) != str(user_id):
                continue
            if scope == "project" and item.get("project") != project_id:
                continue
            if scope == "relevant" and item not in relevant_memories(user_id, project_id=project_id, query=query, limit=20):
                continue
            if query and query.lower() not in str(item.get("note", "")).lower():
                continue
            filtered.append(item)
        memories = filtered
    if not memories:
        return "No matching Commander memories."
    lines = ["Commander memory:"]
    for item in memories[-20:]:
        target = item.get("project") or item.get("scope")
        lines.append(f"- [{item.get('id')}] {target}: {item.get('note')}")
    return compact("\n".join(lines))


def command_forget(memory_id: str | None, user_id: str) -> str:
    if not memory_id:
        return "Usage: /forget <memory_id>"
    data = memory_data()
    before = len(data.get("memories", []))
    data["memories"] = [
        item
        for item in data.get("memories", [])
        if not (item.get("id") == memory_id and (str(item.get("user_id")) == str(user_id) or item.get("scope") == "global"))
    ]
    if len(data["memories"]) == before:
        return f"No removable memory found with ID {memory_id}."
    save_memory(data)
    return f"Forgot memory {memory_id}."


def command_queue(args: list[str], user_id: str) -> str:
    action = args[0].lower() if args else "list"
    if action in {"list", "status"}:
        return tasks_summary()
    if action == "add":
        project_id, rest = project_and_rest(args[1:], user_id=user_id)
        if not project_id or not rest:
            return 'Usage: /queue add <project> "task"'
        task = add_task(project_id, " ".join(rest), user_id=user_id, status="queued", source="queue")
        return f"Queued task {task['id']} for {project_id}."
    if action == "start":
        if len(args) < 2:
            return "Usage: /queue start <task_id>"
        task = get_task(args[1])
        if not task:
            return f"No task found with ID {args[1]}."
        return start_codex(str(task["project"]), str(task["title"]), user_id=user_id, task_id=str(task["id"]))
    if action in {"done", "complete"}:
        if len(args) < 2:
            return "Usage: /queue done <task_id>"
        task = update_task(args[1], {"status": "done", "completed_at": utc_now()})
        return f"Marked task {args[1]} done." if task else f"No task found with ID {args[1]}."
    if action in {"cancel", "drop"}:
        if len(args) < 2:
            return "Usage: /queue cancel <task_id>"
        task = update_task(args[1], {"status": "cancelled", "cancelled_at": utc_now()})
        return f"Cancelled task {args[1]}." if task else f"No task found with ID {args[1]}."
    return "Usage: /queue, /queue add <project> <task>, /queue start <task_id>, /queue done <task_id>, /queue cancel <task_id>"


def command_log(project_id: str, lines: int = DEFAULT_LOG_LINES) -> str:
    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(project_id)
    if not session:
        return f"No session found for {project_id}."
    log_file = Path(session.get("log_file", ""))
    return compact(tail_file(log_file, lines))


def heartbeat_summary(user_id: str) -> str:
    state = user_state(user_id)
    active = state.get("active_project")
    lines = ["Codex Commander heartbeat", f"Time: {utc_now()}"]
    if active:
        lines.append(f"Active project: {active}")
    lines.extend(["", command_status()])
    if active and get_project(str(active)):
        lines.extend(["", command_diff(str(active))])
    return compact("\n".join(lines))


def parse_interval_minutes(value: str | None) -> int:
    if not value:
        return int(os.environ.get("COMMANDER_HEARTBEAT_MINUTES", DEFAULT_HEARTBEAT_MINUTES))
    match = re.search(r"\d+", value)
    if not match:
        return int(os.environ.get("COMMANDER_HEARTBEAT_MINUTES", DEFAULT_HEARTBEAT_MINUTES))
    return max(5, min(240, int(match.group(0))))


def parse_hhmm(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([01]?\d|2[0-3]):?([0-5]\d)", value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def user_quiet_hours(state: dict[str, Any]) -> tuple[str | None, str | None]:
    if state.get("heartbeat_quiet_enabled") is False:
        return None, None
    start = str(state.get("heartbeat_quiet_start") or os.environ.get("COMMANDER_HEARTBEAT_QUIET_START", DEFAULT_QUIET_START))
    end = str(state.get("heartbeat_quiet_end") or os.environ.get("COMMANDER_HEARTBEAT_QUIET_END", DEFAULT_QUIET_END))
    return start, end


def quiet_window_status(state: dict[str, Any]) -> str:
    start, end = user_quiet_hours(state)
    if not start or not end:
        return "off"
    return f"{start}-{end} local time"


def quiet_end_datetime(now_local: dt.datetime, start: str, end: str) -> dt.datetime | None:
    start_parts = parse_hhmm(start)
    end_parts = parse_hhmm(end)
    if not start_parts or not end_parts:
        return None
    start_time = dt.time(start_parts[0], start_parts[1], tzinfo=now_local.tzinfo)
    end_time = dt.time(end_parts[0], end_parts[1], tzinfo=now_local.tzinfo)
    start_dt = now_local.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
    end_dt = now_local.replace(hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0)
    crosses_midnight = start_dt >= end_dt
    if crosses_midnight:
        if now_local >= start_dt:
            return end_dt + dt.timedelta(days=1)
        if now_local < end_dt:
            return end_dt
        return None
    if start_dt <= now_local < end_dt:
        return end_dt
    return None


def next_heartbeat_at(interval_minutes: int, state: dict[str, Any] | None = None) -> dt.datetime:
    state = state or {}
    next_local = local_now() + dt.timedelta(minutes=interval_minutes)
    start, end = user_quiet_hours(state)
    if start and end:
        quiet_end = quiet_end_datetime(next_local, start, end)
        if quiet_end:
            next_local = quiet_end
    return next_local.astimezone(dt.timezone.utc)


def command_heartbeat(args: list[str], user_id: str, chat_id: int | str | None = None) -> str:
    action = args[0].lower() if args else "status"
    if action in {"on", "start", "enable", "every"}:
        interval = parse_interval_minutes(args[1] if len(args) > 1 else None)
        current_state = user_state(user_id)
        next_at = next_heartbeat_at(interval, current_state)
        updates: dict[str, Any] = {
            "heartbeat_enabled": True,
            "heartbeat_interval_minutes": interval,
            "heartbeat_next_at": next_at.isoformat(timespec="seconds"),
            "heartbeat_updated_at": utc_now(),
        }
        if chat_id is not None:
            updates["heartbeat_chat_id"] = chat_id
            updates["last_chat_id"] = chat_id
        update_user_state(user_id, updates)
        quiet = quiet_window_status({**current_state, **updates})
        return f"Heartbeat enabled every {interval} minutes.\nQuiet hours: {quiet}\nNext update: {updates['heartbeat_next_at']}."
    if action in {"off", "stop", "disable"}:
        update_user_state(
            user_id,
            {
                "heartbeat_enabled": False,
                "heartbeat_updated_at": utc_now(),
            },
        )
        return "Heartbeat disabled."
    if action == "quiet":
        if len(args) >= 2 and args[1].lower() in {"off", "disable", "none"}:
            update_user_state(user_id, {"heartbeat_quiet_enabled": False, "heartbeat_updated_at": utc_now()})
            return "Heartbeat quiet hours disabled."
        if len(args) >= 3 and parse_hhmm(args[1]) and parse_hhmm(args[2]):
            state = update_user_state(
                user_id,
                {
                    "heartbeat_quiet_enabled": True,
                    "heartbeat_quiet_start": args[1],
                    "heartbeat_quiet_end": args[2],
                    "heartbeat_updated_at": utc_now(),
                },
            )
            interval = int(state.get("heartbeat_interval_minutes") or DEFAULT_HEARTBEAT_MINUTES)
            state["heartbeat_next_at"] = next_heartbeat_at(interval, state).isoformat(timespec="seconds")
            update_user_state(user_id, state)
            return f"Heartbeat quiet hours set to {args[1]}-{args[2]} local time."
        return "Usage: /heartbeat quiet 23:00 08:00 or /heartbeat quiet off"
    if action == "now":
        if chat_id is not None:
            update_user_state(user_id, {"last_chat_id": chat_id, "heartbeat_chat_id": chat_id})
        return heartbeat_summary(user_id)
    state = user_state(user_id)
    enabled = bool(state.get("heartbeat_enabled"))
    interval = state.get("heartbeat_interval_minutes", "-")
    next_at = state.get("heartbeat_next_at", "-")
    active = state.get("active_project", "-")
    quiet = quiet_window_status(state)
    return f"Heartbeat: {'on' if enabled else 'off'}\nInterval: {interval} minutes\nQuiet hours: {quiet}\nNext update: {next_at}\nActive project: {active}"


def command_stop(project_id: str) -> str:
    refresh_session_states()
    data = sessions_data()
    session = data.get("sessions", {}).get(project_id)
    if not session:
        return f"No session found for {project_id}."
    if session.get("state") != "running":
        return f"{project_id} is not running. Current state: {session.get('state')}."
    pid = int(session.get("pid", 0))
    ok, output = stop_pid(pid)
    session["state"] = "stopped" if ok else "stop_failed"
    session["updated_at"] = utc_now()
    session["stop_output"] = output
    append_timeline_event(
        session,
        "stopped" if ok else "stop_failed",
        "Session stopped by Commander" if ok else "Stop attempt failed",
        "The managed Codex process tree was stopped." if ok else "Commander could not stop the full process tree.",
        status="done" if ok else "warn",
    )
    save_sessions(data)
    if session.get("task_id"):
        update_task(
            str(session["task_id"]),
            {
                "status": "stopped" if ok else "failed",
                "stopped_at": utc_now(),
            },
        )
    PROCESSES.pop(project_id, None)
    return f"Stop {'sent' if ok else 'failed'} for {project_id} (PID {pid}).\n{output}"


def command_diff(project_id: str) -> str:
    project = get_project(project_id)
    if not project:
        return f"Unknown or disabled project: {project_id}"
    path = project_path(project)
    if not path.exists():
        return f"Project path does not exist: {path}"
    if not is_git_repo(path):
        return f"{project_id} is not a Git repository."

    status = git_run(path, "status", "--short", timeout=30)
    stat = git_run(path, "diff", "--stat", timeout=30)
    names = git_run(path, "diff", "--name-only", timeout=30)
    untracked = [line[3:].strip() for line in status.stdout.splitlines() if line.startswith("?? ")]

    output = [
        f"Diff summary for {project_id}",
        f"Branch: {current_branch(path)}",
        "",
        "Status:",
        status.stdout.strip() or "clean",
        "",
        "Diff stat:",
        stat.stdout.strip() or "no tracked file diff",
        "",
        "Tracked changed files:",
        names.stdout.strip() or "none",
    ]
    if untracked:
        output.extend(["", "Untracked files:", "\n".join(untracked)])
    return compact("\n".join(output))


def add_pending_action(project_id: str, action: dict[str, Any]) -> str:
    data = sessions_data()
    sessions = data.setdefault("sessions", {})
    session = sessions.setdefault(
        project_id,
        {
            "project": project_id,
            "state": "idle",
            "updated_at": utc_now(),
            "pending_actions": {},
        },
    )
    pending_id = secrets.token_hex(3)
    action["id"] = pending_id
    action["created_at"] = utc_now()
    session.setdefault("pending_actions", {})[pending_id] = action
    session["updated_at"] = utc_now()
    save_sessions(data)
    return pending_id


def command_commit(project_id: str, message: str) -> str:
    project = get_project(project_id)
    if not project:
        return f"Unknown or disabled project: {project_id}"
    path = project_path(project)
    if not path.exists() or not is_git_repo(path):
        return f"{project_id} is not an available Git repository."

    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(project_id)
    if session and session.get("state") == "running":
        return f"{project_id} is still running. Stop it or wait before creating a commit approval."

    if not has_changes(path):
        return f"No Git changes to commit for {project_id}."

    sensitive = sensitive_changed_files(path)
    if sensitive:
        return "Commit blocked because sensitive-looking files changed:\n" + "\n".join(f"- {item}" for item in sensitive)

    changed = changed_files(path)
    areas = change_bucket_summary(changed) or "local project work"
    pending_id = add_pending_action(
        project_id,
        {
            "type": "commit",
            "message": message,
            "path": str(path),
            "branch": current_branch(path),
        },
    )
    return (
        f"Commit prepared for {project_id} on branch {current_branch(path)}.\n"
        f"Pending approval ID: {pending_id}\n\n"
        f"Summary: {len(changed)} changed file(s).\n"
        f"Areas: {areas}\n\n"
        "Technical filenames are hidden here. Use /diff only when you want code-level detail.\n\n"
        f"Approve with /approve {project_id} {pending_id}\n"
        f"Cancel with /cancel {project_id} {pending_id}"
    )


def command_push(project_id: str) -> str:
    project = get_project(project_id)
    if not project:
        return f"Unknown or disabled project: {project_id}"
    path = project_path(project)
    if not path.exists() or not is_git_repo(path):
        return f"{project_id} is not an available Git repository."
    if has_changes(path):
        return f"Push blocked: {project_id} has uncommitted changes. Commit or discard them first."

    branch = current_branch(path)
    pending_id = add_pending_action(
        project_id,
        {
            "type": "push",
            "path": str(path),
            "branch": branch,
            "remote": "origin",
        },
    )
    return (
        f"Push prepared for {project_id}.\n"
        f"Branch: {branch}\n"
        f"Pending approval ID: {pending_id}\n\n"
        "This is high-impact because it sends local work to the remote repository.\n\n"
        f"Approve with /approve {project_id} {pending_id}\n"
        f"Cancel with /cancel {project_id} {pending_id}"
    )


def execute_pending(project_id: str, pending_id: str | None) -> str:
    data = sessions_data()
    session = data.get("sessions", {}).get(project_id)
    if not session:
        return f"No session found for {project_id}."
    pending = session.get("pending_actions") or {}
    if not pending:
        return f"No pending actions for {project_id}."
    if not pending_id:
        if len(pending) != 1:
            return "Multiple pending actions exist. Include the approval ID."
        pending_id = next(iter(pending))
    action = pending.get(pending_id)
    if not action:
        return f"No pending action {pending_id} for {project_id}."

    action_type = action.get("type")
    if action_type == "commit":
        path = Path(action["path"]).resolve()
        sensitive = sensitive_changed_files(path)
        if sensitive:
            return "Approval blocked because sensitive-looking files changed:\n" + "\n".join(f"- {item}" for item in sensitive)
        add = git_run(path, "add", "-A", timeout=60)
        if add.returncode != 0:
            return "git add failed:\n" + compact(add.stderr or add.stdout)
        commit = git_run(path, "commit", "-m", str(action["message"]), timeout=120)
        if commit.returncode != 0:
            return "git commit failed:\n" + compact(commit.stderr or commit.stdout)
        result = compact(commit.stdout + commit.stderr)
    elif action_type == "push":
        path = Path(action["path"]).resolve()
        if has_changes(path):
            return "Push approval blocked because the worktree now has uncommitted changes."
        branch = str(action["branch"])
        remote = str(action.get("remote", "origin"))
        push = git_run(path, "push", "-u", remote, branch, timeout=180)
        if push.returncode != 0:
            return "git push failed:\n" + compact(push.stderr or push.stdout)
        result = compact(push.stdout + push.stderr)
    elif action_type == "mcp_add":
        name = normalize_mcp_server_name(str(action.get("name", "")))
        command = [str(item) for item in action.get("command", [])]
        ok, error = validate_mcp_command(command)
        if not name or not ok:
            return f"MCP approval blocked: {error or 'invalid server name'}"
        add = run_command(codex_command_args(["mcp", "add", name, "--", *command]), timeout=120)
        if add.returncode != 0:
            return "codex mcp add failed:\n" + compact(add.stderr or add.stdout)
        result = compact((add.stdout + add.stderr).strip() or f"Added MCP server {name}.")
    else:
        return f"Unsupported pending action type: {action_type}"

    pending.pop(pending_id, None)
    session["updated_at"] = utc_now()
    save_sessions(data)
    return f"Approved and executed {action_type} for {project_id}.\n{result}"


def command_cancel(project_id: str, pending_id: str | None) -> str:
    data = sessions_data()
    session = data.get("sessions", {}).get(project_id)
    if not session:
        return f"No session found for {project_id}."
    pending = session.get("pending_actions") or {}
    if not pending:
        return f"No pending actions for {project_id}."
    if not pending_id:
        if len(pending) != 1:
            return "Multiple pending actions exist. Include the approval ID."
        pending_id = next(iter(pending))
    if pending_id not in pending:
        return f"No pending action {pending_id} for {project_id}."
    action_type = pending[pending_id].get("type", "action")
    pending.pop(pending_id, None)
    session["updated_at"] = utc_now()
    save_sessions(data)
    return f"Cancelled pending {action_type} for {project_id}."


def command_check() -> str:
    cfg = projects_config()
    lines = ["Codex Commander check"]
    lines.append(f"Home: {BASE_DIR}")
    lines.append(f"Python: {sys.version.split()[0]}")
    lines.append(f"Codex CLI: {shutil.which('codex') or 'missing'}")
    lines.append(f"Git: {shutil.which('git') or 'missing'}")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    openai = openai_config()
    lines.append(f"Telegram token: {'configured' if token else 'missing'}")
    lines.append(f"Allowed Telegram user IDs: {len(allowed_user_ids())}")
    lines.append(f"OpenAI API key: {'configured' if openai['api_key'] else 'missing'}")
    lines.append(f"OpenAI command model: {openai['command_model']}")
    lines.append(f"OpenAI transcription model: {openai['transcribe_model']}")
    clickup_settings = clickup_settings_from_env()
    lines.append(f"ClickUp API bridge: {'configured' if clickup_settings.configured else 'not configured'}")
    lines.append(f"Default heartbeat minutes: {os.environ.get('COMMANDER_HEARTBEAT_MINUTES', DEFAULT_HEARTBEAT_MINUTES)}")
    lines.append(f"Memories: {len(memory_data().get('memories', []))}")
    lines.append(f"Tasks: {len(tasks_data().get('tasks', []))}")
    lines.append("")
    lines.append(command_projects())
    codex_cfg = cfg.get("codex", {})
    lines.append("")
    lines.append(f"Codex sandbox: {codex_cfg.get('sandbox', 'workspace-write')}")
    return "\n".join(lines)


def command_help() -> str:
    return """Codex Commander commands

/whoami
/help
/projects
/status
/doctor
/inbox
/approvals
/changes [project]
/watch [project]
/timeline [project]
/plan [project] [task]
/brief [project]
/morning
/next
/updates [project]
/mode [free|focused] [project]
/free
/tools
/computer
/browser inspect <url>
/clickup [status|recent] [query]
/skills [query]
/plugins
/mcp [help|request|find|add]
/env
/system
/clipboard [show|set|clear]
/cleanup
/open url <url>
/open app <name>
/file <project> <relative_path> [lines]
/volume up|down|mute [steps]
/focus <project>
/context [project]
/start <project> "<task>"
/log [project] [lines]
/diff [project]
/stop [project]
/commit [project] "<message>"
/push [project]
/approve <project> [approval_id]
/cancel <project> [approval_id]
/heartbeat on [minutes]
/heartbeat off
/heartbeat status
/heartbeat now
/remember [global|project] <note>
/memory [all|global|user|project] [query]
/forget <memory_id>
/profile [project]
/queue
/queue add <project> "<task>"
/queue start <task_id>
/check

Natural language and voice notes are routed through OpenAI into these same commands, then executed through the same safety gates.

No raw shell commands are accepted. Commit and push require a second /approve step. Commander should return evidence before claiming work is done."""


def replace_project_aliases(text: str) -> str:
    result = text
    aliases = sorted(project_alias_map().items(), key=lambda item: len(item[0]), reverse=True)
    placeholders: dict[str, str] = {}
    for index, (alias, project_id) in enumerate(aliases):
        if not alias:
            continue
        placeholder = f"__PROJECT_{index}__"
        pattern = rf"(?<![\w-]){re.escape(alias)}(?![\w-])"
        result = re.sub(pattern, placeholder, result, flags=re.IGNORECASE)
        placeholders[placeholder] = project_id
    for placeholder, project_id in placeholders.items():
        result = result.replace(placeholder, project_id)
    return result


def normalize_voice_command(transcript: str) -> str:
    text = (transcript or "").strip()
    text = re.sub(r"[\s.?!]+$", "", text)
    text = re.sub(r"^(hey\s+)?(codex\s+)?commander[:,\s]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^slash\s+", "/", text, flags=re.IGNORECASE)
    text = text.replace(" forward slash ", " /")
    text = replace_project_aliases(text)

    replacements = [
        (r"^(show me the )?status$", "/status"),
        (r"^(show me )?(the )?projects$", "/projects"),
        (r"^(list|show) projects$", "/projects"),
        (r"^(show me )?(the )?help$", "/help"),
        (r"^(run )?check$", "/check"),
        (r"^(show me the |show the |show )?log for ", "/log "),
        (r"^(show me the |show the |show )?diff for ", "/diff "),
        (r"^(stop|kill) ", "/stop "),
        (r"^(approve) ", "/approve "),
        (r"^(cancel) ", "/cancel "),
        (r"^(push) ", "/push "),
        (r"^(commit) ", "/commit "),
        (r"^(start|begin) ", "/start "),
        (r"^(open|visit|go to) (?=(https?://|www\.|[A-Za-z0-9.-]+\.[A-Za-z]{2,}))", "/open url "),
        (r"^(open|launch) app ", "/open app "),
        (r"^(mute volume|mute sound)$", "/volume mute"),
        (r"^(lower|decrease|turn down) (the )?(volume|sound)$", "/volume down 5"),
        (r"^(raise|increase|turn up) (the )?(volume|sound)$", "/volume up 5"),
        (r"^(take a )?(screenshot|screen shot)$", "/computer screenshot"),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE).strip()

    tokens = parse_message(text)
    known = {
        "help",
        "projects",
        "status",
        "start",
        "log",
        "diff",
        "stop",
        "commit",
        "push",
        "approve",
        "cancel",
        "check",
        "whoami",
        "brief",
        "updates",
        "mode",
        "free",
        "tools",
        "computer",
        "browser",
        "clickup",
        "skills",
        "plugins",
        "mcp",
        "env",
        "system",
        "clipboard",
        "cleanup",
        "open",
        "file",
        "volume",
        "remember",
        "memory",
        "forget",
        "profile",
        "queue",
    }
    if tokens and not tokens[0].startswith("/") and tokens[0].lower() in known:
        return "/" + text
    return text


def parse_message(text: str) -> list[str]:
    return parse_commander_message(text)


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def openai_chat_json(messages: list[dict[str, str]], temperature: float = 0.0) -> dict[str, Any]:
    cfg = openai_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing in .env.")
    payload = {
        "model": cfg["command_model"],
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI command routing failed: HTTP {exc.code}: {redact(error_body)}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return extract_json_object(str(content))


def session_brief() -> str:
    refresh_session_states()
    data = sessions_data()
    sessions = data.get("sessions", {})
    if not sessions:
        return "No sessions yet."
    lines = []
    for project_id, session in sorted(sessions.items()):
        pending = session.get("pending_actions") or {}
        lines.append(
            f"{project_id}: state={session.get('state')}, branch={session.get('branch')}, "
            f"task={session.get('task', '-')}, pending={len(pending)}"
        )
    return "\n".join(lines)


def project_brief() -> str:
    projects = projects_config().get("projects", {})
    lines = []
    for project_id, project in sorted(projects.items()):
        allowed = "enabled" if project.get("allowed", False) else "disabled"
        aliases = ", ".join(project.get("aliases", []))
        lines.append(f"{project_id}: {allowed}; aliases=[{aliases}]")
    return "\n".join(lines)


def validate_generated_command(command: str) -> str:
    command = command.strip()
    if not command.startswith("/"):
        raise RuntimeError("Natural-language router did not return a slash command.")
    tokens = parse_message(command)
    if not tokens:
        raise RuntimeError("Natural-language router returned an empty command.")
    slash = tokens[0].split("@", 1)[0].lower()
    if slash not in NL_ALLOWED_COMMANDS:
        raise RuntimeError(f"Natural-language router returned a blocked command: {slash}")
    return command


def looks_like_start_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(start|run|continue|work on|finish|finalize|make|fix|audit|complete|production ready|100%)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def task_without_project_names(text: str) -> str:
    result = text.strip().rstrip("?")
    aliases = sorted(project_alias_map().items(), key=lambda item: len(item[0]), reverse=True)
    for alias, _project_id in aliases:
        pattern = rf"(?<![\w-]){re.escape(alias)}(?![\w-])"
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    result = re.sub(r"\b(and|both|all|projects?)\b", " ", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+", " ", result).strip(" ,.-")
    if len(result) < 12:
        result = text.strip().rstrip("?")
    return result


def multi_project_start_response(text: str, user_id: str, chat_id: int | str | None, execute: bool = True) -> list[str] | None:
    projects = mentioned_projects(text)
    if len(projects) < 2 or not looks_like_start_request(text):
        return None
    task = task_without_project_names(text)
    responses = [f"I detected {len(projects)} projects: {', '.join(projects)}.\nTask: {task}"]
    for project_id in projects:
        if not execute:
            responses.append(f"Would start {project_id} with task: {task}")
            continue
        update_user_state(
            user_id,
            {
                "active_project": project_id,
                "active_project_set_at": utc_now(),
                **({"last_chat_id": chat_id} if chat_id is not None else {}),
            },
        )
        responses.append(start_codex(project_id, task, user_id=user_id))
    return responses


def focus_project_response(text: str, user_id: str, chat_id: int | str | None, execute: bool = True) -> list[str] | None:
    if not re.search(r"\b(focus|switch|set active|active project)\b", text, flags=re.IGNORECASE):
        return None
    projects = mentioned_projects(text)
    if len(projects) == 1:
        project_id = projects[0]
        if not execute:
            return [f"Would focus {project_id}."]
        return [f"I matched that to {project_id}.", command_focus(project_id, user_id=user_id, chat_id=chat_id)]
    if len(projects) > 1:
        return [f"I found multiple projects: {', '.join(projects)}. Use /focus <project>."]
    return None


def looks_like_brief_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(summary|summarize|brief|latest updates?|what changed|what happened|updates?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def natural_computer_command(text: str) -> str | None:
    lowered = text.lower()
    url_match = re.search(r"\b((?:https?://)?(?:www\.)?[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?:/[^\s\"']*)?)", text)
    if re.search(r"\b(mcp|mcps)\b", lowered) and re.search(r"\b(connect|install|add|setup|set up|request|wire|enable|find|search|research)\b", lowered):
        return f"/mcp request {text}"
    if re.search(r"\b(mcp|mcps)\b", lowered) and re.search(r"\b(show|list|what|available|have|status|help|how)\b", lowered):
        return "/mcp"
    if re.search(r"\b(skills?)\b", lowered) and re.search(r"\b(show|list|what|available|have)\b", lowered):
        return "/skills"
    if re.search(r"\b(plugins?)\b", lowered) and re.search(r"\b(show|list|what|available|have)\b", lowered):
        return "/plugins"
    if re.search(r"\b(doctor|health check|diagnose|diagnostic|self[- ]?test)\b", lowered):
        return "/doctor"
    if re.search(r"\b(approvals?|approve list|pending approvals?|decisions? to approve|approve or cancel)\b", lowered):
        return "/approvals"
    if re.search(r"\b(inbox|what needs my attention|needs attention|pending items|what needs me|decision inbox)\b", lowered):
        return "/inbox"
    if re.search(r"\b(changed projects?|dirty worktrees?|local changes|all changes|changes across projects|what changed across)\b", lowered):
        return "/changes"
    if re.search(r"\b(timeline|run timeline|work timeline|session timeline)\b", lowered):
        projects = mentioned_projects(text)
        return f"/timeline {projects[0]}" if projects else "/timeline"
    if re.search(r"\b(watch|live view|what is .*doing|show progress|progress view)\b", lowered) and re.search(r"\b(project|codex|session|work|doing|progress)\b", lowered):
        projects = mentioned_projects(text)
        return f"/watch {projects[0]}" if projects else "/watch"
    if re.search(r"\b(env|environment|setup|keys?|credentials?)\b", lowered) and re.search(r"\b(show|check|what|status|missing|needed|need)\b", lowered):
        return "/env"
    if re.search(r"\b(system|device|computer|disk|battery|memory)\b", lowered) and re.search(r"\b(status|check|show|how much|health)\b", lowered):
        return "/system"
    if re.search(r"\b(clipboard)\b", lowered) and re.search(r"\b(show|peek|status|what)\b", lowered):
        return "/clipboard show"
    if re.search(r"\b(cleanup|clean up|free disk|free space|disk space|storage)\b", lowered) and re.search(r"\b(plan|scan|check|show|what|free|cleanup|clean)\b", lowered):
        return "/cleanup"
    if re.search(r"\b(work plan|before work|project plan|codex plan|approach|how will you)\b", lowered) and re.search(r"\b(show|create|make|what|tell|plan|approach|before)\b", lowered):
        projects = mentioned_projects(text)
        return f"/plan {projects[0]}" if projects else "/plan"
    if re.search(r"\b(morning brief|wake[- ]?up|daily brief|what matters now)\b", lowered):
        return "/morning"
    if re.search(r"\b(what should i do next|next action|next step|recommendations?|what next)\b", lowered):
        return "/next"
    if url_match and re.search(r"\b(inspect|check|summarize|analyse|analyze|read)\b.*\b(website|site|page|url|link)\b", lowered):
        return f"/browser inspect {url_match.group(1).rstrip('.,)')}"
    if re.search(r"\b(check|show|inspect|what.*in|what.*on)\b.*\bclickup\b", lowered):
        terms = re.sub(r"\b(check|show|inspect|what|is|in|on|clickup|tasks?|latest|recent|the|my|me)\b", " ", lowered)
        query = " ".join(terms.split())
        return f"/clickup recent {query}".strip()
    if url_match and re.search(r"\b(open|visit|go to|browse|launch|pull up)\b", lowered):
        return f"/open url {url_match.group(1).rstrip('.,)')}"
    if re.search(r"\b(mute|silence)\b.*\b(volume|sound|audio)\b|\b(volume|sound|audio)\b.*\b(mute|silence)\b", lowered):
        return "/volume mute"
    if re.search(r"\b(lower|decrease|reduce|turn down)\b.*\b(volume|sound|audio)\b", lowered):
        return "/volume down 5"
    if re.search(r"\b(raise|increase|turn up)\b.*\b(volume|sound|audio)\b", lowered):
        return "/volume up 5"
    if re.search(r"\b(screenshot|screen shot|capture my screen|capture the screen)\b", lowered):
        return "/computer screenshot"
    if re.search(r"\b(check|inspect|what is|what's|status)\b.*\bcodex\b", lowered):
        return "/computer codex"
    if re.search(r"\b(processes|running processes|task manager)\b", lowered):
        return "/computer processes"
    if re.search(r"\b(open|launch|start)\b", lowered):
        apps = app_catalog(computer_tools_config())
        for name in sorted(apps, key=len, reverse=True):
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered):
                return f"/open app {name}"
    return None


def natural_language_response(
    text: str,
    user_id: str,
    user_name: str,
    chat_id: int | str | None,
    channel: str = "telegram",
    execute_commands: bool = True,
) -> list[str]:
    lowered = text.lower()
    computer_command = natural_computer_command(text)
    if computer_command:
        if not execute_commands:
            return [f"Would run: {computer_command}"]
        return handle_text(computer_command, user_id=user_id, user_name=user_name, channel=channel, chat_id=chat_id, allow_natural=False)

    if re.search(r"\b(free mode|general mode|computer mode)\b", lowered):
        if not execute_commands:
            return ["Would run: /mode free"]
        return [command_mode(["free"], user_id=user_id, chat_id=chat_id)]
    if re.search(r"\b(focused mode|focus mode)\b", lowered):
        projects = mentioned_projects(text)
        if projects:
            if not execute_commands:
                return [f"Would run: /mode focused {projects[0]}"]
            return [command_mode(["focused", projects[0]], user_id=user_id, chat_id=chat_id)]
        if not execute_commands:
            return ["Would run: /mode focused"]
        return [command_mode(["focused"], user_id=user_id, chat_id=chat_id)]

    focus = focus_project_response(text, user_id=user_id, chat_id=chat_id, execute=execute_commands)
    if focus:
        return focus

    if looks_like_brief_request(text) and not looks_like_start_request(text):
        if not execute_commands:
            resolved = project_from_assistant_query(None, user_id=user_id, query=text)
            return [f"Would run: /updates {resolved}" if resolved else "Would run: /updates"]
        return [command_updates(None, user_id=user_id, query=text)]

    multi = multi_project_start_response(text, user_id=user_id, chat_id=chat_id, execute=execute_commands)
    if multi:
        return multi

    state = user_state(user_id)
    active = state.get("active_project")
    active_context = project_context_summary(str(active), max_files=2) if active and get_project(str(active)) else "No active project."
    system = """You are Codex Commander, a Telegram personal assistant for controlling local Codex sessions.

Convert natural language into one safe Commander slash command when the user is asking for an action.
If the user is only chatting or asking a question that should not run a command, return a concise assistant reply.

Allowed commands:
/help
/projects
/status
/doctor
/inbox
/approvals
/changes [project]
/watch [project]
/brief [project]
/morning
/next
/updates [project]
/mode [free|focused] [project]
/free
/tools
/computer [status|codex|processes|screenshot]
/browser inspect <url>
/clickup [status|recent] [query]
/skills [query]
/plugins
/mcp [help|request|find|add]
/env
/system
/clipboard [show|set|clear]
/cleanup
/open url <url>
/open app <allowlisted_app>
/file <project> <relative_path> [lines]
/volume up|down|mute [steps]
/focus <project>
/context [project]
/start <project> "<task>"
/log [project] [lines]
/diff [project]
/stop [project]
/commit [project] "<message>"
/push [project]
/approve <project> [approval_id]
/cancel <project> [approval_id]
/check
/heartbeat on [minutes]
/heartbeat off
/heartbeat status
/heartbeat now
/heartbeat quiet 23:00 08:00
/remember [global|project] <note>
/memory [all|global|user|project] [query]
/forget <memory_id>
/profile [project]
/queue
/queue add <project> "<task>"
/queue start <task_id>

Rules:
- Return JSON only.
- Never invent or run raw shell commands.
- Prefer the active project when the user says "this project", "it", or omits the project.
- If the user names a project that is not in Registered projects, return a reply saying it is not registered instead of using the active project.
- If the user asks to work/fix/audit/build in a project, map to /start.
- If the user asks for automatic updates, map to /heartbeat on <minutes>.
- If the user asks not to receive updates at night, map to /heartbeat quiet 23:00 08:00 unless they specify times.
- If the user asks for a morning brief, wake-up report, or what matters now, map to /morning.
- If the user asks what to do next or for recommendations, map to /next.
- If the user asks for a summary, latest updates, what changed, or what happened, map to /updates.
- If the user asks to switch to free/general/computer mode, map to /mode free.
- If the user asks to focus on a project or use focused mode, map to /mode focused <project> when a project is named.
- If the user asks what tools, MCPs, skills, plugins, connectors, or integrations are available, map to /tools.
- If the user asks to install, connect, add, set up, wire, research, or find an MCP, map to /mcp request <original request>.
- If the user specifically asks for MCP status/list/help, map to /mcp.
- If the user specifically asks for skills, map to /skills.
- If the user specifically asks for plugins, map to /plugins.
- If the user asks for a health check, doctor, diagnostic, or self-test, map to /doctor.
- If the user asks what needs their attention, decisions, inbox, or pending items, map to /inbox.
- If the user asks for approvals, pending approvals, approve list, or decisions to approve/cancel, map to /approvals.
- If the user asks for changed projects, dirty worktrees, local changes, or changes across projects, map to /changes.
- If the user asks to watch progress, see the live view, or understand what Codex is doing, map to /watch.
- If the user asks what keys/env setup is missing, map to /env.
- If the user asks device, battery, disk, memory, or system status, map to /system.
- If the user asks to peek at clipboard, map to /clipboard show. Do not set clipboard unless explicitly requested.
- If the user asks about cleanup, storage, or freeing disk space, map to /cleanup. Do not delete files.
- If the user asks to open or visit a website, map to /open url <url>.
- If the user asks to inspect, check, or summarize a website, map to /browser inspect <url>.
- If the user asks to check ClickUp, map to /clickup recent with query terms if present.
- If the user asks to open an app, map to /open app <allowlisted_app>.
- If the user asks to lower, raise, or mute system volume, map to /volume.
- If the user asks for a screenshot or screen capture, map to /computer screenshot.
- If the user asks what Codex is doing at computer/process level, map to /computer codex.
- If the user asks to read a local file, map to /file only when a registered project and relative path are clear.
- If the user asks what is happening with running sessions only, map to /status or /log for the active project.
- If the user says to remember or learn a preference/fact, map to /remember.
- If the user asks what Commander knows/remembers, map to /memory.
- If the user asks about project setup/stack/checks, map to /profile.
- If the user asks about pending tasks, queued work, or the work backlog, map to /queue.
- Commit and push are safe to prepare because Commander still requires /approve.

JSON schema:
{"kind":"command","command":"/status","spoken_summary":"I will check active sessions."}
or
{"kind":"reply","reply":"Short answer."}
"""
    user = f"""User: {user_name} ({user_id})
Message: {text}
Active project: {active or "none"}

Registered projects:
{project_brief()}

Sessions:
{session_brief()}

Relevant Commander memory:
{memory_brief(user_id, project_id=str(active) if active else None, query=text, limit=8)}

Active project context:
{active_context}
"""
    try:
        routed = openai_chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        print(f"{utc_now()} natural router unavailable: {redact(str(exc))}", flush=True)
        if re.search(r"\b(mcp|mcps)\b", lowered):
            return handle_text(f"/mcp request {text}", user_id=user_id, user_name=user_name, channel=channel, chat_id=chat_id, allow_natural=False)
        return ["Natural-language routing is temporarily unavailable. I did not run anything.\nUse /help or a slash command for now."]

    kind = str(routed.get("kind", "")).lower()
    if kind == "reply":
        return [compact(str(routed.get("reply", "I am here.")))]
    if kind != "command":
        return ["I could not decide on a safe Commander action. Use /help or a slash command."]

    try:
        command = validate_generated_command(str(routed.get("command", "")))
    except Exception as exc:
        return [f"I blocked that routed action: {redact(str(exc))}"]

    summary = str(routed.get("spoken_summary", "I translated that into a Commander command.")).strip()
    responses = [f"{summary}\nCommand: {command}"]
    if not execute_commands:
        responses.append(f"Would run: {command}")
        return responses
    responses.extend(handle_text(command, user_id=user_id, user_name=user_name, channel=channel, chat_id=chat_id, allow_natural=False))
    return responses


def project_and_rest(args: list[str], user_id: str, allow_active: bool | None = None) -> tuple[str | None, list[str]]:
    if allow_active is None:
        allow_active = allows_active_project_fallback(user_id)
    if not args:
        return (resolve_project_id(None, user_id=user_id) if allow_active else None), []
    for span in range(min(3, len(args)), 0, -1):
        candidate = " ".join(args[:span])
        resolved = resolve_project_id(candidate, user_id=None)
        if resolved:
            return resolved, args[span:]
    return (resolve_project_id(None, user_id=user_id) if allow_active else None), args


def handle_text(
    text: str,
    user_id: str,
    user_name: str = "unknown",
    channel: str = "telegram",
    chat_id: int | str | None = None,
    allow_natural: bool = True,
) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    tokens = parse_message(text)
    if not tokens:
        return []
    command = tokens[0].split("@", 1)[0].lower()
    args = tokens[1:]

    allow_cfg = allowlist_config()
    if command == "/whoami" and allow_cfg.get("allow_whoami_for_unauthorized", True):
        return [f"Your {channel} user ID is: {user_id}\nUser: {user_name}"]

    if channel != "local" and not is_authorized(user_id):
        configured = bool(allowed_user_ids())
        hint = "Add this ID to allowlist.json or TELEGRAM_ALLOWED_USER_IDS." if not configured else "This user ID is not allowlisted."
        return [f"Unauthorized.\nYour user ID: {user_id}\n{hint}"]

    if channel != "local" and chat_id is not None:
        update_user_state(user_id, {"last_chat_id": chat_id, "last_seen_at": utc_now()})

    if not command.startswith("/"):
        if allow_natural:
            return natural_language_response(text, user_id=user_id, user_name=user_name, chat_id=chat_id, channel=channel)
        return ["Natural-language routing was disabled for this internal command."]

    if command in ("/help", "/start_help"):
        return [command_help()]
    if command == "/check":
        return [command_check()]
    if command == "/projects":
        show_details = bool(args and args[0].lower() in {"full", "details", "detail", "paths", "path"})
        return [command_projects(show_details=show_details)]
    if command == "/status":
        return [command_status()]
    if command == "/doctor":
        return [command_doctor(user_id=user_id)]
    if command == "/inbox":
        return [command_inbox(user_id=user_id)]
    if command == "/approvals":
        return [command_approvals()]
    if command == "/changes":
        return [command_changes(args, user_id=user_id)]
    if command in {"/watch", "/timeline"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
        return [command_watch(project_id, user_id=user_id)]
    if command == "/plan":
        project_id, rest = project_and_rest(args, user_id=user_id)
        return [command_plan(project_id, user_id=user_id, task=" ".join(rest).strip() or None)]
    if command == "/brief":
        project_id, _rest = project_and_rest(args, user_id=user_id)
        return [command_brief(project_id, user_id=user_id)]
    if command == "/morning":
        return [command_morning(user_id=user_id)]
    if command == "/next":
        return [command_next(user_id=user_id)]
    if command == "/updates":
        project_id, _rest = project_and_rest(args, user_id=user_id)
        return [command_updates(project_id, user_id=user_id)]
    if command == "/mode":
        return [command_mode(args, user_id=user_id, chat_id=chat_id)]
    if command == "/free":
        return [command_mode(["free"], user_id=user_id, chat_id=chat_id)]
    if command == "/tools":
        return [command_tools()]
    if command == "/computer":
        return [command_computer(args, user_id=user_id)]
    if command == "/browser":
        return [command_browser(args)]
    if command == "/clickup":
        return [command_clickup(args)]
    if command == "/skills":
        return [command_skills(args)]
    if command == "/plugins":
        return [command_plugins()]
    if command == "/mcp":
        return [command_mcp(args)]
    if command == "/env":
        return [command_env()]
    if command == "/system":
        return [command_system()]
    if command == "/clipboard":
        return [command_clipboard(args)]
    if command == "/cleanup":
        return [command_cleanup(args)]
    if command == "/open":
        return [command_open(args)]
    if command == "/file":
        return [command_file(args, user_id=user_id)]
    if command == "/volume":
        return [command_volume(args)]
    if command == "/focus":
        if not args:
            return ["Usage: /focus <project>\n\n" + command_projects(show_details=False)]
        return [command_focus(" ".join(args), user_id=user_id, chat_id=chat_id)]
    if command == "/context":
        detail_args = [arg for arg in args if arg.lower() in {"full", "details", "detail", "paths", "path"}]
        project_args = [arg for arg in args if arg.lower() not in {"full", "details", "detail", "paths", "path"}]
        return [command_context(" ".join(project_args) if project_args else None, user_id=user_id, show_details=bool(detail_args))]
    if command == "/heartbeat":
        return [command_heartbeat(args, user_id=user_id, chat_id=chat_id)]
    if command == "/remember":
        return [command_remember(args, user_id=user_id)]
    if command == "/memory":
        return [command_memory(args, user_id=user_id)]
    if command == "/forget":
        return [command_forget(args[0] if args else None, user_id=user_id)]
    if command == "/profile":
        return [command_profile(" ".join(args) if args else None, user_id=user_id)]
    if command == "/queue":
        return [command_queue(args, user_id=user_id)]
    if command == "/start":
        project_id, rest = project_and_rest(args, user_id=user_id)
        if not project_id or not rest:
            return ['Usage: /start <project> "<task>" or set /focus <project> first']
        update_user_state(user_id, {"active_project": project_id, "active_project_set_at": utc_now(), **({"last_chat_id": chat_id} if chat_id is not None else {})})
        return [start_codex(project_id, " ".join(rest), user_id=user_id)]
    if command == "/log":
        project_id, rest = project_and_rest(args, user_id=user_id)
        if not project_id:
            return ["Usage: /log <project> [lines] or set /focus <project> first"]
        lines = DEFAULT_LOG_LINES
        if rest and rest[0].isdigit():
            lines = max(20, min(250, int(rest[0])))
        return [command_log(project_id, lines)]
    if command == "/diff":
        project_id, _ = project_and_rest(args, user_id=user_id)
        if not project_id:
            return ["Usage: /diff <project> or set /focus <project> first"]
        return [command_diff(project_id)]
    if command == "/stop":
        project_id, _ = project_and_rest(args, user_id=user_id)
        if not project_id:
            return ["Usage: /stop <project> or set /focus <project> first"]
        return [command_stop(project_id)]
    if command == "/commit":
        project_id, rest = project_and_rest(args, user_id=user_id)
        if not project_id or not rest:
            return ['Usage: /commit <project> "<message>" or set /focus <project> first']
        return [command_commit(project_id, " ".join(rest))]
    if command == "/push":
        project_id, _ = project_and_rest(args, user_id=user_id)
        if not project_id:
            return ["Usage: /push <project> or set /focus <project> first"]
        return [command_push(project_id)]
    if command == "/approve":
        if not args:
            return ["Usage: /approve <project> [approval_id]"]
        if args[0].lower() in {"commander", "mcp", "global"}:
            return [execute_pending("commander", args[1] if len(args) > 1 else None)]
        project_id, rest = project_and_rest(args, user_id=user_id)
        if not project_id:
            return ["Usage: /approve <project> [approval_id]"]
        return [execute_pending(project_id, rest[0] if rest else None)]
    if command == "/cancel":
        if not args:
            return ["Usage: /cancel <project> [approval_id]"]
        if args[0].lower() in {"commander", "mcp", "global"}:
            return [command_cancel("commander", args[1] if len(args) > 1 else None)]
        project_id, rest = project_and_rest(args, user_id=user_id)
        if not project_id:
            return ["Usage: /cancel <project> [approval_id]"]
        return [command_cancel(project_id, rest[0] if rest else None)]

    return ["Unknown command.\n\n" + command_help()]


class TelegramBot(TelegramTransport):
    def __init__(self, token: str) -> None:
        super().__init__(
            token,
            commands=TELEGRAM_COMMANDS,
            short_description="Codex Commander for local Codex sessions.",
            description=(
                "Send slash commands, natural language, or voice notes. "
                "Commander controls registered local Codex projects with approval gates."
            ),
            max_download_bytes=MAX_OPENAI_AUDIO_BYTES,
            formatter=telegram_html,
            redactor=redact,
            splitter=split_for_telegram,
        )

    def keyboard_for_user(self, user_id: str | None = None, text: str = "") -> dict[str, Any]:
        rows: list[list[dict[str, str]]] = contextual_button_rows(text, user_id=user_id)
        rows.extend(
            [
                [{"text": label, "callback_data": data} for label, data in row]
                for row in DEFAULT_BUTTON_ROWS
            ]
        )
        if user_id:
            state = user_state(user_id)
            active = state.get("active_project")
            session = sessions_data().get("sessions", {}).get(str(active), {}) if active else {}
            if active and session.get("state") == "running":
                rows.append([telegram_button(f"Stop {active}", f"/stop {active}")])
            pending = session.get("pending_actions") or {}
            for pending_id, action in list(pending.items())[:2]:
                action_type = action.get("type", "action")
                rows.append(
                    [
                        telegram_button(f"Approve {action_type}", f"/approve {active} {pending_id}"),
                        telegram_button("Cancel", f"/cancel {active} {pending_id}"),
                    ]
                )
        return {"inline_keyboard": dedupe_button_rows(rows)}

    def send_response(self, chat_id: int | str, user_id: str | None, text: str) -> None:
        markup = self.keyboard_for_user(user_id, text=text) if should_attach_buttons(text) else None
        self.send_message(chat_id, text, reply_markup=markup)


def encode_multipart_form(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    file_name: str,
    content_type: str,
) -> tuple[bytes, str]:
    boundary = "----codex-commander-" + secrets.token_hex(16)
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def audio_content_type(path: Path, telegram_mime_type: str | None) -> str:
    if telegram_mime_type:
        return telegram_mime_type
    suffix = path.suffix.lower()
    if suffix in {".ogg", ".oga"}:
        return "audio/ogg"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".webm":
        return "audio/webm"
    return "application/octet-stream"


def transcribe_audio(path: Path, telegram_mime_type: str | None = None) -> str:
    cfg = openai_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing in .env.")
    if path.stat().st_size > MAX_OPENAI_AUDIO_BYTES:
        raise RuntimeError("Audio file is over the OpenAI transcription upload limit of 25 MB.")

    file_name = path.name
    if path.suffix.lower() == ".oga":
        file_name = path.with_suffix(".ogg").name
    fields = {
        "model": cfg["transcribe_model"],
        "response_format": "json",
    }
    if cfg["transcribe_prompt"]:
        fields["prompt"] = cfg["transcribe_prompt"]

    body, content_type = encode_multipart_form(
        fields=fields,
        file_field="file",
        file_path=path,
        file_name=file_name,
        content_type=audio_content_type(path, telegram_mime_type),
    )
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI transcription failed: HTTP {exc.code}: {redact(error_body)}") from exc

    transcript = str(result.get("text", "")).strip()
    if not transcript:
        raise RuntimeError("OpenAI returned an empty transcript.")
    return transcript


def handle_voice_message(
    bot: TelegramBot,
    message: dict[str, Any],
    user_id: str,
    user_name: str,
    chat_id: int | str | None = None,
) -> list[str]:
    if not is_authorized(user_id):
        configured = bool(allowed_user_ids())
        hint = "Add this ID to allowlist.json or TELEGRAM_ALLOWED_USER_IDS." if not configured else "This user ID is not allowlisted."
        return [f"Unauthorized.\nYour user ID: {user_id}\n{hint}"]

    media = message.get("voice") or message.get("audio")
    if not media:
        return []
    file_id = str(media.get("file_id", ""))
    if not file_id:
        return ["Telegram voice message did not include a file_id."]
    file_size = int(media.get("file_size", 0) or 0)
    if file_size > MAX_OPENAI_AUDIO_BYTES:
        return ["Voice message is too large for transcription. Keep it under 25 MB."]

    mime_type = media.get("mime_type")
    local_path = bot.download_file(file_id, VOICE_DIR, preferred_suffix=".ogg")
    transcript = transcribe_audio(local_path, telegram_mime_type=mime_type)
    command = normalize_voice_command(transcript)
    responses = [f"Transcript: {transcript}\nCommand: {command}"]
    responses.extend(handle_text(command, user_id=user_id, user_name=user_name, channel="telegram", chat_id=chat_id))
    return responses


def handle_unsupported_media_message(message: dict[str, Any]) -> list[str]:
    if message.get("photo"):
        return ["I received image(s), but image understanding is not wired into Commander yet. Describe what you want me to do, or send a text/voice command."]
    if message.get("document"):
        document = message.get("document") or {}
        mime_type = str(document.get("mime_type", ""))
        if mime_type.startswith("image/"):
            return ["I received an image file, but image understanding is not wired into Commander yet. Describe what you want me to do, or send a text/voice command."]
        return ["I received a document, but document handling is not wired into Commander yet."]
    if message.get("video"):
        return ["I received a video, but video handling is not wired into Commander yet."]
    return []


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def heartbeat_loop(bot: TelegramBot) -> None:
    while True:
        try:
            now = dt.datetime.now(dt.timezone.utc)
            now_local = local_now()
            data = state_data()
            changed = False
            for user_id, state in data.get("users", {}).items():
                if not state.get("heartbeat_enabled"):
                    continue
                chat_id = state.get("heartbeat_chat_id") or state.get("last_chat_id")
                if not chat_id:
                    continue
                interval = int(state.get("heartbeat_interval_minutes") or DEFAULT_HEARTBEAT_MINUTES)
                next_at = parse_iso_datetime(state.get("heartbeat_next_at"))
                if next_at and next_at > now:
                    continue
                start, end = user_quiet_hours(state)
                if start and end:
                    quiet_end = quiet_end_datetime(now_local, start, end)
                    if quiet_end:
                        state["heartbeat_next_at"] = quiet_end.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
                        changed = True
                        continue
                bot.send_response(chat_id, str(user_id), heartbeat_summary(str(user_id)))
                state["heartbeat_next_at"] = next_heartbeat_at(interval, state).isoformat(timespec="seconds")
                state["heartbeat_last_sent_at"] = utc_now()
                changed = True
            if changed:
                save_state(data)
        except Exception as exc:
            print(f"Heartbeat error: {redact(str(exc))}", flush=True)
        time.sleep(30)


def log_outgoing(user_id: str, text: str) -> None:
    first = (text or "").strip().splitlines()
    preview = first[0] if first else "(empty)"
    print(f"{utc_now()} {user_id} [reply] {redact(preview)[:220]}", flush=True)


def is_transient_poll_exception(exc: BaseException) -> bool:
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError))


def poll_forever() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Create .env from .env.example first.")

    bot = TelegramBot(token)
    if os.environ.get("TELEGRAM_CONFIGURE_BOT_ON_START", "true").lower() not in {"0", "false", "no"}:
        try:
            bot.configure_commands()
            print("Telegram command menu configured.", flush=True)
        except Exception as exc:
            print(f"Telegram command menu setup failed: {redact(str(exc))}", flush=True)
    threading.Thread(target=heartbeat_loop, args=(bot,), daemon=True).start()
    offset: int | None = telegram_update_offset()
    print("Codex Commander Telegram polling started.", flush=True)
    while True:
        try:
            updates = bot.get_updates(offset)
            for update in updates:
                offset = int(update["update_id"]) + 1
                save_telegram_update_offset(offset)
                callback = update.get("callback_query") or {}
                if callback:
                    callback_id = str(callback.get("id", ""))
                    callback_data = str(callback.get("data", ""))
                    callback_message = callback.get("message") or {}
                    chat_id = (callback_message.get("chat") or {}).get("id")
                    sender = callback.get("from") or {}
                    user_id = str(sender.get("id", ""))
                    user_name = sender.get("username") or sender.get("first_name") or "unknown"
                    if callback_id:
                        bot.answer_callback_query(callback_id, "Running")
                    if not chat_id or not callback_data.startswith("cmd:"):
                        continue
                    command_text = callback_data.removeprefix("cmd:")
                    print(f"{utc_now()} {user_id} [button] {redact(command_text)}", flush=True)
                    for response in handle_text(command_text, user_id=user_id, user_name=user_name, channel="telegram", chat_id=chat_id):
                        log_outgoing(user_id, response)
                        bot.send_response(chat_id, user_id, response)
                    continue
                message = update.get("message") or {}
                text = message.get("text") or ""
                chat = message.get("chat") or {}
                sender = message.get("from") or {}
                chat_id = chat.get("id")
                user_id = str(sender.get("id", ""))
                user_name = sender.get("username") or sender.get("first_name") or "unknown"
                if not chat_id:
                    continue
                if text:
                    print(f"{utc_now()} {user_id} {redact(text)}", flush=True)
                    for response in handle_text(text, user_id=user_id, user_name=user_name, channel="telegram", chat_id=chat_id):
                        log_outgoing(user_id, response)
                        bot.send_response(chat_id, user_id, response)
                    continue
                if message.get("voice") or message.get("audio"):
                    print(f"{utc_now()} {user_id} [voice/audio message]", flush=True)
                    try:
                        responses = handle_voice_message(bot, message, user_id=user_id, user_name=user_name, chat_id=chat_id)
                    except Exception as exc:
                        responses = [f"Voice command failed: {redact(str(exc))}"]
                    for response in responses:
                        log_outgoing(user_id, response)
                        bot.send_response(chat_id, user_id, response)
                    continue
                unsupported = handle_unsupported_media_message(message)
                if unsupported:
                    print(f"{utc_now()} {user_id} [unsupported media]", flush=True)
                    for response in unsupported:
                        log_outgoing(user_id, response)
                        bot.send_response(chat_id, user_id, response)
        except KeyboardInterrupt:
            print("Stopping Codex Commander.", flush=True)
            return
        except RuntimeError as exc:
            print(f"Polling error: {redact(str(exc))}", flush=True)
            time.sleep(5)
        except Exception as exc:
            if not is_transient_poll_exception(exc):
                raise
            print(f"Polling error: {redact(str(exc))}", flush=True)
            time.sleep(5)


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="Telegram-controlled Codex Commander")
    parser.add_argument("--poll", action="store_true", help="Start Telegram polling")
    parser.add_argument("--check", action="store_true", help="Check local configuration")
    parser.add_argument("--set-telegram-commands", action="store_true", help="Configure Telegram slash-command menu")
    parser.add_argument("--local", metavar="COMMAND", help='Run a Commander command locally, e.g. --local "/projects"')
    parser.add_argument("--local-nl", metavar="TEXT", help="Route a natural-language message locally")
    parser.add_argument("--local-voice", metavar="TRANSCRIPT", help="Normalize and run a simulated voice transcript locally")
    parser.add_argument("--user-id", default="local", help="User ID for --local command")
    args = parser.parse_args()

    if args.check:
        print(command_check())
        return 0
    if args.set_telegram_commands:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
        TelegramBot(token).configure_commands()
        print("Telegram command menu configured.")
        return 0
    if args.local:
        for response in handle_text(args.local, user_id=args.user_id, user_name="local", channel="local"):
            print(response)
        return 0
    if args.local_nl:
        for response in natural_language_response(
            args.local_nl,
            user_id=args.user_id,
            user_name="local",
            chat_id=None,
            channel="local",
            execute_commands=False,
        ):
            print(response)
        return 0
    if args.local_voice:
        command = normalize_voice_command(args.local_voice)
        print(f"Transcript: {args.local_voice}")
        print(f"Command: {command}")
        for response in handle_text(command, user_id=args.user_id, user_name="local", channel="local"):
            print(response)
        return 0
    if args.poll:
        poll_forever()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
