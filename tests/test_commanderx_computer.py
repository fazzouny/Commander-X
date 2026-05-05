from __future__ import annotations

import json
import unittest
import tempfile
import subprocess
from pathlib import Path

import commander
from commanderx.browser import PageSummaryParser, format_inspection, BrowserInspection
from commanderx.clickup_api import filter_tasks, format_tasks, settings_from_env
from commanderx.cleanup import bytes_to_mb, format_cleanup_scan
from commanderx import computer as computer_tools
from commanderx.computer import app_catalog, normalize_url, resolve_web_shortcut, web_shortcut_catalog


class ComputerToolTests(unittest.TestCase):
    def test_normalize_url_adds_https(self) -> None:
        self.assertEqual(normalize_url("example.com"), "https://example.com")
        self.assertEqual(normalize_url("https://example.com"), "https://example.com")
        self.assertEqual(resolve_web_shortcut("Gmail"), "https://mail.google.com")
        self.assertEqual(normalize_url("gmail"), "https://mail.google.com")

    def test_web_shortcut_catalog_merges_safe_custom_shortcuts(self) -> None:
        shortcuts = web_shortcut_catalog(
            {
                "web_shortcuts": {
                    "Company CRM": "https://crm.example.com",
                    "bad": "file:///C:/secret",
                    "empty": "",
                }
            }
        )

        self.assertEqual(shortcuts["company crm"], "https://crm.example.com")
        self.assertNotIn("bad", shortcuts)
        self.assertEqual(normalize_url("company crm", {"web_shortcuts": {"Company CRM": "https://crm.example.com"}}), "https://crm.example.com")

    def test_app_catalog_merges_custom_apps(self) -> None:
        apps = app_catalog({"apps": {"chrome": ["chrome.exe"], "solo": "solo.exe"}})
        self.assertIn("notepad", apps)
        self.assertEqual(apps["chrome"], ["chrome.exe"])
        self.assertEqual(apps["solo"], ["solo.exe"])

    def test_wmic_process_lines_parses_matching_rows(self) -> None:
        original_run = computer_tools.run_command
        try:
            computer_tools.run_command = lambda args, timeout=8: subprocess.CompletedProcess(  # type: ignore[assignment]
                args,
                0,
                "Node,CommandLine,Name,ProcessId\nHOST,\"python commander.py --poll\",python.exe,123\nHOST,\"node server.js\",node.exe,456\n",
                "",
            )

            lines = computer_tools.wmic_process_lines([], ["commander.py --poll"])
        finally:
            computer_tools.run_command = original_run  # type: ignore[assignment]

        self.assertEqual(lines, ["123 python.exe python commander.py --poll"])

    def test_wmic_process_lines_falls_back_when_unavailable(self) -> None:
        original_run = computer_tools.run_command
        try:
            computer_tools.run_command = lambda args, timeout=8: (_ for _ in ()).throw(FileNotFoundError())  # type: ignore[assignment]

            lines = computer_tools.wmic_process_lines([], ["commander.py --poll"])
        finally:
            computer_tools.run_command = original_run  # type: ignore[assignment]

        self.assertIsNone(lines)

    def test_image_media_from_photo_selects_largest_photo(self) -> None:
        message = {
            "photo": [
                {"file_id": "small", "file_size": 100, "width": 90, "height": 90},
                {"file_id": "large", "file_size": 2000, "width": 900, "height": 900},
            ]
        }

        media = commander.image_media_from_message(message)

        self.assertIsNotNone(media)
        self.assertEqual(media["file_id"], "large")
        self.assertEqual(media["mime_type"], "image/jpeg")

    def test_image_media_from_image_document(self) -> None:
        media = commander.image_media_from_message(
            {
                "document": {
                    "file_id": "doc1",
                    "file_size": 123,
                    "mime_type": "image/png",
                    "file_name": "screenshot.png",
                }
            }
        )

        self.assertIsNotNone(media)
        self.assertEqual(media["suffix"], ".png")
        self.assertEqual(media["kind"], "image document")

    def test_parse_image_data_url_accepts_safe_image_payload(self) -> None:
        mime_type, raw = commander.parse_image_data_url("data:image/png;base64,aGVsbG8=")

        self.assertEqual(mime_type, "image/png")
        self.assertEqual(raw, b"hello")

    def test_parse_image_data_url_blocks_non_image_payloads(self) -> None:
        with self.assertRaises(RuntimeError):
            commander.parse_image_data_url("data:text/plain;base64,aGVsbG8=")

    def test_image_analysis_format_sanitizes_commands_and_context(self) -> None:
        payload = commander.sanitize_image_analysis(
            {
                "summary": "Login page has an error",
                "visible_text": "api_key: sk-test-placeholder C:\\Users\\Name\\repo\\.env",
                "likely_intent": "debug screenshot",
                "risk": "medium",
                "suggested_commands": ["/status", "/run rm -rf /"],
            }
        )
        text = commander.format_image_analysis(payload, caption="fix this screenshot")

        self.assertIn("Login page has an error", text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("technical path", text)
        self.assertNotIn("C:\\Users", text)
        self.assertIn("/status", text)
        self.assertNotIn("/run", text)

    def test_natural_computer_command_routes_common_actions(self) -> None:
        self.assertEqual(commander.natural_computer_command("visit example.com"), "/open url example.com")
        self.assertEqual(commander.natural_computer_command("Open Gmail"), "/open url gmail")
        self.assertEqual(commander.natural_computer_command("pull up google calendar"), "/open url google calendar")
        self.assertEqual(commander.natural_computer_command("show web shortcuts"), "/shortcut")
        self.assertEqual(
            commander.natural_computer_command("add shortcut company crm https://crm.example.com"),
            "/shortcut add company crm https://crm.example.com",
        )
        self.assertEqual(commander.natural_computer_command("inspect website example.com"), "/browser inspect example.com")
        self.assertEqual(commander.natural_computer_command("check clickup campaigns"), "/clickup recent campaigns")
        self.assertEqual(commander.natural_computer_command("How many leads we have"), "/clickup count leads")
        self.assertEqual(commander.natural_computer_command("latest updates about campaigns"), "/clickup recent campaigns")
        self.assertEqual(commander.natural_computer_command("what MCPs are available"), "/mcp")
        self.assertEqual(
            commander.natural_computer_command("Can you connect this mcp https://example.com/mcp"),
            "/mcp request Can you connect this mcp https://example.com/mcp",
        )
        self.assertEqual(commander.natural_computer_command("show available skills"), "/skills")
        self.assertEqual(commander.natural_computer_command("run commander doctor"), "/doctor")
        self.assertEqual(commander.natural_computer_command("What's your new capabilities?"), "/tools")
        self.assertEqual(commander.natural_computer_command("recover OpenClaw"), "/openclaw recover")
        self.assertEqual(
            commander.natural_computer_command("prepare OpenClaw https://github.com/openclaw/openclaw"),
            "/openclaw prepare https://github.com/openclaw/openclaw",
        )
        self.assertEqual(commander.natural_computer_command("start OpenClaw"), "/openclaw start")
        self.assertEqual(commander.natural_computer_command("what needs my attention"), "/inbox")
        self.assertEqual(commander.natural_computer_command("show pending approvals"), "/approvals")
        self.assertEqual(commander.natural_computer_command("You will tell me when it's done?"), "/heartbeat on")
        self.assertEqual(commander.natural_computer_command("show approval history"), "/audit")
        self.assertEqual(commander.natural_computer_command("make me an operator report"), "/report")
        self.assertEqual(commander.natural_computer_command("show mission control"), "/mission")
        self.assertEqual(commander.natural_computer_command("show evidence cards"), "/evidence")
        self.assertEqual(commander.natural_computer_command("show me the session replay"), "/replay")
        self.assertEqual(commander.natural_computer_command("what do I need to know about this project"), "/playback")
        self.assertEqual(commander.natural_computer_command("give me the owner review pack"), "/review")
        self.assertEqual(commander.natural_computer_command("save the owner review pack"), "/review save")
        self.assertEqual(commander.natural_computer_command("show saved review packs"), "/reviews")
        self.assertEqual(commander.natural_computer_command("is this project 100% done?"), "/done")
        self.assertEqual(commander.natural_computer_command("what changed across projects"), "/changes")
        self.assertEqual(commander.natural_computer_command("give me a plain English Codex brief"), "/briefs")
        self.assertEqual(commander.natural_computer_command("show all codex progress"), "/feed")
        self.assertEqual(commander.natural_computer_command("watch codex progress"), "/watch")
        self.assertEqual(commander.natural_computer_command("show the run timeline"), "/timeline")
        self.assertEqual(commander.natural_computer_command("show the work plan"), "/plan")
        self.assertEqual(commander.natural_computer_command("check missing env keys"), "/setup")
        self.assertEqual(commander.natural_computer_command("what do I need to configure"), "/setup")
        self.assertEqual(commander.natural_computer_command("show system status"), "/system")
        self.assertEqual(commander.natural_computer_command("show clipboard"), "/clipboard show")
        self.assertEqual(commander.natural_computer_command("clean duplicate queue items"), "/queue cleanup")
        self.assertEqual(commander.natural_computer_command("show me a cleanup plan"), "/cleanup")
        self.assertEqual(commander.natural_computer_command("give me my morning brief"), "/morning")
        self.assertEqual(commander.natural_computer_command("what should I do next"), "/next")
        self.assertEqual(commander.natural_computer_command("lower the volume"), "/volume down 5")
        self.assertEqual(commander.natural_computer_command("Volume up 20x"), "/volume up 20")
        self.assertEqual(commander.natural_computer_command("Volume to the Max"), "/volume max")
        self.assertEqual(commander.natural_computer_command("take a screenshot"), "/computer screenshot")
        self.assertEqual(commander.natural_computer_command("check codex"), "/computer codex")

    def test_open_command_routes_web_shortcuts_without_app_allowlist(self) -> None:
        original_open_url = commander.computer_open_url
        calls: list[str] = []
        try:
            commander.computer_open_url = lambda url, config=None: calls.append(url) or (True, f"Opened URL: {normalize_url(url, config)}")  # type: ignore[assignment]
            rendered = commander.command_open(["gmail"])
        finally:
            commander.computer_open_url = original_open_url  # type: ignore[assignment]

        self.assertEqual(calls, ["gmail"])
        self.assertIn("https://mail.google.com", rendered)

    def test_open_command_uses_configured_web_shortcuts(self) -> None:
        original_open_url = commander.computer_open_url
        original_config = commander.computer_tools_config
        calls: list[str] = []
        try:
            commander.computer_tools_config = lambda: {"web_shortcuts": {"company crm": "https://crm.example.com"}}  # type: ignore[assignment]
            commander.computer_open_url = lambda url, config=None: calls.append(url) or (True, f"Opened URL: {normalize_url(url, config)}")  # type: ignore[assignment]
            rendered = commander.command_open(["company", "crm"])
        finally:
            commander.computer_open_url = original_open_url  # type: ignore[assignment]
            commander.computer_tools_config = original_config  # type: ignore[assignment]

        self.assertEqual(calls, ["company crm"])
        self.assertIn("https://crm.example.com", rendered)

    def test_shortcut_command_manages_custom_shortcuts_safely(self) -> None:
        original_file = commander.COMPUTER_TOOLS_FILE
        original_audit = commander.record_audit_event
        audit_events: list[tuple[str, str]] = []
        try:
            with tempfile.TemporaryDirectory() as temp:
                commander.COMPUTER_TOOLS_FILE = Path(temp) / "computer_tools.json"
                commander.record_audit_event = (  # type: ignore[assignment]
                    lambda project, action, status, approval_id=None, result=None: audit_events.append((project, status)) or {}
                )

                saved = commander.command_shortcut(["add", "company", "crm", "https://crm.example.com/home?token=abc123"])
                listing = commander.command_shortcut([])
                unsafe = commander.command_shortcut(["add", "bad", "file:///C:/secret"])
                removed = commander.command_shortcut(["delete", "company", "crm"])
        finally:
            commander.COMPUTER_TOOLS_FILE = original_file
            commander.record_audit_event = original_audit  # type: ignore[assignment]

        self.assertIn("Saved web shortcut: company crm", saved)
        self.assertNotIn("token=abc123", saved)
        self.assertIn("company crm (custom)", listing)
        self.assertIn("https://crm.example.com/home", listing)
        self.assertNotIn("token=abc123", listing)
        self.assertIn("must start with http:// or https://", unsafe)
        self.assertIn("Removed custom web shortcut: company crm", removed)
        self.assertEqual(audit_events, [("commander", "completed"), ("commander", "completed")])

    def test_single_project_start_response_starts_resolved_project(self) -> None:
        original_projects_config = commander.projects_config
        original_get_project = commander.get_project
        original_mentioned_projects = commander.mentioned_projects
        original_start_codex = commander.start_codex
        original_update_user_state = commander.update_user_state
        try:
            commander.projects_config = lambda: {
                "projects": {"comx-omnichannel-test": {"allowed": True, "aliases": ["health assistant"]}}
            }
            commander.get_project = lambda project_id: {"allowed": True} if project_id == "comx-omnichannel-test" else None
            commander.mentioned_projects = lambda _text: ["comx-omnichannel-test"]
            commander.update_user_state = lambda *_args, **_kwargs: {}
            calls = []

            def fake_start(project_id, task, **_kwargs):
                calls.append((project_id, task))
                return "started"

            commander.start_codex = fake_start

            self.assertEqual(
                commander.single_project_start_response("Continue building the health assistant.", "u1", "c1"),
                ["started"],
            )
            self.assertEqual(calls, [("comx-omnichannel-test", "Continue building")])
        finally:
            commander.projects_config = original_projects_config
            commander.get_project = original_get_project
            commander.mentioned_projects = original_mentioned_projects
            commander.start_codex = original_start_codex
            commander.update_user_state = original_update_user_state

    def test_project_and_rest_does_not_consume_task_after_alias(self) -> None:
        original_projects_config = commander.projects_config
        original_user_state = commander.user_state
        try:
            commander.projects_config = lambda: {
                "projects": {
                    "comx-omnichannel-test": {
                        "allowed": True,
                        "aliases": ["health companion"],
                    }
                }
            }
            commander.user_state = lambda _user_id: {"assistant_mode": "focused"}

            self.assertEqual(
                commander.project_and_rest(["health", "companion", "Build", "checkpoint", "1"], user_id="u1"),
                ("comx-omnichannel-test", ["Build", "checkpoint", "1"]),
            )
        finally:
            commander.projects_config = original_projects_config
            commander.user_state = original_user_state

    def test_secret_files_are_blocked(self) -> None:
        self.assertTrue(commander.is_sensitive_relative_path(commander.Path(".env")))
        self.assertTrue(commander.is_sensitive_relative_path(commander.Path("config/private.key")))
        self.assertFalse(commander.is_sensitive_relative_path(commander.Path("README.md")))

    def test_volume_parser_handles_voice_style_requests(self) -> None:
        self.assertEqual(commander.parse_volume_command(["up", "20x"]), ("up", 20))
        self.assertEqual(commander.parse_volume_command(["down", "to", "50"]), ("down", 25))
        self.assertEqual(commander.parse_volume_command(["to", "the", "max"]), ("up", 25))
        self.assertEqual(commander.parse_volume_command(["mute", "sound"]), ("mute", 1))
        self.assertEqual(commander.normalize_voice_command("Volume up 20x."), "/volume up 20")
        self.assertEqual(commander.normalize_voice_command("Volume to the Max."), "/volume max")

    def test_openclaw_snapshot_detects_traces_and_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            launcher = home / "tools" / "run-claw.cmd"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("@echo off\n", encoding="utf-8")
            skills = home / ".openclaw" / "skills" / "frontend-design"
            skills.mkdir(parents=True)
            plugins = home / ".claw" / "plugins"
            plugins.mkdir(parents=True)
            (plugins / "installed.json").write_text(
                '{"plugins":{"sample":{"name":"sample","source":{"path":"' + str(launcher).replace("\\", "\\\\") + '"}}}}',
                encoding="utf-8",
            )
            snapshot = commander.openclaw_status_snapshot(
                home=home,
                env={"APPDATA": str(home / "AppData" / "Roaming"), "COMMANDER_OPENCLAW_LAUNCHER": str(launcher)},
                process_rows=[
                    "123 openclaw.exe openclaw gateway status",
                    "456 python.exe commander.py --local /openclaw",
                ],
            )
        self.assertEqual(snapshot["skills_count"], 1)
        self.assertTrue(snapshot["available_launchers"])
        self.assertEqual(snapshot["plugin_sources"][0]["source_exists"], "yes")
        self.assertEqual(commander.summarize_process_rows(snapshot["process_rows"]), ["123 openclaw.exe"])
        self.assertFalse(commander.is_openclaw_process_row("456 python.exe commander.py --local /openclaw"))
        self.assertFalse(commander.is_openclaw_process_row("789 powershell.exe C:\\Repos\\codex-commander /openclaw"))

    def test_openclaw_process_timeout_is_bounded(self) -> None:
        self.assertEqual(commander.openclaw_process_timeout({"COMMANDER_OPENCLAW_PROCESS_TIMEOUT_SECONDS": "1"}), 2)
        self.assertEqual(commander.openclaw_process_timeout({"COMMANDER_OPENCLAW_PROCESS_TIMEOUT_SECONDS": "99"}), 30)
        self.assertEqual(commander.openclaw_process_timeout({"COMMANDER_OPENCLAW_PROCESS_TIMEOUT_SECONDS": "bad"}), 8)

    def test_openclaw_recovery_report_is_research_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / ".openclaw" / "skills" / "browser").mkdir(parents=True)
            report = commander.openclaw_recovery_report(
                home=home,
                env={"APPDATA": str(home / "AppData" / "Roaming")},
                candidates=[
                    {
                        "full_name": "openclaw/openclaw",
                        "url": "https://github.com/openclaw/openclaw",
                        "description": "Candidate repo",
                        "stars": 10,
                        "archived": False,
                        "pushed_at": "2026-04-01T00:00:00Z",
                    }
                ],
                readme_text="Install:\ngit clone https://github.com/openclaw/openclaw.git ~/.openclaw\npnpm install",
            )
        self.assertIn("OpenClaw recovery", report)
        self.assertIn("GitHub candidates", report)
        self.assertIn("/openclaw prepare https://github.com/openclaw/openclaw", report)
        self.assertIn("git clone https://github.com/openclaw/openclaw.git ~/.openclaw", report)
        self.assertIn("Nothing was installed or started", report)

    def test_openclaw_prepare_clone_creates_commander_approval(self) -> None:
        original_add = commander.add_pending_action
        original_which = commander.shutil.which
        actions = []
        try:
            commander.add_pending_action = lambda project_id, action: actions.append((project_id, action)) or "abc123"  # type: ignore[assignment]
            commander.shutil.which = lambda name: "git.exe" if name == "git" else original_which(name)  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                text = commander.prepare_openclaw_clone_response(
                    "https://github.com/openclaw/openclaw",
                    home=home,
                    env={"APPDATA": str(home / "AppData" / "Roaming")},
                )
        finally:
            commander.add_pending_action = original_add  # type: ignore[assignment]
            commander.shutil.which = original_which  # type: ignore[assignment]
        self.assertIn("OpenClaw clone prepared", text)
        self.assertIn("Pending approval ID: abc123", text)
        self.assertEqual(actions[0][0], "commander")
        self.assertEqual(actions[0][1]["type"], "openclaw_clone")
        self.assertEqual(actions[0][1]["repo_url"], "https://github.com/openclaw/openclaw")

    def test_openclaw_pending_response_gets_approval_buttons(self) -> None:
        rows = commander.contextual_button_rows(
            "OpenClaw clone prepared.\nPending approval ID: abc123\n\nApprove with /approve commander abc123"
        )
        labels = [button["text"] for row in rows for button in row]
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertIn("Approve openclaw clone", labels)
        self.assertIn("OpenClaw status", labels)
        self.assertIn("cmd:/approve commander abc123", callbacks)

    def test_openclaw_start_requires_configured_launcher(self) -> None:
        text = commander.prepare_openclaw_start_response(env={})
        self.assertIn("COMMANDER_OPENCLAW_LAUNCHER is not configured", text)

    def test_openclaw_snapshot_tolerates_process_scan_timeout(self) -> None:
        original_process_lines = commander.computer_process_lines
        try:
            commander.computer_process_lines = (  # type: ignore[assignment]
                lambda terms, timeout=8: (_ for _ in ()).throw(subprocess.TimeoutExpired(["powershell"], timeout))
            )

            snapshot = commander.openclaw_status_snapshot(env={})
        finally:
            commander.computer_process_lines = original_process_lines  # type: ignore[assignment]

        self.assertEqual(snapshot["process_rows"], [])
        self.assertIn("timed out", snapshot["process_error"])

    def test_openclaw_start_prepares_approval_for_configured_launcher(self) -> None:
        original_add = commander.add_pending_action
        original_snapshot = commander.openclaw_status_snapshot
        actions = []
        try:
            commander.add_pending_action = lambda project_id, action: actions.append((project_id, action)) or "def456"  # type: ignore[assignment]
            commander.openclaw_status_snapshot = lambda *args, **kwargs: {"process_rows": []}  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as temp:
                launcher = Path(temp) / "openclaw.cmd"
                launcher.write_text("@echo off\n", encoding="utf-8")
                text = commander.prepare_openclaw_start_response(env={"COMMANDER_OPENCLAW_LAUNCHER": str(launcher)})
        finally:
            commander.add_pending_action = original_add  # type: ignore[assignment]
            commander.openclaw_status_snapshot = original_snapshot  # type: ignore[assignment]
        self.assertIn("OpenClaw start prepared", text)
        self.assertIn("Pending approval ID: def456", text)
        self.assertEqual(actions[0][0], "commander")
        self.assertEqual(actions[0][1]["type"], "openclaw_start")


