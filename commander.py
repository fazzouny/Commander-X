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
import base64
import datetime as dt
import html
import json
import os
import re
import secrets
import shlex
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
from commanderx.projects import build_project_alias_map, mentioned_projects as detect_mentioned_projects, normalized_project_text, resolve_project
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
AUDIT_FILE = BASE_DIR / "audit_log.json"
TASKS_FILE = BASE_DIR / "tasks.json"
PROFILES_FILE = BASE_DIR / "project_profiles.json"
COMPUTER_TOOLS_FILE = BASE_DIR / "computer_tools.json"
SYSTEM_PROMPT_FILE = BASE_DIR / "system_prompt.md"
LOG_DIR = BASE_DIR / "logs"
VOICE_DIR = LOG_DIR / "voice"
IMAGE_DIR = LOG_DIR / "images"
SCREENSHOT_DIR = LOG_DIR / "screenshots"
DEFAULT_REPORT_DIR = BASE_DIR / "reports"
ENV_FILE = BASE_DIR / ".env"
SESSION_LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen[str]] = {}

WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

MAX_TELEGRAM_MESSAGE = 3900
DEFAULT_LOG_LINES = 80
MAX_OPENAI_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_OPENAI_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DEFAULT_HEARTBEAT_MINUTES = 30
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "08:00"
NL_ALLOWED_COMMANDS = {
    "/whoami",
    "/help",
    "/projects",
    "/status",
    "/service",
    "/doctor",
    "/inbox",
    "/approvals",
    "/changes",
    "/feed",
    "/briefs",
    "/mission",
    "/evidence",
    "/replay",
    "/playback",
    "/review",
    "/reviews",
    "/objective",
    "/done",
    "/verify",
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
    "/openclaw",
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
    "/audit",
    "/report",
    "/check",
    "/focus",
    "/context",
    "/heartbeat",
    "/remember",
    "/memory",
    "/forget",
    "/profile",
    "/queue",
    "/autopilot",
}
TELEGRAM_COMMANDS = [
    ("help", "Show Commander commands"),
    ("projects", "List registered projects"),
    ("status", "Show active Codex sessions"),
    ("service", "Show Commander service health"),
    ("doctor", "Run Commander health check"),
    ("inbox", "Show decision inbox"),
    ("approvals", "List pending approvals"),
    ("changes", "Show changed projects"),
    ("feed", "Show plain-English work feed"),
    ("briefs", "Show executive Codex briefs"),
    ("mission", "Show mission-control timeline"),
    ("evidence", "Show clean session evidence"),
    ("replay", "Show a plain-English session replay"),
    ("playback", "Show the operator playback view"),
    ("review", "Show owner review pack"),
    ("reviews", "List saved owner review packs"),
    ("objective", "Set or show project objective"),
    ("done", "Check project completion proof"),
    ("verify", "Run project verification checks"),
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
    ("openclaw", "Show OpenClaw install/status"),
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
    ("audit", "Show approval audit history"),
    ("report", "Show operator report"),
    ("heartbeat", "Manage automatic status updates"),
    ("remember", "Save a Commander memory"),
    ("memory", "Show saved Commander memories"),
    ("forget", "Delete a Commander memory"),
    ("profile", "Show project profile"),
    ("queue", "Show or manage task queue"),
    ("autopilot", "Manage autonomous project build loop"),
    ("check", "Check Commander config"),
    ("whoami", "Show your Telegram user ID"),
]
DEFAULT_BUTTON_ROWS = [
    [("Status", "cmd:/status"), ("Projects", "cmd:/projects")],
    [("Service", "cmd:/service"), ("Doctor", "cmd:/doctor")],
    [("Mode", "cmd:/mode"), ("Free Mode", "cmd:/free")],
    [("Mission", "cmd:/mission"), ("Briefs", "cmd:/briefs"), ("Feed", "cmd:/feed")],
    [("Playback", "cmd:/playback"), ("Evidence", "cmd:/evidence"), ("Replay", "cmd:/replay")],
    [("Done?", "cmd:/done"), ("Objective", "cmd:/objective")],
    [("Report", "cmd:/report"), ("Reviews", "cmd:/reviews")],
    [("Morning", "cmd:/morning"), ("Next", "cmd:/next")],
    [("Inbox", "cmd:/inbox"), ("Approvals", "cmd:/approvals"), ("Audit", "cmd:/audit")],
    [("Save Report", "cmd:/report save")],
    [("Changes", "cmd:/changes"), ("Log", "cmd:/log"), ("Diff", "cmd:/diff")],
    [("Context", "cmd:/context")],
    [("Queue", "cmd:/queue"), ("Profile", "cmd:/profile")],
    [("Heartbeat Now", "cmd:/heartbeat now"), ("Heartbeat Off", "cmd:/heartbeat off")],
]

