from __future__ import annotations

import subprocess
import time
import unittest

import dashboard


class DashboardCapabilityTests(unittest.TestCase):
    def test_dashboard_payload_returns_stale_cache_and_schedules_refresh(self) -> None:
        original_cache = dashboard.DASHBOARD_CACHE.copy()
        original_refresh = dashboard.refresh_dashboard_cache_async
        calls: list[bool] = []
        try:
            with dashboard.DASHBOARD_CACHE_LOCK:
                dashboard.DASHBOARD_CACHE.clear()
                dashboard.DASHBOARD_CACHE.update(
                    {
                        "value": {"status": "cached snapshot"},
                        "at": time.monotonic() - dashboard.DASHBOARD_REQUEST_REFRESH_SECONDS - 1,
                        "generated_at": "2026-04-30T00:00:00+00:00",
                        "refreshing": False,
                        "last_error": "",
                        "last_error_at": "",
                    }
                )
            dashboard.refresh_dashboard_cache_async = lambda force=False: calls.append(force) or True  # type: ignore[assignment]

            payload = dashboard.dashboard_payload()
        finally:
            dashboard.refresh_dashboard_cache_async = original_refresh  # type: ignore[assignment]
            with dashboard.DASHBOARD_CACHE_LOCK:
                dashboard.DASHBOARD_CACHE.clear()
                dashboard.DASHBOARD_CACHE.update(original_cache)

        self.assertEqual(payload["status"], "cached snapshot")
        self.assertTrue(payload["dashboard_cache"]["stale"])
        self.assertEqual(calls, [True])

    def test_dashboard_payload_falls_back_while_first_snapshot_warms(self) -> None:
        original_cache = dashboard.DASHBOARD_CACHE.copy()
        original_refresh = dashboard.refresh_dashboard_cache_async
        calls: list[bool] = []
        try:
            with dashboard.DASHBOARD_CACHE_LOCK:
                dashboard.DASHBOARD_CACHE.clear()
                dashboard.DASHBOARD_CACHE.update(
                    {
                        "value": None,
                        "at": 0.0,
                        "generated_at": "",
                        "refreshing": False,
                        "last_error": "",
                        "last_error_at": "",
                    }
                )
            dashboard.refresh_dashboard_cache_async = lambda force=False: calls.append(force) or True  # type: ignore[assignment]

            payload = dashboard.dashboard_payload()
        finally:
            dashboard.refresh_dashboard_cache_async = original_refresh  # type: ignore[assignment]
            with dashboard.DASHBOARD_CACHE_LOCK:
                dashboard.DASHBOARD_CACHE.clear()
                dashboard.DASHBOARD_CACHE.update(original_cache)

        self.assertIn("warming up", payload["status"])
        self.assertEqual(payload["doctor"]["score"], "warming")
        self.assertTrue(payload["dashboard_cache"]["stale"])
        self.assertEqual(calls, [True])

    def test_cached_mcp_summary_uses_dashboard_timeout_and_cache(self) -> None:
        original_run = dashboard.commander.run_command
        original_args = dashboard.commander.codex_command_args
        original_cache = dashboard.MCP_CACHE.copy()
        calls: list[int] = []
        try:
            dashboard.MCP_CACHE["value"] = None
            dashboard.MCP_CACHE["at"] = 0.0
            dashboard.commander.codex_command_args = lambda args: ["codex", *args]  # type: ignore[assignment]
            dashboard.commander.run_command = (  # type: ignore[assignment]
                lambda args, timeout=60: calls.append(timeout)
                or subprocess.CompletedProcess(args, 0, "context7 ready\n", "")
            )

            first = dashboard.cached_mcp_summary()
            second = dashboard.cached_mcp_summary()
        finally:
            dashboard.commander.run_command = original_run  # type: ignore[assignment]
            dashboard.commander.codex_command_args = original_args  # type: ignore[assignment]
            dashboard.MCP_CACHE.clear()
            dashboard.MCP_CACHE.update(original_cache)

        self.assertEqual(first, "context7 ready")
        self.assertEqual(second, "context7 ready")
        self.assertEqual(calls, [dashboard.MCP_TIMEOUT_SECONDS])

    def test_safe_openclaw_dashboard_payload_contains_detector_failures(self) -> None:
        original_openclaw = dashboard.openclaw_dashboard_payload
        try:
            dashboard.openclaw_dashboard_payload = (  # type: ignore[assignment]
                lambda: (_ for _ in ()).throw(TimeoutError("token=abc123 C:\\Users\\Name\\repo\\.env"))
            )

            payload = dashboard.safe_openclaw_dashboard_payload()
        finally:
            dashboard.openclaw_dashboard_payload = original_openclaw  # type: ignore[assignment]

        self.assertEqual(payload["state"], "unavailable")
        self.assertIn("technical path", payload["launcher_error"])
        self.assertIn("[REDACTED]", payload["launcher_error"])
        self.assertNotIn("C:\\Users", payload["launcher_error"])

    def test_fallback_dashboard_payload_includes_work_feed_shape(self) -> None:
        payload = dashboard.fallback_dashboard_payload("warming")
        self.assertIn("session_briefs", payload)
        self.assertEqual(payload["session_briefs"], [])
        self.assertIn("conversation", payload)
        self.assertEqual(payload["conversation"]["items"], [])
        self.assertIn("audit_trail", payload)
        self.assertEqual(payload["audit_trail"]["items"], [])
        self.assertIn("decision_suggestions", payload)
        self.assertEqual(payload["decision_suggestions"], [])
        self.assertIn("mission_timeline", payload)
        self.assertEqual(payload["mission_timeline"], [])
        self.assertIn("session_evidence", payload)
        self.assertEqual(payload["session_evidence"], [])
        self.assertIn("recent_images", payload)
        self.assertEqual(payload["recent_images"], [])
        self.assertIn("work_feed", payload)
        self.assertEqual(payload["work_feed"], [])
        self.assertIn("action_center", payload)
        self.assertEqual(payload["action_center"], [])
        self.assertIn("capabilities", payload)

    def test_dashboard_action_center_combines_decisions_sessions_tasks_and_changes(self) -> None:
        approvals = [
            {
                "project": "example",
                "id": "abc123",
                "type": "push",
                "branch": "main",
                "message": "Push changes",
            }
        ]
        sessions = {
            "runner": {"state": "running", "task": "Fix onboarding"},
            "failed": {"state": "failed", "task": "Audit app"},
        }
        tasks = [{"id": "task1", "project": "queued-app", "status": "queued", "title": "Queued work"}]
        changes = [
            {"project": "changed-app", "changed_count": 3, "areas": "app/user interface (2), tests (1)"}
        ]

        items = dashboard.dashboard_action_center(approvals, sessions, tasks, changes)

        self.assertEqual(items[0]["kind"], "approval")
        self.assertEqual(items[0]["actions"][0]["type"], "approval")
        running = next(item for item in items if item["project"] == "runner")
        self.assertIn({"label": "Stop", "type": "work", "action": "stop", "style": "danger"}, running["actions"])
        queued = next(item for item in items if item["project"] == "queued-app")
        self.assertEqual(queued["actions"][0]["action"], "start")
        changed = next(item for item in items if item["project"] == "changed-app")
        self.assertIn("app/user interface", changed["detail"])
        self.assertNotIn("src/", changed["detail"])

    def test_dashboard_conversation_parses_and_sanitizes_events(self) -> None:
        items = dashboard.dashboard_conversation_items_from_lines(
            [
                "2026-05-01T06:37:49+00:00 123456789 /file app C:\\Users\\Name\\repo\\.env",
                "second line with CAMPAIGN_STRATEGY.md",
                "2026-05-01T06:37:52+00:00 123456789 [reply] Opened C:\\Users\\Name\\repo\\secret.py",
                "2026-05-01T06:38:00+00:00 123456789 [voice/audio message]",
            ]
        )

        self.assertEqual(items[0]["actor"], "Telegram user ...6789")
        self.assertEqual(items[0]["kind"], "user")
        self.assertIn("technical path", items[0]["summary"])
        self.assertNotIn("C:\\Users", items[0]["summary"])
        self.assertNotIn("CAMPAIGN_STRATEGY.md", items[0]["summary"])
        self.assertEqual(items[1]["actor"], "Commander X")
        self.assertEqual(items[1]["kind"], "reply")
        self.assertEqual(items[2]["kind"], "voice")

    def test_dashboard_audit_trail_sanitizes_runtime_events(self) -> None:
        original_audit_data = dashboard.commander.audit_data
        try:
            dashboard.commander.audit_data = lambda: {  # type: ignore[assignment]
                "events": [
                    {
                        "at": "2026-05-01T10:00:00+00:00",
                        "project": "example",
                        "approval_id": "abc123",
                        "type": "commit",
                        "status": "approved",
                        "branch": "main",
                        "summary": "Commit prepared from C:\\Users\\Name\\repo\\secret.py",
                        "result": "Committed C:\\Users\\Name\\repo\\.env",
                    }
                ]
            }
            trail = dashboard.dashboard_audit_trail()
        finally:
            dashboard.commander.audit_data = original_audit_data  # type: ignore[assignment]

        self.assertEqual(trail["counts"]["approved"], 1)
        self.assertEqual(trail["items"][0]["type"], "commit")
        self.assertIn("technical path", trail["items"][0]["summary"])
        self.assertNotIn("C:\\Users", str(trail["items"][0]))

    def test_dashboard_decision_suggestions_propose_safe_memories(self) -> None:
        conversation = {
            "items": [
                {
                    "direction": "User asked",
                    "kind": "user",
                    "summary": "I don't want the heartbeat to send me folder file names. its useless",
                },
                {
                    "direction": "Button pressed",
                    "kind": "button",
                    "summary": "/heartbeat off",
                },
            ]
        }

        suggestions = dashboard.dashboard_decision_suggestions(conversation, memories=[])

        notes = [item["note"] for item in suggestions]
        self.assertTrue(any("hide folder paths and filenames" in note for note in notes))
        self.assertTrue(any("heartbeat quiet/disabled" in note for note in notes))
        self.assertNotIn("C:\\Users", " ".join(notes))

    def test_dashboard_decision_suggestions_skip_existing_memory(self) -> None:
        conversation = {
            "items": [
                {
                    "direction": "User asked",
                    "kind": "user",
                    "summary": "I don't want folder file names",
                }
            ]
        }
        memories = [
            {
                "note": "Keep routine Telegram and heartbeat updates plain-English: hide folder paths and filenames unless I explicitly ask for technical details."
            }
        ]

        suggestions = dashboard.dashboard_decision_suggestions(conversation, memories=memories)

        self.assertFalse(any(item["id"] == "hide-technical-names" for item in suggestions))

    def test_dashboard_recent_images_sanitizes_user_image_context(self) -> None:
        users = {
            "123456789": {
                "last_image": {
                    "at": "2026-05-01T08:00:00+00:00",
                    "kind": "photo",
                    "summary": "Login error",
                    "visible_text": "secret=C:\\Users\\Name\\repo\\.env",
                    "likely_intent": "debug",
                    "risk": "medium",
                    "suggested_commands": ["/watch example", "not-a-command", "/run bad"],
                }
            }
        }

        images = dashboard.dashboard_recent_images(users)

        self.assertEqual(images[0]["user"], "Telegram user ...6789")
        self.assertEqual(images[0]["suggested_commands"], ["/watch example"])
        self.assertIn("technical path", images[0]["visible_text"])
        self.assertNotIn("C:\\Users", images[0]["visible_text"])

    def test_dashboard_recent_images_labels_dashboard_uploads(self) -> None:
        images = dashboard.dashboard_recent_images(
            {
                "dashboard": {
                    "last_image": {
                        "at": "2026-05-01T08:00:00+00:00",
                        "kind": "dashboard upload",
                        "summary": "Local screenshot test",
                        "visible_text": "No secrets",
                        "likely_intent": "test image analysis",
                        "risk": "low",
                        "suggested_commands": ["/status"],
                    }
                }
            }
        )

        self.assertEqual(images[0]["user"], "Dashboard upload")
        self.assertEqual(images[0]["suggested_commands"], ["/status"])

    def test_dashboard_report_action_builds_sanitized_markdown(self) -> None:
        original_payload = dashboard.dashboard_payload
        try:
            dashboard.dashboard_payload = lambda: {  # type: ignore[assignment]
                "generated_at": "2026-05-01T12:00:00+00:00",
                "source": "dashboard",
                "active_project": "example",
                "assistant_mode": "free",
                "heartbeat": {"enabled": False, "quiet": "inactive"},
                "sessions": {"example": {"state": "running"}},
                "mission_timeline": [
                    {
                        "project": "example",
                        "stage": "Working",
                        "direction": "Working in C:\\Users\\Name\\repo\\secret.py",
                        "blocker": "none",
                        "evidence": ["Read README.md"],
                        "next_step": "Review config.json",
                    }
                ],
                "session_evidence": [
                    {
                        "project": "example",
                        "state": "running",
                        "task": "Working in C:\\Users\\Name\\repo\\secret.py",
                        "areas": "src/app.ts",
                        "changed_count": 1,
                        "blocker": "none",
                        "checks": ["python -m py_compile src/app.py"],
                    }
                ],
                "session_briefs": [
                    {
                        "project": "example",
                        "state": "running",
                        "summary": "Working in C:\\Users\\Name\\repo\\secret.py",
                        "task": "Audit onboarding",
                        "areas": "src/app.ts",
                        "changed_count": 1,
                        "needs_attention": False,
                        "blocker": "none",
                        "next_step": "Review README.md",
                    }
                ],
                "work_feed": [],
                "approvals": [],
                "conversation": {"items": [{"direction": "User asked", "summary": "Open C:\\Users\\Name\\repo\\.env"}]},
                "decision_suggestions": [],
                "audit_trail": {"items": []},
                "recent_images": [],
                "changes": [{"project": "example", "changed_count": 1, "areas": "src/app.ts"}],
                "recommendations": [],
            }

            result, status = dashboard.dashboard_report_action({"save": False})
        finally:
            dashboard.dashboard_payload = original_payload  # type: ignore[assignment]

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertIn("Commander X Operator Report", result["text"])
        self.assertIn("technical path", result["text"])
        self.assertNotIn("C:\\Users", result["text"])
        self.assertNotIn("secret.py", result["text"])

    def test_dashboard_image_analyze_rejects_non_image_payload(self) -> None:
        result, status = dashboard.dashboard_image_analyze_action({"data_url": "data:text/plain;base64,aGVsbG8="})

        self.assertEqual(status, 400)
        self.assertFalse(result["ok"])
        self.assertIn("base64 data URL", result["error"])

    def test_dashboard_decision_memory_action_saves_for_active_user(self) -> None:
        original_memory_data = dashboard.commander.memory_data
        original_add_memory = dashboard.commander.add_memory
        original_active_user_id = dashboard.commander.active_user_id
        saved = []
        try:
            dashboard.commander.memory_data = lambda: {"memories": []}  # type: ignore[assignment]
            dashboard.commander.active_user_id = lambda: "owner"  # type: ignore[assignment]
            dashboard.commander.add_memory = (  # type: ignore[assignment]
                lambda note, user_id, scope="user", project_id=None, source="telegram": saved.append(
                    {"id": "abc123", "note": note, "user_id": user_id, "scope": scope, "source": source}
                )
                or saved[-1]
            )

            result, status = dashboard.dashboard_decision_memory_action(
                {
                    "note": "Keep routine updates plain-English unless technical details are requested.",
                    "scope": "user",
                }
            )
        finally:
            dashboard.commander.memory_data = original_memory_data  # type: ignore[assignment]
            dashboard.commander.add_memory = original_add_memory  # type: ignore[assignment]
            dashboard.commander.active_user_id = original_active_user_id  # type: ignore[assignment]

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(saved[0]["user_id"], "owner")
        self.assertEqual(saved[0]["source"], "dashboard-decision")

    def test_capabilities_payload_summarizes_tools_without_secret_values(self) -> None:
        original_computer_tools_config = dashboard.commander.computer_tools_config
        original_app_catalog = dashboard.commander.app_catalog
        original_skill_catalog = dashboard.commander.skill_catalog
        original_plugin_catalog = dashboard.commander.plugin_catalog
        original_clickup_settings = dashboard.commander.clickup_settings_from_env
        original_openclaw_status = dashboard.commander.openclaw_brief_status

        class FakeClickUpSettings:
            configured = True

        try:
            dashboard.commander.computer_tools_config = lambda: {}  # type: ignore[assignment]
            dashboard.commander.app_catalog = lambda config: ["browser", "volume"]  # type: ignore[assignment]
            dashboard.commander.skill_catalog = lambda limit=12: ["playwright", "github"]  # type: ignore[assignment]
            dashboard.commander.plugin_catalog = lambda limit=12: ["GitHub"]  # type: ignore[assignment]
            dashboard.commander.clickup_settings_from_env = lambda: FakeClickUpSettings()  # type: ignore[assignment]
            dashboard.commander.openclaw_brief_status = lambda: "startable"  # type: ignore[assignment]

            payload = dashboard.capabilities_payload()
        finally:
            dashboard.commander.computer_tools_config = original_computer_tools_config  # type: ignore[assignment]
            dashboard.commander.app_catalog = original_app_catalog  # type: ignore[assignment]
            dashboard.commander.skill_catalog = original_skill_catalog  # type: ignore[assignment]
            dashboard.commander.plugin_catalog = original_plugin_catalog  # type: ignore[assignment]
            dashboard.commander.clickup_settings_from_env = original_clickup_settings  # type: ignore[assignment]
            dashboard.commander.openclaw_brief_status = original_openclaw_status  # type: ignore[assignment]

        self.assertEqual(payload["counts"], {"apps": 2, "skills": 2, "plugins": 1})
        self.assertIn("/tools", payload["commands"])
        self.assertIn("OpenClaw status: startable", payload["highlights"])
        self.assertTrue(payload["clickup_configured"])


