from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import commander
from commanderx.browser import PageSummaryParser, format_inspection, BrowserInspection
from commanderx.clickup_api import filter_tasks, format_tasks, settings_from_env
from commanderx.cleanup import bytes_to_mb, format_cleanup_scan
from commanderx.computer import app_catalog, normalize_url


class ComputerToolTests(unittest.TestCase):
    def test_normalize_url_adds_https(self) -> None:
        self.assertEqual(normalize_url("example.com"), "https://example.com")
        self.assertEqual(normalize_url("https://example.com"), "https://example.com")

    def test_app_catalog_merges_custom_apps(self) -> None:
        apps = app_catalog({"apps": {"chrome": ["chrome.exe"], "solo": "solo.exe"}})
        self.assertIn("notepad", apps)
        self.assertEqual(apps["chrome"], ["chrome.exe"])
        self.assertEqual(apps["solo"], ["solo.exe"])

    def test_natural_computer_command_routes_common_actions(self) -> None:
        self.assertEqual(commander.natural_computer_command("visit example.com"), "/open url example.com")
        self.assertEqual(commander.natural_computer_command("inspect website example.com"), "/browser inspect example.com")
        self.assertEqual(commander.natural_computer_command("check clickup campaigns"), "/clickup recent campaigns")
        self.assertEqual(commander.natural_computer_command("what MCPs are available"), "/mcp")
        self.assertEqual(
            commander.natural_computer_command("Can you connect this mcp https://example.com/mcp"),
            "/mcp request Can you connect this mcp https://example.com/mcp",
        )
        self.assertEqual(commander.natural_computer_command("show available skills"), "/skills")
        self.assertEqual(commander.natural_computer_command("run commander doctor"), "/doctor")
        self.assertEqual(commander.natural_computer_command("recover OpenClaw"), "/openclaw recover")
        self.assertEqual(
            commander.natural_computer_command("prepare OpenClaw https://github.com/openclaw/openclaw"),
            "/openclaw prepare https://github.com/openclaw/openclaw",
        )
        self.assertEqual(commander.natural_computer_command("what needs my attention"), "/inbox")
        self.assertEqual(commander.natural_computer_command("show pending approvals"), "/approvals")
        self.assertEqual(commander.natural_computer_command("what changed across projects"), "/changes")
        self.assertEqual(commander.natural_computer_command("watch codex progress"), "/watch")
        self.assertEqual(commander.natural_computer_command("show the run timeline"), "/timeline")
        self.assertEqual(commander.natural_computer_command("show the work plan"), "/plan")
        self.assertEqual(commander.natural_computer_command("check missing env keys"), "/env")
        self.assertEqual(commander.natural_computer_command("show system status"), "/system")
        self.assertEqual(commander.natural_computer_command("show clipboard"), "/clipboard show")
        self.assertEqual(commander.natural_computer_command("show me a cleanup plan"), "/cleanup")
        self.assertEqual(commander.natural_computer_command("give me my morning brief"), "/morning")
        self.assertEqual(commander.natural_computer_command("what should I do next"), "/next")
        self.assertEqual(commander.natural_computer_command("lower the volume"), "/volume down 5")
        self.assertEqual(commander.natural_computer_command("Volume up 20x"), "/volume up 20")
        self.assertEqual(commander.natural_computer_command("Volume to the Max"), "/volume max")
        self.assertEqual(commander.natural_computer_command("take a screenshot"), "/computer screenshot")
        self.assertEqual(commander.natural_computer_command("check codex"), "/computer codex")

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


if __name__ == "__main__":
    unittest.main()