SETUP_CAPABILITIES = [
    {
        "id": "telegram",
        "title": "Telegram control channel",
        "keys": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS"],
        "purpose": "Lets Commander receive private Telegram messages and reject everyone else.",
        "next_step": "Create a BotFather token, run /whoami, then add your user ID to TELEGRAM_ALLOWED_USER_IDS.",
        "required": True,
    },
    {
        "id": "openai",
        "title": "Voice, image, and natural-language intelligence",
        "keys": ["OPENAI_API_KEY"],
        "purpose": "Enables voice-note transcription, image understanding, and smarter natural-language routing.",
        "next_step": "Add OPENAI_API_KEY to .env, then restart Commander.",
        "required": False,
    },
    {
        "id": "clickup",
        "title": "ClickUp task and campaign bridge",
        "keys": ["CLICKUP_API_TOKEN", "CLICKUP_WORKSPACE_ID"],
        "purpose": "Lets Commander answer campaign and task questions from ClickUp.",
        "next_step": "Add CLICKUP_API_TOKEN and CLICKUP_WORKSPACE_ID when you want ClickUp connected.",
        "required": False,
    },
    {
        "id": "github",
        "title": "GitHub PR and issue workflows",
        "keys": ["GITHUB_TOKEN"],
        "purpose": "Lets Commander prepare GitHub PR and issue workflows later.",
        "next_step": "Add GITHUB_TOKEN if you want Commander to work with GitHub beyond local git.",
        "required": False,
    },
    {
        "id": "whatsapp",
        "title": "WhatsApp control channel",
        "keys": ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"],
        "purpose": "Unlocks the later WhatsApp bot channel after Telegram.",
        "next_step": "Add WhatsApp Cloud API keys when you are ready to test WhatsApp control.",
        "required": False,
    },
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
    match = re.search(
        r"^OpenClaw (clone|start) prepared\b.*?Pending approval ID:\s*([A-Za-z0-9]+)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return f"openclaw {match.group(1).lower()}", "commander", match.group(2)
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
            if "openclaw" in action_type:
                rows.append([telegram_button("OpenClaw status", "/openclaw"), telegram_button("Recovery", "/openclaw recover")])
            else:
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
        "models": ["OPENAI_COMMAND_MODEL", "OPENAI_IMAGE_MODEL", "OPENAI_TRANSCRIBE_MODEL", "OPENAI_VOICE_MODEL", "OPENAI_VOICE"],
        "dashboard": ["COMMANDER_DASHBOARD_HOST", "COMMANDER_DASHBOARD_PORT", "COMMANDER_DASHBOARD_TOKEN"],
        "clickup": ["CLICKUP_API_TOKEN", "CLICKUP_WORKSPACE_ID"],
        "meta_ads": ["META_APP_ID", "META_APP_SECRET", "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_BUSINESS_ID"],
        "whatsapp": ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET"],
        "github": ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_DEFAULT_REPO"],
        "browser": ["COMMANDER_BROWSER_HEADLESS", "COMMANDER_BROWSER_TIMEOUT_SECONDS"],
        "openclaw": ["COMMANDER_OPENCLAW_LAUNCHER", "COMMANDER_OPENCLAW_REPO_URL", "COMMANDER_OPENCLAW_INSTALL_TARGET"],
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
    if readiness.get("models", {}).get("OPENAI_IMAGE_MODEL") == "missing" and os.environ.get("OPENAI_COMMAND_MODEL"):
        readiness["models"]["OPENAI_IMAGE_MODEL"] = "configured via OPENAI_COMMAND_MODEL fallback"
    return readiness


def setup_status_items(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    env = os.environ if env is None else env
    rows: list[dict[str, Any]] = []
    for capability in SETUP_CAPABILITIES:
        keys = list(capability["keys"])
        missing = [key for key in keys if not str(env.get(key, "")).strip()]
        configured = len(keys) - len(missing)
        if not missing:
            state = "ready"
        elif configured:
            state = "partial"
        else:
            state = "missing"
        rows.append(
            {
                "id": capability["id"],
                "title": capability["title"],
                "purpose": capability["purpose"],
                "next_step": capability["next_step"],
                "required": bool(capability.get("required")),
                "keys": keys,
                "missing_keys": missing,
                "configured": configured,
                "total": len(keys),
                "state": state,
            }
        )
    return rows


def setup_recommendation_items(limit: int = 4, env: dict[str, str] | None = None) -> list[str]:
    items: list[str] = []
    for item in setup_status_items(env=env):
        if item["state"] == "ready":
            continue
        prefix = "Required setup" if item["required"] else "Optional setup"
        missing = ", ".join(item["missing_keys"])
        state_text = "is partially configured" if item["state"] == "partial" else "needs setup"
        items.append(
            f"{prefix}: {item['title']} {state_text}. {item['purpose']} "
            f"Next: {item['next_step']} Missing: {missing}."
        )
        if len(items) >= limit:
            break
    return items


def openai_config() -> dict[str, str]:
    return {
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "command_model": os.environ.get("OPENAI_COMMAND_MODEL", "gpt-4o-mini"),
        "image_model": os.environ.get("OPENAI_IMAGE_MODEL", os.environ.get("OPENAI_COMMAND_MODEL", "gpt-4o-mini")),
        "transcribe_model": os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        "transcribe_prompt": os.environ.get(
            "OPENAI_TRANSCRIBE_PROMPT",
            (
                "Transcribe this as a command for Codex Commander. Preserve project IDs, "
                "approval IDs, branch names, command words, and technical terms exactly."
            ),
        ),
        "image_prompt": os.environ.get(
            "OPENAI_IMAGE_PROMPT",
            (
                "Analyze this Telegram image for Commander X. Summarize the useful operator context, "
                "visible issue, important visible text, and safest next Commander actions. Never reproduce secrets, "
                "tokens, private keys, credentials, or full local paths."
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


def audit_data() -> dict[str, Any]:
    return read_json(AUDIT_FILE, {"events": []})


def save_audit(data: dict[str, Any]) -> None:
    write_json(AUDIT_FILE, data)


def audit_clean(value: Any, limit: int = 500) -> str:
    return safe_brief_text(compact(str(value or "-"), limit=limit))


def audit_event_summary(action: dict[str, Any]) -> str:
    action_type = str(action.get("type") or "action")
    if action_type == "commit":
        return f"Commit prepared: {audit_clean(action.get('message') or '-')}"
    if action_type == "push":
        return f"Push prepared for branch {audit_clean(action.get('branch') or '-')}"
    if action_type == "mcp_add":
        name = audit_clean(action.get("name") or "MCP server")
        command = " ".join(str(item) for item in action.get("command", [])[:4]) if isinstance(action.get("command"), list) else ""
        return f"MCP install prepared: {name}" + (f" via {audit_clean(command)}" if command else "")
    if action_type == "openclaw_clone":
        return f"OpenClaw clone prepared: {audit_clean(action.get('full_name') or action.get('repo_url') or '-')}"
    if action_type == "openclaw_start":
        return "OpenClaw start prepared"
    return audit_clean(action.get("message") or action_type)


def record_audit_event(
    project_id: str,
    action: dict[str, Any],
    status: str,
    approval_id: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    data = audit_data()
    action_type = str(action.get("type") or "action")
    item = {
        "id": secrets.token_hex(4),
        "at": utc_now(),
        "project": audit_clean(project_id or "commander", limit=120),
        "approval_id": audit_clean(approval_id or action.get("id") or "-", limit=80),
        "type": audit_clean(action_type, limit=80),
        "status": audit_clean(status, limit=80),
        "branch": audit_clean(action.get("branch") or "-", limit=160),
        "summary": audit_event_summary(action),
    }
    if result:
        item["result"] = audit_clean(result, limit=500)
    events = data.setdefault("events", [])
    events.append(item)
    data["events"] = events[-200:]
    save_audit(data)
    return item


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


def project_label(project_id: str, project: dict[str, Any] | None = None, include_id: bool = True) -> str:
    project = project or projects_config().get("projects", {}).get(project_id) or {}
    label = str(project.get("display_name") or project.get("name") or project_id).strip() or project_id
    if include_id and label != project_id:
        return f"{label} ({project_id})"
    return label


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
        f"Project: {project_label(project_id, project)}",
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


def normalize_done_criteria(value: Any) -> list[dict[str, str]]:
    criteria: list[dict[str, str]] = []
    if not isinstance(value, list):
        return criteria
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            text = audit_clean(item.get("text") or item.get("criterion") or item.get("title"), limit=280)
            status = audit_clean(item.get("status") or "open", limit=80).lower()
            evidence = audit_clean(item.get("evidence") or "", limit=360) if item.get("evidence") else ""
        else:
            text = audit_clean(item, limit=280)
            status = "open"
            evidence = ""
        if not text or text == "-":
            continue
        if status not in {"open", "done", "blocked", "waived"}:
            status = "open"
        criteria.append({"id": str(index), "text": text, "status": status, "evidence": evidence})
    return criteria


def profile_store(project_id: str) -> dict[str, Any]:
    data = profiles_data()
    profiles = data.setdefault("profiles", {})
    profile = profiles.setdefault(project_id, {})
    return profile if isinstance(profile, dict) else {}


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
        "objective": stored.get("objective") or "",
        "done_criteria": normalize_done_criteria(stored.get("done_criteria") or []),
        "context_files": [str(item) for item in context_files],
        "notes": stored.get("notes", []),
        "risk_rules": stored.get("risk_rules", []),
        "autopilot": stored.get("autopilot") if isinstance(stored.get("autopilot"), dict) else {},
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
    if profile.get("objective"):
        lines.extend(["", f"Objective: {profile.get('objective')}"])
    done_criteria = profile.get("done_criteria") or []
    if done_criteria:
        lines.extend(["", "Definition of Done:"])
        for item in done_criteria[:10]:
            if isinstance(item, dict):
                suffix = f" - {item.get('evidence')}" if item.get("evidence") else ""
                lines.append(f"- [{item.get('status')}] {item.get('text')}{suffix}")
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


def verification_evidence_from_text(text: str, limit: int = 8) -> list[str]:
    checks: list[str] = []
    patterns = [
        (r"\bpy_compile\b", "Python compile check"),
        (r"\bunittest\b|\bpytest\b", "Python test suite"),
        (r"\bnpm\s+(?:run\s+)?test\b|\bvitest\b|\bjest\b", "JavaScript test suite"),
        (r"\bnpm\s+run\s+build\b|\bnext\s+build\b|\bvite\s+build\b", "Production build check"),
        (r"\bnpm\s+run\s+lint\b|\beslint\b", "Lint check"),
        (r"\bnpm\s+run\s+typecheck\b|\btsc\b", "Type check"),
        (r"\bplaywright\b", "Browser test/check"),
        (r"\bsmoke[- ]?test\b|\bsmoke:\w+\b", "Smoke test"),
    ]
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        for pattern, label in patterns:
            if not re.search(pattern, lowered):
                continue
            has_result = bool(re.search(r"\b(ok|passed|pass|success|succeeded|green|failed|failure|error)\b", lowered))
            is_command_line = bool(
                re.search(r"^\s*(?:exec|[>`$]?\s*(?:npm|pnpm|yarn|python|pytest|npx)\b)", lowered)
                or re.search(r"-command\s+['\"]?[^'\"]*(?:npm|pnpm|yarn|python|pytest|npx|playwright|tsc)", lowered)
            )
            if not has_result and not is_command_line:
                continue
            result = "passed" if re.search(r"\b(ok|passed|pass|success|succeeded|green)\b", lowered) else "failed" if re.search(r"\b(failed|failure|error)\b", lowered) else "run"
            item = f"{label}: {result}"
            if item not in seen:
                checks.append(item)
                seen.add(item)
            break
        if len(checks) >= limit:
            break
    return checks


def approval_events_for_project(project_id: str, limit: int = 6) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for event in reversed(audit_data().get("events", [])):
        if not isinstance(event, dict):
            continue
        if str(event.get("project") or "") != project_id:
            continue
        events.append(
            {
                "at": audit_clean(event.get("at") or "-", limit=80),
                "status": audit_clean(event.get("status") or "recorded", limit=80),
                "type": audit_clean(event.get("type") or "action", limit=80),
                "summary": audit_clean(event.get("summary") or "-", limit=280),
            }
        )
        if len(events) >= limit:
            break
    return events


def final_summary_reports_no_blocker(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"\b(?:current\s+blocker|blocker)\s*:\s*(?:none|no\s+blockers?|nothing)\b", text, flags=re.IGNORECASE))


def session_evidence_card(project_id: str) -> dict[str, Any]:
    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(project_id) or {}
    project = get_project(project_id)
    path = project_path(project) if project else None
    plan = session.get("work_plan") if isinstance(session.get("work_plan"), dict) else {}
    log_file = Path(str(session.get("log_file", ""))) if session else Path()
    last_message_file = Path(str(session.get("last_message_file", ""))) if session else Path()
    log_text = read_recent_log_text(log_file, max_bytes=100_000) if log_file.exists() else ""
    last_message_text = read_recent_log_text(last_message_file, max_bytes=30_000) if last_message_file.exists() else ""
    signals = session.get("progress_signals") if isinstance(session.get("progress_signals"), list) else []
    timeline = timeline_lines(session, limit=6) if session else []
    changed_count = 0
    areas = "no local changes tracked"
    branch = "-"
    if path and path.exists() and is_git_repo(path):
        changed = changed_files(path)
        changed_count = len(changed)
        areas = change_bucket_summary(changed)
        branch = current_branch(path)
    process = "-"
    log_age = None
    if session:
        pid = int(session.get("pid", 0) or 0)
        process = "running" if pid and pid_running(pid) else "not running"
        if log_file.exists():
            age = dt.datetime.now().timestamp() - log_file.stat().st_mtime
            log_age = max(0, int(age // 60))
    blocker = "none reported"
    for signal in reversed(signals):
        if isinstance(signal, dict) and str(signal.get("status") or "") == "warn":
            blocker = f"{safe_brief_text(signal.get('title'))}: {safe_brief_text(signal.get('detail'))}"
            break
    if blocker == "none reported":
        mission = mission_timeline_items(user_id=None, limit=20, sessions={project_id: session} if session else {}, changes=[{"project": project_id, "changed_count": changed_count, "areas": areas}], tasks=[])
        for item in mission:
            if item.get("project") == project_id and item.get("blocker"):
                candidate = safe_brief_text(item.get("blocker"))
                if candidate.lower() not in {"review current state", "review before starting more work", "waiting for instruction", "idle"}:
                    blocker = candidate
                break
    checks = (
        verification_results_as_checks(session.get("verification_results"))
        + verification_evidence_from_text(codex_output_text(log_text))
        + verification_evidence_from_text(last_message_text)
    )
    if str(session.get("state") or "").lower() == "completed" and final_summary_reports_no_blocker(last_message_text) and checks:
        blocker = "none reported"
    expected = plan.get("expected_checks") if isinstance(plan.get("expected_checks"), list) else []
    return {
        "project": audit_clean(project_id, limit=120),
        "state": audit_clean(session.get("state") if session else "no session", limit=80),
        "process": process,
        "task": audit_clean(session.get("task") if session else "No Commander session recorded.", limit=500),
        "task_id": audit_clean(session.get("task_id") if session else "-", limit=100),
        "risk": audit_clean(plan.get("risk") or "unknown", limit=80),
        "approach": [audit_clean(item, limit=260) for item in (plan.get("approach") if isinstance(plan.get("approach"), list) else [])[:4]],
        "checks": [audit_clean(item, limit=220) for item in checks[:8]],
        "expected_checks": [audit_clean(item, limit=220) for item in expected[:5]],
        "changed_count": changed_count,
        "areas": audit_clean(areas, limit=260),
        "branch": audit_clean(session.get("branch") or branch, limit=160),
        "blocker": blocker,
        "timeline": [audit_clean(line.lstrip("- "), limit=320) for line in timeline[:6]],
        "approvals": approval_events_for_project(project_id),
        "log_age_minutes": log_age,
    }


def session_evidence_cards(user_id: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    refresh_session_states()
    projects: list[str] = []
    for project_id in sessions_data().get("sessions", {}):
        if project_id not in projects:
            projects.append(project_id)
    for item in mission_timeline_items(user_id=user_id, limit=max(limit * 2, 12)):
        project_id = str(item.get("project") or "")
        if project_id and project_id not in projects:
            projects.append(project_id)
    return [session_evidence_card(project_id) for project_id in projects[:limit]]


def format_session_evidence_card(card: dict[str, Any]) -> str:
    lines = [
        f"Evidence card: {card.get('project')}",
        f"- State: {card.get('state')} ({card.get('process')})",
        f"- Task: {card.get('task')}",
        f"- Risk: {card.get('risk')}",
        f"- Work areas: {card.get('areas')} ({card.get('changed_count')} changed)",
        f"- Blocker: {card.get('blocker')}",
    ]
    if card.get("log_age_minutes") is not None:
        lines.append(f"- Last log activity: {card.get('log_age_minutes')} min ago")
    approach = card.get("approach") if isinstance(card.get("approach"), list) else []
    if approach:
        lines.append("")
        lines.append("Planned approach:")
        lines.extend(f"{index}. {item}" for index, item in enumerate(approach[:4], start=1))
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    if checks:
        lines.append("")
        lines.append("Checks:")
        lines.extend(f"- {item}" for item in checks[:8])
    else:
        expected = card.get("expected_checks") if isinstance(card.get("expected_checks"), list) else []
        if expected:
            lines.append("")
            lines.append("Expected checks, not proof yet:")
            lines.extend(f"- {item}" for item in expected[:5])
    timeline = card.get("timeline") if isinstance(card.get("timeline"), list) else []
    if timeline:
        lines.append("")
        lines.append("Timeline:")
        lines.extend(f"- {item}" for item in timeline[:6])
    approvals = card.get("approvals") if isinstance(card.get("approvals"), list) else []
    if approvals:
        lines.append("")
        lines.append("Approvals:")
        for item in approvals[:6]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('status')} {item.get('type')}: {item.get('summary')}")
    lines.append("")
    lines.append("Technical filenames and local paths are hidden. Use /diff only when you want code-level detail.")
    return compact("\n".join(lines), limit=3600)


def session_evidence(project_id: str) -> str:
    return format_session_evidence_card(session_evidence_card(project_id))


def replay_story_from_card(card: dict[str, Any]) -> str:
    project = audit_clean(card.get("project"), limit=120)
    state = audit_clean(card.get("state") or "unknown", limit=80)
    task = audit_clean(card.get("task") or "No task recorded.", limit=360)
    areas = audit_clean(card.get("areas") or "no local changes tracked", limit=220)
    changed_count = int(card.get("changed_count") or 0)
    blocker = audit_clean(card.get("blocker") or "none reported", limit=220)
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    if checks:
        proof = f"Commander has {len(checks[:6])} verification signal{'s' if len(checks[:6]) != 1 else ''} recorded."
    else:
        proof = "Commander has no verification checks recorded yet."
    if changed_count:
        direction = f"The work is touching {areas}."
    else:
        direction = "There are no tracked local code changes yet."
    if blocker and blocker.lower() != "none reported":
        blocker_sentence = f"The current blocker is {blocker}."
    else:
        blocker_sentence = "No blocker is currently reported."
    return compact(
        f"{project} is {state}. The requested task is: {task}. {direction} {proof} {blocker_sentence}",
        limit=900,
    )


def replay_outcome_from_card(card: dict[str, Any]) -> str:
    state = str(card.get("state") or "unknown").lower()
    blocker = str(card.get("blocker") or "none reported").lower()
    changed_count = int(card.get("changed_count") or 0)
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    if "running" in state:
        return "Still in progress. The useful view is direction, checks, and blocker rather than final result."
    if "completed" in state or "stopped" in state:
        if checks and blocker in {"", "none reported", "none"}:
            return "Work appears review-ready from the recorded signals."
        if changed_count:
            return "Work has local changes and should be reviewed before commit or push."
        return "The session ended without tracked local changes."
    if "failed" in state:
        return "The session needs intervention before it should continue."
    return "Outcome is not fully known yet because this session has limited recorded state."


def replay_next_step_from_card(card: dict[str, Any], mission_item: dict[str, Any] | None = None) -> str:
    if mission_item and mission_item.get("next_step"):
        return audit_clean(mission_item.get("next_step"), limit=260)
    blocker = str(card.get("blocker") or "none reported").lower()
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    changed_count = int(card.get("changed_count") or 0)
    if blocker and blocker not in {"none reported", "none", "-"}:
        return "Decide whether Commander should continue, restart, or stop this session."
    if changed_count and checks:
        return "Review the clean evidence, then use /diff only if code-level detail is needed."
    if changed_count:
        return "Ask Commander to run checks or show evidence before approving any commit."
    return "Start or continue the project task if this work is still needed."


def session_replay_card(project_id: str) -> dict[str, Any]:
    card = session_evidence_card(project_id)
    mission_items = mission_timeline_items(
        user_id=None,
        limit=20,
        sessions={project_id: sessions_data().get("sessions", {}).get(project_id) or {}},
        changes=[
            {
                "project": project_id,
                "changed_count": card.get("changed_count", 0),
                "areas": card.get("areas", "no local changes tracked"),
            }
        ],
        tasks=[],
    )
    mission_item = next((item for item in mission_items if item.get("project") == project_id), None)
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    timeline = card.get("timeline") if isinstance(card.get("timeline"), list) else []
    approvals = card.get("approvals") if isinstance(card.get("approvals"), list) else []
    replay = {
        "project": audit_clean(card.get("project"), limit=120),
        "state": audit_clean(card.get("state"), limit=80),
        "task": audit_clean(card.get("task"), limit=420),
        "story": audit_clean(replay_story_from_card(card), limit=1000),
        "outcome": audit_clean(replay_outcome_from_card(card), limit=320),
        "work_areas": audit_clean(card.get("areas"), limit=260),
        "changed_count": int(card.get("changed_count") or 0),
        "blocker": audit_clean(card.get("blocker") or "none reported", limit=260),
        "checks": [audit_clean(item, limit=180) for item in checks[:5]],
        "decisions": [
            audit_clean(f"{item.get('status')} {item.get('type')}: {item.get('summary')}", limit=220)
            for item in approvals[:4]
            if isinstance(item, dict)
        ],
        "timeline": [audit_clean(item, limit=260) for item in timeline[:5]],
        "next_step": replay_next_step_from_card(card, mission_item),
        "freshness": audit_clean(mission_item.get("freshness") if mission_item else "unknown", limit=80),
        "last_activity_minutes": mission_item.get("last_activity_minutes") if mission_item else card.get("log_age_minutes"),
    }
    return replay


def session_replay_cards(user_id: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    projects: list[str] = []
    for card in session_evidence_cards(user_id=user_id, limit=max(limit, 6)):
        project_id = str(card.get("project") or "")
        if project_id and project_id not in projects:
            projects.append(project_id)
    return [session_replay_card(project_id) for project_id in projects[:limit]]


def format_session_replay_card(card: dict[str, Any]) -> str:
    lines = [
        f"Session replay: {card.get('project')}",
        f"- State: {card.get('state')} ({card.get('freshness')})",
        f"- Story: {card.get('story')}",
        f"- Outcome: {card.get('outcome')}",
        f"- Work areas: {card.get('work_areas')} ({card.get('changed_count')} changed)",
        f"- Blocker: {card.get('blocker')}",
        f"- Next: {card.get('next_step')}",
    ]
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    if checks:
        lines.append("")
        lines.append("Checks seen:")
        lines.extend(f"- {item}" for item in checks[:5])
    decisions = card.get("decisions") if isinstance(card.get("decisions"), list) else []
    if decisions:
        lines.append("")
        lines.append("Decisions:")
        lines.extend(f"- {item}" for item in decisions[:4])
    timeline = card.get("timeline") if isinstance(card.get("timeline"), list) else []
    if timeline:
        lines.append("")
        lines.append("What happened:")
        lines.extend(f"- {item}" for item in timeline[:5])
    lines.append("")
    lines.append("This replay hides technical filenames and local paths. Use /evidence for proof or /diff for code-level detail.")
    return compact("\n".join(lines), limit=3600)


def session_replay(project_id: str) -> str:
    return format_session_replay_card(session_replay_card(project_id))


def playback_primary_action(replay: dict[str, Any], approvals: list[dict[str, Any]]) -> str:
    project_id = audit_clean(replay.get("project"), limit=120)
    blocker = str(replay.get("blocker") or "none reported").lower()
    review_only_blockers = {"review before starting more work", "review current state", "waiting for instruction", "waiting to start"}
    changed_count = int(replay.get("changed_count") or 0)
    checks = replay.get("checks") if isinstance(replay.get("checks"), list) else []
    state = str(replay.get("state") or "").lower()
    if approvals:
        first = approvals[0]
        return f"Review pending {audit_clean(first.get('type') or 'approval', limit=80)} approval: /approvals"
    if blocker and blocker not in {"none reported", "none", "-"} and blocker not in review_only_blockers:
        return f"Inspect the blocker in plain English: /watch {project_id}"
    if changed_count and checks:
        return f"Review proof first, then code detail only if needed: /evidence {project_id}"
    if changed_count:
        return f"Ask Commander to verify before approval: /watch {project_id}"
    if "running" in state:
        return f"Watch the active run: /watch {project_id}"
    return f"Start the next task when ready: /start {project_id} \"task\""


def playback_confidence(replay: dict[str, Any], approvals: list[dict[str, Any]]) -> str:
    checks = replay.get("checks") if isinstance(replay.get("checks"), list) else []
    blocker = str(replay.get("blocker") or "none reported").lower()
    review_only_blockers = {"review before starting more work", "review current state", "waiting for instruction", "waiting to start"}
    changed_count = int(replay.get("changed_count") or 0)
    if approvals:
        return "needs decision"
    if blocker and blocker not in {"none reported", "none", "-"} and blocker not in review_only_blockers:
        return "blocked"
    if checks and changed_count:
        return "reviewable"
    if checks:
        return "verified signal"
    if changed_count:
        return "needs checks"
    return "limited signal"


def project_pending_approvals(project_id: str, limit: int = 4) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in pending_approvals():
        if str(item.get("project") or "") != project_id:
            continue
        items.append(
            {
                "id": audit_clean(item.get("id"), limit=80),
                "type": audit_clean(item.get("type"), limit=80),
                "branch": audit_clean(item.get("branch"), limit=120),
                "message": audit_clean(item.get("message"), limit=220),
                "created_at": audit_clean(item.get("created_at"), limit=120),
            }
        )
        if len(items) >= limit:
            break
    return items


def operator_playback_card(project_id: str, user_id: str | None = None) -> dict[str, Any]:
    replay = session_replay_card(project_id)
    approvals = project_pending_approvals(project_id)
    image_summary = "No recent image context."
    if user_id:
        image_summary = audit_clean(last_image_context_summary(user_id), limit=360)
    checks = replay.get("checks") if isinstance(replay.get("checks"), list) else []
    decisions = replay.get("decisions") if isinstance(replay.get("decisions"), list) else []
    card = {
        "project": audit_clean(project_id, limit=120),
        "state": audit_clean(replay.get("state"), limit=80),
        "confidence": playback_confidence(replay, approvals),
        "story": audit_clean(replay.get("story"), limit=900),
        "outcome": audit_clean(replay.get("outcome"), limit=320),
        "blocker": audit_clean(replay.get("blocker") or "none reported", limit=260),
        "work_areas": audit_clean(replay.get("work_areas"), limit=260),
        "changed_count": int(replay.get("changed_count") or 0),
        "checks": [audit_clean(item, limit=180) for item in checks[:4]],
        "decisions": [audit_clean(item, limit=220) for item in decisions[:4]],
        "pending_approvals": approvals,
        "visual_context": image_summary,
        "next_step": audit_clean(replay.get("next_step"), limit=260),
        "primary_action": playback_primary_action(replay, approvals),
        "commands": [
            f"/playback {project_id}",
            f"/watch {project_id}",
            f"/evidence {project_id}",
            f"/replay {project_id}",
        ],
        "log_age_minutes": replay.get("last_activity_minutes"),
    }
    return card


def operator_playback_cards(user_id: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    projects: list[str] = []
    for card in session_replay_cards(user_id=user_id, limit=max(limit, 6)):
        project_id = str(card.get("project") or "")
        if project_id and project_id not in projects:
            projects.append(project_id)
    active = str(user_state(user_id).get("active_project") or "") if user_id else ""
    if active and active not in projects and get_project(active):
        projects.insert(0, active)
    return [operator_playback_card(project_id, user_id=user_id) for project_id in projects[:limit]]


def format_operator_playback_card(card: dict[str, Any]) -> str:
    approvals = card.get("pending_approvals") if isinstance(card.get("pending_approvals"), list) else []
    lines = [
        f"Operator playback: {card.get('project')}",
        f"- State: {card.get('state')}",
        f"- Confidence: {card.get('confidence')}",
        f"- Story: {card.get('story')}",
        f"- Outcome: {card.get('outcome')}",
        f"- Work areas: {card.get('work_areas')} ({card.get('changed_count')} changed)",
        f"- Blocker: {card.get('blocker')}",
        f"- Next: {card.get('next_step')}",
        f"- Primary action: {card.get('primary_action')}",
    ]
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    if checks:
        lines.append("")
        lines.append("Proof:")
        lines.extend(f"- {item}" for item in checks[:4])
    if approvals:
        lines.append("")
        lines.append("Pending approvals:")
        for item in approvals[:4]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('type')} [{item.get('id')}]: {item.get('message') or item.get('branch')}")
    visual_context = str(card.get("visual_context") or "")
    if visual_context and not visual_context.lower().startswith("no recent image"):
        lines.append("")
        lines.append(f"Recent visual context: {visual_context}")
    lines.append("")
    lines.append("Commands: " + ", ".join(str(item) for item in (card.get("commands") or [])[:4]))
    lines.append("Technical filenames and local paths are hidden. Use /diff only when you want code-level detail.")
    return compact("\n".join(lines), limit=3600)


def operator_playback(project_id: str, user_id: str | None = None) -> str:
    return format_operator_playback_card(operator_playback_card(project_id, user_id=user_id))


def done_criteria_evidence_signals(criteria: list[dict[str, str]], limit: int = 5) -> list[str]:
    signals: list[str] = []
    for index, item in enumerate(criteria, start=1):
        if not isinstance(item, dict):
            continue
        if item.get("status") not in {"done", "waived"}:
            continue
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            continue
        criterion_id = str(item.get("id") or index)
        signals.append(audit_clean(f"Criterion {criterion_id}: {evidence}", limit=180))
        if len(signals) >= limit:
            break
    return signals


def merge_verification_signals(primary: list[Any], secondary: list[str], limit: int = 5) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in list(primary or []) + list(secondary or []):
        item = audit_clean(raw, limit=180)
        if not item or item in seen:
            continue
        merged.append(item)
        seen.add(item)
        if len(merged) >= limit:
            break
    return merged


def project_completion_card(project_id: str, user_id: str | None = None) -> dict[str, Any]:
    profile = project_profile(project_id)
    playback = operator_playback_card(project_id, user_id=user_id)
    criteria = normalize_done_criteria(profile.get("done_criteria") or [])
    objective = audit_clean(profile.get("objective") or "", limit=500) if profile.get("objective") else ""
    playback_checks = playback.get("checks") if isinstance(playback.get("checks"), list) else []
    approvals = playback.get("pending_approvals") if isinstance(playback.get("pending_approvals"), list) else []
    open_criteria = [item for item in criteria if item.get("status") == "open"]
    blocked_criteria = [item for item in criteria if item.get("status") == "blocked"]
    done_criteria = [item for item in criteria if item.get("status") in {"done", "waived"}]
    checks = merge_verification_signals(playback_checks, done_criteria_evidence_signals(done_criteria))
    confidence = str(playback.get("confidence") or "")
    state = str(playback.get("state") or "")
    changed_count = int(playback.get("changed_count") or 0)
    no_hazards = not approvals and confidence != "blocked" and state != "running"
    strict_done = bool(objective) and bool(criteria) and not open_criteria and not blocked_criteria and bool(checks) and no_hazards and changed_count == 0
    if strict_done:
        verdict = "100% done candidate"
        next_step = "Archive or start the next objective."
    elif not objective:
        verdict = "objective missing"
        next_step = f"Set the intended objective with /objective set {project_id} \"objective\"."
    elif not criteria:
        verdict = "definition of done missing"
        next_step = f"Add proof criteria with /objective add {project_id} \"criterion\"."
    elif state == "running":
        verdict = "in progress"
        next_step = f"Watch the active run: /watch {project_id}"
    elif confidence == "blocked" or blocked_criteria:
        verdict = "blocked"
        next_step = f"Inspect the blocker: /playback {project_id}"
    elif approvals:
        verdict = "waiting for approval"
        next_step = "Review pending approval requests with /approvals."
    elif open_criteria:
        verdict = "not done"
        next_step = f"Continue work on open criteria or mark proof with /objective done {project_id} <number> \"evidence\"."
    elif not checks:
        verdict = "needs verification"
        next_step = f"Run or request verification before done: /playback {project_id}"
    elif changed_count:
        verdict = "reviewable, not final"
        next_step = f"Review evidence and approve commit/push when ready: /evidence {project_id}"
    else:
        verdict = "done candidate"
        next_step = "Review the final proof before calling it complete."
    criteria_score = (len(done_criteria) / len(criteria)) if criteria else 0.0
    percent = 0
    if objective:
        percent += 20
    percent += int(criteria_score * 50)
    if checks:
        percent += 15
    if no_hazards:
        percent += 15
    if not strict_done:
        percent = min(percent, 99)
    return {
        "project": audit_clean(project_id, limit=120),
        "objective": objective,
        "verdict": verdict,
        "completion_percent": percent,
        "state": audit_clean(state, limit=80),
        "confidence": audit_clean(confidence, limit=120),
        "criteria": criteria,
        "done_criteria": len(done_criteria),
        "total_criteria": len(criteria),
        "checks": [audit_clean(item, limit=180) for item in checks[:5]],
        "pending_approvals": approvals,
        "changed_count": changed_count,
        "blocker": audit_clean(playback.get("blocker") or "none reported", limit=260),
        "next_step": audit_clean(next_step, limit=300),
        "primary_action": audit_clean(playback.get("primary_action"), limit=300),
    }


def format_project_completion(card: dict[str, Any]) -> str:
    lines = [
        f"Completion check: {card.get('project')}",
        f"- Verdict: {card.get('verdict')}",
        f"- Completion: {card.get('completion_percent')}%",
        f"- Objective: {card.get('objective') or 'not set'}",
        f"- State: {card.get('state')} ({card.get('confidence')})",
        f"- Blocker: {card.get('blocker')}",
        f"- Changed work areas count: {card.get('changed_count')}",
        f"- Next: {card.get('next_step')}",
    ]
    criteria = card.get("criteria") if isinstance(card.get("criteria"), list) else []
    lines.append("")
    lines.append("Definition of Done:")
    if criteria:
        for index, item in enumerate(criteria[:12], start=1):
            if isinstance(item, dict):
                evidence = f" - {item.get('evidence')}" if item.get("evidence") else ""
                lines.append(f"{index}. [{item.get('status')}] {item.get('text')}{evidence}")
    else:
        lines.append("- Not configured.")
    checks = card.get("checks") if isinstance(card.get("checks"), list) else []
    lines.append("")
    lines.append("Verification proof:")
    if checks:
        lines.extend(f"- {item}" for item in checks[:5])
    else:
        lines.append("- No verification proof recorded yet.")
    approvals = card.get("pending_approvals") if isinstance(card.get("pending_approvals"), list) else []
    if approvals:
        lines.append("")
        lines.append("Pending approvals:")
        for item in approvals[:4]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('type')} [{item.get('id')}]: {item.get('message') or item.get('branch')}")
    lines.append("")
    lines.append("Commander X must not call a project 100% done unless the objective is set, all criteria have proof, verification exists, no blockers/approvals are pending, and local changes are settled.")
    return compact("\n".join(lines), limit=3600)


def project_completion(project_id: str, user_id: str | None = None) -> str:
    return format_project_completion(project_completion_card(project_id, user_id=user_id))


def update_project_objective(project_id: str, objective: str | None = None, add_criterion: str | None = None, mark_done: tuple[int, str] | None = None) -> None:
    data = profiles_data()
    profiles = data.setdefault("profiles", {})
    profile = profiles.setdefault(project_id, {})
    if not isinstance(profile, dict):
        profile = {}
        profiles[project_id] = profile
    if objective is not None:
        profile["objective"] = audit_clean(objective, limit=600)
    criteria = normalize_done_criteria(profile.get("done_criteria") or [])
    if add_criterion:
        criteria.append({"id": str(len(criteria) + 1), "text": audit_clean(add_criterion, limit=280), "status": "open", "evidence": ""})
    if mark_done:
        index, evidence = mark_done
        if 1 <= index <= len(criteria):
            criteria[index - 1]["status"] = "done"
            criteria[index - 1]["evidence"] = audit_clean(evidence or "Marked done by operator.", limit=360)
    profile["done_criteria"] = criteria
    save_profiles(data)


def command_objective(args: list[str], user_id: str) -> str:
    action = args[0].lower() if args else "show"
    if action == "set":
        project_id, rest = project_and_rest(args[1:], user_id=user_id)
        if not project_id or not rest:
            return 'Usage: /objective set <project> "intended objective"'
        update_project_objective(project_id, objective=" ".join(rest))
        return f"Objective set for {project_id}.\n\n" + project_completion(project_id, user_id=user_id)
    if action == "add":
        project_id, rest = project_and_rest(args[1:], user_id=user_id)
        if not project_id or not rest:
            return 'Usage: /objective add <project> "done criterion"'
        update_project_objective(project_id, add_criterion=" ".join(rest))
        return f"Definition-of-Done criterion added for {project_id}.\n\n" + project_completion(project_id, user_id=user_id)
    if action in {"done", "check"}:
        project_id, rest = project_and_rest(args[1:], user_id=user_id)
        if not project_id or len(rest) < 2 or not rest[0].isdigit():
            return 'Usage: /objective done <project> <criterion_number> "evidence"'
        update_project_objective(project_id, mark_done=(int(rest[0]), " ".join(rest[1:])))
        return f"Criterion {rest[0]} marked done for {project_id}.\n\n" + project_completion(project_id, user_id=user_id)
    project_id, _rest = project_and_rest(args if action != "show" else args[1:], user_id=user_id)
    if not project_id:
        return "Usage: /objective <project>, /objective set <project> \"objective\", /objective add <project> \"criterion\""
    return project_completion(project_id, user_id=user_id)


def command_done(args: list[str], user_id: str) -> str:
    project_id, _rest = project_and_rest(args, user_id=user_id)
    if not project_id:
        return "Usage: /done <project> or set /focus <project> first."
    return project_completion(project_id, user_id=user_id)


def save_owner_review_pack(project_id: str, content: str) -> str:
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{slugify(project_id, limit=48)}-owner-review-{timestamp}.md"
    (directory / filename).write_text(redact(content).strip() + "\n", encoding="utf-8")
    return filename


def human_report_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def owner_review_label_from_file(path: Path, slug: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:8]:
            if line.lower().startswith("owner review pack:"):
                return safe_brief_text(line.split(":", 1)[1].strip())
    except OSError:
        pass
    projects = projects_config().get("projects", {})
    for project_id, project in projects.items():
        label = project_label(project_id, project=project, include_id=False)
        if slug in {slugify(project_id, limit=48), slugify(label, limit=48)}:
            return safe_brief_text(label)
    return safe_brief_text(slug.replace("-", " ").title())


def saved_owner_review_packs(limit: int = 8) -> list[dict[str, Any]]:
    directory = report_dir()
    if not directory.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        paths = list(directory.glob("*-owner-review-*.md"))
    except OSError:
        return []
    for path in paths:
        if not path.is_file():
            continue
        match = re.match(r"(?P<slug>.+)-owner-review-(?P<stamp>\d{8}-\d{6})\.md$", path.name)
        if not match:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        saved_at = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        records.append(
            {
                "project": owner_review_label_from_file(path, match.group("slug")),
                "saved_at": saved_at,
                "size": human_report_size(stat.st_size),
                "filename": redact(path.name),
            }
        )
    records.sort(key=lambda item: str(item["saved_at"]), reverse=True)
    return records[:limit]


def command_reviews(args: list[str]) -> str:
    show_files = any(arg.lower() in {"details", "detail", "file", "files", "full"} for arg in args)
    records = saved_owner_review_packs(limit=10)
    if not records:
        return "No saved owner review packs yet.\nCreate one with /review <project> save."
    lines = ["Saved owner review packs"]
    for index, record in enumerate(records, start=1):
        lines.append(f"{index}. {record['project']} - saved {record['saved_at']} ({record['size']})")
        if show_files:
            lines.append(f"   File: {record['filename']}")
    lines.extend(
        [
            "",
            "Create a fresh pack with: /review <project> save",
            "Default view hides local paths and technical filenames.",
        ]
    )
    return compact("\n".join(lines), limit=2800)


def command_review(args: list[str], user_id: str) -> str:
    save_requested = any(arg.lower() in {"save", "saved", "export", "report"} for arg in args)
    project_args = [arg for arg in args if arg.lower() not in {"save", "saved", "export", "report"}]
    project_id, _rest = project_and_rest(project_args, user_id=user_id)
    if not project_id:
        return "Usage: /review <project> [save] or set /focus <project> first."
    completion = project_completion_card(project_id, user_id=user_id)
    evidence = session_evidence_card(project_id)
    project_name = safe_brief_text(project_label(project_id, include_id=False))
    criteria_total = int(completion.get("total_criteria") or 0)
    criteria_done = int(completion.get("done_criteria") or 0)
    changed_count = int(completion.get("changed_count") or 0)
    checks = completion.get("checks") if isinstance(completion.get("checks"), list) else []
    blocker = safe_brief_text(completion.get("blocker") or "none reported")
    verdict = safe_brief_text(completion.get("verdict") or "unknown")

    if criteria_total and criteria_done == criteria_total and not re.search(r"\b(blocked|running)\b", verdict, flags=re.IGNORECASE):
        owner_status = "The intended local build objective is complete and ready for owner review."
    else:
        owner_status = "The project still needs work before owner sign-off."

    if changed_count:
        final_gate = "It is not called final yet because local changes still need human review and a commit decision."
    elif completion.get("pending_approvals"):
        final_gate = "It is waiting for an approval decision."
    elif blocker.lower() not in {"none reported", "none", "-"}:
        final_gate = f"It still has a blocker: {blocker}."
    else:
        final_gate = "It has no reported blocker; final sign-off is a review decision."

    proof_raw = checks[:4] or (evidence.get("checks") if isinstance(evidence.get("checks"), list) else [])[:4]
    proof = [safe_brief_text(item) for item in proof_raw if str(item or "").strip()]
    lines = [
        f"Owner review pack: {project_name}",
        f"- Status: {owner_status}",
        f"- Definition of Done: {criteria_done}/{criteria_total or '-'} complete",
        f"- Completion view: {completion.get('completion_percent')}% ({verdict})",
        f"- Blocker: {blocker}",
        f"- Review gate: {final_gate}",
    ]
    if proof:
        lines.append("")
        lines.append("Proof you can trust:")
        lines.extend(f"- {item}" for item in proof[:4])
    lines.extend(
        [
            "",
            "Next safe actions:",
            f"- Read proof: /evidence {project_id}",
            f"- See code-level changes only if needed: /diff {project_id}",
            f"- Prepare a commit after review: /commit {project_id} \"reviewed local milestone\"",
            "",
            "No deploy, push, production credentials, or external messages are included in this review pack.",
        ]
    )
    content = compact("\n".join(lines), limit=3000)
    if save_requested:
        filename = save_owner_review_pack(project_id, content)
        content += f"\n\nSaved locally in Commander reports as: {filename}"
    return content


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
    autopilot = profile.get("autopilot") if isinstance(profile.get("autopilot"), dict) else {}
    if autopilot.get("enabled") and autopilot.get("local_full_access"):
        approval_boundaries[1] = (
            "Local project edits, local cleanup, test/build commands, and development dependency installs are allowed; "
            "still ask before production credentials, deployment, pushing, external messages, spending money, or destructive actions outside this project."
        )
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
- If this project is in autonomous local-build mode, continue through the requested local milestone without waiting for operator approval for normal local edits, local cleanup, tests, builds, or development dependency installs.
- Do not push, publish, deploy, spend money, send external messages, change credentials, or modify billing/legal/identity settings.
- Do not delete production data.
- Do not reveal secrets or print .env values.
- If Git reports dubious ownership, do not change global Git config. Use per-command safe-directory flags such as `git -c safe.directory={path.as_posix()} status --short --branch`.
- On Windows, if `npm` is blocked by PowerShell execution policy, use `npm.cmd`; do not create local `npm.cmd` or other executable shims in the project.
- Return evidence before saying work is complete: files changed, checks run, current blocker, and next step.
- Do not claim the project is 100% done unless the objective and every Definition-of-Done criterion are satisfied with evidence.
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
    session_snapshot: dict[str, Any] | None = None
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
            session_snapshot = dict(session)
            save_sessions(data)
    if exit_code == 0 and session_snapshot:
        auto_update_done_criteria_from_session_summary(project_id, session_snapshot)
    if task_id:
        update_task(str(task_id), {"status": "done" if exit_code == 0 else "failed", "completed_at": utc_now(), "exit_code": exit_code})
    PROCESSES.pop(project_id, None)


def refresh_session_states() -> None:
    with SESSION_LOCK:
        data = sessions_data()
        changed = False
        for project_id, session in data.get("sessions", {}).items():
            if not isinstance(session, dict):
                continue
            if refresh_session_progress(project_id, session):
                changed = True
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
    approval_policy = codex_cfg.get("approval_policy", "never")
    extra_args = [str(item) for item in codex_cfg.get("extra_args", [])]
    extra_args.extend(str(item) for item in project.get("codex_extra_args", []) if item)
    profile = project_profile(project_id)
    plan = build_work_plan(project_id, task, profile)
    prompt = build_codex_prompt(project_id, path, task, user_id=user_id, profile=profile, plan=plan)
    args = codex_command_args([
        "-a",
        str(approval_policy),
        "exec",
        *extra_args,
        "-C",
        str(path),
        "-s",
        sandbox,
        "--skip-git-repo-check",
        "--color",
        "never",
        "-o",
        str(last_message_file),
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
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            log_handle.write(f"\nCodex CLI exited before Commander could send the prompt: {exc}\n")
            log_handle.flush()
            if branch and is_git_repo(path) and not has_changes(path):
                original = project.get("default_branch") or "main"
                git_run(path, "checkout", str(original), timeout=45)
                git_run(path, "branch", "-D", branch, timeout=45)
                log_handle.write(f"Rolled back empty task branch: {branch}\n")
            hint = compact(read_recent_log_text(log_file), limit=1400)
            return f"Codex CLI exited before the session started for {project_id}.\n\n{hint}"

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
        lines.append(f"- {project_label(project_id, include_id=False)}: {state}, phase {phase}, PID {pid}, updated {updated}{pending_note}")
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
    task = summarize_task_for_human(session.get("task"))
    return f"{session.get('state', 'unknown')} ({running}) - task: {task}"


def project_queue_lines(project_id: str, limit: int = 4) -> list[str]:
    sync_tasks_with_sessions()
    tasks = [
        task
        for task in tasks_data().get("tasks", [])
        if task.get("project") == project_id and task.get("status") in {"queued", "running", "review", "failed", "stopped"}
    ]
    if not tasks:
        return ["No active queued Commander tasks for this project."]
    return [f"{task.get('status')}: {summarize_task_for_human(task.get('title'))}" for task in tasks[-limit:]]


def short_human_text(value: Any, limit: int = 180) -> str:
    text = safe_brief_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def summarize_task_for_human(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if not raw:
        return "-"
    if "health companion" in lowered or "diabetes companion" in lowered or "companion product" in lowered:
        if "checkpoint 2" in lowered or "schema" in lowered or "alembic" in lowered:
            return "Build the next Health Companion foundation: database design, patient records, safety logs, and first migration."
        if "checkpoint 1" in lowered or "repository skeleton" in lowered or "healthz" in lowered:
            return "Build the first Health Companion foundation from the real PRD: backend health check, patient and clinician starter screens, local infrastructure, and verification."
        return "Build Health Companion AI from the real PRD, with Arabic-first patient and clinician experiences plus clinical safety boundaries."
    if "local mvp" in lowered and ("telegram" in lowered or "whatsapp" in lowered):
        return "Build the local web MVP with Telegram and WhatsApp-ready channel scaffolding."
    if ("npm run test" in lowered or "npm.cmd run test" in lowered) and ("smoke" in lowered or "build" in lowered):
        return "Verify the current milestone with test, build, and smoke checks, then report evidence."
    if "audit" in lowered and "onboarding" in lowered:
        return "Audit the onboarding flow, fix blockers, and verify the result."
    if "production ready" in lowered or "100%" in lowered:
        return "Close remaining blockers against the project objective and verify completion evidence."
    return short_human_text(raw, limit=150)


def friendly_session_state(state: Any) -> str:
    raw = str(state or "unknown")
    labels = {
        "running": "working now",
        "completed": "ready for review",
        "done": "done",
        "finished_unknown": "finished, needs quick review",
        "failed": "blocked",
        "stop_failed": "stop needs review",
        "stopped": "stopped",
        "idle": "idle",
        "queued": "queued",
        "review": "needs review",
        "changed": "local changes waiting for review",
    }
    return labels.get(raw, raw.replace("_", " "))


def command_updates(project_id: str | None, user_id: str, query: str | None = None) -> str:
    resolved = project_from_assistant_query(project_id, user_id=user_id, query=query)
    if not resolved or not get_project(resolved):
        return command_overview(user_id=user_id)

    project = get_project(resolved)
    assert project is not None
    path = project_path(project)
    changed = changed_files(path) if path.exists() and is_git_repo(path) else []
    recent_docs = recent_project_documents(resolved)

    lines = [f"Latest updates: {project_label(resolved, project, include_id=False)}"]
    lines.append("")
    lines.append("Codex:")
    lines.append(f"- {project_session_line(resolved)}")
    lines.append("")
    lines.append("Queue:")
    lines.extend(f"- {line}" for line in project_queue_lines(resolved))
    lines.append("")
    lines.append("Local work:")
    if path.exists() and is_git_repo(path):
        lines.append(f"- Review state: {len(changed)} changed file(s) on the current task branch.")
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
        label = project_label(project_id, project)
        if show_details:
            lines.append(f"- {label}: {allowed}, {exists}, {git}, branch {branch}, path {path}")
        else:
            lines.append(f"- {label}: {allowed}, branch {branch}")
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
    return f"Active project set to {project_label(resolved)}.\n\n{project_context_summary(resolved, max_files=2)}"


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
            "- Memory, project profiles, task queue, work feed, heartbeats",
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
    lines.append("OpenClaw:")
    lines.append(openclaw_brief_status())
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


def friendly_local_path(path: str | Path) -> str:
    try:
        resolved = Path(path).expanduser().resolve()
        home = Path.home().resolve()
        try:
            return "~/" + resolved.relative_to(home).as_posix()
        except ValueError:
            return str(resolved)
    except OSError:
        return str(path)


def openclaw_locations(home: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Path]:
    home = home or Path.home()
    env = env or os.environ
    appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
    configured_launcher = env.get("COMMANDER_OPENCLAW_LAUNCHER", "").strip()
    return {
        "home": home,
        "skills": home / ".openclaw" / "skills",
        "openclaw_home": home / ".openclaw",
        "claw_home": home / ".claw",
        "plugins_json": home / ".claw" / "plugins" / "installed.json",
        "legacy_checkout": home / "claw-code",
        "legacy_launcher": Path(configured_launcher) if configured_launcher else home / "claw-code" / "rust" / "run-claw.cmd",
        "npm_openclaw": appdata / "npm" / "openclaw.cmd",
    }


def openclaw_plugin_sources(plugins_json: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(plugins_json.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    rows: list[dict[str, str]] = []
    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        return rows
    for plugin_id, plugin in plugins.items():
        if not isinstance(plugin, dict):
            continue
        source = plugin.get("source") or {}
        source_path = source.get("path") if isinstance(source, dict) else ""
        rows.append(
            {
                "id": str(plugin_id),
                "name": str(plugin.get("name") or plugin_id),
                "source": str(source_path or ""),
                "source_exists": "yes" if source_path and Path(str(source_path)).exists() else "no",
            }
        )
    return rows


def openclaw_process_timeout(env: dict[str, str] | None = None) -> int:
    env = env or os.environ
    raw = env.get("COMMANDER_OPENCLAW_PROCESS_TIMEOUT_SECONDS", "8")
    if raw.isdigit():
        return max(2, min(30, int(raw)))
    return 8


def is_openclaw_process_row(row: str) -> bool:
    lowered = row.lower()
    if "commander.py" in lowered or "codex-commander" in lowered:
        return False
    parts = row.strip().split(maxsplit=2)
    name = parts[1].lower() if len(parts) >= 2 else ""
    if name in {"powershell.exe", "pwsh.exe", "cmd.exe", "python.exe"}:
        return bool(
            "run-claw.cmd" in lowered
            or "\\claw-code\\" in lowered
            or "/claw-code/" in lowered
            or "node_modules\\openclaw" in lowered
            or "node_modules/openclaw" in lowered
        )
    return bool(
        re.search(r"\bopenclaw(?:\.exe)?\b", lowered)
        or "run-claw.cmd" in lowered
        or "\\claw-code\\" in lowered
        or "/claw-code/" in lowered
    )


def openclaw_status_snapshot(
    home: Path | None = None,
    env: dict[str, str] | None = None,
    process_rows: list[str] | None = None,
) -> dict[str, Any]:
    locations = openclaw_locations(home=home, env=env)
    cli = shutil.which("openclaw") or shutil.which("claw")
    skills_path = locations["skills"]
    skills_count = 0
    if skills_path.exists():
        try:
            skills_count = sum(1 for child in skills_path.iterdir() if child.is_dir())
        except OSError:
            skills_count = 0
    plugin_sources = openclaw_plugin_sources(locations["plugins_json"])
    process_error = ""
    if process_rows is not None:
        raw_process_rows = process_rows
    else:
        try:
            raw_process_rows = computer_process_lines(["openclaw", "claw-code", "run-claw"], timeout=openclaw_process_timeout(env))
        except subprocess.TimeoutExpired:
            raw_process_rows = []
            process_error = "OpenClaw process scan timed out."
        except Exception as exc:
            raw_process_rows = []
            process_error = safe_brief_text(redact(str(exc)))
    process_rows = [row for row in raw_process_rows if is_openclaw_process_row(row)]
    launchers = [
        ("PATH command", Path(cli) if cli else None),
        ("npm shim", locations["npm_openclaw"]),
        ("legacy launcher", locations["legacy_launcher"]),
    ]
    available_launchers = [
        {"label": label, "path": str(path)}
        for label, path in launchers
        if path and Path(path).exists()
    ]
    return {
        "cli": cli or "",
        "locations": locations,
        "skills_count": skills_count,
        "plugin_sources": plugin_sources,
        "process_rows": process_rows,
        "process_error": process_error,
        "available_launchers": available_launchers,
        "legacy_checkout_exists": locations["legacy_checkout"].exists(),
        "openclaw_home_exists": locations["openclaw_home"].exists(),
        "claw_home_exists": locations["claw_home"].exists(),
    }


def openclaw_brief_status() -> str:
    snapshot = openclaw_status_snapshot()
    if snapshot["available_launchers"]:
        label = snapshot["available_launchers"][0]["label"]
        return f"launchable via {label}; skills: {snapshot['skills_count']}"
    traces = []
    if snapshot["openclaw_home_exists"]:
        traces.append(".openclaw")
    if snapshot["claw_home_exists"]:
        traces.append(".claw")
    if traces:
        return f"traces found ({', '.join(traces)}), but no launcher found"
    return "not detected"


def summarize_process_rows(rows: list[str], limit: int = 8) -> list[str]:
    summary: list[str] = []
    for row in rows[:limit]:
        parts = row.strip().split(maxsplit=2)
        if len(parts) >= 2 and parts[0].isdigit():
            summary.append(f"{parts[0]} {parts[1]}")
        elif row.strip():
            summary.append(redact(row.strip())[:80])
    return summary


def openclaw_research_timeout(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("COMMANDER_OPENCLAW_RESEARCH_TIMEOUT_SECONDS") or env.get("COMMANDER_MCP_RESEARCH_TIMEOUT_SECONDS", "12")
    try:
        return max(3, min(45, int(raw)))
    except ValueError:
        return 12


def normalize_github_repo_url(url: str) -> tuple[bool, str, str]:
    value = (url or "").strip().rstrip("/").removesuffix(".git")
    if not value:
        return False, "", ""
    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        owner, repo = value.split("/", 1)
    elif parsed.scheme in {"http", "https"} and parsed.netloc.lower() in {"github.com", "www.github.com"}:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            return False, "", ""
        owner, repo = parts[0], parts[1].removesuffix(".git")
    else:
        return False, "", ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return False, "", ""
    full_name = f"{owner}/{repo}"
    return True, f"https://github.com/{full_name}", full_name


def github_api_json(url: str, env: dict[str, str] | None = None) -> tuple[dict[str, Any] | None, str]:
    env = os.environ if env is None else env
    headers = {
        "User-Agent": "CommanderX/1.0 (+https://github.com/fazzouny/Commander-X)",
        "Accept": "application/vnd.github+json",
    }
    token = env.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=openclaw_research_timeout(env)) as response:
            data = json.loads(response.read(500_000).decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return None, f"GitHub API returned HTTP {exc.code}."
    except Exception as exc:
        return None, f"GitHub API lookup failed: {redact(str(exc))}."
    if isinstance(data, dict):
        return data, "GitHub API lookup succeeded."
    return None, "GitHub API returned an unexpected payload."


def github_search_openclaw_repos(limit: int = 5, env: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], str]:
    env = os.environ if env is None else env
    raw_enabled = env.get("COMMANDER_OPENCLAW_WEB_RESEARCH")
    if raw_enabled is not None and raw_enabled.strip().lower() in {"0", "false", "no", "off"}:
        return [], "OpenClaw web research is disabled by COMMANDER_OPENCLAW_WEB_RESEARCH."
    query = urllib.parse.urlencode(
        {
            "q": "openclaw in:name,description",
            "sort": "stars",
            "order": "desc",
            "per_page": str(max(1, min(10, limit))),
        }
    )
    data, detail = github_api_json(f"https://api.github.com/search/repositories?{query}", env=env)
    if not data:
        return [], detail
    candidates: list[dict[str, Any]] = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        html_url = str(item.get("html_url") or "")
        ok, normalized, full_name = normalize_github_repo_url(html_url)
        if not ok:
            continue
        candidates.append(
            {
                "full_name": full_name,
                "url": normalized,
                "description": str(item.get("description") or "").strip(),
                "stars": int(item.get("stargazers_count") or 0),
                "archived": bool(item.get("archived")),
                "pushed_at": str(item.get("pushed_at") or ""),
                "source": "GitHub search",
            }
        )
        if len(candidates) >= limit:
            break
    return candidates, f"Searched GitHub repositories for OpenClaw; found {len(candidates)} candidate(s)."


def github_repo_readme_text(full_name: str, env: dict[str, str] | None = None) -> tuple[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name or ""):
        return "", "Invalid GitHub repository name."
    data, detail = github_api_json(f"https://api.github.com/repos/{full_name}/readme", env=env)
    if not data:
        return "", detail
    content = str(data.get("content") or "")
    encoding = str(data.get("encoding") or "").lower()
    if encoding != "base64" or not content:
        return "", "README was found but could not be decoded."
    try:
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception as exc:
        return "", f"README decode failed: {redact(str(exc))}."
    return decoded, f"Read README from {full_name}."


def openclaw_install_command_hints(text: str, limit: int = 8) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    lower_prefixes = (
        "git clone",
        "npm install",
        "npm i ",
        "pnpm install",
        "pnpm i ",
        "cargo install",
        "run-claw",
        "powershell ",
        "pwsh ",
        "irm ",
        "iwr ",
        "curl ",
        "bash ",
        "winget ",
        "docker run",
        "docker compose",
    )
    for raw in re.split(r"[\r\n]+", text or ""):
        line = raw.strip().strip("`").strip()
        line = re.sub(r"^\s*[$>]\s*", "", line)
        if not line or len(line) > 220:
            continue
        lower_line = line.lower()
        if lower_line in {"bash", "shell", "sh", "cmd", "powershell", "pwsh"}:
            continue
        is_openclaw_setup_command = False
        if line.startswith("openclaw "):
            parts = line.split()
            is_openclaw_setup_command = len(parts) > 1 and parts[1] in {"onboard", "gateway", "doctor", "install", "setup", "init"}
        if not (is_openclaw_setup_command or lower_line.startswith(lower_prefixes)):
            continue
        safe = redact(line)
        key = safe.lower()
        if key in seen:
            continue
        seen.add(key)
        hints.append(safe)
        if len(hints) >= limit:
            break
    return hints


def openclaw_install_target(home: Path | None = None, env: dict[str, str] | None = None) -> Path:
    home = home or Path.home()
    env = os.environ if env is None else env
    configured = env.get("COMMANDER_OPENCLAW_INSTALL_TARGET", "").strip()
    if configured:
        return Path(configured).expanduser()
    return home / "claw-code"


def format_openclaw_repo_candidate(candidate: dict[str, Any], index: int) -> str:
    archived = " archived" if candidate.get("archived") else ""
    pushed = str(candidate.get("pushed_at") or "")[:10] or "unknown"
    return "\n".join(
        [
            f"{index}. {candidate.get('full_name')} - {candidate.get('stars', 0)} stars{archived}, updated {pushed}",
            f"   URL: {candidate.get('url')}",
            f"   Prepare clone: /openclaw prepare {candidate.get('url')}",
        ]
    )


def openclaw_recovery_report(
    home: Path | None = None,
    env: dict[str, str] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    readme_text: str | None = None,
) -> str:
    env = os.environ if env is None else env
    snapshot = openclaw_status_snapshot(home=home, env=env)
    launchable = bool(snapshot["available_launchers"])
    lines = [
        "OpenClaw recovery",
        "",
        f"Current status: {'launchable' if launchable else 'not launchable'}",
        f"Local traces: skills={snapshot['skills_count']}, plugin cache={'yes' if snapshot['claw_home_exists'] else 'no'}, legacy checkout={'yes' if snapshot['legacy_checkout_exists'] else 'no'}",
        "",
    ]
    if launchable:
        lines.extend(
            [
                "OpenClaw already has a launch candidate.",
                "Use /openclaw details before changing anything.",
                "Nothing was installed.",
            ]
        )
        return "\n".join(lines)

    repo_url = env.get("COMMANDER_OPENCLAW_REPO_URL", "").strip()
    discovered: list[dict[str, Any]] = []
    research_detail = ""
    if repo_url:
        ok, normalized, full_name = normalize_github_repo_url(repo_url)
        if ok:
            discovered = [{"full_name": full_name, "url": normalized, "description": "Configured in .env", "stars": 0, "archived": False, "pushed_at": "", "source": "COMMANDER_OPENCLAW_REPO_URL"}]
            research_detail = "Using COMMANDER_OPENCLAW_REPO_URL from .env."
        else:
            research_detail = "COMMANDER_OPENCLAW_REPO_URL is set, but it is not a valid GitHub repository URL."
    elif candidates is not None:
        discovered = candidates
        research_detail = "Using supplied OpenClaw repository candidates."
    else:
        discovered, research_detail = github_search_openclaw_repos(env=env)

    lines.extend(["Candidate source research:", research_detail, ""])
    if discovered:
        lines.append("GitHub candidates. Treat these as leads, not proof of official ownership:")
        lines.extend(format_openclaw_repo_candidate(item, index + 1) for index, item in enumerate(discovered[:5]))
        top = discovered[0]
        text = readme_text
        readme_detail = ""
        if text is None and top.get("full_name"):
            text, readme_detail = github_repo_readme_text(str(top["full_name"]), env=env)
        hints = openclaw_install_command_hints(text or "")
        if hints:
            lines.extend(["", f"Install clues from README: {readme_detail or 'provided README text'}"])
            lines.extend(f"- {hint}" for hint in hints)
        elif readme_detail:
            lines.extend(["", f"{readme_detail} I did not find concise install command clues."])
    else:
        lines.extend(
            [
                "No GitHub candidate was found automatically.",
                "If you know the repo, send:",
                "/openclaw prepare https://github.com/owner/repo",
            ]
        )

    lines.extend(
        [
            "",
            "Safety:",
            "- Nothing was installed or started.",
            "- /openclaw prepare only creates an approval to clone source code.",
            "- Running installer scripts or launchers should remain a separate approval-gated step.",
        ]
    )
    return compact("\n".join(lines), limit=3600)


def prepare_openclaw_clone_response(repo_url: str, home: Path | None = None, env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    ok, normalized, full_name = normalize_github_repo_url(repo_url or env.get("COMMANDER_OPENCLAW_REPO_URL", ""))
    if not ok:
        return "Usage: /openclaw prepare https://github.com/owner/repo"
    if not shutil.which("git"):
        return "OpenClaw clone cannot be prepared because git is missing from PATH."
    target = openclaw_install_target(home=home, env=env).expanduser().resolve()
    if target.exists():
        return f"OpenClaw clone blocked because the target already exists: {friendly_local_path(target)}"
    pending_id = add_pending_action(
        "commander",
        {
            "type": "openclaw_clone",
            "repo_url": normalized,
            "full_name": full_name,
            "target": str(target),
            "message": f"Clone OpenClaw source from {full_name}",
        },
    )
    return (
        "OpenClaw clone prepared.\n"
        f"Pending approval ID: {pending_id}\n\n"
        f"Repository: {normalized}\n"
        f"Target: {friendly_local_path(target)}\n"
        f"Command: git clone --depth 1 {normalized} {friendly_local_path(target)}\n\n"
        "This only clones source code. It does not run install scripts, start OpenClaw, or change credentials.\n\n"
        f"Approve with /approve commander {pending_id}\n"
        f"Cancel with /cancel commander {pending_id}"
    )


def execute_openclaw_clone(action: dict[str, Any]) -> tuple[bool, str]:
    ok, normalized, _full_name = normalize_github_repo_url(str(action.get("repo_url") or ""))
    if not ok:
        return False, "OpenClaw clone blocked because the repository URL is invalid."
    target = Path(str(action.get("target") or "")).expanduser().resolve()
    if not str(target):
        return False, "OpenClaw clone blocked because the target path is missing."
    if target.exists():
        return False, f"OpenClaw clone blocked because the target already exists: {friendly_local_path(target)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    clone = run_command(["git", "clone", "--depth", "1", normalized, str(target)], timeout=600)
    if clone.returncode != 0:
        return False, "OpenClaw clone failed:\n" + compact(clone.stderr or clone.stdout)
    launcher_hints = [
        target / "rust" / "run-claw.cmd",
        target / "run-claw.cmd",
        target / "bin" / "openclaw.cmd",
    ]
    found_launchers = [friendly_local_path(path) for path in launcher_hints if path.exists()]
    lines = [compact((clone.stdout + clone.stderr).strip() or f"Cloned {normalized}.")]
    if found_launchers:
        lines.extend(["", "Launcher candidates found:"])
        lines.extend(f"- {item}" for item in found_launchers)
        lines.append("Set COMMANDER_OPENCLAW_LAUNCHER to the launcher you trust before starting it.")
    else:
        lines.append("No known Windows launcher was detected yet. Review the repository README before running anything.")
    return True, "\n".join(lines)


def configured_openclaw_launcher(env: dict[str, str] | None = None) -> tuple[Path | None, str]:
    env = os.environ if env is None else env
    raw = env.get("COMMANDER_OPENCLAW_LAUNCHER", "").strip()
    if not raw:
        return None, "COMMANDER_OPENCLAW_LAUNCHER is not configured."
    launcher = Path(raw).expanduser()
    try:
        resolved = launcher.resolve()
    except OSError as exc:
        return None, f"OpenClaw launcher path could not be resolved: {redact(str(exc))}."
    if not resolved.exists() or not resolved.is_file():
        return None, f"OpenClaw launcher was not found: {friendly_local_path(resolved)}"
    if os.name == "nt" and resolved.suffix.lower() not in {".cmd", ".bat", ".exe", ".com"}:
        return None, "OpenClaw launcher must be a .cmd, .bat, .exe, or .com file on Windows."
    return resolved, ""


def openclaw_launcher_command(launcher: Path) -> list[str]:
    if os.name == "nt" and launcher.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", str(launcher)]
    return [str(launcher)]


def prepare_openclaw_start_response(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    snapshot = openclaw_status_snapshot(env=env)
    if snapshot["process_rows"]:
        rows = "\n".join(f"- {item}" for item in summarize_process_rows(snapshot["process_rows"]))
        return "OpenClaw already appears to be running:\n" + rows
    launcher, error = configured_openclaw_launcher(env=env)
    if not launcher:
        return (
            "OpenClaw start cannot be prepared yet.\n\n"
            f"Reason: {error}\n\n"
            "Set COMMANDER_OPENCLAW_LAUNCHER in .env to a launcher you trust, then restart Commander.\n"
            "Use /openclaw recover if you need to find or reinstall OpenClaw first."
        )
    pending_id = add_pending_action(
        "commander",
        {
            "type": "openclaw_start",
            "launcher": str(launcher),
            "message": "Start configured OpenClaw launcher",
        },
    )
    return (
        "OpenClaw start prepared.\n"
        f"Pending approval ID: {pending_id}\n\n"
        f"Launcher: {friendly_local_path(launcher)}\n\n"
        "This starts only the launcher configured in COMMANDER_OPENCLAW_LAUNCHER. "
        "Telegram cannot provide a raw launcher command.\n\n"
        f"Approve with /approve commander {pending_id}\n"
        f"Cancel with /cancel commander {pending_id}"
    )


def execute_openclaw_start(action: dict[str, Any]) -> tuple[bool, str]:
    env = os.environ
    launcher, error = configured_openclaw_launcher(env=env)
    if not launcher:
        return False, f"OpenClaw start blocked: {error}"
    requested = Path(str(action.get("launcher") or "")).expanduser().resolve()
    if requested != launcher:
        return False, "OpenClaw start blocked because the configured launcher changed after approval was prepared."
    command = openclaw_launcher_command(launcher)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        process = subprocess.Popen(
            command,
            cwd=str(launcher.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        return False, f"OpenClaw start failed: {redact(str(exc))}"
    return True, f"Started configured OpenClaw launcher, PID {process.pid}.\nUse /openclaw to check runtime status."


def command_openclaw(args: list[str]) -> str:
    action = args[0].lower() if args else "status"
    snapshot = openclaw_status_snapshot()
    locations = snapshot["locations"]
    if action in {"status", "check", "find", "where", "details"}:
        launchable = bool(snapshot["available_launchers"])
        lines = [
            "OpenClaw status",
            f"Runtime: {'launchable' if launchable else 'not launchable'}",
            f"CLI on PATH: {'yes' if snapshot['cli'] else 'no'}",
            f"Skills cache: {'found' if snapshot['openclaw_home_exists'] else 'missing'} ({snapshot['skills_count']} skill folder(s))",
            f"Plugin cache: {'found' if snapshot['claw_home_exists'] else 'missing'}",
            f"Legacy checkout: {'found' if snapshot['legacy_checkout_exists'] else 'missing'}",
            "",
        ]
        if snapshot["available_launchers"]:
            lines.append("Launch candidates:")
            for item in snapshot["available_launchers"][:4]:
                lines.append(f"- {item['label']}: {friendly_local_path(item['path'])}")
        else:
            lines.extend(
                [
                    "No OpenClaw launcher is currently available.",
                    "Commander found traces, but cannot turn it on until OpenClaw is reinstalled or a launcher path is configured.",
                    "",
                    "Useful next steps:",
                    "- Reinstall/clone OpenClaw cleanly.",
                    "- Or set COMMANDER_OPENCLAW_LAUNCHER to the local launcher path.",
                ]
            )
        if action == "details":
            lines.extend(
                [
                    "",
                    "Known locations:",
                    f"- OpenClaw home: {friendly_local_path(locations['openclaw_home'])}",
                    f"- Claw plugin cache: {friendly_local_path(locations['claw_home'])}",
                    f"- Legacy launcher: {friendly_local_path(locations['legacy_launcher'])}",
                    f"- NPM shim: {friendly_local_path(locations['npm_openclaw'])}",
                ]
            )
            if snapshot["plugin_sources"]:
                lines.extend(["", "Plugin source references:"])
                for row in snapshot["plugin_sources"][:6]:
                    source = row["source"]
                    label = friendly_local_path(source) if source else "none"
                    lines.append(f"- {row['name']}: {label} ({'exists' if row['source_exists'] == 'yes' else 'missing'})")
        lines.extend(["", "Running processes:"])
        lines.extend(summarize_process_rows(snapshot["process_rows"]) or ["none"])
        lines.append("")
        lines.append("No OpenClaw process was started by this command.")
        return compact("\n".join(lines), limit=3400)

    if action == "doctor":
        if not snapshot["available_launchers"]:
            return "OpenClaw doctor cannot run because no OpenClaw launcher was found.\nUse /openclaw details for traces and recovery paths."
        if snapshot["cli"]:
            result = run_command(["openclaw", "doctor"], timeout=120)
            return compact((result.stdout + result.stderr).strip() or "openclaw doctor returned no output.", limit=3400)
        return "OpenClaw launcher exists, but there is no PATH CLI. Configure COMMANDER_OPENCLAW_LAUNCHER or reinstall OpenClaw before running doctor."

    if action in {"recover", "recovery", "install", "setup", "reinstall", "repair"}:
        return openclaw_recovery_report()

    if action in {"prepare", "clone"}:
        return prepare_openclaw_clone_response(args[1] if len(args) > 1 else "")

    if action in {"start", "launch", "run"}:
        return prepare_openclaw_start_response()

    return "Usage: /openclaw, /openclaw details, /openclaw recover, /openclaw prepare <github-url>, /openclaw start, or /openclaw doctor"


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
    lines = ["Commander setup readiness", "", "Capability summary:"]
    status_label = {"ready": "OK", "partial": "PARTIAL", "missing": "MISSING"}
    for item in setup_status_items():
        label = status_label.get(str(item["state"]), str(item["state"]).upper())
        lines.append(
            f"- {label}: {item['title']} ({item['configured']}/{item['total']} configured) - {item['purpose']}"
        )
        if item["state"] != "ready":
            lines.append(f"  Next: {item['next_step']}")
    lines.append("")
    lines.append("Detailed .env checklist:")
    for group, keys in readiness.items():
        configured = sum(1 for status in keys.values() if str(status).startswith("configured"))
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


def sanitize_service_line(line: str) -> str:
    clean = redact((line or "").strip())
    clean = re.sub(r"[A-Za-z]:\\[^\s\"']+", "[local path]", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean[:220]


def service_process_state(process_lines: list[str], marker: str) -> str:
    marker_lower = marker.lower()
    for line in process_lines:
        if marker_lower not in line.lower():
            continue
        pid = line.strip().split(maxsplit=1)[0] if line.strip() else "-"
        return f"running, PID {pid}"
    return "not found"


def service_log_line(path: Path, patterns: list[str] | None = None) -> str:
    if not path.exists():
        return "missing"
    try:
        recent = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError as exc:
        return f"unreadable: {sanitize_service_line(str(exc))}"
    lines = [line for line in recent if line.strip()]
    if not lines:
        return "empty"
    if patterns:
        regex = re.compile("|".join(patterns), flags=re.IGNORECASE)
        matches = [line for line in lines if regex.search(line)]
        if matches:
            return sanitize_service_line(matches[-1])
    return sanitize_service_line(lines[-1])


def command_service() -> str:
    process_warning = ""
    try:
        process_lines = computer_process_lines(["commander.py --poll", "dashboard.py"], timeout=8)
    except subprocess.TimeoutExpired:
        process_lines = []
        process_warning = "Process scan timed out; Commander will keep serving logs and commands."
    except Exception as exc:
        process_lines = []
        process_warning = f"Process scan unavailable: {sanitize_service_line(str(exc))}"
    poller = service_process_state(process_lines, "commander.py --poll")
    dashboard = service_process_state(process_lines, "dashboard.py")
    lines = [
        "Commander X service status",
        f"Poller: {poller}",
        f"Dashboard: {dashboard}",
        "",
        "Recent service signals:",
        "- Poller: " + service_log_line(LOG_DIR / "commander-service.out.log", ["started", "configured", "Polling error", "Heartbeat error"]),
        "- Poller errors: " + service_log_line(LOG_DIR / "commander-service.err.log", ["Traceback", "Error", "Exception", "failed", "ConnectionReset"]),
        "- Dashboard: " + service_log_line(LOG_DIR / "dashboard.out.log", ["listening", "GET /api/dashboard", "error"]),
        "- Dashboard errors: " + service_log_line(LOG_DIR / "dashboard.err.log", ["Traceback", "Error", "Exception", "failed"]),
    ]
    if process_warning:
        lines.append("- Process scan: " + process_warning)
    lines.extend(["", "No secrets or raw process command lines are shown here."])
    return compact("\n".join(lines), limit=2600)


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


def command_audit(limit: int = 12) -> str:
    events = audit_data().get("events", [])
    if not events:
        return "No approval audit events recorded yet."
    lines = ["Approval audit:"]
    for event in reversed(events[-limit:]):
        if not isinstance(event, dict):
            continue
        status = audit_clean(event.get("status") or "recorded", limit=80)
        action_type = audit_clean(event.get("type") or "action", limit=80)
        project = audit_clean(event.get("project") or "-", limit=120)
        at = str(event.get("at") or "-")
        summary = audit_clean(event.get("summary") or "-", limit=280)
        approval_id = audit_clean(event.get("approval_id") or "-", limit=80)
        lines.append(f"- {at}: {status} {action_type} for {project} [{approval_id}]")
        lines.append(f"  {summary}")
    lines.append("")
    lines.append("Technical paths, filenames, and secrets are hidden in this view.")
    return compact("\n".join(lines), limit=3600)


def report_dir() -> Path:
    configured = str(os.environ.get("COMMANDER_REPORT_DIR") or "").strip()
    if not configured:
        return DEFAULT_REPORT_DIR
    path = Path(os.path.expandvars(os.path.expanduser(configured)))
    return path if path.is_absolute() else BASE_DIR / path


def report_limit() -> int:
    raw = str(os.environ.get("COMMANDER_REPORT_LIMIT") or "12").strip()
    if not raw.isdigit():
        return 12
    return max(4, min(40, int(raw)))


def report_clean(value: Any, limit: int = 500) -> str:
    return audit_clean(value, limit=limit)


def report_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [item for item in value["items"] if isinstance(item, dict)]
    return []


def report_counts_line(payload: dict[str, Any]) -> str:
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
    session_values = [item for item in sessions.values() if isinstance(item, dict)] if isinstance(sessions, dict) else []
    running = sum(1 for item in session_values if item.get("state") == "running")
    approvals = report_items(payload, "approvals")
    changes = report_items(payload, "changes")
    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), list) else []
    return (
        f"Sessions: {len(session_values)} tracked, {running} running. "
        f"Approvals: {len(approvals)} waiting. "
        f"Changed projects: {len(changes)}. "
        f"Recommendations: {len(recommendations)}."
    )


def operator_report_payload(user_id: str | None = None, limit: int | None = None, source: str = "telegram") -> dict[str, Any]:
    user_id = user_id or active_user_id()
    limit = limit or report_limit()
    refresh_session_states()
    sync_tasks_with_sessions()
    sessions = sessions_data().get("sessions", {})
    tasks = tasks_data().get("tasks", [])
    changes = changed_project_details(limit=limit, max_files=0)
    work_feed = work_feed_items(user_id=user_id, limit=limit, sessions=sessions, changes=changes, tasks=tasks)
    briefs = session_brief_items(user_id=user_id, limit=limit, sessions=sessions, changes=changes, tasks=tasks)
    mission = mission_timeline_items(user_id=user_id, limit=limit, sessions=sessions, changes=changes, tasks=tasks)
    evidence_cards = session_evidence_cards(user_id=user_id, limit=min(limit, 8))
    replay_cards = session_replay_cards(user_id=user_id, limit=min(limit, 6))
    playback_cards = operator_playback_cards(user_id=user_id, limit=min(limit, 6))
    completion_cards = [project_completion_card(str(card.get("project")), user_id=user_id) for card in playback_cards if card.get("project")][: min(limit, 6)]
    events = audit_data().get("events", [])
    audit_items: list[dict[str, Any]] = []
    for event in reversed(events[-limit:]):
        if not isinstance(event, dict):
            continue
        audit_items.append(
            {
                "at": str(event.get("at") or ""),
                "project": report_clean(event.get("project") or "-", limit=120),
                "approval_id": report_clean(event.get("approval_id") or "-", limit=80),
                "type": report_clean(event.get("type") or "action", limit=80),
                "status": report_clean(event.get("status") or "recorded", limit=80),
                "summary": report_clean(event.get("summary") or "-", limit=500),
            }
        )
    state = user_state(user_id)
    image = state.get("last_image") if isinstance(state.get("last_image"), dict) else {}
    recent_images = []
    if image:
        recent_images.append(
            {
                "at": str(image.get("at") or ""),
                "kind": report_clean(image.get("kind") or "image", limit=80),
                "summary": report_clean(image.get("summary") or "-", limit=360),
                "risk": report_clean(image.get("risk") or "-", limit=120),
            }
        )
    return {
        "generated_at": utc_now(),
        "source": source,
        "active_project": report_clean(state.get("active_project") or "none", limit=120),
        "assistant_mode": report_clean(assistant_mode(user_id), limit=80),
        "heartbeat": {
            "enabled": bool(state.get("heartbeat_enabled")),
            "interval_minutes": state.get("heartbeat_interval_minutes"),
            "quiet": report_clean(quiet_window_status(state), limit=160),
        },
        "sessions": sessions,
        "mission_timeline": mission,
        "session_evidence": evidence_cards,
        "session_replay": replay_cards,
        "operator_playback": playback_cards,
        "project_completion": completion_cards,
        "session_briefs": briefs,
        "work_feed": work_feed,
        "approvals": pending_approvals(),
        "audit_trail": {"items": audit_items},
        "changes": changes,
        "recommendations": recommendation_items(user_id=user_id, limit=limit),
        "recent_images": recent_images,
    }


def format_operator_report(payload: dict[str, Any], source: str | None = None, limit: int | None = None) -> str:
    limit = limit or report_limit()
    generated_at = report_clean(payload.get("generated_at") or utc_now(), limit=80)
    source = report_clean(source or payload.get("source") or "dashboard", limit=80)
    heartbeat = payload.get("heartbeat") if isinstance(payload.get("heartbeat"), dict) else {}
    raw_active_project = payload.get("active_project")
    raw_mode = payload.get("assistant_mode")
    if heartbeat and all(isinstance(value, dict) for value in heartbeat.values()):
        heartbeat = next(iter(heartbeat.values()), {})
        raw_active_project = raw_active_project or heartbeat.get("active_project")
        raw_mode = raw_mode or heartbeat.get("assistant_mode")
    active_project = report_clean(raw_active_project or "none", limit=120)
    mode = report_clean(raw_mode or "unknown", limit=80)
    heartbeat_state = "on" if isinstance(heartbeat, dict) and heartbeat.get("enabled") else "off"
    heartbeat_quiet = report_clean(heartbeat.get("quiet") if isinstance(heartbeat, dict) else "-", limit=160)

    lines = [
        "# Commander X Operator Report",
        "",
        f"Generated: {generated_at}",
        f"Source: {source}",
        "",
        "## Executive Snapshot",
        f"- {report_counts_line(payload)}",
        f"- Mode: {mode}; focused project: {active_project}.",
        f"- Heartbeat: {heartbeat_state}; quiet window: {heartbeat_quiet}.",
        "- Safety: secrets, full local paths, and technical filenames are hidden by default.",
    ]

    mission = report_items(payload, "mission_timeline")[:limit]
    lines.extend(["", "## Mission Timeline"])
    if mission:
        for index, item in enumerate(mission, start=1):
            evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            evidence_text = "; ".join(report_clean(line, 180) for line in evidence[:3]) or "No detailed evidence yet."
            lines.extend(
                [
                    f"{index}. {report_clean(item.get('project'), 120)} - {report_clean(item.get('stage'), 160)}",
                    f"   Direction: {report_clean(item.get('direction'), 360)}",
                    f"   Blocker: {report_clean(item.get('blocker'), 260)}",
                    f"   Evidence: {evidence_text}",
                    f"   Next: {report_clean(item.get('next_step'), 260)}",
                ]
            )
    else:
        lines.append("- No mission timeline items right now.")

    evidence_cards = report_items(payload, "session_evidence")[:limit]
    lines.extend(["", "## Session Evidence"])
    if evidence_cards:
        for index, card in enumerate(evidence_cards, start=1):
            checks = card.get("checks") if isinstance(card.get("checks"), list) else []
            checks_text = "; ".join(report_clean(item, 140) for item in checks[:4]) or "No checks recorded yet."
            lines.extend(
                [
                    f"{index}. {report_clean(card.get('project'), 120)} - {report_clean(card.get('state'), 80)}",
                    f"   Task: {report_clean(card.get('task'), 360)}",
                    f"   Work areas: {report_clean(card.get('areas'), 260)} ({int(card.get('changed_count') or 0)} changed)",
                    f"   Blocker: {report_clean(card.get('blocker'), 260)}",
                    f"   Checks: {checks_text}",
                ]
            )
    else:
        lines.append("- No session evidence cards recorded yet.")

    replay_cards = report_items(payload, "session_replay")[:limit]
    lines.extend(["", "## Session Replay"])
    if replay_cards:
        for index, card in enumerate(replay_cards, start=1):
            checks = card.get("checks") if isinstance(card.get("checks"), list) else []
            checks_text = "; ".join(report_clean(item, 140) for item in checks[:3]) or "No checks recorded yet."
            lines.extend(
                [
                    f"{index}. {report_clean(card.get('project'), 120)} - {report_clean(card.get('state'), 80)}",
                    f"   Story: {report_clean(card.get('story'), 600)}",
                    f"   Outcome: {report_clean(card.get('outcome'), 260)}",
                    f"   Blocker: {report_clean(card.get('blocker'), 220)}",
                    f"   Checks: {checks_text}",
                    f"   Next: {report_clean(card.get('next_step'), 260)}",
                ]
            )
    else:
        lines.append("- No session replay cards recorded yet.")

    playback_cards = report_items(payload, "operator_playback")[:limit]
    lines.extend(["", "## Operator Playback"])
    if playback_cards:
        for index, card in enumerate(playback_cards, start=1):
            checks = card.get("checks") if isinstance(card.get("checks"), list) else []
            approvals = card.get("pending_approvals") if isinstance(card.get("pending_approvals"), list) else []
            checks_text = "; ".join(report_clean(item, 140) for item in checks[:3]) or "No checks recorded yet."
            approvals_text = f"{len(approvals)} pending" if approvals else "none"
            lines.extend(
                [
                    f"{index}. {report_clean(card.get('project'), 120)} - {report_clean(card.get('confidence'), 120)}",
                    f"   Story: {report_clean(card.get('story'), 520)}",
                    f"   Outcome: {report_clean(card.get('outcome'), 260)}",
                    f"   Blocker: {report_clean(card.get('blocker'), 220)}",
                    f"   Proof: {checks_text}",
                    f"   Approvals: {approvals_text}",
                    f"   Primary action: {report_clean(card.get('primary_action'), 260)}",
                ]
            )
    else:
        lines.append("- No operator playback cards recorded yet.")

    completion_cards = report_items(payload, "project_completion")[:limit]
    lines.extend(["", "## Completion Checks"])
    if completion_cards:
        for index, card in enumerate(completion_cards, start=1):
            lines.extend(
                [
                    f"{index}. {report_clean(card.get('project'), 120)} - {report_clean(card.get('verdict'), 120)} ({int(card.get('completion_percent') or 0)}%)",
                    f"   Objective: {report_clean(card.get('objective') or 'not set', 420)}",
                    f"   Criteria: {int(card.get('done_criteria') or 0)}/{int(card.get('total_criteria') or 0)} done",
                    f"   Blocker: {report_clean(card.get('blocker'), 220)}",
                    f"   Next: {report_clean(card.get('next_step'), 260)}",
                ]
            )
    else:
        lines.append("- No completion checks recorded yet.")

    briefs = report_items(payload, "session_briefs")[:limit]
    lines.extend(["", "## Session Briefs"])
    if briefs:
        for index, item in enumerate(briefs, start=1):
            activity = item.get("last_activity_minutes")
            activity_text = f"{activity} min ago" if isinstance(activity, int) else "not available"
            attention = "yes" if item.get("needs_attention") else "no"
            lines.extend(
                [
                    f"{index}. {report_clean(item.get('project'), 120)} - {report_clean(item.get('state'), 80)}",
                    f"   Update: {report_clean(item.get('summary'), 360)}",
                    f"   Task: {report_clean(item.get('task'), 360)}",
                    f"   Work areas: {report_clean(item.get('areas'), 260)} ({int(item.get('changed_count') or 0)} changed)",
                    f"   Attention needed: {attention}; blocker: {report_clean(item.get('blocker'), 220)}",
                    f"   Last activity: {activity_text}",
                    f"   Next: {report_clean(item.get('next_step'), 260)}",
                ]
            )
    else:
        lines.append("- No active Commander session briefs.")

    feed = report_items(payload, "work_feed")[:limit]
    lines.extend(["", "## Work Feed"])
    if feed:
        for index, item in enumerate(feed, start=1):
            lines.append(
                f"{index}. {report_clean(item.get('project'), 120)} - "
                f"{report_clean(item.get('current_step') or item.get('phase') or item.get('state'), 260)}"
            )
            lines.append(f"   Direction: {report_clean(item.get('detail') or item.get('task'), 360)}")
            lines.append(f"   Next: {report_clean(item.get('next_step') or item.get('command'), 260)}")
    else:
        lines.append("- No active work-feed items.")

    approvals = report_items(payload, "approvals")[:limit]
    lines.extend(["", "## Pending Approvals"])
    if approvals:
        for item in approvals:
            lines.append(
                f"- {report_clean(item.get('project'), 120)} [{report_clean(item.get('id'), 80)}]: "
                f"{report_clean(item.get('type'), 80)} - {report_clean(item.get('message') or item.get('branch'), 280)}"
            )
    else:
        lines.append("- None.")

    conversation = report_items(payload, "conversation")[:limit]
    lines.extend(["", "## Conversation Signals"])
    if conversation:
        for item in conversation:
            lines.append(
                f"- {report_clean(item.get('direction') or item.get('actor'), 120)}: "
                f"{report_clean(item.get('summary'), 360)}"
            )
    else:
        lines.append("- No recent conversation signals included in this snapshot.")

    suggestions = report_items(payload, "decision_suggestions")[:limit]
    lines.extend(["", "## Decision Memory Suggestions"])
    if suggestions:
        for item in suggestions:
            lines.append(f"- {report_clean(item.get('title'), 160)}: {report_clean(item.get('note'), 500)}")
    else:
        lines.append("- No unsaved decision-memory suggestions.")

    audit = report_items(payload, "audit_trail")[:limit]
    lines.extend(["", "## Approval Audit"])
    if audit:
        for item in audit:
            lines.append(
                f"- {report_clean(item.get('at'), 80)}: {report_clean(item.get('status'), 80)} "
                f"{report_clean(item.get('type'), 80)} for {report_clean(item.get('project'), 120)}. "
                f"{report_clean(item.get('summary'), 360)}"
            )
    else:
        lines.append("- No approval audit events recorded.")

    images = report_items(payload, "recent_images")[:limit]
    lines.extend(["", "## Recent Image Context"])
    if images:
        for item in images:
            lines.append(
                f"- {report_clean(item.get('kind'), 80)}: {report_clean(item.get('summary'), 360)} "
                f"(risk: {report_clean(item.get('risk'), 120)})"
            )
    else:
        lines.append("- No recent image context.")

    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), list) else []
    lines.extend(["", "## Recommended Next Actions"])
    if recommendations:
        for index, item in enumerate(recommendations[:limit], start=1):
            lines.append(f"{index}. {report_clean(item, 420)}")
    else:
        lines.append("- No urgent recommendations.")

    return "\n".join(lines).strip() + "\n"


def save_operator_report(markdown: str) -> Path:
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = directory / f"commander-x-report-{stamp}.md"
    path.write_text(redact(markdown), encoding="utf-8")
    return path


def command_report(args: list[str], user_id: str) -> str:
    save_requested = any(arg.lower() in {"save", "export", "archive", "write"} for arg in args)
    show_details = any(arg.lower() in {"path", "file", "details", "full"} for arg in args)
    payload = operator_report_payload(user_id=user_id, source="telegram")
    markdown = format_operator_report(payload, source="telegram")
    if not save_requested:
        return compact(markdown, limit=3600)
    path = save_operator_report(markdown)
    report_id = path.stem.removeprefix("commander-x-report-")
    lines = [
        "Saved Commander X operator report.",
        f"Report ID: {report_id}",
        "Open the dashboard for the full preview, or ask for /report to see the current snapshot.",
    ]
    if show_details:
        lines.append(f"Local file: {path}")
    lines.extend(["", markdown])
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
        item = task_inbox_item(task)
        if item:
            key = task_inbox_dedupe_key(task)
            if any(existing.get("dedupe_key") == key for existing in items):
                continue
            item["dedupe_key"] = key
            items.append(item)
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
    for item in items:
        item.pop("dedupe_key", None)
    return items[:limit]


def task_inbox_dedupe_key(task: dict[str, Any]) -> str:
    project = str(task.get("project") or "-")
    status = str(task.get("status") or "queued")
    summary = summarize_task_for_human(task.get("title") or "")
    return f"{project}:{status}:{summary.lower()}"


def task_inbox_item(task: dict[str, Any]) -> dict[str, str] | None:
    status = str(task.get("status", "queued"))
    if status not in {"queued", "review", "failed"}:
        return None
    project_id = str(task.get("project") or "-")
    task_id = str(task.get("id") or "")
    project_name = project_label(project_id, include_id=False) if get_project(project_id) else project_id
    summary = summarize_task_for_human(task.get("title") or "-")
    if status == "queued":
        next_action = f"Start it with /queue start {task_id}." if task_id else f"Start it with /queue start <task_id>."
    elif status == "review":
        next_action = f"Review it, then mark it done with /queue done {task_id} or cancel it with /queue cancel {task_id}." if task_id else "Review it, then mark it done or cancel it from the dashboard."
    else:
        next_action = f"Review what happened with /playback {project_id}, then mark it done with /queue done {task_id} or cancel it with /queue cancel {task_id}." if task_id else f"Review what happened with /playback {project_id}."
    return {
        "kind": "task",
        "priority": "medium" if status != "failed" else "high",
        "title": f"{friendly_session_state(status)}: {safe_brief_text(project_name)}",
        "detail": short_human_text(f"{summary} {next_action}", limit=360),
    }


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
    items.extend(autopilot_recommendation_items(limit=3))
    settings = clickup_settings_from_env()
    if not settings.configured or not os.environ.get("GITHUB_TOKEN") or not os.environ.get("WHATSAPP_ACCESS_TOKEN"):
        items.extend(setup_recommendation_items(limit=3))
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


def autopilot_recommendation_items(limit: int = 4) -> list[str]:
    items: list[str] = []
    profiles = profiles_data().get("profiles", {})
    for project_id, profile in sorted(profiles.items()):
        if not isinstance(profile, dict):
            continue
        autopilot = profile.get("autopilot")
        if not isinstance(autopilot, dict) or not autopilot.get("enabled"):
            continue
        ok, reason, criterion = autopilot_can_start(project_id)
        if ok:
            criterion_text = criterion.get("text") if isinstance(criterion, dict) else "next open criterion"
            detail = f"Autopilot for {project_label(project_id, include_id=False)} is ready: {safe_brief_text(criterion_text)}. {autopilot_next_action(project_id, reason, can_start=True)}"
            items.append(safe_brief_text(detail))
        elif reason in {"no open criteria", "blocked criteria need review", "objective already complete"}:
            detail = f"Autopilot for {project_label(project_id, include_id=False)} is waiting: {reason}. {autopilot_next_action(project_id, reason, can_start=False)}"
            items.append(safe_brief_text(detail))
        if len(items) >= limit:
            break
    return items


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


def session_last_activity_minutes(session: dict[str, Any]) -> int | None:
    log_file = Path(str(session.get("log_file", "")))
    if log_file.exists():
        return max(0, int((time.time() - log_file.stat().st_mtime) // 60))
    parsed = parse_iso_datetime(str(session.get("updated_at") or session.get("started_at") or ""))
    if parsed:
        return max(0, int((dt.datetime.now(dt.timezone.utc) - parsed).total_seconds() // 60))
    return None


def latest_timeline_summary(session: dict[str, Any]) -> tuple[str, str]:
    timeline = session.get("timeline") if isinstance(session, dict) else []
    if isinstance(timeline, list):
        for item in reversed(timeline):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("phase") or "").strip()
                detail = str(item.get("detail") or "").strip()
                if title or detail:
                    return title or "Update", detail
    return str(session.get("current_phase") or session.get("state") or "Unknown"), ""


def feed_item_from_session(project_id: str, session: dict[str, Any], change: dict[str, Any] | None = None) -> dict[str, Any]:
    state = str(session.get("state") or "unknown")
    phase = str(session.get("current_phase") or state)
    project_name = project_label(project_id, include_id=False)
    title, detail = latest_timeline_summary(session)
    pending = session.get("pending_actions") or {}
    age = session_last_activity_minutes(session)
    plan = session.get("work_plan") if isinstance(session.get("work_plan"), dict) else {}
    risk = str(plan.get("risk") or "unknown")
    if pending:
        blocker = f"{len(pending)} approval(s) waiting"
        next_step = f"Review approvals or ask to watch {project_name}."
    elif state == "running":
        blocker = "none reported"
        next_step = f"Watch progress for {project_name}."
    elif state in {"failed", "finished_unknown", "stop_failed"}:
        blocker = "session needs review"
        next_step = f"Review {project_name} before continuing."
    elif state in {"stopped", "idle"}:
        blocker = "idle"
        next_step = f"Start new work for {project_name}."
    else:
        blocker = "review current state"
        next_step = f"Review {project_name} for detail."
    return {
        "project": project_id,
        "kind": "session",
        "state": state,
        "phase": phase,
        "task": str(session.get("task") or "-"),
        "current_step": title,
        "detail": detail,
        "risk": risk,
        "last_activity_minutes": age,
        "changed_count": int((change or {}).get("changed_count") or 0),
        "areas": str((change or {}).get("areas") or "no local changes tracked"),
        "blocker": blocker,
        "next_step": next_step,
        "command": f"/watch {project_id}",
        "priority": 0 if state == "running" else 1 if pending else 2 if state in {"failed", "finished_unknown", "stop_failed"} else 4,
    }


def feed_item_from_task(task: dict[str, Any], change: dict[str, Any] | None = None) -> dict[str, Any]:
    project_id = str(task.get("project") or "-")
    status = str(task.get("status") or "queued")
    next_step = f"Start queued work with /queue start {task.get('id')}" if status == "queued" else f"Review queue state with /queue."
    return {
        "project": project_id,
        "kind": "task",
        "state": status,
        "phase": status,
        "task": str(task.get("title") or "-"),
        "current_step": "Task is waiting in Commander queue",
        "detail": str(task.get("title") or ""),
        "risk": "unknown",
        "last_activity_minutes": None,
        "changed_count": int((change or {}).get("changed_count") or 0),
        "areas": str((change or {}).get("areas") or "no local changes tracked"),
        "blocker": "waiting to start" if status == "queued" else "needs review",
        "next_step": next_step,
        "command": f"/queue start {task.get('id')}" if status == "queued" else "/queue",
        "priority": 3 if status == "queued" else 2,
    }


def feed_item_from_changes(change: dict[str, Any]) -> dict[str, Any]:
    project_id = str(change.get("project") or "-")
    return {
        "project": project_id,
        "kind": "changes",
        "state": "changed",
        "phase": "review",
        "task": "Local worktree has changes",
        "current_step": "Changes are present but no Commander session is active",
        "detail": "Commander is hiding filenames by default.",
        "risk": "review",
        "last_activity_minutes": None,
        "changed_count": int(change.get("changed_count") or 0),
        "areas": str(change.get("areas") or "changed areas unavailable"),
        "blocker": "review before starting more work",
        "next_step": f"Use /changes for summary or /watch {project_id}.",
        "command": f"/watch {project_id}",
        "priority": 4,
    }


def work_feed_items(
    user_id: str | None = None,
    limit: int = 12,
    sessions: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if sessions is None:
        refresh_session_states()
    if tasks is None:
        sync_tasks_with_sessions()
    sessions = sessions if sessions is not None else sessions_data().get("sessions", {})
    changes = changes if changes is not None else changed_project_details(limit=20, max_files=0)
    tasks = tasks if tasks is not None else tasks_data().get("tasks", [])
    change_map = {str(row.get("project")): row for row in changes}
    items: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for project_id, session in sorted(sessions.items()):
        if not isinstance(session, dict):
            continue
        if session.get("state") in {"archived"}:
            continue
        items.append(feed_item_from_session(project_id, session, change_map.get(project_id)))
        seen_projects.add(project_id)
    for task in visible_task_records(tasks, limit=20):
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "queued")
        project_id = str(task.get("project") or "")
        if status not in {"queued", "review", "failed"} or project_id in seen_projects:
            continue
        items.append(feed_item_from_task(task, change_map.get(project_id)))
        seen_projects.add(project_id)
    for change in changes:
        project_id = str(change.get("project") or "")
        if not project_id or project_id in seen_projects:
            continue
        items.append(feed_item_from_changes(change))
        seen_projects.add(project_id)
    if user_id:
        active = str(user_state(user_id).get("active_project") or "")
        if active and active not in seen_projects and get_project(active):
            profile = project_profile(active)
            items.append(
                {
                    "project": active,
                    "kind": "focus",
                    "state": "focused",
                    "phase": "idle",
                    "task": "Focused project is selected",
                    "current_step": "No Commander-started Codex session is active",
                    "detail": "Commander will use this project when you omit a project name.",
                    "risk": "low",
                    "last_activity_minutes": None,
                    "changed_count": int(profile.get("changed_count") or 0),
                    "areas": change_bucket_summary(profile.get("changed_preview") or []) if profile.get("changed_preview") else "no local changes tracked",
                    "blocker": "waiting for instruction",
                    "next_step": f"Start work with /start {active} \"task\".",
                    "command": f"/start {active} \"task\"",
                    "priority": 5,
                }
            )
    items.sort(key=lambda item: (int(item.get("priority") or 9), str(item.get("project") or "")))
    return items[:limit]


def format_work_feed(items: list[dict[str, Any]], title: str = "Commander X work feed") -> str:
    if not items:
        return "No active Commander work feed items. Start with /start <project> \"task\" or /queue add <project> \"task\"."
    lines = [title, "Plain-English view. Technical filenames are hidden unless you ask for /diff.", ""]
    for index, item in enumerate(items, start=1):
        age = item.get("last_activity_minutes")
        activity = f"{age} min ago" if isinstance(age, int) else "not available"
        lines.extend(
            [
                f"{index}. {item.get('project')} - {item.get('state')}",
                f"   Task: {item.get('task')}",
                f"   Now: {item.get('current_step')}",
                f"   Direction: {item.get('detail') or item.get('phase')}",
                f"   Work areas: {item.get('areas')} ({item.get('changed_count')} changed)",
                f"   Blocker: {item.get('blocker')}",
                f"   Last activity: {activity}",
                f"   Next: {item.get('next_step')}",
            ]
        )
    return compact("\n".join(lines), limit=3600)


def safe_brief_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    text = re.sub(r"(?i)\b[A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)+[^\\/:*?\"<>|\r\n]+", "technical path", text)
    text = re.sub(r"(?i)(?:^|\s)(?:\.{1,2}/|/)?(?:[\w.-]+/)+[\w.-]+\.[a-z0-9]{1,8}\b", " technical path", text)
    text = re.sub(r"(?i)\b[\w.-]+\.(?:tsx|ts|jsx|js|mjs|cjs|py|md|mdx|json|css|scss|html|yml|yaml|toml|env)\b", "technical file", text)
    return " ".join(text.split())


def read_recent_log_text(path: Path, max_bytes: int = 120_000) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def codex_output_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n")
    matches = list(re.finditer(r"(?im)^\s*(?:codex|assistant)\s*$", normalized))
    if matches:
        return normalized[matches[-1].end() :]
    return normalized


def add_progress_signal(signals: list[dict[str, str]], phase: str, title: str, detail: str, status: str = "done") -> None:
    item = {
        "phase": phase,
        "title": safe_brief_text(title),
        "detail": safe_brief_text(detail),
        "status": status,
    }
    if signals and signals[-1].get("phase") == item["phase"] and signals[-1].get("title") == item["title"]:
        signals[-1] = item
        return
    signals.append(item)


def progress_signals_from_text(text: str, limit: int = 6) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).strip()
        if not line:
            continue
        lowered = line.lower()
        if len(line) > 500 or "<html" in lowered or "<svg" in lowered or "cloudflare" in lowered or "analytics-events" in lowered:
            continue
        planning_line = bool(
            re.search(r"\b(i will|i'll|i.ll|i am going to|i'm going to|i.m going to|going to|plan to|should|expected checks)\b", lowered)
        )
        commandish = bool(
            re.search(r"^\s*(?:exec|[>`$]?\s*(?:npm|pnpm|yarn|python|pytest|npx|git)\b)", lowered)
            or re.search(r"-command\s+['\"]?[^'\"]*(?:npm|pnpm|yarn|python|pytest|npx|git|playwright|tsc)", lowered)
        )
        if "windows sandbox: setup refresh failed" in lowered:
            add_progress_signal(
                signals,
                "blocked",
                "Local shell blocked",
                "Codex could not run project commands because the Windows sandbox failed before checks could start.",
                "warn",
            )
        elif "authrequired" in lowered or "oauth-protected-resource" in lowered:
            continue
        elif "access is denied" in lowered:
            add_progress_signal(
                signals,
                "blocked",
                "Local permission issue",
                "A local cache, plugin, or process operation hit an access-denied error.",
                "warn",
            )
        elif "current blocker" in lowered or re.search(r"\bblocker:\b", lowered):
            add_progress_signal(signals, "blocked", "Blocker reported", "Codex reported a blocker that needs review.", "warn")
        elif re.search(r"\b(i'll inspect|i.ll inspect|i'm checking|i.ll check|inspect the|reading|reviewing)\b", lowered):
            add_progress_signal(signals, "inspect", "Inspecting project", "Codex is reading project state and context.", "active")
        elif re.search(r"\b(git status|get-childitem|select-string|get-content|rg |findstr|list_mcp_resources)\b", lowered):
            add_progress_signal(signals, "inspect", "Inspecting project", "Codex is reading project state and context.", "active")
        elif not planning_line and re.search(r"\b(apply_patch|success\. updated|success\. added|created|updated|modified|changed)\b", lowered):
            add_progress_signal(signals, "edit", "Making changes", "Codex appears to be changing local project files.", "active")
        elif (commandish or not planning_line) and re.search(
            r"\b(npm\s+(?:run\s+)?(?:test|build|lint|typecheck|smoke)|pnpm\s+(?:run\s+)?(?:test|build|lint|typecheck)|yarn\s+(?:test|build|lint)|pytest|unittest|py_compile|playwright|smoke[- ]?test|tsc\b|next\s+build|vite\s+build)\b",
            lowered,
        ):
            add_progress_signal(signals, "verify", "Running checks", "Codex is verifying the work with local checks.", "active")
        elif "no files changed" in lowered:
            add_progress_signal(signals, "report", "No local changes made", "Codex reported that it did not change files.", "done")
        elif re.match(r"^\s*1\.\s+done\b", line, flags=re.IGNORECASE) or "next recommended action" in lowered:
            add_progress_signal(signals, "report", "Final report ready", "Codex wrote an outcome summary for review.", "done")
        elif "retrying" in lowered and "failed" in lowered:
            add_progress_signal(signals, "retry", "Retrying after failure", "Codex is retrying after a tool or environment failure.", "active")
    return signals[-limit:]


def log_progress_signals(log_file: Path, limit: int = 6) -> list[dict[str, str]]:
    return progress_signals_from_text(codex_output_text(read_recent_log_text(log_file)), limit=limit)


def refresh_session_progress(project_id: str, session: dict[str, Any]) -> bool:
    log_file = Path(str(session.get("log_file", "")))
    if not log_file.exists():
        return False
    try:
        mtime = str(log_file.stat().st_mtime_ns)
    except OSError:
        return False
    if session.get("progress_log_mtime") == mtime and session.get("progress_signals"):
        return False
    signals = log_progress_signals(log_file)
    if not signals:
        return False
    if session.get("progress_log_mtime") == mtime and session.get("progress_signals") == signals:
        return False
    session["progress_log_mtime"] = mtime
    session["progress_signals"] = signals
    latest = signals[-1]
    session["current_progress"] = latest
    append_timeline_event(
        session,
        str(latest.get("phase") or "progress"),
        str(latest.get("title") or "Progress update"),
        str(latest.get("detail") or ""),
        status=str(latest.get("status") or "done"),
    )
    session["updated_at"] = utc_now()
    return True


def session_brief_items(
    user_id: str | None = None,
    limit: int = 10,
    sessions: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if sessions is None:
        refresh_session_states()
    sessions = sessions if sessions is not None else sessions_data().get("sessions", {})
    feed = work_feed_items(user_id=user_id, limit=max(limit * 2, 12), sessions=sessions, changes=changes, tasks=tasks)
    briefs: list[dict[str, Any]] = []
    for item in feed:
        project_id = str(item.get("project") or "-")
        session = sessions.get(project_id) if isinstance(sessions, dict) else {}
        if not isinstance(session, dict):
            session = {}
        state = str(item.get("state") or "unknown")
        blocker = safe_brief_text(item.get("blocker") or "none reported")
        progress = session.get("current_progress") if isinstance(session.get("current_progress"), dict) else {}
        progress_title = safe_brief_text(progress.get("title") if isinstance(progress, dict) else "")
        progress_detail = safe_brief_text(progress.get("detail") if isinstance(progress, dict) else "")
        progress_status = str(progress.get("status") or "") if isinstance(progress, dict) else ""
        progress_signals = session.get("progress_signals") if isinstance(session.get("progress_signals"), list) else []
        warning_signals = [
            signal
            for signal in progress_signals
            if isinstance(signal, dict) and str(signal.get("status") or "") == "warn"
        ]
        specific_warnings = [signal for signal in warning_signals if safe_brief_text(signal.get("title")) != "Blocker reported"]
        warning_signal = (specific_warnings or warning_signals)[-1] if (specific_warnings or warning_signals) else None
        if isinstance(warning_signal, dict):
            progress_title = safe_brief_text(warning_signal.get("title"))
            progress_detail = safe_brief_text(warning_signal.get("detail"))
            blocker = f"{progress_title}: {progress_detail}"
        elif progress_status == "warn" and progress_title != "-":
            blocker = f"{progress_title}: {progress_detail}"
        signal_lines = [
            f"{safe_brief_text(signal.get('title'))} - {safe_brief_text(signal.get('detail'))}"
            for signal in progress_signals[-3:]
            if isinstance(signal, dict)
        ]
        timeline = signal_lines or ([safe_brief_text(line.lstrip("- ")) for line in timeline_lines(session, limit=3)] if session else [])
        timeline = [line for line in timeline if line and line != "-"]
        if state == "running":
            summary = f"Codex is actively working. Current focus: {item.get('current_step') or item.get('phase') or 'progress update unavailable'}."
        elif "approval" in blocker.lower():
            summary = "Work is paused because Commander needs your approval before continuing."
        elif state in {"failed", "finished_unknown", "stop_failed"}:
            summary = f"{progress_title}: {progress_detail}" if progress_title != "-" else "This session needs review before more work is started."
        elif isinstance(warning_signal, dict):
            summary = f"Finished with blocker: {progress_title}: {progress_detail}"
        elif item.get("kind") == "changes":
            summary = "There are local project changes to review, but no active Commander-run Codex session."
        elif item.get("kind") == "task":
            summary = "A queued Commander task is waiting to be started or reviewed."
        else:
            summary = safe_brief_text(item.get("detail") or item.get("phase") or "No active blocker reported.")
        needs_attention = state in {"failed", "finished_unknown", "stop_failed"} or (
            blocker not in {"none reported", "idle", "waiting for instruction", "-"}
        )
        briefs.append(
            {
                "project": project_id,
                "state": state,
                "phase": safe_brief_text(item.get("phase") or state),
                "task": summarize_task_for_human(item.get("task")),
                "summary": safe_brief_text(summary),
                "areas": safe_brief_text(item.get("areas") or "no local changes tracked"),
                "changed_count": int(item.get("changed_count") or 0),
                "blocker": blocker,
                "needs_attention": needs_attention,
                "last_activity_minutes": item.get("last_activity_minutes"),
                "next_step": safe_brief_text(item.get("next_step") or item.get("command") or "-"),
                "timeline": timeline,
                "priority": int(item.get("priority") or 9),
            }
        )
    briefs.sort(key=lambda item: (int(item.get("priority") or 9), str(item.get("project") or "")))
    return briefs[:limit]


def format_session_briefs(items: list[dict[str, Any]], title: str = "Commander X session briefs") -> str:
    if not items:
        return "No active Commander briefs right now. Start with /start <project> \"task\" or /feed for the broader work view."
    lines = [title, "Executive view. Technical filenames and local paths are hidden unless you ask for /diff or /projects full.", ""]
    for index, item in enumerate(items, start=1):
        age = item.get("last_activity_minutes")
        activity = f"{age} min ago" if isinstance(age, int) else "not available"
        attention = "yes" if item.get("needs_attention") else "no"
        timeline = "; ".join(str(line) for line in item.get("timeline", [])[:3]) or "No detailed timeline yet."
        project_name = project_label(str(item.get("project") or ""), include_id=False)
        state_label = friendly_session_state(item.get("state"))
        lines.extend(
            [
                f"{index}. {project_name} - {state_label}",
                f"   Update: {item.get('summary')}",
                f"   Task: {item.get('task')}",
                f"   Work areas: {item.get('areas')} ({item.get('changed_count')} changed)",
                f"   Attention needed: {attention} - {item.get('blocker')}",
                f"   Last activity: {activity}",
                f"   Timeline: {timeline}",
                f"   Next: {item.get('next_step')}",
            ]
        )
    return compact("\n".join(lines), limit=3600)


def mission_stage_from_brief(item: dict[str, Any]) -> tuple[str, str, int]:
    state = str(item.get("state") or "unknown")
    blocker = str(item.get("blocker") or "")
    phase = str(item.get("phase") or state)
    if "approval" in blocker.lower():
        return "Waiting for your approval", "warn", 0
    if state == "running":
        return f"Working: {safe_brief_text(phase)}", "good", 1
    if state in {"failed", "finished_unknown", "stop_failed"}:
        return "Needs review before continuing", "bad", 2
    if item.get("needs_attention"):
        return "Needs operator review", "warn", 3
    if state in {"queued", "review"}:
        return "Queued for Commander", "warn", 4
    if state == "changed":
        return "Local changes need review", "warn", 5
    if state in {"completed", "done"}:
        return "Completed, ready for review", "good", 6
    if state in {"focused", "idle", "stopped"}:
        return "Idle and waiting", "good", 7
    return f"Tracking: {safe_brief_text(state)}", "good", 8


def mission_timeline_items(
    user_id: str | None = None,
    limit: int = 10,
    sessions: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    briefs = session_brief_items(user_id=user_id, limit=max(limit * 2, 12), sessions=sessions, changes=changes, tasks=tasks)
    items: list[dict[str, Any]] = []
    for brief in briefs:
        stage, status, stage_order = mission_stage_from_brief(brief)
        evidence = brief.get("timeline") if isinstance(brief.get("timeline"), list) else []
        direction = brief.get("summary") or brief.get("task") or "No current direction reported."
        age = brief.get("last_activity_minutes")
        if isinstance(age, int):
            freshness = "fresh" if age <= 10 else "stale" if age >= 60 else "recent"
        else:
            freshness = "unknown"
        items.append(
            {
                "project": safe_brief_text(brief.get("project")),
                "stage": safe_brief_text(stage),
                "status": status,
                "direction": safe_brief_text(direction),
                "blocker": safe_brief_text(brief.get("blocker") or "none reported"),
                "work_areas": safe_brief_text(brief.get("areas") or "no local changes tracked"),
                "changed_count": int(brief.get("changed_count") or 0),
                "freshness": freshness,
                "last_activity_minutes": age,
                "evidence": [safe_brief_text(line) for line in evidence[:4] if str(line).strip()],
                "next_step": safe_brief_text(brief.get("next_step") or "-"),
                "command": f"/watch {safe_brief_text(brief.get('project'))}",
                "priority": int(brief.get("priority") or 9) + stage_order,
            }
        )
    items.sort(key=lambda item: (int(item.get("priority") or 9), str(item.get("project") or "")))
    return items[:limit]


def format_mission_timeline(items: list[dict[str, Any]], title: str = "Commander X mission control") -> str:
    if not items:
        return "No mission timeline items right now. Start with /start <project> \"task\" or /queue add <project> \"task\"."
    lines = [title, "Plain-English timeline. Technical filenames and local paths are hidden unless you ask for /diff.", ""]
    for index, item in enumerate(items, start=1):
        age = item.get("last_activity_minutes")
        activity = f"{age} min ago" if isinstance(age, int) else "not available"
        evidence = "; ".join(str(line) for line in item.get("evidence", [])[:3]) or "No detailed evidence yet."
        lines.extend(
            [
                f"{index}. {item.get('project')} - {item.get('stage')}",
                f"   Direction: {item.get('direction')}",
                f"   Work areas: {item.get('work_areas')} ({item.get('changed_count')} changed)",
                f"   Blocker: {item.get('blocker')}",
                f"   Evidence: {evidence}",
                f"   Last activity: {activity} ({item.get('freshness')})",
                f"   Next: {item.get('next_step')}",
            ]
        )
    return compact("\n".join(lines), limit=3600)


def command_mission(args: list[str], user_id: str) -> str:
    project_id = None
    if args and args[0].lower() not in {"all", "global", "overview", "summary"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
    items = mission_timeline_items(user_id=user_id, limit=12)
    if project_id:
        items = [item for item in items if item.get("project") == project_id]
        return format_mission_timeline(items, title=f"Commander X mission control: {project_id}")
    return format_mission_timeline(items)


def command_evidence(args: list[str], user_id: str) -> str:
    project_id = None
    if args and args[0].lower() not in {"all", "global", "overview", "summary"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
    if project_id:
        return session_evidence(project_id)
    cards = session_evidence_cards(user_id=user_id, limit=8)
    if not cards:
        return "No session evidence cards yet. Start with /start <project> \"task\"."
    lines = ["Commander X evidence cards", "Plain-English proof of current session state. Technical filenames and paths are hidden.", ""]
    for index, card in enumerate(cards, start=1):
        checks = card.get("checks") if isinstance(card.get("checks"), list) else []
        check_summary = "; ".join(str(item) for item in checks[:3]) or "No checks recorded yet."
        lines.extend(
            [
                f"{index}. {card.get('project')} - {card.get('state')}",
                f"   Task: {card.get('task')}",
                f"   Work areas: {card.get('areas')} ({card.get('changed_count')} changed)",
                f"   Blocker: {card.get('blocker')}",
                f"   Checks: {check_summary}",
                f"   Open: /evidence {card.get('project')}",
            ]
        )
    return compact("\n".join(lines), limit=3600)


def command_replay(args: list[str], user_id: str) -> str:
    project_id = None
    if args and args[0].lower() not in {"all", "global", "overview", "summary"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
    if project_id:
        return session_replay(project_id)
    cards = session_replay_cards(user_id=user_id, limit=6)
    if not cards:
        return "No session replay cards yet. Start with /start <project> \"task\"."
    lines = [
        "Commander X session replay",
        "Plain-English run stories. Technical filenames and paths are hidden.",
        "",
    ]
    for index, card in enumerate(cards, start=1):
        checks = card.get("checks") if isinstance(card.get("checks"), list) else []
        check_summary = "; ".join(str(item) for item in checks[:3]) or "No checks recorded yet."
        lines.extend(
            [
                f"{index}. {card.get('project')} - {card.get('state')}",
                f"   Story: {card.get('story')}",
                f"   Outcome: {card.get('outcome')}",
                f"   Blocker: {card.get('blocker')}",
                f"   Checks: {check_summary}",
                f"   Next: {card.get('next_step')}",
                f"   Open: /replay {card.get('project')}",
            ]
        )
    return compact("\n".join(lines), limit=3600)


def command_playback(args: list[str], user_id: str) -> str:
    project_id = None
    if args and args[0].lower() not in {"all", "global", "overview", "summary"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
    elif not args:
        project_id = resolve_project_id(None, user_id=user_id) if allows_active_project_fallback(user_id) else None
    if project_id:
        return operator_playback(project_id, user_id=user_id)
    cards = operator_playback_cards(user_id=user_id, limit=6)
    if not cards:
        return "No operator playback cards yet. Use /focus <project> or /start <project> \"task\"."
    lines = [
        "Commander X operator playback",
        "Assistant-style project playback. Technical filenames and paths are hidden.",
        "",
    ]
    for index, card in enumerate(cards, start=1):
        checks = card.get("checks") if isinstance(card.get("checks"), list) else []
        proof = "; ".join(str(item) for item in checks[:2]) or "No checks recorded yet."
        lines.extend(
            [
                f"{index}. {card.get('project')} - {card.get('confidence')}",
                f"   Story: {card.get('story')}",
                f"   Outcome: {card.get('outcome')}",
                f"   Proof: {proof}",
                f"   Primary action: {card.get('primary_action')}",
                f"   Open: /playback {card.get('project')}",
            ]
        )
    return compact("\n".join(lines), limit=3600)


def command_briefs(args: list[str], user_id: str) -> str:
    project_id = None
    if args and args[0].lower() not in {"all", "global", "overview", "summary"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
    items = session_brief_items(user_id=user_id, limit=12)
    if project_id:
        items = [item for item in items if item.get("project") == project_id]
        return format_session_briefs(items, title=f"Commander X session brief: {project_label(project_id, include_id=False)}")
    return format_session_briefs(items)


def command_feed(args: list[str], user_id: str) -> str:
    project_id = None
    if args and args[0].lower() not in {"all", "global", "overview", "summary"}:
        project_id, _rest = project_and_rest(args, user_id=user_id)
    items = work_feed_items(user_id=user_id, limit=14)
    if project_id:
        items = [item for item in items if item.get("project") == project_id]
        return format_work_feed(items, title=f"Commander X work feed: {project_id}")
    return format_work_feed(items)


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
        signals = session.get("progress_signals") if isinstance(session.get("progress_signals"), list) else []
        if signals:
            lines.extend(["", "Human progress signals:"])
            for signal in signals[-4:]:
                if isinstance(signal, dict):
                    lines.append(f"- {safe_brief_text(signal.get('title'))}: {safe_brief_text(signal.get('detail'))}")
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


def parse_volume_command(args: list[str]) -> tuple[str, int] | None:
    text = " ".join(args).strip().lower()
    if not text:
        return None
    if re.search(r"\b(mute|silent|silence|off|zero|0)\b", text):
        return "mute", 1
    if re.search(r"\b(max|maximize|maximum|full|100|hundred|crank)\b", text):
        return "up", 25

    action = args[0].lower() if args else ""
    if action in {"lower", "decrease", "reduce", "quieter", "down"}:
        normalized = "down"
    elif action in {"raise", "increase", "louder", "up"}:
        normalized = "up"
    else:
        return None

    steps = 1
    match = re.search(r"\b(\d{1,3})\s*x?\b|x\s*(\d{1,3})\b", text)
    if match:
        value = int(match.group(1) or match.group(2))
        if re.search(r"\bto\s+\d{1,3}\b", text) and value > 25:
            if normalized == "down":
                steps = max(1, min(25, round((100 - min(value, 100)) / 2)))
            else:
                steps = max(1, min(25, round((min(value, 100)) / 4)))
        else:
            steps = value
    return normalized, max(1, min(25, steps))


def command_volume(args: list[str]) -> str:
    if not env_bool("COMMANDER_ALLOW_VOLUME_KEYS", True):
        return "Volume control is disabled by COMMANDER_ALLOW_VOLUME_KEYS."
    parsed = parse_volume_command(args)
    if not parsed:
        return "Usage: /volume up [steps], /volume down [steps], /volume max, or /volume mute"
    action, steps = parsed
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
            "- /volume up|down|max|mute [steps]",
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
            "- /clickup count [query]",
            "",
            "Note: the Codex Desktop ClickUp connector is available to this Codex chat, but the always-on Commander service needs ClickUp API credentials to work from Telegram while this chat is closed.",
        ]
        return "\n".join(lines)
    if action in {"recent", "tasks", "task", "count", "counts", "summary"}:
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
        if action in {"count", "counts", "summary"}:
            statuses: dict[str, int] = {}
            for task in filtered:
                raw_status = task.get("status")
                status = raw_status.get("status") if isinstance(raw_status, dict) else raw_status
                label = str(status or "unknown").strip() or "unknown"
                statuses[label] = statuses.get(label, 0) + 1
            title = "ClickUp count" + (f" for: {query}" if query else "")
            lines = [title, "", f"Matching tasks: {len(filtered)}"]
            if statuses:
                lines.extend(["", "Status breakdown:"])
                lines.extend(f"- {status}: {count}" for status, count in sorted(statuses.items(), key=lambda item: (-item[1], item[0]))[:8])
            lines.extend(["", "Sample:", clickup_format_tasks(filtered, limit=5)])
            return compact("\n".join(lines), limit=3600)
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


def update_project_autopilot(project_id: str, enabled: bool, user_id: str, interval_minutes: int | None = None) -> dict[str, Any]:
    data = profiles_data()
    profiles = data.setdefault("profiles", {})
    profile = profiles.setdefault(project_id, {})
    if not isinstance(profile, dict):
        profile = {}
        profiles[project_id] = profile
    autopilot = profile.setdefault("autopilot", {})
    if not isinstance(autopilot, dict):
        autopilot = {}
        profile["autopilot"] = autopilot
    autopilot.update(
        {
            "enabled": enabled,
            "local_full_access": True,
            "updated_at": utc_now(),
            "updated_by": str(user_id),
            "mode": "continue_until_definition_of_done",
        }
    )
    if interval_minutes is not None:
        autopilot["interval_minutes"] = max(1, min(240, interval_minutes))
    else:
        autopilot.setdefault("interval_minutes", 5)
    save_profiles(data)
    return autopilot


def autopilot_profile(project_id: str) -> dict[str, Any]:
    profile = profiles_data().get("profiles", {}).get(project_id)
    if not isinstance(profile, dict):
        return {}
    autopilot = profile.get("autopilot")
    return autopilot if isinstance(autopilot, dict) else {}


def autopilot_open_criterion(project_id: str) -> dict[str, str] | None:
    profile = project_profile(project_id)
    criteria = normalize_done_criteria(profile.get("done_criteria") or [])
    for criterion in criteria:
        if criterion.get("status") == "open":
            return criterion
    return None


def autopilot_task_for_criterion(project_id: str, criterion: dict[str, str]) -> str:
    project_name = project_label(project_id, include_id=False)
    criterion_id = criterion.get("id") or "next"
    criterion_text = criterion.get("text") or "next open Definition-of-Done criterion"
    return (
        f"Autonomous continuation for {project_name}. Continue from the completed and verified local checkpoints. "
        f"Focus only on Definition-of-Done criterion {criterion_id}: {criterion_text}. "
        "Build the local product capability, update or add tests, run the relevant verification commands, and leave clear evidence. "
        "You have permission to edit, create, reorganize, and clean files inside this new local project, run local checks/builds, and install development dependencies if needed. "
        "Do not deploy, push, spend money, send real external messages, use production credentials, modify billing/legal/identity settings, or claim V1 is done until every Definition-of-Done criterion has proof. "
        "Report for a non-technical owner: what capability became possible, what can be seen, what was verified, blockers, and the next criterion."
    )


def autopilot_can_start(project_id: str, now: dt.datetime | None = None) -> tuple[bool, str, dict[str, str] | None]:
    now = now or dt.datetime.now(dt.timezone.utc)
    autopilot = autopilot_profile(project_id)
    if not autopilot.get("enabled"):
        return False, "autopilot is off", None
    refresh_session_states()
    session = sessions_data().get("sessions", {}).get(project_id) or {}
    if session.get("state") == "running":
        return False, "session already running", None
    auto_update_done_criteria_from_session_summary(project_id, session)
    pending = session.get("pending_actions") if isinstance(session.get("pending_actions"), dict) else {}
    if pending:
        return False, "pending approval exists", None
    last_started = parse_iso_datetime(str(autopilot.get("last_started_at") or ""))
    interval = int(autopilot.get("interval_minutes") or 5)
    if last_started and last_started + dt.timedelta(minutes=interval) > now:
        return False, "cooldown active", None
    card = project_completion_card(project_id)
    verdict = str(card.get("verdict") or "")
    if "100% done" in verdict:
        return False, "objective already complete", None
    if card.get("pending_approvals"):
        return False, "pending approval exists", None
    criteria = normalize_done_criteria(project_profile(project_id).get("done_criteria") or [])
    if any(criterion.get("status") == "blocked" for criterion in criteria):
        return False, "blocked criteria need review", None
    criterion = autopilot_open_criterion(project_id)
    if not criterion:
        return False, "no open criteria", None
    return True, "ready", criterion


def autopilot_next_action(project_id: str, reason: str, can_start: bool = False) -> str:
    reason_l = (reason or "").lower()
    if can_start or reason_l == "ready":
        return "Run /autopilot run or wait for the next heartbeat tick."
    if "off" in reason_l:
        return f"Enable with /autopilot on {project_id} [minutes]."
    if "running" in reason_l:
        return f"Watch progress with /watch {project_id}."
    if "pending approval" in reason_l:
        return "Review decisions with /approvals."
    if "cooldown" in reason_l:
        return "Wait for the cooldown, then check /autopilot status again."
    if "objective already complete" in reason_l or "100% done" in reason_l:
        return f"Review completion with /done {project_id}. Add a new objective only if you want more scope."
    if "blocked" in reason_l:
        return f"Review blocked criteria with /objective {project_id}."
    if "no open criteria" in reason_l:
        return f"Review completion with /done {project_id}. If this milestone needs more work, add a new criterion with /objective add {project_id} \"criterion\"."
    return f"Check /done {project_id} and /objective {project_id} before starting more autonomous work."


def autopilot_tick_once(user_id: str = "autopilot") -> list[str]:
    messages: list[str] = []
    profiles = profiles_data().get("profiles", {})
    for project_id, profile in sorted(profiles.items()):
        if not isinstance(profile, dict):
            continue
        autopilot = profile.get("autopilot")
        if not isinstance(autopilot, dict) or not autopilot.get("enabled"):
            continue
        ok, reason, criterion = autopilot_can_start(project_id)
        if not ok or not criterion:
            messages.append(f"{project_id}: {reason}")
            continue
        task = autopilot_task_for_criterion(project_id, criterion)
        data = profiles_data()
        live_profile = data.setdefault("profiles", {}).setdefault(project_id, {})
        if isinstance(live_profile, dict):
            live_autopilot = live_profile.setdefault("autopilot", {})
            if isinstance(live_autopilot, dict):
                live_autopilot["last_started_at"] = utc_now()
                live_autopilot["last_criterion_id"] = criterion.get("id")
                live_autopilot["last_task"] = task
                save_profiles(data)
        try:
            started = start_codex(project_id, task, user_id=user_id)
            messages.append(f"{project_id}: started criterion {criterion.get('id')}")
            print(f"{utc_now()} autopilot started {project_id}: {criterion.get('id')}", flush=True)
            print(redact(started.splitlines()[0] if started else "started"), flush=True)
        except Exception as exc:
            messages.append(f"{project_id}: start failed: {redact(str(exc))}")
            print(f"{utc_now()} autopilot start failed for {project_id}: {redact(str(exc))}", flush=True)
    return messages


def command_autopilot(args: list[str], user_id: str) -> str:
    action = args[0].lower() if args else "status"
    if action in {"on", "enable", "start"}:
        project_id, rest = project_and_rest(args[1:], user_id=user_id)
        if not project_id:
            return "Usage: /autopilot on <project> [minutes]"
        interval = parse_interval_minutes(rest[0]) if rest else 5
        update_project_autopilot(project_id, True, user_id=user_id, interval_minutes=interval)
        ok, reason, criterion = autopilot_can_start(project_id)
        line = f"Autopilot enabled for {project_label(project_id, include_id=False)} every {interval} minutes."
        if criterion:
            line += f"\nNext criterion: {criterion.get('id')}. {criterion.get('text')}"
        line += f"\nStatus: {reason}."
        line += f"\nNext action: {autopilot_next_action(project_id, reason, can_start=ok)}"
        return line
    if action in {"off", "disable", "stop"}:
        project_id, _rest = project_and_rest(args[1:], user_id=user_id)
        if not project_id:
            return "Usage: /autopilot off <project>"
        update_project_autopilot(project_id, False, user_id=user_id)
        return f"Autopilot disabled for {project_label(project_id, include_id=False)}."
    if action in {"run", "tick", "now"}:
        messages = autopilot_tick_once(user_id=user_id)
        return "Autopilot tick:\n" + "\n".join(f"- {message}" for message in messages)
    lines = ["Commander autopilot:"]
    profiles = profiles_data().get("profiles", {})
    found = False
    for project_id, profile in sorted(profiles.items()):
        if not isinstance(profile, dict):
            continue
        autopilot = profile.get("autopilot")
        if not isinstance(autopilot, dict):
            continue
        found = True
        ok, reason, criterion = autopilot_can_start(project_id)
        next_text = f"{criterion.get('id')}. {criterion.get('text')}" if criterion else reason
        next_action = autopilot_next_action(project_id, reason, can_start=ok)
        lines.append(
            f"- {project_label(project_id, include_id=False)}: {'on' if autopilot.get('enabled') else 'off'}; "
            f"next: {next_text}; can start: {'yes' if ok else 'no'} ({reason}); next action: {next_action}"
        )
    if not found:
        lines.append("- No project autopilot configured.")
    return compact("\n".join(lines), limit=2400)


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
    lines = [
        "Commander X update",
        f"Time: {utc_now()}",
        "Plain-English summary for a non-technical owner. I will focus on product progress, blockers, and decisions.",
    ]
    if active:
        lines.append(f"Focused project: {project_label(str(active), include_id=False)}")
    refresh_session_states()
    sessions = sessions_data().get("sessions", {})
    lines.extend(["", "Projects Commander is tracking:"])
    if sessions:
        for project_id, session in sorted(sessions.items()):
            status = friendly_session_state(session.get("state"))
            task = summarize_task_for_human(session.get("task"))
            lines.append(f"- {project_label(project_id, include_id=False)}: {status}. {task}")
    else:
        lines.append("- No project work has been started yet.")
    lines.append("")
    if active and get_project(str(active)):
        lines.append(command_briefs([str(active)], user_id=user_id))
    else:
        lines.append(command_briefs([], user_id=user_id))
    lines.append("")
    lines.append("Technical filenames and local paths are hidden here, along with raw logs. Ask for /diff, /log, or /projects full only when you want code-level detail.")
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
    record_audit_event(project_id, action, "prepared", approval_id=pending_id)
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
    elif action_type == "openclaw_clone":
        ok_clone, result = execute_openclaw_clone(action)
        if not ok_clone:
            return result
    elif action_type == "openclaw_start":
        ok_start, result = execute_openclaw_start(action)
        if not ok_start:
            return result
    else:
        return f"Unsupported pending action type: {action_type}"

    pending.pop(pending_id, None)
    session["updated_at"] = utc_now()
    save_sessions(data)
    record_audit_event(project_id, action, "approved", approval_id=pending_id, result=result)
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
    action = pending[pending_id]
    action_type = action.get("type", "action")
    pending.pop(pending_id, None)
    session["updated_at"] = utc_now()
    save_sessions(data)
    record_audit_event(project_id, action, "cancelled", approval_id=pending_id)
    return f"Cancelled pending {action_type} for {project_id}."


ALLOWED_VERIFY_EXECUTABLES = {
    "node",
    "npm",
    "npm.cmd",
    "pnpm",
    "pnpm.cmd",
    "yarn",
    "yarn.cmd",
    "python",
    "python.exe",
    "pytest",
    "npx",
    "npx.cmd",
}


def verification_command_args(command: str) -> list[str]:
    parts = shlex.split(command, posix=True)
    if not parts:
        raise ValueError("empty verification command")
    executable = parts[0].lower()
    if executable == "npm" and os.name == "nt":
        parts[0] = shutil.which("npm.cmd") or "npm.cmd"
        executable = "npm.cmd"
    elif executable == "npx" and os.name == "nt":
        parts[0] = shutil.which("npx.cmd") or "npx.cmd"
        executable = "npx.cmd"
    elif executable == "pnpm" and os.name == "nt":
        parts[0] = shutil.which("pnpm.cmd") or "pnpm.cmd"
        executable = "pnpm.cmd"
    elif executable == "yarn" and os.name == "nt":
        parts[0] = shutil.which("yarn.cmd") or "yarn.cmd"
        executable = "yarn.cmd"
    if executable not in ALLOWED_VERIFY_EXECUTABLES:
        raise ValueError(f"verification command is not allowlisted: {parts[0]}")
    return parts


def verification_results_as_checks(results: Any) -> list[str]:
    checks: list[str] = []
    if not isinstance(results, list):
        return checks
    for item in results:
        if not isinstance(item, dict):
            continue
        command = audit_clean(item.get("command") or "check", limit=120)
        status = audit_clean(item.get("status") or "unknown", limit=40)
        checks.append(f"{command}: {status}")
    return checks


CRITERION_STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "into",
    "locally",
    "paths",
    "real",
    "support",
    "supports",
    "that",
    "the",
    "with",
    "without",
}


def criterion_completion_evidence_from_text(criterion: dict[str, str], text: str) -> str:
    summary = " ".join((text or "").split())
    if not summary:
        return ""
    lowered = summary.lower()
    if not re.search(r"\b(done|complete|completed|implemented|built|usable|supports)\b", lowered):
        return ""
    if not re.search(r"\b(verified|verification|checks?|tests?|passed|succeeded|green)\b", lowered):
        return ""

    criterion_id = str(criterion.get("id") or "").strip()
    criterion_text = str(criterion.get("text") or "")
    has_local_evidence = False
    if criterion_id:
        id_pattern = re.compile(rf"\b(?:criterion|definition-of-done criterion|dod criterion)\s*{re.escape(criterion_id)}\b", re.IGNORECASE)
        for match in id_pattern.finditer(summary):
            window = lowered[max(0, match.start() - 240) : match.end() + 520]
            if re.search(r"\b(done|complete|completed|implemented|built|usable|supports)\b", window) and re.search(
                r"\b(verified|verification|checks?|tests?|passed|succeeded|green)\b",
                window,
            ):
                has_local_evidence = True
                break

    if not has_local_evidence:
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", criterion_text.lower())
            if len(token) > 3 and token not in CRITERION_STOPWORDS
        ]
        threshold = min(6, max(3, len(set(tokens)) // 2))
        segments = [
            segment.strip().lower()
            for segment in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+|;\s+", text)
            if segment.strip()
        ]
        for segment in segments:
            if not re.search(r"\b(done|complete|completed|implemented|built|usable|supports)\b", segment):
                continue
            if not re.search(r"\b(verified|verification|checks?|tests?|passed|succeeded|green)\b", segment):
                continue
            token_hits = sum(1 for token in set(tokens) if token in segment)
            if token_hits >= threshold:
                has_local_evidence = True
                break

    if not has_local_evidence:
        return ""

    checks = verification_evidence_from_text(text, limit=4)
    if checks:
        return f"Codex final summary reported this criterion complete and verified; checks: {', '.join(checks)}."
    return "Codex final summary reported this criterion complete and verified."


def auto_update_done_criteria_from_session_summary(project_id: str, session: dict[str, Any]) -> int:
    if not isinstance(session, dict) or session.get("state") != "completed":
        return 0
    last_message = Path(str(session.get("last_message_file") or ""))
    if not last_message.exists() or not last_message.is_file():
        return 0
    try:
        summary = last_message.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    data = profiles_data()
    profiles = data.setdefault("profiles", {})
    profile = profiles.get(project_id)
    if not isinstance(profile, dict):
        return 0
    criteria = normalize_done_criteria(profile.get("done_criteria") or [])
    changed = 0
    for criterion in criteria:
        if criterion.get("status") == "done":
            continue
        evidence = criterion_completion_evidence_from_text(criterion, summary)
        if not evidence:
            continue
        criterion["status"] = "done"
        criterion["evidence"] = audit_clean(evidence, limit=360)
        changed += 1
        break
    if changed:
        profile["done_criteria"] = criteria
        save_profiles(data)
    return changed


def auto_update_done_criteria_from_verification(project_id: str, path: Path, results: list[dict[str, Any]]) -> int:
    passed_commands = {str(item.get("command") or "").lower() for item in results if item.get("status") == "passed"}
    all_passed = bool(results) and all(item.get("status") == "passed" for item in results)
    data = profiles_data()
    profiles = data.setdefault("profiles", {})
    profile = profiles.get(project_id)
    if not isinstance(profile, dict):
        return 0
    criteria = normalize_done_criteria(profile.get("done_criteria") or [])
    objective_text = str(profile.get("objective") or "").lower()
    uses_legacy_node_mvp = "health companion" not in objective_text and "diabetes companion" not in objective_text
    changed = 0

    def mark(index: int, evidence: str) -> None:
        nonlocal changed
        if criteria[index].get("status") == "done":
            return
        criteria[index]["status"] = "done"
        criteria[index]["evidence"] = audit_clean(evidence, limit=360)
        changed += 1

    for index, criterion in enumerate(criteria):
        text = str(criterion.get("text") or "").lower()
        if uses_legacy_node_mvp and any(word in text for word in ("message", "inbox", "data model", "conversation")) and (path / "src/model.js").exists():
            mark(index, "Shared conversation/message model exists.")
        elif uses_legacy_node_mvp and ("web app" in text or "dashboard" in text or "local web" in text) and (path / "src/server.js").exists() and (path / "public/index.html").exists() and any("smoke" in item for item in passed_commands):
            mark(index, "Local server and browser UI files exist; smoke verification passed.")
        elif uses_legacy_node_mvp and "telegram" in text and (path / "src/adapters/telegram.js").exists():
            mark(index, "Telegram adapter scaffold exists in the local MVP.")
        elif uses_legacy_node_mvp and "whatsapp" in text and (path / "src/adapters/whatsapp.js").exists():
            mark(index, "WhatsApp mock adapter boundary exists in the local MVP.")
        elif ("setup" in text or "docs" in text or ".env" in text) and (path / "README.md").exists() and (path / ".env.example").exists():
            mark(index, "README setup notes and .env.example exist without committing .env.")
        elif ("verification" in text or "checks" in text) and all_passed:
            mark(index, "Configured verification commands passed: " + ", ".join(item.get("command", "check") for item in results))
    if changed:
        profile["done_criteria"] = criteria
        save_profiles(data)
    return changed


def command_verify(args: list[str], user_id: str) -> str:
    project_id, _rest = project_and_rest(args, user_id=user_id)
    if not project_id:
        return "Usage: /verify <project> or set /focus <project> first"
    project = get_project(project_id)
    if not project:
        return f"Unknown or disabled project: {project_id}"
    path = project_path(project)
    if not path.exists():
        return f"Project path does not exist: {path}"
    profile = project_profile(project_id)
    commands = [str(item) for item in profile.get("verification_commands") or [] if str(item).strip()]
    if not commands:
        return f"No verification commands configured for {project_id}."

    results: list[dict[str, Any]] = []
    lines = [f"Verification: {project_id}"]
    for command in commands[:8]:
        try:
            run_args = verification_command_args(command)
        except ValueError as exc:
            result = {"command": command, "status": "blocked", "returncode": -1, "summary": str(exc), "at": utc_now()}
            results.append(result)
            lines.append(f"- {command}: blocked ({exc})")
            continue
        completed = subprocess.run(
            run_args,
            cwd=str(path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        output = compact(redact((completed.stdout or completed.stderr or "").strip()), limit=320)
        status = "passed" if completed.returncode == 0 else "failed"
        results.append(
            {
                "command": command,
                "actual": " ".join(run_args),
                "status": status,
                "returncode": completed.returncode,
                "summary": output,
                "at": utc_now(),
            }
        )
        lines.append(f"- {command}: {status}")
        if output:
            lines.append(f"  {output}")

    data = sessions_data()
    session = data.setdefault("sessions", {}).setdefault(project_id, {"project": project_id})
    session["verification_results"] = results
    session["updated_at"] = utc_now()
    session["current_phase"] = "verified" if all(item.get("status") == "passed" for item in results) else "verify_failed"
    session["state"] = "completed" if all(item.get("status") == "passed" for item in results) else "failed"
    append_timeline_event(
        session,
        "verify",
        "Verification completed" if session["state"] == "completed" else "Verification failed",
        f"{sum(1 for item in results if item.get('status') == 'passed')}/{len(results)} check(s) passed.",
        status="done" if session["state"] == "completed" else "warn",
    )
    save_sessions(data)
    marked = auto_update_done_criteria_from_verification(project_id, path, results)
    if marked:
        lines.append(f"- DoD evidence updated: {marked} criterion/criteria marked done.")
    lines.append("")
    lines.append(project_completion(project_id, user_id=user_id))
    return compact("\n".join(lines), limit=3600)


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
/service
/doctor
/inbox
/approvals
/changes [project]
/feed [project]
/briefs [project]
/report [save]
/mission [project]
/evidence [project]
/replay [project]
/playback [project]
/review [project] [save]
/objective [project]
/objective set <project> "<objective>"
/objective add <project> "<done criterion>"
/objective done <project> <criterion_number> "<evidence>"
/done [project]
/verify [project]
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
/clickup [status|recent|count] [query]
/skills [query]
/plugins
/mcp [help|request|find|add]
/openclaw [details|recover|prepare|start|doctor]
/env
/system
/clipboard [show|set|clear]
/cleanup
/open url <url>
/open app <name>
/file <project> <relative_path> [lines]
/volume up|down|max|mute [steps]
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
/audit
/report [save]
/reviews [details]
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
/autopilot status
/autopilot on <project> [minutes]
/autopilot off <project>
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
    volume_command = volume_command_from_natural_text(text)
    if volume_command:
        return volume_command

    replacements = [
        (r"^(show me the )?status$", "/status"),
        (r"^(show me )?(the )?projects$", "/projects"),
        (r"^(list|show) projects$", "/projects"),
        (r"^(show me )?(the )?(service|daemon|poller) status$", "/service"),
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
        "service",
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
        "briefs",
        "mode",
        "free",
        "tools",
        "computer",
        "browser",
        "clickup",
        "skills",
        "plugins",
        "mcp",
        "openclaw",
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
        "reviews",
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


def max_openai_image_bytes() -> int:
    raw = os.environ.get("COMMANDER_MAX_IMAGE_BYTES", "")
    if raw.isdigit():
        return max(128 * 1024, min(25 * 1024 * 1024, int(raw)))
    return DEFAULT_MAX_OPENAI_IMAGE_BYTES


def image_media_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        candidates = [photo for photo in photos if isinstance(photo, dict) and photo.get("file_id")]
        if not candidates:
            return None
        selected = sorted(
            candidates,
            key=lambda item: (
                int(item.get("file_size", 0) or 0),
                int(item.get("width", 0) or 0) * int(item.get("height", 0) or 0),
            ),
            reverse=True,
        )[0]
        return {
            "file_id": str(selected.get("file_id") or ""),
            "file_size": int(selected.get("file_size", 0) or 0),
            "mime_type": "image/jpeg",
            "suffix": ".jpg",
            "kind": "photo",
        }
    document = message.get("document")
    if isinstance(document, dict):
        mime_type = str(document.get("mime_type") or "")
        if not mime_type.startswith("image/"):
            return None
        file_name = str(document.get("file_name") or "")
        suffix = Path(file_name).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            suffix = ".jpg" if mime_type == "image/jpeg" else ".png"
        return {
            "file_id": str(document.get("file_id") or ""),
            "file_size": int(document.get("file_size", 0) or 0),
            "mime_type": mime_type,
            "suffix": suffix,
            "kind": "image document",
        }
    return None


def image_content_type(path: Path, telegram_mime_type: str | None = None) -> str:
    if telegram_mime_type and telegram_mime_type.startswith("image/"):
        return telegram_mime_type
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/jpeg"


def image_suffix_for_mime_type(mime_type: str) -> str:
    normalized = mime_type.lower().replace("image/jpg", "image/jpeg")
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(normalized, ".jpg")


def parse_image_data_url(data_url: str) -> tuple[str, bytes]:
    value = (data_url or "").strip()
    match = re.fullmatch(r"data:(image/(?:jpeg|jpg|png|webp|gif));base64,([A-Za-z0-9+/=\s\r\n]+)", value, flags=re.IGNORECASE)
    if not match:
        raise RuntimeError("Expected a base64 data URL for a JPEG, PNG, WebP, or GIF image.")
    mime_type = match.group(1).lower().replace("image/jpg", "image/jpeg")
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise RuntimeError("Unsupported image type. Use JPEG, PNG, WebP, or GIF.")
    encoded = re.sub(r"\s+", "", match.group(2))
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RuntimeError("Image data could not be decoded.") from exc
    if not raw:
        raise RuntimeError("Image data is empty.")
    if len(raw) > max_openai_image_bytes():
        raise RuntimeError(f"Image file is too large. Limit: {max_openai_image_bytes() // (1024 * 1024)} MB.")
    return mime_type, raw


def image_data_url(path: Path, telegram_mime_type: str | None = None) -> str:
    size = path.stat().st_size
    if size > max_openai_image_bytes():
        raise RuntimeError(f"Image file is too large. Limit: {max_openai_image_bytes() // (1024 * 1024)} MB.")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{image_content_type(path, telegram_mime_type)};base64,{encoded}"


def sanitize_image_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in ("summary", "visible_text", "likely_intent", "risk"):
        sanitized[key] = safe_brief_text(compact(str(payload.get(key) or "-"), limit=900))
    commands = payload.get("suggested_commands")
    if not isinstance(commands, list):
        commands = []
    safe_commands: list[str] = []
    for command in commands[:4]:
        value = str(command or "").strip()
        if not value.startswith("/"):
            continue
        try:
            safe_commands.append(validate_generated_command(value))
        except Exception:
            continue
    sanitized["suggested_commands"] = safe_commands
    return sanitized


def save_user_image_context(user_id: str, kind: str, analysis: dict[str, Any], caption: str = "") -> dict[str, Any]:
    sanitized = sanitize_image_analysis(analysis)
    record = {
        "at": utc_now(),
        "kind": safe_brief_text(kind or "image"),
        "summary": sanitized.get("summary"),
        "visible_text": sanitized.get("visible_text"),
        "likely_intent": sanitized.get("likely_intent"),
        "risk": sanitized.get("risk"),
        "suggested_commands": sanitized.get("suggested_commands", []),
    }
    if caption.strip():
        record["caption"] = safe_brief_text(caption)
    update_user_state(user_id, {"last_image": record})
    return record


def openai_image_analysis(path: Path, caption: str = "", telegram_mime_type: str | None = None) -> dict[str, Any]:
    cfg = openai_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing in .env.")
    data_url = image_data_url(path, telegram_mime_type)
    prompt = cfg["image_prompt"]
    if caption.strip():
        prompt += f"\n\nTelegram caption from user: {caption.strip()}"
    payload = {
        "model": cfg["image_model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return JSON only. You are a safe visual assistant for a Telegram-controlled local agent. "
                    "Do not return raw shell commands. Suggested commands must be existing Commander slash commands only. "
                    "If sensitive values are visible, say sensitive information may be visible but do not transcribe it."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.0,
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
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI image analysis failed: HTTP {exc.code}: {redact(error_body)}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return sanitize_image_analysis(extract_json_object(str(content)))


def format_image_analysis(analysis: dict[str, Any], caption: str = "") -> str:
    lines = ["Image received", "Commander analyzed it safely. I did not run an action from the image alone.", ""]
    if caption.strip():
        lines.append(f"Caption: {safe_brief_text(caption)}")
    lines.extend(
        [
            f"Summary: {analysis.get('summary') or '-'}",
            f"Visible text: {analysis.get('visible_text') or '-'}",
            f"Likely intent: {analysis.get('likely_intent') or '-'}",
            f"Risk: {analysis.get('risk') or '-'}",
        ]
    )
    commands = analysis.get("suggested_commands") if isinstance(analysis.get("suggested_commands"), list) else []
    if commands:
        lines.extend(["", "Suggested Commander actions:"])
        lines.extend(f"- {command}" for command in commands[:4])
    lines.extend(["", "Send a text or voice instruction if you want me to act on this image."])
    return compact("\n".join(lines), limit=3600)


def last_image_context_summary(user_id: str) -> str:
    state = user_state(user_id)
    image = state.get("last_image") if isinstance(state.get("last_image"), dict) else {}
    if not image:
        return "No recent image."
    lines = [
        f"At: {image.get('at') or '-'}",
        f"Summary: {image.get('summary') or '-'}",
        f"Visible text: {image.get('visible_text') or '-'}",
        f"Likely intent: {image.get('likely_intent') or '-'}",
        f"Risk: {image.get('risk') or '-'}",
    ]
    return compact("\n".join(lines), limit=1200)


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
    result = re.sub(r"\b(the|this|that)\s*[.,!?;:]*\s*$", " ", result, flags=re.IGNORECASE)
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


def single_project_start_response(text: str, user_id: str, chat_id: int | str | None, execute: bool = True) -> list[str] | None:
    if not looks_like_start_request(text):
        return None
    projects = mentioned_projects(text)
    if len(projects) > 1:
        return None
    project_id = projects[0] if projects else None
    if not project_id and allows_active_project_fallback(user_id):
        project_id = resolve_project_id(None, user_id=user_id)
    if not project_id or not get_project(project_id):
        return None
    task = task_without_project_names(text)
    if not execute:
        return [f"Would start {project_id} with task: {task}"]
    update_user_state(
        user_id,
        {
            "active_project": project_id,
            "active_project_set_at": utc_now(),
            **({"last_chat_id": chat_id} if chat_id is not None else {}),
        },
    )
    return [start_codex(project_id, task, user_id=user_id)]


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


def project_info_response(text: str, user_id: str, execute: bool = True) -> list[str] | None:
    projects = mentioned_projects(text)
    if len(projects) != 1 or looks_like_start_request(text):
        return None
    if not re.search(
        r"\b(about|know|knows|tell|explain|info|information|context|brief|status|update|updates|what is|what's|whats)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return None
    project_id = projects[0]
    if not execute:
        return [f"Would run: /updates {project_id}"]
    return [command_updates(project_id, user_id=user_id, query=text)]


def volume_command_from_natural_text(text: str) -> str | None:
    lowered = text.lower()
    if not re.search(r"\b(volume|sound|audio)\b", lowered):
        return None
    if re.search(r"\b(mute|silence|silent|off)\b", lowered):
        return "/volume mute"
    if re.search(r"\b(max|maximize|maximum|full|100|hundred|crank)\b", lowered):
        return "/volume max"
    action = ""
    if re.search(r"\b(lower|decrease|reduce|turn down|down|quieter)\b", lowered):
        action = "down"
    elif re.search(r"\b(raise|increase|turn up|up|louder)\b", lowered):
        action = "up"
    if not action:
        return None
    parsed = parse_volume_command([action, *parse_message(text)])
    if not parsed:
        return f"/volume {action} 5"
    _parsed_action, steps = parsed
    if not re.search(r"\d|max|maximum|full|100|hundred|crank", lowered):
        steps = 5
    return f"/volume {action} {steps}"


def natural_computer_command(text: str) -> str | None:
    lowered = text.lower()
    url_match = re.search(r"\b((?:https?://)?(?:www\.)?[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?:/[^\s\"']*)?)", text)
    volume_command = volume_command_from_natural_text(text)
    if volume_command:
        return volume_command
    if re.search(r"\b(mcp|mcps)\b", lowered) and re.search(r"\b(connect|install|add|setup|set up|request|wire|enable|find|search|research)\b", lowered):
        return f"/mcp request {text}"
    if re.search(r"\b(mcp|mcps)\b", lowered) and re.search(r"\b(show|list|what|available|have|status|help|how)\b", lowered):
        return "/mcp"
    if re.search(r"\b(openclaw|open claw|claw)\b", lowered) and re.search(r"\b(start|launch|turn on|run)\b", lowered):
        return "/openclaw start"
    if re.search(r"\b(openclaw|open claw|claw)\b", lowered) and re.search(r"\b(install|setup|set up|reinstall|recover|repair|fix|download)\b", lowered):
        return "/openclaw recover"
    if re.search(r"\b(openclaw|open claw|claw)\b", lowered) and re.search(r"\b(prepare|clone)\b", lowered):
        url_match = re.search(r"https?://\S+", text)
        return f"/openclaw prepare {url_match.group(0).rstrip('.,)')}" if url_match else "/openclaw recover"
    if re.search(r"\b(openclaw|open claw|claw)\b", lowered) and re.search(r"\b(status|where|find|check|doctor|installed|running|available)\b", lowered):
        return "/openclaw details" if re.search(r"\b(where|find|installed|available)\b", lowered) else "/openclaw"
    if re.search(r"\b(skills?)\b", lowered) and re.search(r"\b(show|list|what|available|have)\b", lowered):
        return "/skills"
    if re.search(r"\b(plugins?)\b", lowered) and re.search(r"\b(show|list|what|available|have)\b", lowered):
        return "/plugins"
    if re.search(r"\b(doctor|health check|diagnose|diagnostic|self[- ]?test)\b", lowered):
        return "/doctor"
    if re.search(r"\b(service|daemon|poller|dashboard)\b", lowered) and re.search(r"\b(status|health|running|check|alive|up)\b", lowered):
        return "/service"
    if re.search(r"\b(capabilities|new capabilities|what can you do|what are your tools|available tools|features|abilities)\b", lowered):
        return "/tools"
    if re.search(r"\b(audit|approval history|approved history|what was approved|what got cancelled|decision history)\b", lowered):
        return "/audit"
    if re.search(r"\b(operator report|commander report|status report|export report|report snapshot|briefing pack)\b", lowered):
        return "/report"
    if re.search(r"\b(approvals?|approve list|pending approvals?|decisions? to approve|approve or cancel)\b", lowered):
        return "/approvals"
    if re.search(r"\b(tell|notify|let me know|message|ping)\b", lowered) and re.search(
        r"\b(done|finished|complete|completed|completion)\b",
        lowered,
    ):
        return "/heartbeat on"
    if re.search(r"\b(inbox|what needs my attention|needs attention|pending items|what needs me|decision inbox)\b", lowered):
        return "/inbox"
    if re.search(r"\b(executive brief|executive update|session briefs?|codex briefs?|plain english summary|non[- ]technical update|what is codex doing right now|what are my codex sessions doing)\b", lowered):
        projects = mentioned_projects(text)
        return f"/briefs {projects[0]}" if projects else "/briefs"
    if re.search(r"\b(mission control|mission timeline|timeline view|control room|what is the direction|where are we)\b", lowered):
        projects = mentioned_projects(text)
        return f"/mission {projects[0]}" if projects else "/mission"
    if re.search(r"\b(session replay|run replay|replay cards?|what happened in this run|what happened during|reconstruct(?:ed)? run|run story|session story|codex story)\b", lowered):
        projects = mentioned_projects(text)
        return f"/replay {projects[0]}" if projects else "/replay"
    if re.search(r"\b(operator playback|playback view|project playback|assistant playback|what do i need to know|what should i do about this project|brief me on this project|one view|single view)\b", lowered):
        projects = mentioned_projects(text)
        return f"/playback {projects[0]}" if projects else "/playback"
    if re.search(r"\b(saved owner reviews?|saved review packs?|previous review packs?|review history|saved project reports?)\b", lowered):
        return "/reviews"
    if re.search(r"\b(owner review|review pack|handoff pack|sign[- ]?off|ready for review|review this project|what should i review)\b", lowered):
        projects = mentioned_projects(text)
        save_suffix = " save" if re.search(r"\b(save|export|report|download|write)\b", lowered) else ""
        return f"/review {projects[0]}{save_suffix}" if projects else f"/review{save_suffix}"
    if re.search(r"\b(100% done|one hundred percent done|is .* done|done yet|completion check|definition of done|objective|intended objective|done criteria|completion proof)\b", lowered):
        projects = mentioned_projects(text)
        if re.search(r"\b(set|define|add|mark)\b", lowered) and re.search(r"\b(objective|criterion|criteria|definition of done)\b", lowered):
            return None
        return f"/done {projects[0]}" if projects else "/done"
    if re.search(r"\b(evidence card|session evidence|proof of work|what was verified|checks run|show evidence)\b", lowered):
        projects = mentioned_projects(text)
        return f"/evidence {projects[0]}" if projects else "/evidence"
    if re.search(r"\b(work feed|live feed|codex feed|project feed|all project progress|all codex progress|what is codex doing across)\b", lowered):
        projects = mentioned_projects(text)
        return f"/feed {projects[0]}" if projects else "/feed"
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
    if re.search(r"\b(how many|count|number of|total)\b", lowered) and re.search(r"\b(leads?|prospects?|campaigns?|deals?|opportunities)\b", lowered):
        terms = re.sub(r"\b(how|many|count|number|of|total|do|we|have|are|there|the|my|our|running|active|current)\b", " ", lowered)
        query = " ".join(terms.split()) or "leads"
        return f"/clickup count {query}".strip()
    if re.search(r"\b(latest|recent|updates?|status|what.*happening|progress)\b", lowered) and re.search(r"\b(campaigns?|leads?|prospects?|deals?|opportunities)\b", lowered):
        terms = re.sub(r"\b(latest|recent|updates?|status|what|is|are|happening|progress|about|for|the|my|our|running|active|current)\b", " ", lowered)
        query = " ".join(terms.split()) or "campaigns"
        return f"/clickup recent {query}".strip()
    if url_match and re.search(r"\b(open|visit|go to|browse|launch|pull up)\b", lowered):
        return f"/open url {url_match.group(1).rstrip('.,)')}"
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

    project_info = project_info_response(text, user_id=user_id, execute=execute_commands)
    if project_info:
        return project_info

    if looks_like_brief_request(text) and not looks_like_start_request(text):
        if not execute_commands:
            resolved = project_from_assistant_query(None, user_id=user_id, query=text)
            return [f"Would run: /updates {resolved}" if resolved else "Would run: /updates"]
        return [command_updates(None, user_id=user_id, query=text)]

    multi = multi_project_start_response(text, user_id=user_id, chat_id=chat_id, execute=execute_commands)
    if multi:
        return multi

    single = single_project_start_response(text, user_id=user_id, chat_id=chat_id, execute=execute_commands)
    if single:
        return single

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
/service
/doctor
/inbox
/approvals
/changes [project]
/feed [project]
/briefs [project]
/report [save]
/mission [project]
/evidence [project]
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
/clickup [status|recent|count] [query]
/skills [query]
/plugins
/mcp [help|request|find|add]
/openclaw [details|recover|prepare|start|doctor]
/env
/system
/clipboard [show|set|clear]
/cleanup
/open url <url>
/open app <allowlisted_app>
/file <project> <relative_path> [lines]
/volume up|down|max|mute [steps]
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
/audit
/report [save]
/reviews [details]
/mission [project]
/replay [project]
/playback [project]
/objective [project]
/done [project]
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
- If the user says "this image", "the screenshot", "what I sent", or "make it work" after an image, use the Recent Telegram image context to understand intent, but still map only to safe slash commands.
- If the user names a project that is not in Registered projects, return a reply saying it is not registered instead of using the active project.
- If the user asks to work, fix, security-audit, or build inside a registered project, map to /start.
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
- If the user asks where OpenClaw is or whether OpenClaw is installed/running, map to /openclaw details.
- If the user asks to start, launch, run, or turn on OpenClaw, map to /openclaw start.
- If the user asks to install, recover, repair, or set up OpenClaw, map to /openclaw recover unless they provide a GitHub URL and explicitly ask to prepare/clone it.
- If the user specifically asks for skills, map to /skills.
- If the user specifically asks for plugins, map to /plugins.
- If the user asks whether Commander, the poller, service, daemon, or dashboard is running, map to /service.
- If the user asks for a health check, doctor, diagnostic, or self-test, map to /doctor.
- If the user asks what needs their attention, decisions, inbox, or pending items, map to /inbox.
- If the user asks for approvals, pending approvals, approve list, or decisions to approve/cancel, map to /approvals.
- If the user asks for approval history, audit trail, what was approved, or what was cancelled, map to /audit.
- If the user asks for an operator report, Commander report, exportable status report, report snapshot, or briefing pack, map to /report.
- If the user asks for saved owner review packs, previous review packs, review history, or saved project reports, map to /reviews.
- If the user asks for changed projects, dirty worktrees, local changes, or changes across projects, map to /changes.
- If the user asks for a work feed, live feed, or all project/Codex progress, map to /feed.
- If the user asks for an executive brief, plain-English session update, non-technical update, or what Codex is doing right now, map to /briefs.
- If the user asks for mission control, mission timeline, a control-room view, direction, or "where are we", map to /mission.
- If the user asks for evidence cards, proof of work, checks run, or what was verified, map to /evidence.
- If the user asks for a session replay, run story, or what happened during a Codex run, map to /replay.
- If the user asks for operator playback, a one-view project briefing, what they need to know, or what to do next for a project, map to /playback.
- If the user asks whether a project is done, 100% done, or fulfilled its objective, map to /done.
- If the user asks to set an intended objective or add Definition-of-Done criteria, map to /objective set or /objective add.
- If the user asks to watch progress, see the live view, or understand what Codex is doing, map to /watch.
- If the user asks what keys/env setup is missing, map to /env.
- If the user asks device, battery, disk, memory, or system status, map to /system.
- If the user asks to peek at clipboard, map to /clipboard show. Do not set clipboard unless explicitly requested.
- If the user asks about cleanup, storage, or freeing disk space, map to /cleanup. Do not delete files.
- If the user asks to open or visit a website, map to /open url <url>.
- If the user asks to inspect, check, or summarize a website, map to /browser inspect <url>.
- If the user asks to check ClickUp, map to /clickup recent with query terms if present.
- If the user asks how many leads, prospects, deals, opportunities, or campaigns exist, map to /clickup count <query>.
- If the user asks for latest campaign or lead updates, map to /clickup recent <query>.
- If the user asks to open an app, map to /open app <allowlisted_app>.
- If the user asks to lower, raise, maximize, set to 100, or mute system volume, map to /volume with the requested direction/steps.
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

Recent Telegram image context:
{last_image_context_summary(user_id)}
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
    aliases = project_alias_map()
    for span in range(min(3, len(args)), 0, -1):
        candidate = " ".join(args[:span])
        normalized = normalized_project_text(candidate)
        resolved = aliases.get(candidate.strip().lower()) or aliases.get(normalized) or aliases.get(re.sub(r"\s+", "-", normalized))
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
    if command == "/service":
        return [command_service()]
    if command == "/doctor":
        return [command_doctor(user_id=user_id)]
    if command == "/inbox":
        return [command_inbox(user_id=user_id)]
    if command == "/approvals":
        return [command_approvals()]
    if command == "/audit":
        return [command_audit()]
    if command == "/report":
        return [command_report(args, user_id=user_id)]
    if command == "/reviews":
        return [command_reviews(args)]
    if command == "/changes":
        return [command_changes(args, user_id=user_id)]
    if command == "/feed":
        return [command_feed(args, user_id=user_id)]
    if command == "/briefs":
        return [command_briefs(args, user_id=user_id)]
    if command == "/mission":
        return [command_mission(args, user_id=user_id)]
    if command == "/evidence":
        return [command_evidence(args, user_id=user_id)]
    if command == "/replay":
        return [command_replay(args, user_id=user_id)]
    if command == "/playback":
        return [command_playback(args, user_id=user_id)]
    if command == "/review":
        return [command_review(args, user_id=user_id)]
    if command == "/objective":
        return [command_objective(args, user_id=user_id)]
    if command == "/done":
        return [command_done(args, user_id=user_id)]
    if command == "/verify":
        return [command_verify(args, user_id=user_id)]
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
    if command == "/openclaw":
        return [command_openclaw(args)]
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
    if command == "/autopilot":
        return [command_autopilot(args, user_id=user_id)]
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


def handle_image_message(
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

    media = image_media_from_message(message)
    if not media:
        return []
    file_id = str(media.get("file_id") or "")
    if not file_id:
        return ["Telegram image did not include a file_id."]
    file_size = int(media.get("file_size", 0) or 0)
    if file_size and file_size > max_openai_image_bytes():
        return [f"Image is too large for analysis. Keep it under {max_openai_image_bytes() // (1024 * 1024)} MB."]

    caption = str(message.get("caption") or "").strip()
    local_path = bot.download_file(file_id, IMAGE_DIR, preferred_suffix=str(media.get("suffix") or ".jpg"))
    analysis = openai_image_analysis(local_path, caption=caption, telegram_mime_type=str(media.get("mime_type") or ""))
    record = save_user_image_context(user_id, str(media.get("kind") or "image"), analysis, caption=caption)
    if chat_id is not None:
        update_user_state(user_id, {"last_image": record, "last_chat_id": chat_id})
    return [format_image_analysis(analysis, caption=caption)]


def handle_unsupported_media_message(message: dict[str, Any]) -> list[str]:
    if message.get("photo"):
        return ["I received image(s), but could not analyze them. Describe what you want me to do, or send a text/voice command."]
    if message.get("document"):
        document = message.get("document") or {}
        mime_type = str(document.get("mime_type", ""))
        if mime_type.startswith("image/"):
            return ["I received an image file, but could not analyze it. Describe what you want me to do, or send a text/voice command."]
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
            autopilot_tick_once(user_id="autopilot")
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
                if message.get("photo") or (isinstance(message.get("document"), dict) and str(message.get("document", {}).get("mime_type", "")).startswith("image/")):
                    print(f"{utc_now()} {user_id} [image message]", flush=True)
                    try:
                        responses = handle_image_message(bot, message, user_id=user_id, user_name=user_name, chat_id=chat_id)
                    except Exception as exc:
                        responses = [f"Image analysis failed: {redact(str(exc))}"]
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