class DashboardApprovalTests(unittest.TestCase):
    def test_dashboard_project_read_action_dispatches_safe_work_actions(self) -> None:
        original_get_project = dashboard.commander.get_project
        original_watch = dashboard.commander.command_watch
        original_plan = dashboard.commander.command_plan
        original_feed = dashboard.commander.command_feed
        original_briefs = dashboard.commander.command_briefs
        original_evidence = dashboard.commander.session_evidence
        original_changes = dashboard.commander.changed_project_details
        try:
            dashboard.commander.get_project = lambda project: {"allowed": True} if project == "example" else None  # type: ignore[assignment]
            dashboard.commander.command_watch = lambda project, user_id: f"watch {project} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_plan = lambda project, user_id: f"plan {project} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_feed = lambda args, user_id: f"feed {args[0]} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_briefs = lambda args, user_id: f"brief {args[0]} {user_id}"  # type: ignore[assignment]
            dashboard.commander.session_evidence = lambda project: f"evidence {project}"  # type: ignore[assignment]
            dashboard.commander.changed_project_details = lambda limit=30, max_files=0: [  # type: ignore[assignment]
                {"project": "example", "changed_count": 2, "branch": "main", "areas": "app/user interface (2)"}
            ]

            watch, watch_status = dashboard.dashboard_project_read_action("example", "watch")
            plan, plan_status = dashboard.dashboard_project_read_action("example", "plan")
            feed, feed_status = dashboard.dashboard_project_read_action("example", "feed")
            brief, brief_status = dashboard.dashboard_project_read_action("example", "brief")
            evidence, evidence_status = dashboard.dashboard_project_read_action("example", "evidence")
            changes, changes_status = dashboard.dashboard_project_read_action("example", "changes")
            missing, missing_status = dashboard.dashboard_project_read_action("missing", "watch")
        finally:
            dashboard.commander.get_project = original_get_project  # type: ignore[assignment]
            dashboard.commander.command_watch = original_watch  # type: ignore[assignment]
            dashboard.commander.command_plan = original_plan  # type: ignore[assignment]
            dashboard.commander.command_feed = original_feed  # type: ignore[assignment]
            dashboard.commander.command_briefs = original_briefs  # type: ignore[assignment]
            dashboard.commander.session_evidence = original_evidence  # type: ignore[assignment]
            dashboard.commander.changed_project_details = original_changes  # type: ignore[assignment]

        self.assertEqual(watch_status, 200)
        self.assertEqual(watch["text"], "watch example dashboard")
        self.assertEqual(plan_status, 200)
        self.assertEqual(plan["text"], "plan example dashboard")
        self.assertEqual(feed_status, 200)
        self.assertEqual(feed["text"], "feed example dashboard")
        self.assertEqual(brief_status, 200)
        self.assertEqual(brief["text"], "brief example dashboard")
        self.assertEqual(evidence_status, 200)
        self.assertEqual(evidence["text"], "evidence example")
        self.assertEqual(changes_status, 200)
        self.assertIn("Changed work areas: example", changes["text"])
        self.assertIn("app/user interface", changes["text"])
        self.assertNotIn("src/", changes["text"])
        self.assertEqual(missing_status, 404)
        self.assertFalse(missing["ok"])

    def test_dashboard_approval_action_requires_identifiers(self) -> None:
        payload, status = dashboard.dashboard_approval_action({"project": "example"}, "approve")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_dashboard_approval_action_dispatches_approve_and_cancel(self) -> None:
        original_execute = dashboard.commander.execute_pending
        original_cancel = dashboard.commander.command_cancel
        calls: list[tuple[str, str, str]] = []
        try:
            dashboard.commander.execute_pending = lambda project, approval_id: calls.append(("approve", project, approval_id)) or "approved"  # type: ignore[assignment]
            dashboard.commander.command_cancel = lambda project, approval_id: calls.append(("cancel", project, approval_id)) or "cancelled"  # type: ignore[assignment]
            approved, approved_status = dashboard.dashboard_approval_action(
                {"project": "commander", "approval_id": "abc123"},
                "approve",
            )
            cancelled, cancelled_status = dashboard.dashboard_approval_action(
                {"project": "commander", "approval_id": "abc123"},
                "cancel",
            )
        finally:
            dashboard.commander.execute_pending = original_execute  # type: ignore[assignment]
            dashboard.commander.command_cancel = original_cancel  # type: ignore[assignment]
        self.assertEqual(approved_status, 200)
        self.assertEqual(cancelled_status, 200)
        self.assertEqual(approved["text"], "approved")
        self.assertEqual(cancelled["text"], "cancelled")
        self.assertEqual(calls, [("approve", "commander", "abc123"), ("cancel", "commander", "abc123")])