class BrowserAndClickUpTests(unittest.TestCase):
    def test_page_summary_parser_extracts_basics(self) -> None:
        parser = PageSummaryParser()
        parser.feed('<html><head><title>Demo</title><meta name="description" content="A demo page"></head><body><h1>Hello</h1><a href="/x">x</a></body></html>')
        self.assertEqual(parser.title, "Demo")
        self.assertEqual(parser.description, "A demo page")
        self.assertEqual(parser.h1, ["Hello"])
        self.assertEqual(parser.links, 1)

    def test_browser_format_reports_error(self) -> None:
        text = format_inspection(BrowserInspection(ok=False, url="https://example.com", status=500, error="bad"))
        self.assertIn("Status: 500", text)
        self.assertIn("Error: bad", text)

    def test_clickup_settings_and_filters(self) -> None:
        settings = settings_from_env({"CLICKUP_API_TOKEN": "tok", "CLICKUP_WORKSPACE_ID": "123"})
        self.assertTrue(settings.configured)
        tasks = [
            {"id": "1", "name": "Campaign lead review", "status": {"status": "open"}},
            {"id": "2", "name": "Engineering task", "status": {"status": "open"}},
        ]
        self.assertEqual([task["id"] for task in filter_tasks(tasks, "campaign lead")], ["1"])
        self.assertIn("Campaign lead review", format_tasks(tasks, limit=1))

    def test_clickup_count_summarizes_matching_tasks(self) -> None:
        original_settings = commander.clickup_settings_from_env
        original_tasks = commander.clickup_filtered_team_tasks
        try:
            commander.clickup_settings_from_env = lambda: settings_from_env({"CLICKUP_API_TOKEN": "tok", "CLICKUP_WORKSPACE_ID": "123"})  # type: ignore[assignment]
            commander.clickup_filtered_team_tasks = lambda _settings: {  # type: ignore[assignment]
                "tasks": [
                    {"id": "1", "name": "Lead: Alpha clinic", "status": {"status": "open"}},
                    {"id": "2", "name": "Lead: Beta group", "status": {"status": "qualified"}},
                    {"id": "3", "name": "Campaign copy", "status": {"status": "open"}},
                ]
            }
            text = commander.command_clickup(["count", "lead"])
        finally:
            commander.clickup_settings_from_env = original_settings  # type: ignore[assignment]
            commander.clickup_filtered_team_tasks = original_tasks  # type: ignore[assignment]
        self.assertIn("Matching tasks: 2", text)
        self.assertIn("open: 1", text)
        self.assertIn("qualified: 1", text)

    def test_cleanup_helpers_format_non_destructive_plan(self) -> None:
        self.assertEqual(bytes_to_mb(1024 * 1024), 1.0)
        text = format_cleanup_scan(
            [
                {
                    "label": "Temp",
                    "size_mb": 12.5,
                    "files": 3,
                    "exists": True,
                    "truncated": False,
                    "risk": "low",
                    "action": "Review files.",
                }
            ]
        )
        self.assertIn("No files were deleted", text)
        self.assertIn("Temp", text)

    def test_doctor_score_penalizes_warnings_and_failures(self) -> None:
        checks = [
            {"status": "good", "label": "ok", "detail": "ok"},
            {"status": "warn", "label": "warn", "detail": "warn"},
            {"status": "bad", "label": "bad", "detail": "bad"},
        ]
        self.assertEqual(commander.doctor_score(checks), 52)

    def test_change_bucket_summary_hides_filenames(self) -> None:
        summary = commander.change_bucket_summary(["src/components/App.tsx", "README.md", "tests/app.spec.ts"])
        self.assertIn("app/user interface", summary)
        self.assertIn("docs/content", summary)
        self.assertNotIn("App.tsx", summary)

    def test_work_feed_formats_plain_english_without_filenames(self) -> None:
        sessions = {
            "example": {
                "state": "running",
                "task": "Fix onboarding",
                "current_phase": "running",
                "updated_at": commander.utc_now(),
                "timeline": [
                    {"title": "Plan prepared", "detail": "Risk: medium", "status": "done"},
                    {"title": "Codex session launched", "detail": "Commander is watching.", "status": "active"},
                ],
                "work_plan": {"risk": "medium"},
                "pending_actions": {},
            }
        }
        changes = [{"project": "example", "changed_count": 3, "areas": "app/user interface (2), tests (1)"}]
        items = commander.work_feed_items(user_id=None, sessions=sessions, changes=changes, tasks=[])
        text = commander.format_work_feed(items)
        self.assertIn("example - running", text)
        self.assertIn("Work areas: app/user interface (2), tests (1)", text)
        self.assertNotIn("src/", text)
        self.assertNotIn("README.md", text)
        self.assertNotIn("App.tsx", text)

    def test_session_briefs_hide_technical_paths(self) -> None:
        sessions = {
            "example": {
                "state": "running",
                "task": "Fix src/components/App.tsx onboarding",
                "current_phase": "running",
                "updated_at": commander.utc_now(),
                "timeline": [
                    {"title": "Reviewed src/components/App.tsx", "detail": "README.md notes updated", "status": "done"},
                    {"title": "Codex session launched", "detail": "Commander is watching.", "status": "active"},
                ],
                "work_plan": {"risk": "medium"},
                "pending_actions": {},
            }
        }
        changes = [{"project": "example", "changed_count": 2, "areas": "app/user interface (2)"}]

        items = commander.session_brief_items(user_id=None, sessions=sessions, changes=changes, tasks=[])
        text = commander.format_session_briefs(items)

        self.assertIn("example - working now", text)
        self.assertIn("app/user interface", text)
        self.assertIn("Attention needed: no", text)
        self.assertNotIn("src/", text)
        self.assertNotIn("README.md", text)
        self.assertNotIn("App.tsx", text)

    def test_session_briefs_use_owner_friendly_health_companion_task(self) -> None:
        sessions = {
            "comx-omnichannel-test": {
                "state": "finished_unknown",
                "task": "Read PROJECT_BRIEF.md, AGENTS.md, and docs/source/health-companion-source-requirements.md first. Build Checkpoint 1 from the Health Companion AI Codex spec only: create repository skeleton and FastAPI /healthz backend skeleton.",
                "current_phase": "review",
                "updated_at": commander.utc_now(),
                "timeline": [{"title": "Final report ready", "detail": "Codex wrote an outcome summary for review.", "status": "done"}],
                "work_plan": {"risk": "high"},
                "pending_actions": {},
            }
        }
        changes = [{"project": "comx-omnichannel-test", "changed_count": 4, "areas": "app/user interface (2), backend/service (2)"}]

        items = commander.session_brief_items(user_id=None, sessions=sessions, changes=changes, tasks=[])
        text = commander.format_session_briefs(items)

        self.assertIn("Health Companion", text)
        self.assertIn("finished, needs quick review", text)
        self.assertIn("Build the first Health Companion foundation", text)
        self.assertNotIn("technical file", text)
        self.assertNotIn("PROJECT_BRIEF.md", text)

    def test_mission_timeline_formats_direction_without_filenames(self) -> None:
        sessions = {
            "example": {
                "state": "running",
                "task": "Fix src/components/App.tsx onboarding",
                "current_phase": "inspect",
                "updated_at": commander.utc_now(),
                "timeline": [
                    {"title": "Reviewed src/components/App.tsx", "detail": "README.md notes updated", "status": "done"},
                    {"title": "Running checks", "detail": "npm test", "status": "active"},
                ],
                "work_plan": {"risk": "medium"},
                "pending_actions": {},
            }
        }
        changes = [{"project": "example", "changed_count": 2, "areas": "app/user interface (2)"}]

        items = commander.mission_timeline_items(user_id=None, sessions=sessions, changes=changes, tasks=[])
        text = commander.format_mission_timeline(items)

        self.assertEqual(items[0]["project"], "example")
        self.assertIn("Working", items[0]["stage"])
        self.assertIn("Direction:", text)
        self.assertIn("Evidence:", text)
        self.assertNotIn("src/", text)
        self.assertNotIn("README.md", text)
        self.assertNotIn("App.tsx", text)

    def test_session_evidence_card_summarizes_checks_without_filenames(self) -> None:
        original_sessions = commander.sessions_data
        original_refresh = commander.refresh_session_states
        original_project = commander.get_project
        original_project_path = commander.project_path
        original_is_git = commander.is_git_repo
        original_changed = commander.changed_files
        original_branch = commander.current_branch
        original_pid = commander.pid_running
        original_audit = commander.audit_data
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "example.log"
            log.write_text(
                "python -m py_compile src/app.py\n"
                "Ran 4 tests in 0.5s OK\n"
                "ERROR codex_core::exec: exec error: windows sandbox: setup refresh failed with status exit code: 1\n",
                encoding="utf-8",
            )
            try:
                commander.refresh_session_states = lambda: None  # type: ignore[assignment]
                commander.sessions_data = lambda: {  # type: ignore[assignment]
                    "sessions": {
                        "example": {
                            "state": "running",
                            "pid": 123,
                            "task": "Fix C:\\Users\\Name\\repo\\secret.py",
                            "task_id": "task1",
                            "log_file": str(log),
                            "branch": "main",
                            "work_plan": {
                                "risk": "medium",
                                "approach": ["Inspect src/app.py", "Run README.md checks"],
                                "expected_checks": ["python -m unittest discover"],
                            },
                            "progress_signals": [
                                {
                                    "title": "Local shell blocked",
                                    "detail": "Could not inspect C:\\Users\\Name\\repo\\secret.py",
                                    "status": "warn",
                                }
                            ],
                            "timeline": [
                                {"title": "Reviewed src/app.py", "detail": "README.md notes", "status": "done"},
                            ],
                        }
                    }
                }
                commander.get_project = lambda project_id: {"allowed": True, "path": tmp}  # type: ignore[assignment]
                commander.project_path = lambda project: Path(tmp)  # type: ignore[assignment]
                commander.is_git_repo = lambda path: True  # type: ignore[assignment]
                commander.changed_files = lambda path: ["src/app.py", "README.md"]  # type: ignore[assignment]
                commander.current_branch = lambda path: "main"  # type: ignore[assignment]
                commander.pid_running = lambda pid: True  # type: ignore[assignment]
                commander.audit_data = lambda: {  # type: ignore[assignment]
                    "events": [
                        {
                            "project": "example",
                            "status": "prepared",
                            "type": "commit",
                            "summary": "Commit C:\\Users\\Name\\repo\\.env",
                        }
                    ]
                }

                card = commander.session_evidence_card("example")
                text = commander.format_session_evidence_card(card)
            finally:
                commander.sessions_data = original_sessions  # type: ignore[assignment]
                commander.refresh_session_states = original_refresh  # type: ignore[assignment]
                commander.get_project = original_project  # type: ignore[assignment]
                commander.project_path = original_project_path  # type: ignore[assignment]
                commander.is_git_repo = original_is_git  # type: ignore[assignment]
                commander.changed_files = original_changed  # type: ignore[assignment]
                commander.current_branch = original_branch  # type: ignore[assignment]
                commander.pid_running = original_pid  # type: ignore[assignment]
                commander.audit_data = original_audit  # type: ignore[assignment]

        self.assertEqual(card["process"], "running")
        self.assertIn("Python compile check", " ".join(card["checks"]))
        self.assertIn("technical path", text)
        self.assertIn("technical file", text)
        self.assertNotIn("C:\\Users", text)
        self.assertNotIn("secret.py", text)
        self.assertNotIn("src/app.py", text)

    def test_session_replay_card_tells_story_without_filenames(self) -> None:
        original_sessions = commander.sessions_data
        original_refresh = commander.refresh_session_states
        original_project = commander.get_project
        original_project_path = commander.project_path
        original_is_git = commander.is_git_repo
        original_changed = commander.changed_files
        original_branch = commander.current_branch
        original_pid = commander.pid_running
        original_audit = commander.audit_data
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "example.log"
            log.write_text(
                "python -m py_compile src/app.py\n"
                "Ran 4 tests in 0.5s OK\n",
                encoding="utf-8",
            )
            try:
                commander.refresh_session_states = lambda: None  # type: ignore[assignment]
                commander.sessions_data = lambda: {  # type: ignore[assignment]
                    "sessions": {
                        "example": {
                            "state": "running",
                            "pid": 123,
                            "task": "Fix C:\\Users\\Name\\repo\\secret.py onboarding",
                            "task_id": "task1",
                            "log_file": str(log),
                            "branch": "main",
                            "work_plan": {"risk": "medium", "approach": ["Inspect src/app.py"]},
                            "progress_signals": [],
                            "timeline": [
                                {"title": "Reviewed src/app.py", "detail": "README.md notes", "status": "done"},
                                {"title": "Ran checks", "detail": "python -m py_compile src/app.py", "status": "done"},
                            ],
                        }
                    }
                }
                commander.get_project = lambda project_id: {"allowed": True, "path": tmp}  # type: ignore[assignment]
                commander.project_path = lambda project: Path(tmp)  # type: ignore[assignment]
                commander.is_git_repo = lambda path: True  # type: ignore[assignment]
                commander.changed_files = lambda path: ["src/app.py", "README.md"]  # type: ignore[assignment]
                commander.current_branch = lambda path: "main"  # type: ignore[assignment]
                commander.pid_running = lambda pid: True  # type: ignore[assignment]
                commander.audit_data = lambda: {"events": []}  # type: ignore[assignment]

                replay = commander.session_replay_card("example")
                rendered = commander.format_session_replay_card(replay)
            finally:
                commander.sessions_data = original_sessions  # type: ignore[assignment]
                commander.refresh_session_states = original_refresh  # type: ignore[assignment]
                commander.get_project = original_project  # type: ignore[assignment]
                commander.project_path = original_project_path  # type: ignore[assignment]
                commander.is_git_repo = original_is_git  # type: ignore[assignment]
                commander.changed_files = original_changed  # type: ignore[assignment]
                commander.current_branch = original_branch  # type: ignore[assignment]
                commander.pid_running = original_pid  # type: ignore[assignment]
                commander.audit_data = original_audit  # type: ignore[assignment]

        self.assertIn("Story", rendered)
        self.assertIn("Outcome", rendered)
        self.assertIn("Python compile check", rendered)
        self.assertNotIn("C:\\Users", rendered)
        self.assertNotIn("secret.py", rendered)
        self.assertNotIn("src/app.py", rendered)

    def test_operator_playback_combines_story_proof_and_action_safely(self) -> None:
        original_sessions = commander.sessions_data
        original_refresh = commander.refresh_session_states
        original_project = commander.get_project
        original_project_path = commander.project_path
        original_is_git = commander.is_git_repo
        original_changed = commander.changed_files
        original_branch = commander.current_branch
        original_pid = commander.pid_running
        original_audit = commander.audit_data
        original_user_state = commander.user_state
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "example.log"
            log.write_text("python -m py_compile src/app.py\nRan 4 tests in 0.5s OK\n", encoding="utf-8")
            try:
                commander.refresh_session_states = lambda: None  # type: ignore[assignment]
                commander.sessions_data = lambda: {  # type: ignore[assignment]
                    "sessions": {
                        "example": {
                            "state": "running",
                            "pid": 123,
                            "task": "Fix C:\\Users\\Name\\repo\\secret.py onboarding",
                            "task_id": "task1",
                            "log_file": str(log),
                            "branch": "main",
                            "work_plan": {"risk": "medium"},
                            "pending_actions": {
                                "abc": {
                                    "type": "commit",
                                    "message": "Commit C:\\Users\\Name\\repo\\secret.py",
                                    "created_at": "2026-05-01T12:00:00+00:00",
                                }
                            },
                            "progress_signals": [],
                            "timeline": [{"title": "Ran checks", "detail": "python -m py_compile src/app.py", "status": "done"}],
                        }
                    }
                }
                commander.get_project = lambda project_id: {"allowed": True, "path": tmp}  # type: ignore[assignment]
                commander.project_path = lambda project: Path(tmp)  # type: ignore[assignment]
                commander.is_git_repo = lambda path: True  # type: ignore[assignment]
                commander.changed_files = lambda path: ["src/app.py"]  # type: ignore[assignment]
                commander.current_branch = lambda path: "main"  # type: ignore[assignment]
                commander.pid_running = lambda pid: True  # type: ignore[assignment]
                commander.audit_data = lambda: {"events": []}  # type: ignore[assignment]
                commander.user_state = lambda user_id: {"last_image": {"summary": "Saw C:\\Users\\Name\\repo\\secret.py"}}  # type: ignore[assignment]

                card = commander.operator_playback_card("example", user_id="1")
                rendered = commander.format_operator_playback_card(card)
            finally:
                commander.sessions_data = original_sessions  # type: ignore[assignment]
                commander.refresh_session_states = original_refresh  # type: ignore[assignment]
                commander.get_project = original_project  # type: ignore[assignment]
                commander.project_path = original_project_path  # type: ignore[assignment]
                commander.is_git_repo = original_is_git  # type: ignore[assignment]
                commander.changed_files = original_changed  # type: ignore[assignment]
                commander.current_branch = original_branch  # type: ignore[assignment]
                commander.pid_running = original_pid  # type: ignore[assignment]
                commander.audit_data = original_audit  # type: ignore[assignment]
                commander.user_state = original_user_state  # type: ignore[assignment]

        self.assertEqual(card["confidence"], "needs decision")
        self.assertIn("/approvals", card["primary_action"])
        self.assertIn("Proof:", rendered)
        self.assertIn("Pending approvals:", rendered)
        self.assertNotIn("C:\\Users", rendered)
        self.assertNotIn("secret.py", rendered)
        self.assertNotIn("src/app.py", rendered)

    def test_project_completion_requires_objective_criteria_and_clean_state(self) -> None:
        original_profile = commander.project_profile
        original_playback = commander.operator_playback_card
        try:
            commander.project_profile = lambda project: {  # type: ignore[assignment]
                "project": project,
                "objective": "Ship C:\\Users\\Name\\repo\\secret.py workflow",
                "done_criteria": [
                    {"text": "Workflow works in README.md", "status": "done", "evidence": "Manual QA passed"},
                    {"text": "Verification passes", "status": "done", "evidence": "python -m unittest discover"},
                ],
            }
            commander.operator_playback_card = lambda project, user_id=None: {  # type: ignore[assignment]
                "project": project,
                "state": "completed",
                "confidence": "reviewable",
                "checks": ["python -m py_compile src/app.py"],
                "pending_approvals": [],
                "changed_count": 0,
                "blocker": "none reported",
                "primary_action": "Archive project",
            }

            card = commander.project_completion_card("example", user_id="1")
            rendered = commander.format_project_completion(card)
        finally:
            commander.project_profile = original_profile  # type: ignore[assignment]
            commander.operator_playback_card = original_playback  # type: ignore[assignment]

        self.assertEqual(card["verdict"], "100% done candidate")
        self.assertEqual(card["completion_percent"], 100)
        self.assertIn("Definition of Done", rendered)
        self.assertNotIn("C:\\Users", rendered)
        self.assertNotIn("README.md", rendered)
        self.assertNotIn("src/app.py", rendered)

    def test_project_completion_caps_at_99_when_changes_remain(self) -> None:
        original_profile = commander.project_profile
        original_playback = commander.operator_playback_card
        try:
            commander.project_profile = lambda project: {  # type: ignore[assignment]
                "project": project,
                "objective": "Ship the workflow",
                "done_criteria": [{"text": "Workflow works", "status": "done", "evidence": "QA passed"}],
            }
            commander.operator_playback_card = lambda project, user_id=None: {  # type: ignore[assignment]
                "project": project,
                "state": "completed",
                "confidence": "reviewable",
                "checks": ["Python test suite"],
                "pending_approvals": [],
                "changed_count": 2,
                "blocker": "none reported",
                "primary_action": "Review evidence",
            }

            card = commander.project_completion_card("example", user_id="1")
        finally:
            commander.project_profile = original_profile  # type: ignore[assignment]
            commander.operator_playback_card = original_playback  # type: ignore[assignment]

        self.assertEqual(card["verdict"], "reviewable, not final")
        self.assertLess(card["completion_percent"], 100)

    def test_project_completion_uses_done_criterion_evidence_as_proof(self) -> None:
        original_profile = commander.project_profile
        original_playback = commander.operator_playback_card
        try:
            commander.project_profile = lambda project: {  # type: ignore[assignment]
                "project": project,
                "objective": "Ship the workflow",
                "done_criteria": [
                    {"id": "1", "text": "Workflow works", "status": "done", "evidence": "Local verification passed"}
                ],
            }
            commander.operator_playback_card = lambda project, user_id=None: {  # type: ignore[assignment]
                "project": project,
                "state": "completed",
                "confidence": "reviewable",
                "checks": [],
                "pending_approvals": [],
                "changed_count": 0,
                "blocker": "none reported",
                "primary_action": "Review evidence",
            }

            card = commander.project_completion_card("example", user_id="1")
            rendered = commander.format_project_completion(card)
        finally:
            commander.project_profile = original_profile  # type: ignore[assignment]
            commander.operator_playback_card = original_playback  # type: ignore[assignment]

        self.assertIn("Criterion 1: Local verification passed", card["checks"])
        self.assertNotIn("No verification proof recorded yet", rendered)

    def test_completed_session_final_summary_clears_stale_blocker(self) -> None:
        original_sessions = commander.sessions_data
        original_refresh = commander.refresh_session_states
        original_project = commander.get_project
        original_project_path = commander.project_path
        original_is_git = commander.is_git_repo
        original_changed = commander.changed_files
        original_branch = commander.current_branch
        original_pid = commander.pid_running
        original_audit = commander.audit_data
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "session.log"
            last_message = Path(tmp) / "last-message.txt"
            log.write_text("python -m pytest\npassed\n", encoding="utf-8")
            last_message.write_text(
                "3. Verified\npython -m pytest passed\n4. Risks / Notes\nCurrent blocker: none for local evidence.\n",
                encoding="utf-8",
            )
            try:
                commander.refresh_session_states = lambda: None  # type: ignore[assignment]
                commander.sessions_data = lambda: {  # type: ignore[assignment]
                    "sessions": {
                        "example": {
                            "state": "completed",
                            "pid": 0,
                            "task": "Verify local app",
                            "task_id": "task1",
                            "log_file": str(log),
                            "last_message_file": str(last_message),
                            "branch": "main",
                            "work_plan": {"risk": "medium"},
                            "progress_signals": [
                                {"title": "Blocker reported", "detail": "Codex reported a blocker that needs review.", "status": "warn"}
                            ],
                            "timeline": [{"title": "Codex run finished", "detail": "Review summary.", "status": "done"}],
                        }
                    }
                }
                commander.get_project = lambda project_id: {"allowed": True, "path": tmp}  # type: ignore[assignment]
                commander.project_path = lambda project: Path(tmp)  # type: ignore[assignment]
                commander.is_git_repo = lambda path: True  # type: ignore[assignment]
                commander.changed_files = lambda path: []  # type: ignore[assignment]
                commander.current_branch = lambda path: "main"  # type: ignore[assignment]
                commander.pid_running = lambda pid: False  # type: ignore[assignment]
                commander.audit_data = lambda: {"events": []}  # type: ignore[assignment]

                card = commander.session_evidence_card("example")
            finally:
                commander.sessions_data = original_sessions  # type: ignore[assignment]
                commander.refresh_session_states = original_refresh  # type: ignore[assignment]
                commander.get_project = original_project  # type: ignore[assignment]
                commander.project_path = original_project_path  # type: ignore[assignment]
                commander.is_git_repo = original_is_git  # type: ignore[assignment]
                commander.changed_files = original_changed  # type: ignore[assignment]
                commander.current_branch = original_branch  # type: ignore[assignment]
                commander.pid_running = original_pid  # type: ignore[assignment]
                commander.audit_data = original_audit  # type: ignore[assignment]

        self.assertEqual(card["blocker"], "none reported")
        self.assertTrue(card["checks"])

    def test_owner_review_pack_is_nontechnical_and_actionable(self) -> None:
        original_project_and_rest = commander.project_and_rest
        original_completion = commander.project_completion_card
        original_evidence = commander.session_evidence_card
        original_label = commander.project_label
        try:
            commander.project_and_rest = lambda args, user_id, allow_active=None: ("example", [])  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Example Product"  # type: ignore[assignment]
            commander.project_completion_card = lambda project_id, user_id=None: {  # type: ignore[assignment]
                "total_criteria": 12,
                "done_criteria": 12,
                "completion_percent": 99,
                "verdict": "reviewable, not final",
                "blocker": "none reported",
                "changed_count": 4,
                "checks": ["Python test suite: passed", "Criterion 12: C:\\AI-Company\\Example\\src\\app.tsx passed"],
                "pending_approvals": [],
            }
            commander.session_evidence_card = lambda project_id: {"checks": ["Fallback proof"]}  # type: ignore[assignment]

            rendered = commander.command_review(["example"], user_id="1")
        finally:
            commander.project_and_rest = original_project_and_rest  # type: ignore[assignment]
            commander.project_completion_card = original_completion  # type: ignore[assignment]
            commander.session_evidence_card = original_evidence  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        self.assertIn("Owner review pack: Example Product", rendered)
        self.assertIn("12/12 complete", rendered)
        self.assertIn("Python test suite: passed", rendered)
        self.assertIn("/evidence example", rendered)
        self.assertIn("/commit example", rendered)
        self.assertNotIn("C:\\", rendered)
        self.assertNotIn("app.tsx", rendered)

    def test_owner_review_pack_can_be_saved_without_paths_or_secrets(self) -> None:
        original_project_and_rest = commander.project_and_rest
        original_completion = commander.project_completion_card
        original_evidence = commander.session_evidence_card
        original_label = commander.project_label
        original_report_dir = commander.report_dir
        try:
            commander.project_and_rest = lambda args, user_id, allow_active=None: ("example", [])  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Example Product"  # type: ignore[assignment]
            commander.project_completion_card = lambda project_id, user_id=None: {  # type: ignore[assignment]
                "total_criteria": 2,
                "done_criteria": 2,
                "completion_percent": 99,
                "verdict": "reviewable, not final",
                "blocker": "none reported",
                "changed_count": 1,
                "checks": ["Local checks passed for C:\\AI-Company\\Example\\src\\app.py", "api_key=secret123"],
                "pending_approvals": [],
            }
            commander.session_evidence_card = lambda project_id: {"checks": []}  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                commander.report_dir = lambda: Path(tmp)  # type: ignore[assignment]
                rendered = commander.command_review(["example", "save"], user_id="1")
                files = list(Path(tmp).glob("example-owner-review-*.md"))
                self.assertEqual(len(files), 1)
                saved = files[0].read_text(encoding="utf-8")
        finally:
            commander.project_and_rest = original_project_and_rest  # type: ignore[assignment]
            commander.project_completion_card = original_completion  # type: ignore[assignment]
            commander.session_evidence_card = original_evidence  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]
            commander.report_dir = original_report_dir  # type: ignore[assignment]

        self.assertIn("Saved locally in Commander reports.", rendered)
        self.assertIn("Owner review pack: Example Product", saved)
        self.assertNotIn("C:\\", saved)
        self.assertNotIn("app.py", saved)
        self.assertNotIn("secret123", saved)
        self.assertIn("[REDACTED]", saved)
        self.assertNotIn("example-owner-review", rendered)
        self.assertIn("Open it from dashboard Owner Reviews", rendered)

    def test_saved_owner_reviews_list_hides_filenames_by_default(self) -> None:
        original_report_dir = commander.report_dir
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report = root / "example-owner-review-20260503-230945.md"
                report.write_text(
                    "Owner review pack: Example Product\n"
                    "- Proof: Local checks passed for C:\\AI-Company\\Example\\src\\app.py\n",
                    encoding="utf-8",
                )
                commander.report_dir = lambda: root  # type: ignore[assignment]

                rendered = commander.command_reviews([])
                detailed = commander.command_reviews(["details"])
        finally:
            commander.report_dir = original_report_dir  # type: ignore[assignment]

        self.assertIn("Saved owner review packs", rendered)
        self.assertIn("Example Product", rendered)
        self.assertNotIn("example-owner-review", rendered)
        self.assertNotIn("C:\\", rendered)
        self.assertIn("example-owner-review", detailed)

    def test_saved_owner_review_pack_preview_hides_paths_and_filenames(self) -> None:
        original_report_dir = commander.report_dir
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report = root / "example-owner-review-20260504-010000.md"
                report.write_text(
                    "Owner review pack: Example Product\n"
                    "- Proof: Local checks passed for C:\\AI-Company\\Example\\src\\app.py\n"
                    "- Secret: api_key=secret123\n",
                    encoding="utf-8",
                )
                commander.report_dir = lambda: root  # type: ignore[assignment]

                preview = commander.saved_owner_review_pack_preview("Example Product")
        finally:
            commander.report_dir = original_report_dir  # type: ignore[assignment]

        self.assertIsNotNone(preview)
        assert preview is not None
        text = preview["text"]
        self.assertEqual(preview["project"], "Example Product")
        self.assertIn("Owner review pack: Example Product", text)
        self.assertNotIn("C:\\", text)
        self.assertNotIn("app.py", text)
        self.assertNotIn("secret123", text)
        self.assertNotIn("example-owner-review", str(preview))
        self.assertIn("[REDACTED]", text)

    def test_log_progress_signals_detect_blockers_without_paths(self) -> None:
        raw = """
        exec "powershell" -Command 'git status' in C:\\AI-Company\\Example
        ERROR codex_core::exec: exec error: windows sandbox: setup refresh failed with status exit code: 1
        src/components/App.tsx
        Current blocker: cannot inspect C:\\AI-Company\\Example\\src\\components\\App.tsx
        """

        signals = commander.progress_signals_from_text(raw)
        rendered = "\n".join(f"{item['title']}: {item['detail']}" for item in signals)

        self.assertIn("Inspecting project", rendered)
        self.assertIn("Local shell blocked", rendered)
        self.assertIn("Blocker reported", rendered)
        self.assertNotIn("C:\\AI-Company", rendered)
        self.assertNotIn("App.tsx", rendered)

    def test_log_model_output_ignores_prompt_expected_checks(self) -> None:
        raw = """
        Commander work plan:
        Expected checks:
        - npm run test
        - npm run build
        2026-05-02T08:09:01Z ERROR AuthRequired oauth-protected-resource
        codex
        I will inspect the project first.
        """

        output = commander.codex_output_text(raw)
        checks = commander.verification_evidence_from_text(output)
        signals = commander.progress_signals_from_text(output)
        rendered = "\n".join(f"{item['title']}: {item['detail']}" for item in signals)

        self.assertEqual(checks, [])
        self.assertIn("Inspecting project", rendered)
        self.assertNotIn("Connector needs authentication", rendered)
        self.assertNotIn("Running checks", rendered)

    def test_verification_evidence_ignores_planned_check_mentions(self) -> None:
        raw = "I will wire npm run test and npm run build into package scripts."

        self.assertEqual(commander.verification_evidence_from_text(raw), [])
        self.assertEqual(commander.progress_signals_from_text(raw), [])

        actual = "exec\npowershell -Command 'npm run test' in C:\\Project\nsucceeded in 1s"
        self.assertIn("JavaScript test suite: run", commander.verification_evidence_from_text(actual))
        self.assertIn("Running checks", str(commander.progress_signals_from_text(actual)))

    def test_verification_command_args_uses_windows_npm_cmd(self) -> None:
        args = commander.verification_command_args("npm run test")
        if commander.os.name == "nt":
            self.assertTrue(args[0].lower().endswith("npm.cmd"))
        else:
            self.assertEqual(args[0], "npm")
        self.assertEqual(args[-2:], ["run", "test"])

    def test_health_companion_dod_does_not_use_legacy_node_mvp_evidence(self) -> None:
        original_profiles_data = commander.profiles_data
        original_save_profiles = commander.save_profiles
        profile_data = {
            "profiles": {
                "health": {
                    "objective": "Build Health Companion AI V1",
                    "done_criteria": [
                        {
                            "text": "WhatsApp and Telegram intake paths support safe patient messages.",
                            "status": "open",
                            "evidence": "",
                        }
                    ],
                }
            }
        }
        saved: dict[str, object] = {}
        try:
            commander.profiles_data = lambda: profile_data  # type: ignore[assignment]
            commander.save_profiles = lambda data: saved.update(data)  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "src" / "adapters").mkdir(parents=True)
                (root / "src" / "model.js").write_text("module.exports = {}", encoding="utf-8")
                (root / "src" / "adapters" / "telegram.js").write_text("", encoding="utf-8")
                (root / "src" / "adapters" / "whatsapp.js").write_text("", encoding="utf-8")

                changed = commander.auto_update_done_criteria_from_verification(
                    "health",
                    root,
                    [{"command": "python -m pytest", "status": "passed"}],
                )
        finally:
            commander.profiles_data = original_profiles_data  # type: ignore[assignment]
            commander.save_profiles = original_save_profiles  # type: ignore[assignment]

        self.assertEqual(changed, 0)
        self.assertEqual(profile_data["profiles"]["health"]["done_criteria"][0]["status"], "open")
        self.assertEqual(saved, {})

    def test_session_summary_marks_completed_autopilot_criterion(self) -> None:
        original_profiles_data = commander.profiles_data
        original_save_profiles = commander.save_profiles
        profile_data = {
            "profiles": {
                "health": {
                    "objective": "Build Health Companion AI V1",
                    "done_criteria": [
                        {"text": "Source requirements extracted.", "status": "done", "evidence": "ok"},
                        {"text": "Repository scaffold exists.", "status": "done", "evidence": "ok"},
                        {"text": "Data foundation exists.", "status": "done", "evidence": "ok"},
                        {"text": "Patient auth flow is usable locally.", "status": "done", "evidence": "ok"},
                        {"id": "5", "text": "Patient web experience supports Arabic-first onboarding.", "status": "open", "evidence": ""},
                        {"id": "6", "text": "WhatsApp and Telegram intake paths support safe patient messages.", "status": "open", "evidence": ""},
                    ],
                }
            }
        }
        saved: dict[str, object] = {}
        try:
            commander.profiles_data = lambda: profile_data  # type: ignore[assignment]
            commander.save_profiles = lambda data: saved.update(data)  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                last_message = Path(tmp) / "last-message.txt"
                last_message.write_text(
                    "\n".join(
                        [
                            "1. Done",
                            "Criterion 5 is locally implemented and verified.",
                            "Ran and passed: node companion/frontend/tests/unit/patient-web-criterion5.test.js",
                            "Next recommended action: Proceed to criterion 6 for WhatsApp and Telegram intake.",
                        ]
                    ),
                    encoding="utf-8",
                )
                changed = commander.auto_update_done_criteria_from_session_summary(
                    "health",
                    {"state": "completed", "last_message_file": str(last_message)},
                )
        finally:
            commander.profiles_data = original_profiles_data  # type: ignore[assignment]
            commander.save_profiles = original_save_profiles  # type: ignore[assignment]

        criteria = profile_data["profiles"]["health"]["done_criteria"]
        self.assertEqual(changed, 1)
        self.assertEqual(criteria[4]["status"], "done")
        self.assertIn("complete and verified", criteria[4]["evidence"])
        self.assertEqual(criteria[5]["status"], "open")
        self.assertTrue(saved)

    def test_autopilot_task_keeps_external_boundaries(self) -> None:
        task = commander.autopilot_task_for_criterion(
            "health",
            {"id": "5", "text": "Patient web experience supports Arabic-first onboarding."},
        )

        self.assertIn("Definition-of-Done criterion 5", task)
        self.assertIn("permission to edit", task)
        self.assertIn("Do not deploy", task)
        self.assertIn("claim V1 is done", task)

    def test_autopilot_next_action_explains_owner_action(self) -> None:
        text = commander.autopilot_next_action("health", "no open criteria")

        self.assertIn("/done health", text)
        self.assertIn("/objective add health", text)

    def test_autopilot_recommendations_surface_owner_action(self) -> None:
        original_profiles_data = commander.profiles_data
        original_can_start = commander.autopilot_can_start
        original_label = commander.project_label
        try:
            commander.profiles_data = lambda: {  # type: ignore[assignment]
                "profiles": {"health": {"autopilot": {"enabled": True, "interval_minutes": 5}}}
            }
            commander.autopilot_can_start = lambda project_id: (False, "no open criteria", None)  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            items = commander.autopilot_recommendation_items()
        finally:
            commander.profiles_data = original_profiles_data  # type: ignore[assignment]
            commander.autopilot_can_start = original_can_start  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        self.assertEqual(len(items), 1)
        self.assertIn("Health Companion AI", items[0])
        self.assertIn("/done health", items[0])
        self.assertIn("/objective add health", items[0])

    def test_setup_recommendations_explain_capability_before_keys(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "configured",
            "TELEGRAM_ALLOWED_USER_IDS": "123",
            "OPENAI_API_KEY": "configured",
        }

        items = commander.setup_recommendation_items(limit=2, env=env)

        self.assertEqual(len(items), 2)
        self.assertIn("ClickUp task and campaign bridge", items[0])
        self.assertIn("campaign and task questions", items[0])
        self.assertIn("CLICKUP_API_TOKEN", items[0])
        self.assertIn("GitHub PR and issue workflows", items[1])
        self.assertIn("Optional setup", items[1])

    def test_autopilot_pauses_for_blocked_criteria(self) -> None:
        original_autopilot_profile = commander.autopilot_profile
        original_refresh = commander.refresh_session_states
        original_sessions = commander.sessions_data
        original_completion = commander.project_completion_card
        original_project_profile = commander.project_profile
        try:
            commander.autopilot_profile = lambda project_id: {"enabled": True, "interval_minutes": 1}  # type: ignore[assignment]
            commander.refresh_session_states = lambda: None  # type: ignore[assignment]
            commander.sessions_data = lambda: {"sessions": {}}  # type: ignore[assignment]
            commander.project_completion_card = lambda project_id: {"verdict": "not done", "pending_approvals": []}  # type: ignore[assignment]
            commander.project_profile = lambda project_id: {  # type: ignore[assignment]
                "done_criteria": [
                    {"id": "1", "text": "Owner decision needed", "status": "blocked", "evidence": ""},
                    {"id": "2", "text": "Build next feature", "status": "open", "evidence": ""},
                ]
            }

            ok, reason, criterion = commander.autopilot_can_start("health")
        finally:
            commander.autopilot_profile = original_autopilot_profile  # type: ignore[assignment]
            commander.refresh_session_states = original_refresh  # type: ignore[assignment]
            commander.sessions_data = original_sessions  # type: ignore[assignment]
            commander.project_completion_card = original_completion  # type: ignore[assignment]
            commander.project_profile = original_project_profile  # type: ignore[assignment]

        self.assertFalse(ok)
        self.assertEqual(reason, "blocked criteria need review")
        self.assertIsNone(criterion)

    def test_autopilot_tick_starts_open_criterion(self) -> None:
        original_profiles_data = commander.profiles_data
        original_save_profiles = commander.save_profiles
        original_can_start = commander.autopilot_can_start
        original_start = commander.start_codex
        original_label = commander.project_label
        profile_data = {"profiles": {"health": {"autopilot": {"enabled": True, "interval_minutes": 1}}}}
        saved: dict[str, object] = {}
        calls: list[tuple[str, str, str]] = []
        try:
            commander.profiles_data = lambda: profile_data  # type: ignore[assignment]
            commander.save_profiles = lambda data: saved.update(data)  # type: ignore[assignment]
            commander.autopilot_can_start = lambda project_id: (  # type: ignore[assignment]
                True,
                "ready",
                {"id": "5", "text": "Patient web experience supports Arabic-first onboarding."},
            )
            commander.start_codex = lambda project_id, task, user_id="system", task_id=None: calls.append(  # type: ignore[assignment]
                (project_id, task, user_id)
            ) or "Started health."
            commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            messages = commander.autopilot_tick_once(user_id="autopilot")
        finally:
            commander.profiles_data = original_profiles_data  # type: ignore[assignment]
            commander.save_profiles = original_save_profiles  # type: ignore[assignment]
            commander.autopilot_can_start = original_can_start  # type: ignore[assignment]
            commander.start_codex = original_start  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "health")
        self.assertEqual(calls[0][2], "autopilot")
        self.assertIn("criterion 5", calls[0][1])
        self.assertIn("health: started criterion 5", messages)
        self.assertEqual(profile_data["profiles"]["health"]["autopilot"]["last_criterion_id"], "5")
        self.assertTrue(saved)

    def test_refresh_session_progress_updates_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "session.log"
            log_path.write_text(
                "ERROR codex_core::exec: exec error: windows sandbox: setup refresh failed with status exit code: 1\n",
                encoding="utf-8",
            )
            session = {"log_file": str(log_path), "timeline": []}

            changed = commander.refresh_session_progress("example", session)
            text = "\n".join(commander.timeline_lines(session))

        self.assertTrue(changed)
        self.assertEqual(session["current_progress"]["title"], "Local shell blocked")
        self.assertIn("Local shell blocked", text)

    def test_session_brief_keeps_warning_visible_after_final_report(self) -> None:
        sessions = {
            "example": {
                "state": "completed",
                "task": "Fix onboarding",
                "current_progress": {"title": "Final report ready", "detail": "Codex wrote a summary.", "status": "done"},
                "progress_signals": [
                    {
                        "title": "Local shell blocked",
                        "detail": "Codex could not run project commands because the Windows sandbox failed before checks could start.",
                        "status": "warn",
                    },
                    {"title": "Final report ready", "detail": "Codex wrote an outcome summary for review.", "status": "done"},
                ],
                "timeline": [],
            }
        }
        changes = [{"project": "example", "changed_count": 0, "areas": "no changed areas"}]

        text = commander.format_session_briefs(commander.session_brief_items(sessions=sessions, changes=changes, tasks=[]))

        self.assertIn("Finished with blocker: Local shell blocked", text)
        self.assertIn("Local shell blocked", text)

    def test_heartbeat_summary_uses_briefs_not_diff(self) -> None:
        original_user_state = commander.user_state
        original_get_project = commander.get_project
        original_status = commander.command_status
        original_briefs = commander.command_briefs
        original_diff = commander.command_diff
        try:
            commander.user_state = lambda user_id: {"active_project": "example"}  # type: ignore[assignment]
            commander.get_project = lambda project_id: {"allowed": True}  # type: ignore[assignment]
            commander.command_status = lambda: "Active Codex sessions:\n- example: running"  # type: ignore[assignment]
            commander.command_briefs = lambda args, user_id: "Commander X session brief: example\nWork areas: app/user interface"  # type: ignore[assignment]
            commander.command_diff = lambda project_id: (_ for _ in ()).throw(AssertionError("heartbeat should not call diff"))  # type: ignore[assignment]

            text = commander.heartbeat_summary("user")
        finally:
            commander.user_state = original_user_state  # type: ignore[assignment]
            commander.get_project = original_get_project  # type: ignore[assignment]
            commander.command_status = original_status  # type: ignore[assignment]
            commander.command_briefs = original_briefs  # type: ignore[assignment]
            commander.command_diff = original_diff  # type: ignore[assignment]

        self.assertIn("Commander X session brief", text)
        self.assertIn("Technical filenames and local paths are hidden", text)

    def test_session_timeline_summarizes_phases(self) -> None:
        plan = commander.build_work_plan("example", "Fix onboarding", {"verification_commands": ["npm test"]})
        timeline = commander.initial_session_timeline("Fix onboarding", branch_created=True, plan=plan)
        session = {"state": "running", "timeline": timeline}
        lines = commander.timeline_lines(session)
        text = "\n".join(lines)
        self.assertIn("Task received", text)
        self.assertIn("Plan prepared", text)
        self.assertIn("Codex session launched", text)

    def test_work_plan_includes_risk_checks_and_approval_boundaries(self) -> None:
        plan = commander.build_work_plan(
            "example",
            "Deploy production auth fix",
            {"verification_commands": ["npm run test", "npm run build"], "stack": ["Node"]},
        )
        text = commander.format_work_plan(plan)
        self.assertEqual(plan["risk"], "high")
        self.assertIn("Project: example", text)
        self.assertIn("npm run test", text)
        self.assertIn("explicit approval", text)

    def test_contextual_buttons_include_approval_actions(self) -> None:
        rows = commander.contextual_button_rows(
            "Commit prepared for taalam-campaigns on branch main.\nPending approval ID: abc123\n\nApprove with /approve taalam-campaigns abc123"
        )
        labels = [button["text"] for row in rows for button in row]
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertIn("Approve commit", labels)
        self.assertIn("Cancel", labels)
        self.assertIn("cmd:/approve taalam-campaigns abc123", callbacks)
        self.assertIn("cmd:/cancel taalam-campaigns abc123", callbacks)

    def test_mcp_url_request_does_not_attempt_install(self) -> None:
        original_fetch = commander.fetch_mcp_url_text
        original_search = commander.npm_search_mcp_packages
        try:
            commander.fetch_mcp_url_text = lambda url: (True, "Meta Ads AI connectors", "Read 23 bytes")  # type: ignore[assignment]
            commander.npm_search_mcp_packages = lambda query, limit=5: ([], f"Searched npm for: {query} mcp")  # type: ignore[assignment]
            text = commander.command_mcp(["request", "https://www.facebook.com/business/news/meta-ads-ai-connectors"])
        finally:
            commander.fetch_mcp_url_text = original_fetch  # type: ignore[assignment]
            commander.npm_search_mcp_packages = original_search  # type: ignore[assignment]
        self.assertIn("did not find an explicit", text)
        self.assertIn("Nothing was installed", text)
        self.assertIn("Meta Ads", text)

    def test_mcp_research_extracts_install_command(self) -> None:
        candidates = commander.mcp_install_candidates_from_text(
            "Install with: codex mcp add meta-ads -- npx -y @vendor/mcp-server",
            source="docs",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "meta-ads")
        self.assertEqual(candidates[0]["command"], ["npx", "-y", "@vendor/mcp-server"])
        self.assertEqual(candidates[0]["trust"], "scoped community package")

    def test_mcp_trust_labels_known_vendor_scopes(self) -> None:
        self.assertEqual(commander.mcp_package_trust_label("@modelcontextprotocol/server-github"), "known vendor scope")
        self.assertEqual(commander.mcp_package_trust_label("@unknown/server"), "scoped community package")
        self.assertEqual(commander.mcp_package_trust_label("meta-ads-mcp"), "unscoped community package")

    def test_mcp_url_request_prepares_single_found_command(self) -> None:
        original_fetch = commander.fetch_mcp_url_text
        original_add = commander.add_pending_action
        try:
            commander.fetch_mcp_url_text = lambda url: (True, "codex mcp add vendor -- uvx vendor-mcp", "Read 40 bytes")  # type: ignore[assignment]
            commander.add_pending_action = lambda project_id, action: "abc123"  # type: ignore[assignment]
            text = commander.command_mcp(["request", "https://docs.example.test/mcp"])
        finally:
            commander.fetch_mcp_url_text = original_fetch  # type: ignore[assignment]
            commander.add_pending_action = original_add  # type: ignore[assignment]
        self.assertIn("MCP install prepared for vendor", text)
        self.assertIn("Pending approval ID: abc123", text)
        self.assertIn("codex mcp add vendor -- uvx vendor-mcp", text)

    def test_mcp_request_extracts_inline_install_command(self) -> None:
        original_add = commander.add_pending_action
        try:
            commander.add_pending_action = lambda project_id, action: "xyz789"  # type: ignore[assignment]
            text = commander.command_mcp(["request", "the", "docs", "say", "npx", "-y", "@vendor/mcp-server"])
        finally:
            commander.add_pending_action = original_add  # type: ignore[assignment]
        self.assertIn("MCP install prepared", text)
        self.assertIn("Pending approval ID: xyz789", text)

    def test_mcp_add_requires_safe_runner(self) -> None:
        ok, error = commander.validate_mcp_command(["bash", "-c", "echo hi"])
        self.assertFalse(ok)
        self.assertIn("Allowed MCP runners", error)
        ok, error = commander.validate_mcp_command(["npx", "-y", "@vendor/mcp-server"])
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_polling_treats_connection_reset_as_transient(self) -> None:
        self.assertTrue(commander.is_transient_poll_exception(ConnectionResetError("reset")))
        self.assertFalse(commander.is_transient_poll_exception(ValueError("bug")))

    def test_audit_event_records_sanitized_approval_history(self) -> None:
        original_audit_file = commander.AUDIT_FILE
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.AUDIT_FILE = Path(temp) / "audit_log.json"
                commander.record_audit_event(
                    "example",
                    {
                        "id": "abc123",
                        "type": "commit",
                        "message": "api_key=secret123 Fix issue in C:\\Users\\Name\\repo\\.env",
                        "path": "C:\\Users\\Name\\repo",
                    },
                    "prepared",
                    approval_id="abc123",
                )
                events = commander.audit_data()["events"]
                text = commander.command_audit()
            finally:
                commander.AUDIT_FILE = original_audit_file

        self.assertEqual(events[0]["project"], "example")
        self.assertEqual(events[0]["status"], "prepared")
        self.assertIn("technical path", events[0]["summary"])
        self.assertIn("[REDACTED]", events[0]["summary"])
        self.assertNotIn("C:\\Users", str(events[0]))
        self.assertIn("Approval audit:", text)
        self.assertIn("prepared commit for example", text)

    def test_operator_report_sanitizes_paths_and_secrets(self) -> None:
        payload = {
            "generated_at": "2026-05-01T12:00:00+00:00",
            "source": "test",
            "active_project": "example",
            "assistant_mode": "free",
            "heartbeat": {"enabled": True, "quiet": "23:00-08:00"},
            "sessions": {"example": {"state": "running"}},
            "mission_timeline": [
                {
                    "project": "example",
                    "stage": "Working",
                    "direction": "Editing C:\\Users\\Name\\repo\\secret.py",
                    "blocker": "none",
                    "evidence": ["Checked README.md"],
                    "next_step": "Review config.json",
                }
            ],
            "session_evidence": [
                {
                    "project": "example",
                    "state": "running",
                    "task": "Fix C:\\Users\\Name\\repo\\secret.py",
                    "areas": "src/app.ts",
                    "changed_count": 1,
                    "blocker": "none",
                    "checks": ["python -m py_compile src/app.py"],
                }
            ],
            "session_replay": [
                {
                    "project": "example",
                    "state": "running",
                    "story": "Edited C:\\Users\\Name\\repo\\secret.py and checked README.md",
                    "outcome": "Review config.json before commit",
                    "blocker": "none",
                    "checks": ["python -m py_compile src/app.py"],
                    "next_step": "Review README.md",
                }
            ],
            "operator_playback": [
                {
                    "project": "example",
                    "confidence": "reviewable",
                    "story": "Edited C:\\Users\\Name\\repo\\secret.py",
                    "outcome": "Review config.json before commit",
                    "blocker": "none",
                    "checks": ["python -m py_compile src/app.py"],
                    "pending_approvals": [],
                    "primary_action": "Review README.md",
                }
            ],
            "session_briefs": [
                {
                    "project": "example",
                    "state": "running",
                    "summary": "Editing C:\\Users\\Name\\repo\\secret.py",
                    "task": "Fix api_key=secret123 in settings",
                    "areas": "src/app.ts",
                    "changed_count": 1,
                    "needs_attention": True,
                    "blocker": "token=abc123",
                    "next_step": "Review README.md",
                }
            ],
            "work_feed": [
                {
                    "project": "example",
                    "current_step": "Checking C:\\Users\\Name\\repo\\.env",
                    "detail": "Updated config.json",
                    "next_step": "/watch example",
                }
            ],
            "approvals": [{"project": "example", "id": "abc123", "type": "commit", "message": "Fix secret.py"}],
            "conversation": {"items": [{"direction": "User asked", "summary": "Show C:\\Users\\Name\\repo\\.env"}]},
            "decision_suggestions": [{"title": "Hide files", "note": "No folder/file names by default"}],
            "audit_trail": {
                "items": [
                    {
                        "at": "2026-05-01T12:01:00+00:00",
                        "status": "prepared",
                        "type": "commit",
                        "project": "example",
                        "summary": "Commit C:\\Users\\Name\\repo\\.env with token=abc123",
                    }
                ]
            },
            "recent_images": [{"kind": "photo", "summary": "Visible secret.py", "risk": "medium"}],
            "changes": [{"project": "example", "changed_count": 1, "areas": "src/app.ts"}],
            "recommendations": ["Check C:\\Users\\Name\\repo\\.env before push."],
        }

        text = commander.format_operator_report(payload, source="test")

        self.assertIn("Commander X Operator Report", text)
        self.assertIn("technical path", text)
        self.assertIn("technical file", text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("C:\\Users", text)
        self.assertNotIn("secret.py", text)

    def test_save_operator_report_uses_configured_report_dir(self) -> None:
        original = commander.os.environ.get("COMMANDER_REPORT_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_REPORT_DIR"] = temp
                path = commander.save_operator_report("# Report\napi_key=secret123\n")
            finally:
                if original is None:
                    commander.os.environ.pop("COMMANDER_REPORT_DIR", None)
                else:
                    commander.os.environ["COMMANDER_REPORT_DIR"] = original

            self.assertEqual(path.parent, Path(temp))
            self.assertTrue(path.exists())
            self.assertIn("[REDACTED]", path.read_text(encoding="utf-8"))

    def test_commander_backup_payload_excludes_secrets_paths_and_ids(self) -> None:
        original_projects = commander.projects_config
        original_profiles = commander.profiles_data
        original_computer = commander.computer_tools_config
        original_sessions = commander.sessions_data
        original_tasks = commander.tasks_data
        original_memory = commander.memory_data
        original_audit = commander.audit_data
        original_allowed = commander.allowed_user_ids
        try:
            commander.projects_config = lambda: {  # type: ignore[assignment]
                "projects": {
                    "health": {
                        "name": "Health Companion",
                        "path": "C:\\Users\\Name\\secret\\app",
                        "allowed": True,
                        "aliases": ["health assistant"],
                    }
                }
            }
            commander.profiles_data = lambda: {  # type: ignore[assignment]
                "profiles": {
                    "health": {
                        "objective": "Ship the assistant api_key=secret123",
                        "verification_commands": ["npm test"],
                        "notes": ["Do not expose token=abc123"],
                    }
                }
            }
            commander.computer_tools_config = lambda: {  # type: ignore[assignment]
                "apps": {"private app": ["C:\\Users\\Name\\secret.exe"]},
                "web_shortcuts": {"crm": "https://crm.example.com/home?token=abc123"},
                "safe_roots": ["C:\\Users\\Name"],
            }
            commander.sessions_data = lambda: {"sessions": {"health": {"state": "running", "pending_actions": {"a1": {}}}}}  # type: ignore[assignment]
            commander.tasks_data = lambda: {"tasks": [{"id": "t1"}]}  # type: ignore[assignment]
            commander.memory_data = lambda: {"memories": [{"id": "m1"}]}  # type: ignore[assignment]
            commander.audit_data = lambda: {"events": [{"id": "e1"}]}  # type: ignore[assignment]
            commander.allowed_user_ids = lambda: ["123456789"]  # type: ignore[assignment]

            payload = commander.commander_backup_payload()
        finally:
            commander.projects_config = original_projects  # type: ignore[assignment]
            commander.profiles_data = original_profiles  # type: ignore[assignment]
            commander.computer_tools_config = original_computer  # type: ignore[assignment]
            commander.sessions_data = original_sessions  # type: ignore[assignment]
            commander.tasks_data = original_tasks  # type: ignore[assignment]
            commander.memory_data = original_memory  # type: ignore[assignment]
            commander.audit_data = original_audit  # type: ignore[assignment]
            commander.allowed_user_ids = original_allowed  # type: ignore[assignment]

        text = json.dumps(payload)
        self.assertEqual(payload["setup"]["allowlist"]["allowed_user_count"], 1)
        self.assertTrue(payload["projects"]["health"]["has_local_path"])
        self.assertEqual(payload["computer_tools"]["safe_root_count"], 1)
        self.assertEqual(payload["computer_tools"]["web_shortcuts"]["crm"], "https://crm.example.com/home")
        self.assertNotIn("C:\\Users", text)
        self.assertNotIn("123456789", text)
        self.assertNotIn("token=abc123", text)
        self.assertNotIn("api_key=secret123", text)

    def test_command_backup_saves_sanitized_snapshot(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                text = commander.command_backup(["save"])
                paths = list(Path(temp).glob("commander-x-safe-config-*.json"))
                saved = paths[0].read_text(encoding="utf-8") if paths else ""
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(len(paths), 1)
        self.assertIn("Saved Commander safe config backup", text)
        self.assertIn("commander-x-safe-config-backup", saved)
        self.assertNotIn("sk-", saved)

    def test_backup_restore_check_validates_latest_backup_without_paths(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                commander.save_commander_backup()
                report = commander.backup_restore_check_payload()
                text = commander.format_backup_restore_check(report)
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(report["status"], "ready")
        self.assertIn("Ready for manual restore", text)
        self.assertNotIn(temp, text)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", text)

    def test_backup_restore_check_flags_leaky_backup(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                path = Path(temp) / "commander-x-safe-config-bad.json"
                path.write_text(
                    json.dumps(
                        {
                            "kind": "commander-x-safe-config-backup",
                            "schema_version": 1,
                            "projects": {"bad": {"path": "C:\\Users\\Name\\secret"}},
                            "computer_tools": {"web_shortcuts": {}},
                            "setup": {},
                            "state_summary": {},
                        }
                    ),
                    encoding="utf-8",
                )
                report = commander.backup_restore_check_payload(path.name)
                text = commander.format_backup_restore_check(report)
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(report["status"], "attention")
        self.assertIn("Privacy scan", text)
        self.assertIn("local path", text)
        self.assertNotIn(temp, text)

    def test_backup_restore_plan_is_dry_run_and_summarizes_files(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        payload = {
            "kind": "commander-x-safe-config-backup",
            "schema_version": 1,
            "projects": {
                "health": {
                    "id": "health",
                    "name": "Health Companion",
                    "allowed": True,
                    "aliases": ["health assistant"],
                    "has_local_path": True,
                    "objective": "Build a local health assistant",
                    "done_criteria": [{"text": "Web app works"}],
                    "verification_commands": ["npm test"],
                    "stack": ["React"],
                }
            },
            "computer_tools": {
                "app_names": ["chrome", "notepad"],
                "web_shortcuts": {"dashboard": "https://example.com"},
                "safe_root_count": 1,
            },
            "setup": {"env_readiness": {"core": {"configured": 2, "missing": 1, "total": 3}}, "heartbeat_defaults": {"minutes": 30}},
            "state_summary": {},
        }
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                commander.save_commander_backup(payload)
                plan = commander.backup_restore_plan_payload()
                text = commander.format_backup_restore_plan(plan)
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(plan["status"], "ready")
        self.assertFalse(plan["writes_files"])
        self.assertIn("projects.json", text)
        self.assertIn("project_profiles.json", text)
        self.assertIn("computer_tools.json", text)
        self.assertIn("Files changed: none", text)
        self.assertIn("Health Companion", text)
        self.assertNotIn(temp, text)

    def test_backup_import_preview_drafts_sanitized_config_without_writes(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        payload = {
            "kind": "commander-x-safe-config-backup",
            "schema_version": 1,
            "projects": {
                "health": {
                    "id": "health",
                    "name": "Health Companion",
                    "allowed": True,
                    "aliases": ["health assistant"],
                    "has_local_path": True,
                    "objective": "Build a local health assistant",
                    "done_criteria": [{"text": "Web app works", "done": False}],
                    "verification_commands": ["npm test"],
                    "stack": ["React"],
                }
            },
            "computer_tools": {
                "app_names": ["chrome"],
                "web_shortcuts": {"dashboard": "https://example.com"},
                "safe_root_count": 1,
            },
            "setup": {},
            "state_summary": {},
        }
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                commander.save_commander_backup(payload)
                preview = commander.backup_restore_import_preview_payload(include_drafts=True)
                text = commander.format_backup_restore_import_preview(preview)
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(preview["status"], "ready")
        self.assertFalse(preview["writes_files"])
        self.assertIn("projects.json", text)
        self.assertIn("<REENTER_LOCAL_PROJECT_PATH>", text)
        self.assertIn("<REENTER_APP_COMMAND_OR_PATH>", text)
        self.assertIn("Files changed: none", text)
        self.assertNotIn(temp, text)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", text)

    def test_backup_import_save_writes_ignored_review_artifact_only(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_report_dir = commander.os.environ.get("COMMANDER_REPORT_DIR")
        with tempfile.TemporaryDirectory() as backup_temp, tempfile.TemporaryDirectory() as report_temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = backup_temp
                commander.os.environ["COMMANDER_REPORT_DIR"] = report_temp
                commander.save_commander_backup()
                text = commander.command_backup(["import", "save"])
                paths = list(Path(report_temp).glob("commander-x-backup-import-preview-*.md"))
                saved = paths[0].read_text(encoding="utf-8") if paths else ""
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                if original_report_dir is None:
                    commander.os.environ.pop("COMMANDER_REPORT_DIR", None)
                else:
                    commander.os.environ["COMMANDER_REPORT_DIR"] = original_report_dir

        self.assertEqual(len(paths), 1)
        self.assertIn("Saved backup import review artifact", text)
        self.assertIn("Files changed: none", text)
        self.assertIn("Backup config import preview", saved)
        self.assertNotIn(backup_temp, text + saved)
        self.assertNotIn(report_temp, text + saved)

    def test_backup_import_list_and_open_saved_drafts_hide_paths(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_report_dir = commander.os.environ.get("COMMANDER_REPORT_DIR")
        with tempfile.TemporaryDirectory() as backup_temp, tempfile.TemporaryDirectory() as report_temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = backup_temp
                commander.os.environ["COMMANDER_REPORT_DIR"] = report_temp
                commander.save_commander_backup()
                commander.command_backup(["import", "save"])
                records = commander.saved_backup_import_previews()
                listing = commander.command_backup(["import", "list"])
                opened = commander.command_backup(["import", "open", records[0]["id"]])
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                if original_report_dir is None:
                    commander.os.environ.pop("COMMANDER_REPORT_DIR", None)
                else:
                    commander.os.environ["COMMANDER_REPORT_DIR"] = original_report_dir

        self.assertEqual(len(records), 1)
        self.assertIn("Saved backup import drafts", listing)
        self.assertIn(records[0]["id"], listing)
        self.assertIn("Saved backup import draft", opened)
        self.assertIn("Backup config import preview", opened)
        self.assertNotIn(backup_temp, listing + opened)
        self.assertNotIn(report_temp, listing + opened)

    def test_backup_import_compare_reports_name_differences_without_paths(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_projects = commander.projects_config
        original_profiles = commander.profiles_data
        original_tools = commander.computer_tools_config
        original_apps = commander.app_catalog
        original_shortcuts = commander.safe_web_shortcuts_backup
        payload = {
            "kind": "commander-x-safe-config-backup",
            "schema_version": 1,
            "projects": {
                "health": {"id": "health", "name": "Health", "allowed": True, "has_local_path": True},
                "new-project": {"id": "new-project", "name": "New Project", "allowed": True, "has_local_path": True},
            },
            "computer_tools": {
                "app_names": ["chrome", "notepad"],
                "web_shortcuts": {"dashboard": "https://example.com"},
                "safe_root_count": 1,
            },
            "setup": {},
            "state_summary": {},
        }
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                commander.save_commander_backup(payload)
                commander.projects_config = lambda: {"projects": {"health": {"path": "C:\\Users\\Name\\health", "allowed": True}}}  # type: ignore[assignment]
                commander.profiles_data = lambda: {"profiles": {"health": {"objective": "Build health"}}}  # type: ignore[assignment]
                commander.computer_tools_config = lambda: {"safe_roots": [], "web_shortcuts": {"crm": "https://crm.example.com"}}  # type: ignore[assignment]
                commander.app_catalog = lambda config: {"chrome": ["chrome"]}  # type: ignore[assignment]
                commander.safe_web_shortcuts_backup = lambda config: {"crm": "https://crm.example.com"}  # type: ignore[assignment]
                compare = commander.backup_import_compare_payload()
                text = commander.format_backup_import_compare(compare)
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                commander.projects_config = original_projects  # type: ignore[assignment]
                commander.profiles_data = original_profiles  # type: ignore[assignment]
                commander.computer_tools_config = original_tools  # type: ignore[assignment]
                commander.app_catalog = original_apps  # type: ignore[assignment]
                commander.safe_web_shortcuts_backup = original_shortcuts  # type: ignore[assignment]

        self.assertEqual(compare["status"], "attention")
        self.assertIn("Backup import comparison", text)
        self.assertIn("Missing from current: new-project", text)
        self.assertIn("Extra in current: crm", text)
        self.assertIn("Files changed: none", text)
        self.assertNotIn(temp, text)
        self.assertNotIn("C:\\Users", text)

    def test_backup_import_impact_explains_differences_without_paths(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_projects = commander.projects_config
        original_profiles = commander.profiles_data
        original_tools = commander.computer_tools_config
        original_apps = commander.app_catalog
        original_shortcuts = commander.safe_web_shortcuts_backup
        payload = {
            "kind": "commander-x-safe-config-backup",
            "schema_version": 1,
            "projects": {
                "health": {"id": "health", "name": "Health", "allowed": True, "has_local_path": True},
                "new-project": {"id": "new-project", "name": "New Project", "allowed": True, "has_local_path": True},
            },
            "computer_tools": {
                "app_names": ["chrome", "notepad"],
                "web_shortcuts": {"dashboard": "https://example.com"},
                "safe_root_count": 1,
            },
            "setup": {},
            "state_summary": {},
        }
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                commander.save_commander_backup(payload)
                commander.projects_config = lambda: {"projects": {"health": {"path": "C:\\Users\\Name\\health", "allowed": True}}}  # type: ignore[assignment]
                commander.profiles_data = lambda: {"profiles": {"health": {"objective": "Build health"}}}  # type: ignore[assignment]
                commander.computer_tools_config = lambda: {"safe_roots": [], "web_shortcuts": {"crm": "https://crm.example.com"}}  # type: ignore[assignment]
                commander.app_catalog = lambda config: {"chrome": ["chrome"]}  # type: ignore[assignment]
                commander.safe_web_shortcuts_backup = lambda config: {"crm": "https://crm.example.com"}  # type: ignore[assignment]
                impact = commander.backup_import_impact_payload()
                text = commander.format_backup_import_impact(impact)
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                commander.projects_config = original_projects  # type: ignore[assignment]
                commander.profiles_data = original_profiles  # type: ignore[assignment]
                commander.computer_tools_config = original_tools  # type: ignore[assignment]
                commander.app_catalog = original_apps  # type: ignore[assignment]
                commander.safe_web_shortcuts_backup = original_shortcuts  # type: ignore[assignment]

        self.assertEqual(impact["status"], "review")
        self.assertEqual(impact["primary_risk"], "high")
        self.assertGreaterEqual(impact["risk_summary"]["high"], 1)
        self.assertTrue(impact["review_cards"])
        self.assertIn("Backup import impact", text)
        self.assertIn("Risk groups: high", text)
        self.assertIn("Plain-English meaning", text)
        self.assertIn("Projects Commander knows about", text)
        self.assertIn("Project goals and done criteria", text)
        self.assertIn("Review before allowing device access", text)
        self.assertIn("backup-only: new-project", text)
        self.assertIn("Live config files changed: none", text)
        self.assertNotIn(temp, text)
        self.assertNotIn("C:\\Users", text)

    def test_backup_import_prepare_creates_review_only_approval(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_add = commander.add_pending_action
        actions: list[tuple[str, dict[str, object]]] = []
        with tempfile.TemporaryDirectory() as temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                commander.save_commander_backup()
                commander.add_pending_action = lambda project_id, action: actions.append((project_id, action)) or "abc123"  # type: ignore[assignment]
                text = commander.command_backup(["import", "prepare"])
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                commander.add_pending_action = original_add  # type: ignore[assignment]

        self.assertIn("Backup import apply gate prepared", text)
        self.assertIn("Pending approval ID: abc123", text)
        self.assertIn("What approval will not do", text)
        self.assertEqual(actions[0][0], "commander")
        self.assertEqual(actions[0][1]["type"], "backup_import_apply_review")
        self.assertFalse(actions[0][1]["writes_live_config"])
        self.assertNotIn(temp, text)

    def test_backup_import_pending_response_gets_approval_buttons(self) -> None:
        rows = commander.contextual_button_rows(
            "Backup import apply gate prepared.\nPending approval ID: abc123\n\nApprove with /approve commander abc123"
        )
        labels = [button["text"] for row in rows for button in row]
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertIn("Approve backup import gate", labels)
        self.assertIn("Compare draft", labels)
        self.assertIn("cmd:/approve commander abc123", callbacks)

    def test_backup_import_apply_approval_saves_review_artifact_without_live_config(self) -> None:
        original_backup_dir = commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_report_dir = commander.os.environ.get("COMMANDER_REPORT_DIR")
        original_sessions_file = commander.SESSIONS_FILE
        original_audit_file = commander.AUDIT_FILE
        with tempfile.TemporaryDirectory() as backup_temp, tempfile.TemporaryDirectory() as report_temp, tempfile.TemporaryDirectory() as state_temp:
            try:
                commander.os.environ["COMMANDER_BACKUP_DIR"] = backup_temp
                commander.os.environ["COMMANDER_REPORT_DIR"] = report_temp
                commander.SESSIONS_FILE = Path(state_temp) / "sessions.json"
                commander.AUDIT_FILE = Path(state_temp) / "audit_log.json"
                backup_path = commander.save_commander_backup()
                commander.write_json(
                    commander.SESSIONS_FILE,
                    {
                        "sessions": {
                            "commander": {
                                "state": "idle",
                                "pending_actions": {
                                    "gate1": {
                                        "type": "backup_import_apply_review",
                                        "backup": backup_path.name,
                                        "writes_live_config": False,
                                    }
                                },
                            }
                        }
                    },
                )
                result = commander.execute_pending("commander", "gate1")
                reports = list(Path(report_temp).glob("commander-x-backup-import-preview-*.md"))
                sessions = commander.sessions_data()
            finally:
                if original_backup_dir is None:
                    commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                if original_report_dir is None:
                    commander.os.environ.pop("COMMANDER_REPORT_DIR", None)
                else:
                    commander.os.environ["COMMANDER_REPORT_DIR"] = original_report_dir
                commander.SESSIONS_FILE = original_sessions_file
                commander.AUDIT_FILE = original_audit_file

        self.assertIn("Backup import apply gate approved", result)
        self.assertIn("Live Commander config files changed: none", result)
        self.assertEqual(len(reports), 1)
        self.assertEqual(sessions["sessions"]["commander"]["pending_actions"], {})
        self.assertNotIn(backup_temp, result)
        self.assertNotIn(report_temp, result)

    def test_service_helpers_hide_paths_and_detect_processes(self) -> None:
        self.assertEqual(
            commander.service_process_state(["123 python.exe python commander.py --poll"], "commander.py --poll"),
            "running, PID 123",
        )
        self.assertEqual(commander.service_process_state([], "dashboard.py"), "not found")
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "service.log"
            log.write_text("ignored\nTraceback from C:\\Users\\someone\\secret\\file.py\n", encoding="utf-8")
            line = commander.service_log_line(log, ["Traceback"])
        self.assertIn("[local path]", line)
        self.assertNotIn("someone", line)

    def test_command_service_tolerates_process_scan_timeout(self) -> None:
        original_process_lines = commander.computer_process_lines
        original_service_log_line = commander.service_log_line
        try:
            commander.computer_process_lines = (  # type: ignore[assignment]
                lambda terms, timeout=8: (_ for _ in ()).throw(subprocess.TimeoutExpired(["powershell"], timeout))
            )
            commander.service_log_line = lambda path, patterns=None: "empty"  # type: ignore[assignment]

            text = commander.command_service()
        finally:
            commander.computer_process_lines = original_process_lines  # type: ignore[assignment]
            commander.service_log_line = original_service_log_line  # type: ignore[assignment]

        self.assertIn("Commander X service status", text)
        self.assertIn("Process scan timed out", text)
        self.assertIn("No secrets", text)

    def test_inbox_items_include_pending_approvals(self) -> None:
        original_sessions = commander.sessions_data
        original_refresh = commander.refresh_session_states
        original_tasks = commander.tasks_data
        original_recs = commander.recommendation_items
        try:
            commander.refresh_session_states = lambda: None  # type: ignore[assignment]
            commander.sessions_data = lambda: {  # type: ignore[assignment]
                "sessions": {
                    "example": {
                        "state": "finished_unknown",
                        "pending_actions": {
                            "abc": {"type": "commit", "branch": "main", "message": "Test"}
                        },
                    }
                }
            }
            commander.tasks_data = lambda: {"tasks": []}  # type: ignore[assignment]
            commander.recommendation_items = lambda user_id=None, limit=8: []  # type: ignore[assignment]
            inbox = commander.inbox_items(user_id="1")
            self.assertEqual(inbox[0]["kind"], "approval")
            self.assertIn("/approve example abc", inbox[0]["detail"])
        finally:
            commander.sessions_data = original_sessions  # type: ignore[assignment]
            commander.refresh_session_states = original_refresh  # type: ignore[assignment]
            commander.tasks_data = original_tasks  # type: ignore[assignment]
            commander.recommendation_items = original_recs  # type: ignore[assignment]

    def test_inbox_items_summarize_and_dedupe_failed_autopilot_tasks(self) -> None:
        original_sessions = commander.sessions_data
        original_tasks = commander.tasks_data
        original_recs = commander.recommendation_items
        original_pending = commander.pending_approvals
        original_get_project = commander.get_project
        original_label = commander.project_label
        long_task = (
            "Autonomous continuation for Health Companion AI. Continue from the completed and verified local checkpoints. "
            "Focus only on Definition-of-Done criterion 5: Patient web experience supports Arabic-first onboarding. "
            "Build the local product capability, update or add tests, run the relevant verification commands, and leave clear evidence."
        )
        try:
            commander.pending_approvals = lambda: []  # type: ignore[assignment]
            commander.sessions_data = lambda: {"sessions": {}}  # type: ignore[assignment]
            commander.tasks_data = lambda: {  # type: ignore[assignment]
                "tasks": [
                    {"id": "a1", "project": "health", "status": "failed", "title": long_task},
                    {"id": "a2", "project": "health", "status": "failed", "title": long_task},
                ]
            }
            commander.recommendation_items = lambda user_id=None, limit=8: []  # type: ignore[assignment]
            commander.get_project = lambda project_id: {"name": "Health Companion AI"} if project_id == "health" else None  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            inbox = commander.inbox_items(user_id="1")
        finally:
            commander.sessions_data = original_sessions  # type: ignore[assignment]
            commander.tasks_data = original_tasks  # type: ignore[assignment]
            commander.recommendation_items = original_recs  # type: ignore[assignment]
            commander.pending_approvals = original_pending  # type: ignore[assignment]
            commander.get_project = original_get_project  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        task_items = [item for item in inbox if item["kind"] == "task"]
        self.assertEqual(len(task_items), 1)
        self.assertIn("Health Companion AI", task_items[0]["title"])
        self.assertIn("Build Health Companion AI", task_items[0]["detail"])
        self.assertNotIn("Definition-of-Done", task_items[0]["detail"])
        self.assertLess(len(task_items[0]["detail"]), 360)

    def test_inbox_task_dedupe_keeps_most_urgent_status(self) -> None:
        original_sessions = commander.sessions_data
        original_tasks = commander.tasks_data
        original_recs = commander.recommendation_items
        original_pending = commander.pending_approvals
        original_get_project = commander.get_project
        original_label = commander.project_label
        title = "Build Health Companion AI from the real PRD with Arabic-first flows."
        try:
            commander.pending_approvals = lambda: []  # type: ignore[assignment]
            commander.sessions_data = lambda: {"sessions": {}}  # type: ignore[assignment]
            commander.tasks_data = lambda: {  # type: ignore[assignment]
                "tasks": [
                    {"id": "review1", "project": "health", "status": "review", "title": title},
                    {"id": "failed1", "project": "health", "status": "failed", "title": title},
                ]
            }
            commander.recommendation_items = lambda user_id=None, limit=8: []  # type: ignore[assignment]
            commander.get_project = lambda project_id: {"name": "Health Companion AI"} if project_id == "health" else None  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            inbox = commander.inbox_items(user_id="1")
        finally:
            commander.sessions_data = original_sessions  # type: ignore[assignment]
            commander.tasks_data = original_tasks  # type: ignore[assignment]
            commander.recommendation_items = original_recs  # type: ignore[assignment]
            commander.pending_approvals = original_pending  # type: ignore[assignment]
            commander.get_project = original_get_project  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        task_items = [item for item in inbox if item["kind"] == "task"]
        self.assertEqual(len(task_items), 1)
        self.assertIn("blocked: Health Companion AI", task_items[0]["title"])
        self.assertIn("/queue done failed1", task_items[0]["detail"])
        self.assertIn("1 similar queue item hidden", task_items[0]["detail"])
        self.assertNotIn("review1", task_items[0]["detail"])

    def test_tasks_summary_is_owner_readable_and_deduped(self) -> None:
        original_refresh = commander.refresh_session_states
        original_sync = commander.sync_tasks_with_sessions
        original_tasks = commander.tasks_data
        original_get_project = commander.get_project
        original_label = commander.project_label
        raw_prompt = (
            "Autonomous continuation for Health Companion AI. Continue from the completed and verified local checkpoints. "
            "Focus only on Definition-of-Done criterion 5: Patient web experience supports Arabic-first onboarding. "
            "Build the local product capability, update or add tests, run the relevant verification commands, and leave clear evidence."
        )
        try:
            commander.refresh_session_states = lambda: None  # type: ignore[assignment]
            commander.sync_tasks_with_sessions = lambda: None  # type: ignore[assignment]
            commander.tasks_data = lambda: {  # type: ignore[assignment]
                "tasks": [
                    {"id": "review1", "project": "health", "status": "review", "title": raw_prompt},
                    {"id": "failed1", "project": "health", "status": "failed", "title": raw_prompt},
                ]
            }
            commander.get_project = lambda project_id: {"name": "Health Companion AI"} if project_id == "health" else None  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            text = commander.tasks_summary()
        finally:
            commander.refresh_session_states = original_refresh  # type: ignore[assignment]
            commander.sync_tasks_with_sessions = original_sync  # type: ignore[assignment]
            commander.tasks_data = original_tasks  # type: ignore[assignment]
            commander.get_project = original_get_project  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        self.assertIn("[failed1] blocked: Health Companion AI", text)
        self.assertIn("Build Health Companion AI from the real PRD", text)
        self.assertIn("1 similar queue item hidden", text)
        self.assertNotIn("Definition-of-Done", text)
        self.assertNotIn("review1", text)

    def test_queue_cleanup_preview_and_apply_archive_duplicates(self) -> None:
        original_tasks = commander.tasks_data
        original_save = commander.save_tasks
        original_audit = commander.record_audit_event
        original_get_project = commander.get_project
        original_label = commander.project_label
        data = {
            "tasks": [
                {"id": "queued1", "project": "health", "status": "queued", "title": "Build Health Companion AI"},
                {"id": "failed1", "project": "health", "status": "failed", "title": "Build Health Companion AI"},
                {"id": "other1", "project": "health", "status": "queued", "title": "Write a different test"},
            ]
        }
        saved: list[dict[str, object]] = []
        audit: list[tuple[str, str]] = []
        try:
            commander.tasks_data = lambda: data  # type: ignore[assignment]
            commander.save_tasks = lambda payload: saved.append(payload)  # type: ignore[assignment]
            commander.record_audit_event = lambda project, action, status, approval_id=None, result=None: audit.append((project, status)) or {}  # type: ignore[assignment]
            commander.get_project = lambda project_id: {"name": "Health Companion AI"} if project_id == "health" else None  # type: ignore[assignment]
            commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            preview = commander.command_queue(["cleanup"], user_id="owner")
            applied = commander.command_queue(["cleanup", "apply"], user_id="owner")
        finally:
            commander.tasks_data = original_tasks  # type: ignore[assignment]
            commander.save_tasks = original_save  # type: ignore[assignment]
            commander.record_audit_event = original_audit  # type: ignore[assignment]
            commander.get_project = original_get_project  # type: ignore[assignment]
            commander.project_label = original_label  # type: ignore[assignment]

        self.assertIn("Queue cleanup preview", preview)
        self.assertIn("Nothing changed", preview)
        self.assertEqual(data["tasks"][0]["status"], "archived")
        self.assertEqual(data["tasks"][0]["previous_status"], "queued")
        self.assertEqual(data["tasks"][0]["archived_keep_id"], "failed1")
        self.assertEqual(data["tasks"][1]["status"], "failed")
        self.assertEqual(data["tasks"][2]["status"], "queued")
        self.assertTrue(saved)
        self.assertEqual(audit, [("commander", "completed")])
        self.assertIn("Queue cleanup applied", applied)
        self.assertIn("Archived duplicates are hidden", applied)


if __name__ == "__main__":
    unittest.main()