class DashboardTaskTests(unittest.TestCase):
    def test_dashboard_task_action_requires_task_id(self) -> None:
        payload, status = dashboard.dashboard_task_action({}, "start")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_dashboard_task_action_dispatches_queue_commands(self) -> None:
        original_queue = dashboard.commander.command_queue
        calls: list[tuple[list[str], str]] = []
        try:
            dashboard.commander.command_queue = lambda args, user_id: calls.append((args, user_id)) or f"{args[0]} {args[1]}"  # type: ignore[assignment]
            started, start_status = dashboard.dashboard_task_action({"task_id": "task123"}, "start")
            done, done_status = dashboard.dashboard_task_action({"task_id": "task123"}, "done")
            cancelled, cancel_status = dashboard.dashboard_task_action({"task_id": "task123"}, "cancel")
        finally:
            dashboard.commander.command_queue = original_queue  # type: ignore[assignment]
        self.assertEqual(start_status, 200)
        self.assertEqual(done_status, 200)
        self.assertEqual(cancel_status, 200)
        self.assertEqual(started["text"], "start task123")
        self.assertEqual(done["text"], "done task123")
        self.assertEqual(cancelled["text"], "cancel task123")
        self.assertEqual(
            calls,
            [
                (["start", "task123"], "dashboard"),
                (["done", "task123"], "dashboard"),
                (["cancel", "task123"], "dashboard"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
