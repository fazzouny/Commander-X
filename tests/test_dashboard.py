from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

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

    def test_dashboard_payload_marks_recent_cached_snapshot_fresh(self) -> None:
        original_cache = dashboard.DASHBOARD_CACHE.copy()
        original_refresh = dashboard.refresh_dashboard_cache_async
        calls: list[bool] = []
        try:
            with dashboard.DASHBOARD_CACHE_LOCK:
                dashboard.DASHBOARD_CACHE.clear()
                dashboard.DASHBOARD_CACHE.update(
                    {
                        "value": {"status": "recent cached snapshot"},
                        "at": time.monotonic() - dashboard.DASHBOARD_CACHE_SECONDS - 1,
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

        self.assertEqual(payload["status"], "recent cached snapshot")
        self.assertFalse(payload["dashboard_cache"]["stale"])
        self.assertEqual(calls, [])

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
        self.assertIn("session_replay", payload)
        self.assertEqual(payload["session_replay"], [])
        self.assertIn("operator_playback", payload)
        self.assertEqual(payload["operator_playback"], [])
        self.assertIn("project_completion", payload)
        self.assertEqual(payload["project_completion"], [])
        self.assertIn("owner_reviews", payload)
        self.assertEqual(payload["owner_reviews"], [])
        self.assertIn("autopilot", payload)
        self.assertEqual(payload["autopilot"], [])
        self.assertIn("recent_images", payload)
        self.assertEqual(payload["recent_images"], [])
        self.assertIn("work_feed", payload)
        self.assertEqual(payload["work_feed"], [])
        self.assertIn("action_center", payload)
        self.assertEqual(payload["action_center"], [])
        self.assertIn("service_health", payload)
        self.assertEqual(payload["service_health"]["overall"], "checking")
        self.assertIn("capabilities", payload)
        self.assertIn("setup_status", payload)
        self.assertTrue(payload["setup_status"])
        self.assertIn("title", payload["setup_status"][0])

    def test_lightweight_dashboard_session_cards_do_not_need_log_scans(self) -> None:
        sessions = {
            "example": {
                "state": "completed",
                "task": "Finish local milestone",
                "work_plan": {"risk": "low", "expected_checks": ["python -m unittest"]},
                "verification_results": [{"command": "python -m unittest", "status": "passed"}],
                "branch": "main",
                "timeline": [{"title": "Done", "detail": "Local checks passed"}],
            }
        }
        changes = [{"project": "example", "changed_count": 2, "areas": "app logic"}]
        mission = [{"project": "example", "blocker": "none reported", "freshness": "fresh", "last_activity_minutes": 3}]

        evidence = dashboard.dashboard_session_evidence_cards(sessions, changes, mission)
        replay = dashboard.dashboard_session_replay_cards(evidence, mission)
        playback = dashboard.dashboard_operator_playback_cards(replay, approvals=[], user_id=None)

        self.assertEqual(evidence[0]["project"], "example")
        self.assertEqual(evidence[0]["changed_count"], 2)
        self.assertTrue(evidence[0]["checks"])
        self.assertIn("Finish local milestone", replay[0]["story"])
        self.assertEqual(playback[0]["confidence"], "reviewable")

    def test_dashboard_owner_review_packs_hide_file_ids(self) -> None:
        original_reviews = dashboard.commander.saved_owner_review_packs
        try:
            dashboard.commander.saved_owner_review_packs = lambda limit=8: [  # type: ignore[assignment]
                {
                    "project": "Example Product",
                    "saved_at": "2026-05-04 01:00",
                    "size": "1.1 KB",
                    "filename": "example-owner-review-20260504-010000.md",
                }
            ]

            items = dashboard.dashboard_owner_review_packs()
        finally:
            dashboard.commander.saved_owner_review_packs = original_reviews  # type: ignore[assignment]

        text = str(items)
        self.assertEqual(items[0]["project"], "Example Product")
        self.assertIn("/reviews", items[0]["command"])
        self.assertNotIn("example-owner-review", text)
        self.assertNotIn(".md", text)

    def test_dashboard_autopilot_status_is_lightweight_and_plain_english(self) -> None:
        original_profiles = dashboard.commander.profiles_data
        original_label = dashboard.commander.project_label
        try:
            dashboard.commander.profiles_data = lambda: {  # type: ignore[assignment]
                "profiles": {
                    "example": {
                        "autopilot": {"enabled": True, "interval_minutes": 7},
                        "done_criteria": [
                            {"id": "1", "text": "Backend works", "status": "done", "evidence": "tests passed"},
                            {"id": "2", "text": "Dashboard owner view works", "status": "open", "evidence": ""},
                        ],
                    }
                }
            }
            dashboard.commander.project_label = lambda project_id, project=None, include_id=True: "Example Product"  # type: ignore[assignment]

            rows = dashboard.dashboard_autopilot_status(sessions={})
        finally:
            dashboard.commander.profiles_data = original_profiles  # type: ignore[assignment]
            dashboard.commander.project_label = original_label  # type: ignore[assignment]

        self.assertEqual(rows[0]["project"], "Example Product")
        self.assertTrue(rows[0]["enabled"])
        self.assertTrue(rows[0]["can_start"])
        self.assertEqual(rows[0]["done_criteria"], 1)
        self.assertEqual(rows[0]["total_criteria"], 2)
        self.assertIn("Dashboard owner view works", rows[0]["next_criterion"])
        self.assertIn("/autopilot run", rows[0]["next_action"])
        self.assertEqual(rows[0]["command"], "/autopilot run")

    def test_dashboard_project_completion_cards_include_owner_scorecard(self) -> None:
        original_completion = dashboard.commander.project_completion_card
        original_label = dashboard.commander.project_label
        try:
            dashboard.commander.project_completion_card = lambda project_id, user_id=None: {  # type: ignore[assignment]
                "project": project_id,
                "objective": "Ship a local health assistant owner demo.",
                "verdict": "not done",
                "completion_percent": 85,
                "state": "completed",
                "confidence": "reviewable",
                "criteria": [
                    {"id": "1", "text": "Patient onboarding works", "status": "done", "evidence": "tests passed"},
                    {"id": "2", "text": "Clinician review works", "status": "open", "evidence": ""},
                ],
                "done_criteria": 1,
                "total_criteria": 2,
                "checks": ["python -m unittest"],
                "pending_approvals": [],
                "changed_count": 3,
                "blocker": "none reported",
                "next_step": "Continue work on the open criteria or add proof with /objective done health <number> \"evidence\".",
            }
            dashboard.commander.project_label = lambda project_id, project=None, include_id=True: "Health Companion AI"  # type: ignore[assignment]

            cards = dashboard.dashboard_project_completion_cards(
                [
                    {
                        "project": "health",
                        "state": "completed",
                        "confidence": "reviewable",
                        "checks": ["python -m unittest"],
                        "pending_approvals": [],
                        "changed_count": 3,
                        "blocker": "none reported",
                    }
                ],
                user_id="owner",
            )
        finally:
            dashboard.commander.project_completion_card = original_completion  # type: ignore[assignment]
            dashboard.commander.project_label = original_label  # type: ignore[assignment]

        card = cards[0]
        self.assertEqual(card["project"], "health")
        self.assertEqual(card["project_id"], "health")
        self.assertEqual(card["project_name"], "Health Companion AI")
        self.assertEqual(card["owner_status"], "Still missing agreed outcomes")
        self.assertEqual(card["owner_confidence"], "Medium")
        self.assertIn("1/2 success criteria", card["owner_summary"])
        self.assertIn("1 success criterion still open.", card["owner_attention"])
        self.assertIn("3 changed work area", " ".join(card["owner_attention"]))
        self.assertIn("/objective done health", card["owner_next_action"])
        self.assertFalse(card["owner_can_call_done"])

    def test_dashboard_project_completion_prioritizes_actionable_projects(self) -> None:
        original_completion = dashboard.commander.project_completion_card
        original_label = dashboard.commander.project_label
        completions = {
            "missing": {
                "project": "missing",
                "objective": "",
                "verdict": "objective missing",
                "completion_percent": 0,
                "state": "completed",
                "confidence": "reviewable",
                "criteria": [],
                "done_criteria": 0,
                "total_criteria": 0,
                "checks": [],
                "pending_approvals": [],
                "changed_count": 0,
                "blocker": "none reported",
                "next_step": "Set the intended objective.",
            },
            "health": {
                "project": "health",
                "objective": "Ship the health companion.",
                "verdict": "reviewable, not final",
                "completion_percent": 99,
                "state": "completed",
                "confidence": "reviewable",
                "criteria": [{"id": "1", "text": "Clinical safety works", "status": "done", "evidence": "tests passed"}],
                "done_criteria": 1,
                "total_criteria": 1,
                "checks": ["python -m unittest"],
                "pending_approvals": [],
                "changed_count": 2,
                "blocker": "none reported",
                "next_step": "Review what changed.",
            },
        }
        try:
            dashboard.commander.project_completion_card = lambda project_id, user_id=None: completions[project_id]  # type: ignore[assignment]
            dashboard.commander.project_label = lambda project_id, project=None, include_id=True: {"health": "Health Companion AI", "missing": "Unconfigured Project"}.get(project_id, project_id)  # type: ignore[assignment]

            cards = dashboard.dashboard_project_completion_cards(
                [
                    {"project": "missing", "state": "completed", "confidence": "reviewable", "checks": [], "pending_approvals": [], "changed_count": 0, "blocker": "none reported"},
                    {"project": "health", "state": "completed", "confidence": "blocked", "checks": ["python -m unittest"], "pending_approvals": [], "changed_count": 2, "blocker": "needs owner review"},
                ],
                user_id="owner",
            )
        finally:
            dashboard.commander.project_completion_card = original_completion  # type: ignore[assignment]
            dashboard.commander.project_label = original_label  # type: ignore[assignment]

        self.assertEqual(cards[0]["project_id"], "health")
        self.assertEqual(cards[0]["project_name"], "Health Companion AI")
        self.assertEqual(cards[0]["verdict"], "reviewable, not final")
        self.assertEqual(cards[1]["verdict"], "objective missing")

    def test_dashboard_recommendations_include_autopilot_actions(self) -> None:
        original_autopilot_recs = dashboard.commander.autopilot_recommendation_items
        original_clickup = dashboard.commander.clickup_settings_from_env
        try:
            dashboard.commander.autopilot_recommendation_items = lambda limit=3: [  # type: ignore[assignment]
                "Autopilot for Health Companion AI is waiting: no open criteria. Review completion with /done health."
            ]
            dashboard.commander.clickup_settings_from_env = lambda: type("Settings", (), {"configured": True})()  # type: ignore[assignment]

            items = dashboard.dashboard_recommendations(
                user_id="owner",
                changes=[],
                snapshot={"disk": []},
                sessions={},
                openclaw={"state": "unavailable"},
            )
        finally:
            dashboard.commander.autopilot_recommendation_items = original_autopilot_recs  # type: ignore[assignment]
            dashboard.commander.clickup_settings_from_env = original_clickup  # type: ignore[assignment]

        self.assertIn("Autopilot for Health Companion AI", items[0])
        self.assertIn("/done health", items[0])

    def test_dashboard_service_health_flags_transient_poller_issue(self) -> None:
        original_process_lines = dashboard.commander.computer_process_lines
        original_log_line = dashboard.commander.service_log_line
        original_audit_data = dashboard.commander.audit_data
        try:
            dashboard.commander.computer_process_lines = lambda markers, timeout=4: [  # type: ignore[assignment]
                "100 commander.py --poll",
                "200 dashboard.py",
            ]
            dashboard.commander.audit_data = lambda: {  # type: ignore[assignment]
                "events": [
                    {
                        "at": "2026-05-04T16:49:00+00:00",
                        "type": "service_restart",
                        "status": "checked",
                        "summary": "Dry run from C:\\Users\\Name\\repo\\.env",
                    }
                ]
            }

            def log_line(path, patterns=None) -> str:
                if path.name == "commander-service.out.log":
                    return "Polling error: handshake operation timed out"
                if path.name == "dashboard.out.log":
                    return '2026-05-04 dashboard 127.0.0.1 "GET /api/dashboard HTTP/1.1" 200 -'
                return "empty"

            dashboard.commander.service_log_line = log_line  # type: ignore[assignment]

            health = dashboard.dashboard_service_health()
        finally:
            dashboard.commander.computer_process_lines = original_process_lines  # type: ignore[assignment]
            dashboard.commander.service_log_line = original_log_line  # type: ignore[assignment]
            dashboard.commander.audit_data = original_audit_data  # type: ignore[assignment]

        self.assertEqual(health["overall"], "warn")
        self.assertIn("running", health["summary"])
        self.assertEqual(health["items"][0]["label"], "Telegram control")
        self.assertEqual(health["items"][0]["status"], "warn")
        self.assertIn("temporary connection issue", health["items"][0]["detail"])
        self.assertNotIn("commander.py --poll", str(health))
        self.assertEqual(health["recovery"][0]["status"], "checked")
        self.assertIn("technical path", health["recovery"][0]["summary"])
        self.assertNotIn("C:\\Users", str(health))
        self.assertIsNone(health["restart_cooldown"])

    def test_dashboard_service_recovery_history_filters_and_sanitizes_events(self) -> None:
        original_audit_data = dashboard.commander.audit_data
        try:
            dashboard.commander.audit_data = lambda: {  # type: ignore[assignment]
                "events": [
                    {"type": "commit", "status": "approved", "summary": "Commit ok"},
                    {
                        "at": "2026-05-04T16:49:00+00:00",
                        "type": "service_restart",
                        "status": "scheduled",
                        "summary": "Restarted from C:\\Users\\Name\\repo\\.env",
                    },
                ]
            }

            history = dashboard.dashboard_service_recovery_history()
        finally:
            dashboard.commander.audit_data = original_audit_data  # type: ignore[assignment]

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "scheduled")
        self.assertIn("technical path", history[0]["summary"])
        self.assertNotIn("C:\\Users", str(history))

    def test_recent_service_restart_schedule_detects_cooldown(self) -> None:
        original_audit_data = dashboard.commander.audit_data
        try:
            dashboard.commander.audit_data = lambda: {  # type: ignore[assignment]
                "events": [
                    {
                        "at": dashboard.dt.datetime.now(dashboard.dt.timezone.utc).isoformat(timespec="seconds"),
                        "type": "service_restart",
                        "status": "scheduled",
                        "summary": "Restarted from C:\\Users\\Name\\repo\\.env",
                    }
                ]
            }

            cooldown = dashboard.recent_service_restart_schedule(cooldown_seconds=120)
        finally:
            dashboard.commander.audit_data = original_audit_data  # type: ignore[assignment]

        self.assertIsNotNone(cooldown)
        assert cooldown is not None
        self.assertGreater(int(cooldown["remaining_seconds"]), 0)
        self.assertIn("technical path", cooldown["summary"])
        self.assertNotIn("C:\\Users", str(cooldown))

    def test_dashboard_recommendations_include_service_health_attention(self) -> None:
        original_autopilot_recs = dashboard.commander.autopilot_recommendation_items
        original_clickup = dashboard.commander.clickup_settings_from_env
        try:
            dashboard.commander.autopilot_recommendation_items = lambda limit=3: []  # type: ignore[assignment]
            dashboard.commander.clickup_settings_from_env = lambda: type("Settings", (), {"configured": True})()  # type: ignore[assignment]

            items = dashboard.dashboard_recommendations(
                user_id="owner",
                changes=[],
                snapshot={"disk": []},
                sessions={},
                openclaw={"state": "unavailable"},
                service_health={"overall": "warn", "summary": "Telegram had a temporary timeout."},
            )
        finally:
            dashboard.commander.autopilot_recommendation_items = original_autopilot_recs  # type: ignore[assignment]
            dashboard.commander.clickup_settings_from_env = original_clickup  # type: ignore[assignment]

        self.assertIn("Commander service needs attention", items[0])
        self.assertIn("Telegram had a temporary timeout", items[0])

    def test_dashboard_service_restart_action_is_guarded_and_supports_dry_run(self) -> None:
        original_command = dashboard.service_restart_command
        original_schedule = dashboard.schedule_service_restart
        original_invalidate = dashboard.invalidate_dashboard_cache
        original_audit = dashboard.record_service_restart_audit
        original_recent = dashboard.recent_service_restart_schedule
        scheduled: list[bool] = []
        invalidated: list[bool] = []
        audit_events: list[tuple[str, str]] = []
        try:
            dashboard.service_restart_command = lambda: ["powershell", "-File", "start-services.ps1", "-Restart"]  # type: ignore[assignment]
            dashboard.schedule_service_restart = lambda: scheduled.append(True)  # type: ignore[assignment]
            dashboard.invalidate_dashboard_cache = lambda: invalidated.append(True)  # type: ignore[assignment]
            dashboard.record_service_restart_audit = lambda status, summary, result=None: audit_events.append((status, summary))  # type: ignore[assignment]
            dashboard.recent_service_restart_schedule = lambda cooldown_seconds=None: None  # type: ignore[assignment]

            bad, bad_status = dashboard.dashboard_service_restart_action({"action": "shell"})
            dry, dry_status = dashboard.dashboard_service_restart_action({"action": "restart", "dry_run": True})
            scheduled_result, scheduled_status = dashboard.dashboard_service_restart_action({"action": "restart"})
        finally:
            dashboard.service_restart_command = original_command  # type: ignore[assignment]
            dashboard.schedule_service_restart = original_schedule  # type: ignore[assignment]
            dashboard.invalidate_dashboard_cache = original_invalidate  # type: ignore[assignment]
            dashboard.record_service_restart_audit = original_audit  # type: ignore[assignment]
            dashboard.recent_service_restart_schedule = original_recent  # type: ignore[assignment]

        self.assertEqual(bad_status, 400)
        self.assertFalse(bad["ok"])
        self.assertEqual(dry_status, 200)
        self.assertFalse(dry["scheduled"])
        self.assertIn("dry run passed", dry["text"])
        self.assertEqual(scheduled_status, 202)
        self.assertTrue(scheduled_result["scheduled"])
        self.assertEqual(scheduled, [True])
        self.assertEqual(invalidated, [True])
        self.assertEqual([item[0] for item in audit_events], ["rejected", "checked", "scheduled"])
        self.assertIn("dry run passed", audit_events[1][1])

    def test_dashboard_service_restart_action_blocks_duplicate_schedule(self) -> None:
        original_command = dashboard.service_restart_command
        original_schedule = dashboard.schedule_service_restart
        original_audit = dashboard.record_service_restart_audit
        original_recent = dashboard.recent_service_restart_schedule
        scheduled: list[bool] = []
        audit_events: list[tuple[str, str]] = []
        try:
            dashboard.service_restart_command = lambda: ["powershell", "-File", "start-services.ps1", "-Restart"]  # type: ignore[assignment]
            dashboard.schedule_service_restart = lambda: scheduled.append(True)  # type: ignore[assignment]
            dashboard.record_service_restart_audit = lambda status, summary, result=None: audit_events.append((status, summary))  # type: ignore[assignment]
            dashboard.recent_service_restart_schedule = lambda cooldown_seconds=None: {"remaining_seconds": 88, "summary": "already scheduled"}  # type: ignore[assignment]

            result, status = dashboard.dashboard_service_restart_action({"action": "restart"})
        finally:
            dashboard.service_restart_command = original_command  # type: ignore[assignment]
            dashboard.schedule_service_restart = original_schedule  # type: ignore[assignment]
            dashboard.record_service_restart_audit = original_audit  # type: ignore[assignment]
            dashboard.recent_service_restart_schedule = original_recent  # type: ignore[assignment]

        self.assertEqual(status, 409)
        self.assertFalse(result["ok"])
        self.assertFalse(result["scheduled"])
        self.assertEqual(result["cooldown_seconds"], 88)
        self.assertEqual(scheduled, [])
        self.assertEqual(audit_events[0][0], "blocked")

    def test_dashboard_service_restart_action_reports_missing_script(self) -> None:
        original_command = dashboard.service_restart_command
        original_audit = dashboard.record_service_restart_audit
        audit_events: list[tuple[str, str]] = []
        try:
            dashboard.service_restart_command = lambda: None  # type: ignore[assignment]
            dashboard.record_service_restart_audit = lambda status, summary, result=None: audit_events.append((status, summary))  # type: ignore[assignment]
            result, status = dashboard.dashboard_service_restart_action({"action": "restart"})
        finally:
            dashboard.service_restart_command = original_command  # type: ignore[assignment]
            dashboard.record_service_restart_audit = original_audit  # type: ignore[assignment]

        self.assertEqual(status, 500)
        self.assertFalse(result["ok"])
        self.assertIn("not available", result["error"])
        self.assertEqual(audit_events[0][0], "failed")
        self.assertIn("restart script", audit_events[0][1])

    def test_dashboard_inbox_uses_owner_task_summaries(self) -> None:
        original_approvals = dashboard.commander.pending_approvals
        original_tasks = dashboard.commander.tasks_data
        original_visible = dashboard.commander.visible_task_records
        original_task_item = dashboard.commander.task_inbox_item
        original_task_key = dashboard.commander.task_inbox_dedupe_key
        try:
            task = {"id": "a1", "project": "health", "status": "failed", "title": "long internal prompt"}
            dashboard.commander.pending_approvals = lambda: []  # type: ignore[assignment]
            dashboard.commander.tasks_data = lambda: {"tasks": [task, dict(task, id="a2")]}  # type: ignore[assignment]
            dashboard.commander.visible_task_records = lambda tasks, limit=8: tasks  # type: ignore[assignment]
            dashboard.commander.task_inbox_item = lambda item: {  # type: ignore[assignment]
                "kind": "task",
                "priority": "high",
                "title": "blocked: Health Companion AI",
                "detail": "Build Health Companion AI from the real PRD. Review with /playback health.",
            }
            dashboard.commander.task_inbox_dedupe_key = lambda item: "health:failed:summary"  # type: ignore[assignment]

            items = dashboard.dashboard_inbox(user_id="owner", recommendations=[])
        finally:
            dashboard.commander.pending_approvals = original_approvals  # type: ignore[assignment]
            dashboard.commander.tasks_data = original_tasks  # type: ignore[assignment]
            dashboard.commander.visible_task_records = original_visible  # type: ignore[assignment]
            dashboard.commander.task_inbox_item = original_task_item  # type: ignore[assignment]
            dashboard.commander.task_inbox_dedupe_key = original_task_key  # type: ignore[assignment]

        task_items = [item for item in items if item["kind"] == "task"]
        self.assertEqual(len(task_items), 1)
        self.assertIn("Health Companion AI", task_items[0]["title"])
        self.assertIn("/playback health", task_items[0]["detail"])

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

    def test_dashboard_action_center_surfaces_queue_cleanup_actions(self) -> None:
        tasks = [
            {"id": "queued1", "project": "health", "status": "queued", "title": "Build Health Companion AI"},
            {"id": "failed1", "project": "health", "status": "failed", "title": "Build Health Companion AI"},
        ]

        items = dashboard.dashboard_action_center([], {}, tasks, [])

        cleanup = next(item for item in items if item["kind"] == "queue")
        self.assertEqual(cleanup["project"], "commander")
        self.assertIn("1 duplicate", cleanup["detail"])
        self.assertEqual(cleanup["actions"][0], {"label": "Preview", "type": "queue", "action": "cleanup-preview"})
        self.assertEqual(
            cleanup["actions"][1],
            {"label": "Archive duplicates", "type": "queue", "action": "cleanup-apply", "style": "danger"},
        )

    def test_dashboard_action_center_surfaces_service_health_restart(self) -> None:
        service_health = {
            "overall": "warn",
            "summary": "Commander is running, but one service signal should be watched.",
            "restart_cooldown": None,
        }

        items = dashboard.dashboard_action_center([], {}, [], [], service_health=service_health)

        service = items[0]
        self.assertEqual(service["kind"], "service")
        self.assertEqual(service["project"], "commander")
        self.assertEqual(service["actions"][0]["type"], "service")
        self.assertEqual(service["actions"][0]["action"], "restart")
        self.assertNotIn("disabled", service["actions"][0])

    def test_dashboard_action_center_disables_service_restart_during_cooldown(self) -> None:
        service_health = {
            "overall": "bad",
            "summary": "Commander needs attention before relying on remote control.",
            "restart_cooldown": {"remaining_seconds": 87, "summary": "already scheduled"},
        }

        items = dashboard.dashboard_action_center([], {}, [], [], service_health=service_health)

        action = items[0]["actions"][0]
        self.assertEqual(items[0]["priority"], "high")
        self.assertEqual(action["type"], "service")
        self.assertTrue(action["disabled"])
        self.assertIn("87s", action["label"])

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
                "session_replay": [
                    {
                        "project": "example",
                        "state": "running",
                        "story": "Worked inside C:\\Users\\Name\\repo\\secret.py",
                        "outcome": "Review README.md before commit",
                        "blocker": "none",
                        "checks": ["python -m py_compile src/app.py"],
                        "next_step": "Review config.json",
                    }
                ],
                "operator_playback": [
                    {
                        "project": "example",
                        "confidence": "reviewable",
                        "story": "Worked inside C:\\Users\\Name\\repo\\secret.py",
                        "outcome": "Review README.md before commit",
                        "blocker": "none",
                        "checks": ["python -m py_compile src/app.py"],
                        "pending_approvals": [],
                        "primary_action": "Review config.json",
                    }
                ],
                "project_completion": [
                    {
                        "project": "example",
                        "verdict": "not done",
                        "completion_percent": 70,
                        "objective": "Fix C:\\Users\\Name\\repo\\secret.py",
                        "done_criteria": 1,
                        "total_criteria": 2,
                        "blocker": "none",
                        "next_step": "Review README.md",
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

    def test_dashboard_backup_action_saves_and_lists_without_paths(self) -> None:
        original_backup_dir = dashboard.commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                saved, saved_status = dashboard.dashboard_backup_action({"action": "save"})
                listed, listed_status = dashboard.dashboard_backup_action({"action": "list"})
                payload = dashboard.dashboard_backups_payload()
            finally:
                if original_backup_dir is None:
                    dashboard.commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(saved_status, 200)
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["saved"])
        self.assertIn("Saved Commander safe config backup", saved["text"])
        self.assertNotIn(temp, saved["text"])
        self.assertEqual(listed_status, 200)
        self.assertTrue(listed["ok"])
        self.assertEqual(len(payload["items"]), 1)
        self.assertIn("commander-x-safe-config-", payload["items"][0]["name"])
        self.assertNotIn(temp, str(payload))

    def test_dashboard_backup_preview_includes_restore_guidance(self) -> None:
        result, status = dashboard.dashboard_backup_action({"action": "preview"})

        self.assertEqual(status, 200)
        self.assertFalse(result["saved"])
        self.assertIn("Commander safe config backup", result["text"])
        self.assertIn("restore_guidance", result["backups"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", str(result))

    def test_dashboard_backup_check_returns_restore_report(self) -> None:
        original_backup_dir = dashboard.commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                dashboard.commander.save_commander_backup()
                result, status = dashboard.dashboard_backup_action({"action": "check"})
            finally:
                if original_backup_dir is None:
                    dashboard.commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertIn("Backup restore check", result["text"])
        self.assertIn("restore_check", result["backups"])
        self.assertNotIn(temp, str(result))

    def test_dashboard_backup_plan_returns_dry_run(self) -> None:
        original_backup_dir = dashboard.commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                dashboard.commander.save_commander_backup()
                result, status = dashboard.dashboard_backup_action({"action": "plan"})
            finally:
                if original_backup_dir is None:
                    dashboard.commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertIn("Backup restore dry run", result["text"])
        self.assertIn("Files changed: none", result["text"])
        self.assertIn("restore_plan", result["backups"])
        self.assertNotIn(temp, str(result))

    def test_dashboard_backup_import_preview_returns_draft_without_writes(self) -> None:
        original_backup_dir = dashboard.commander.os.environ.get("COMMANDER_BACKUP_DIR")
        with tempfile.TemporaryDirectory() as temp:
            try:
                dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = temp
                dashboard.commander.save_commander_backup()
                result, status = dashboard.dashboard_backup_action({"action": "import"})
            finally:
                if original_backup_dir is None:
                    dashboard.commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertIn("Backup config import preview", result["text"])
        self.assertIn("Files changed: none", result["text"])
        self.assertIn("import_preview", result)
        self.assertIn("import_preview", result["backups"])
        self.assertFalse(result["import_preview"]["writes_files"])
        self.assertNotIn(temp, str(result))

    def test_dashboard_backup_save_import_writes_review_artifact_only(self) -> None:
        original_backup_dir = dashboard.commander.os.environ.get("COMMANDER_BACKUP_DIR")
        original_report_dir = dashboard.commander.os.environ.get("COMMANDER_REPORT_DIR")
        with tempfile.TemporaryDirectory() as backup_temp, tempfile.TemporaryDirectory() as report_temp:
            try:
                dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = backup_temp
                dashboard.commander.os.environ["COMMANDER_REPORT_DIR"] = report_temp
                dashboard.commander.save_commander_backup()
                result, status = dashboard.dashboard_backup_action({"action": "import-save"})
                paths = list(Path(report_temp).glob("commander-x-backup-import-preview-*.md"))
            finally:
                if original_backup_dir is None:
                    dashboard.commander.os.environ.pop("COMMANDER_BACKUP_DIR", None)
                else:
                    dashboard.commander.os.environ["COMMANDER_BACKUP_DIR"] = original_backup_dir
                if original_report_dir is None:
                    dashboard.commander.os.environ.pop("COMMANDER_REPORT_DIR", None)
                else:
                    dashboard.commander.os.environ["COMMANDER_REPORT_DIR"] = original_report_dir

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertTrue(result["saved"])
        self.assertEqual(len(paths), 1)
        self.assertIn("Saved backup import review artifact", result["text"])
        self.assertIn("Files changed: none", result["text"])
        self.assertNotIn(backup_temp, str(result))
        self.assertNotIn(report_temp, str(result))

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

    def test_dashboard_web_shortcut_action_manages_custom_shortcuts_safely(self) -> None:
        original_file = dashboard.commander.COMPUTER_TOOLS_FILE
        original_audit = dashboard.commander.record_audit_event
        audit_events: list[tuple[str, str]] = []
        try:
            with tempfile.TemporaryDirectory() as temp:
                dashboard.commander.COMPUTER_TOOLS_FILE = Path(temp) / "computer_tools.json"
                dashboard.commander.record_audit_event = (  # type: ignore[assignment]
                    lambda project, action, status, approval_id=None, result=None: audit_events.append((project, status)) or {}
                )

                saved, saved_status = dashboard.dashboard_web_shortcut_action(
                    {"action": "add", "name": "Company CRM", "url": "https://crm.example.com/home?token=abc123"}
                )
                unsafe, unsafe_status = dashboard.dashboard_web_shortcut_action(
                    {"action": "add", "name": "Bad", "url": "file:///C:/secret"}
                )
                removed, removed_status = dashboard.dashboard_web_shortcut_action(
                    {"action": "delete", "name": "Company CRM"}
                )
        finally:
            dashboard.commander.COMPUTER_TOOLS_FILE = original_file
            dashboard.commander.record_audit_event = original_audit  # type: ignore[assignment]

        self.assertEqual(saved_status, 200)
        self.assertTrue(saved["ok"])
        self.assertIn("company crm", saved["text"])
        self.assertNotIn("token=abc123", saved["text"])
        self.assertEqual(unsafe_status, 400)
        self.assertFalse(unsafe["ok"])
        self.assertEqual(removed_status, 200)
        self.assertTrue(removed["ok"])
        self.assertEqual(audit_events, [("commander", "completed"), ("commander", "completed")])

    def test_dashboard_web_shortcuts_payload_marks_custom_entries(self) -> None:
        rows = dashboard.dashboard_web_shortcuts_payload(
            {"web_shortcuts": {"Company CRM": "https://crm.example.com/home?token=abc123"}}
        )
        custom = next(item for item in rows if item["name"] == "company crm")

        self.assertEqual(custom["source"], "custom")
        self.assertEqual(custom["url"], "https://crm.example.com/home")
        self.assertEqual(custom["command"], "/open company crm")


class DashboardApprovalTests(unittest.TestCase):
    def test_dashboard_project_read_action_dispatches_safe_work_actions(self) -> None:
        original_get_project = dashboard.commander.get_project
        original_watch = dashboard.commander.command_watch
        original_plan = dashboard.commander.command_plan
        original_feed = dashboard.commander.command_feed
        original_briefs = dashboard.commander.command_briefs
        original_evidence = dashboard.commander.session_evidence
        original_replay = dashboard.commander.session_replay
        original_playback = dashboard.commander.operator_playback
        original_done = dashboard.commander.project_completion
        original_review = dashboard.commander.command_review
        original_changes = dashboard.commander.changed_project_details
        try:
            dashboard.commander.get_project = lambda project: {"allowed": True} if project == "example" else None  # type: ignore[assignment]
            dashboard.commander.command_watch = lambda project, user_id: f"watch {project} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_plan = lambda project, user_id: f"plan {project} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_feed = lambda args, user_id: f"feed {args[0]} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_briefs = lambda args, user_id: f"brief {args[0]} {user_id}"  # type: ignore[assignment]
            dashboard.commander.session_evidence = lambda project: f"evidence {project}"  # type: ignore[assignment]
            dashboard.commander.session_replay = lambda project: f"replay {project}"  # type: ignore[assignment]
            dashboard.commander.operator_playback = lambda project, user_id=None: f"playback {project} {user_id}"  # type: ignore[assignment]
            dashboard.commander.project_completion = lambda project, user_id=None: f"done {project} {user_id}"  # type: ignore[assignment]
            dashboard.commander.command_review = lambda args, user_id: f"review {args[0]} {user_id}"  # type: ignore[assignment]
            dashboard.commander.changed_project_details = lambda limit=30, max_files=0: [  # type: ignore[assignment]
                {"project": "example", "changed_count": 2, "branch": "main", "areas": "app/user interface (2)"}
            ]

            watch, watch_status = dashboard.dashboard_project_read_action("example", "watch")
            plan, plan_status = dashboard.dashboard_project_read_action("example", "plan")
            feed, feed_status = dashboard.dashboard_project_read_action("example", "feed")
            brief, brief_status = dashboard.dashboard_project_read_action("example", "brief")
            evidence, evidence_status = dashboard.dashboard_project_read_action("example", "evidence")
            replay, replay_status = dashboard.dashboard_project_read_action("example", "replay")
            playback, playback_status = dashboard.dashboard_project_read_action("example", "playback")
            done, done_status = dashboard.dashboard_project_read_action("example", "done")
            review, review_status = dashboard.dashboard_project_read_action("example", "review")
            changes, changes_status = dashboard.dashboard_project_read_action("example", "changes")
            missing, missing_status = dashboard.dashboard_project_read_action("missing", "watch")
        finally:
            dashboard.commander.get_project = original_get_project  # type: ignore[assignment]
            dashboard.commander.command_watch = original_watch  # type: ignore[assignment]
            dashboard.commander.command_plan = original_plan  # type: ignore[assignment]
            dashboard.commander.command_feed = original_feed  # type: ignore[assignment]
            dashboard.commander.command_briefs = original_briefs  # type: ignore[assignment]
            dashboard.commander.session_evidence = original_evidence  # type: ignore[assignment]
            dashboard.commander.session_replay = original_replay  # type: ignore[assignment]
            dashboard.commander.operator_playback = original_playback  # type: ignore[assignment]
            dashboard.commander.project_completion = original_done  # type: ignore[assignment]
            dashboard.commander.command_review = original_review  # type: ignore[assignment]
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
        self.assertEqual(replay_status, 200)
        self.assertEqual(replay["text"], "replay example")
        self.assertEqual(playback_status, 200)
        self.assertEqual(playback["text"], "playback example dashboard")
        self.assertEqual(done_status, 200)
        self.assertEqual(done["text"], "done example dashboard")
        self.assertEqual(review_status, 200)
        self.assertEqual(review["text"], "review example dashboard")
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

    def test_dashboard_queue_cleanup_action_dispatches_preview_and_apply(self) -> None:
        original_queue = dashboard.commander.command_queue
        calls: list[tuple[list[str], str]] = []
        try:
            dashboard.commander.command_queue = lambda args, user_id: calls.append((args, user_id)) or "ok"  # type: ignore[assignment]
            preview, preview_status = dashboard.dashboard_queue_cleanup_action({"action": "cleanup-preview"})
            applied, applied_status = dashboard.dashboard_queue_cleanup_action({"action": "cleanup-apply"})
            bad, bad_status = dashboard.dashboard_queue_cleanup_action({"action": "delete"})
        finally:
            dashboard.commander.command_queue = original_queue  # type: ignore[assignment]

        self.assertEqual(preview_status, 200)
        self.assertEqual(applied_status, 200)
        self.assertEqual(bad_status, 400)
        self.assertEqual(preview["text"], "ok")
        self.assertEqual(applied["text"], "ok")
        self.assertFalse(bad["ok"])
        self.assertEqual(calls, [(["cleanup"], "dashboard"), (["cleanup", "apply"], "dashboard")])

    def test_dashboard_review_save_action_is_project_scoped(self) -> None:
        original_get_project = dashboard.commander.get_project
        original_review = dashboard.commander.command_review
        calls: list[tuple[list[str], str]] = []
        try:
            dashboard.commander.get_project = lambda project: {"allowed": True} if project == "example" else None  # type: ignore[assignment]
            dashboard.commander.command_review = lambda args, user_id: calls.append((args, user_id)) or "saved review"  # type: ignore[assignment]

            missing_project, missing_status = dashboard.dashboard_review_save_action({})
            unknown_project, unknown_status = dashboard.dashboard_review_save_action({"project": "missing"})
            saved, saved_status = dashboard.dashboard_review_save_action({"project": "example"})
        finally:
            dashboard.commander.get_project = original_get_project  # type: ignore[assignment]
            dashboard.commander.command_review = original_review  # type: ignore[assignment]

        self.assertEqual(missing_status, 400)
        self.assertFalse(missing_project["ok"])
        self.assertEqual(unknown_status, 404)
        self.assertFalse(unknown_project["ok"])
        self.assertEqual(saved_status, 200)
        self.assertEqual(saved["text"], "saved review")
        self.assertEqual(calls, [(["example", "save"], "dashboard")])

    def test_dashboard_review_preview_action_opens_saved_pack_without_file_ids(self) -> None:
        original_preview = dashboard.commander.saved_owner_review_pack_preview
        calls: list[str] = []
        try:
            def preview(project: str) -> dict[str, str] | None:
                calls.append(project)
                if project != "Example Product":
                    return None
                return {
                    "project": "Example Product",
                    "saved_at": "2026-05-04 01:00",
                    "size": "1.1 KB",
                    "text": "Owner review pack: Example Product\n- Proof: local checks passed",
                }

            dashboard.commander.saved_owner_review_pack_preview = preview  # type: ignore[assignment]

            missing_project, missing_status = dashboard.dashboard_review_preview_action({})
            unknown_project, unknown_status = dashboard.dashboard_review_preview_action({"project": "missing"})
            opened, opened_status = dashboard.dashboard_review_preview_action({"project": "Example Product"})
        finally:
            dashboard.commander.saved_owner_review_pack_preview = original_preview  # type: ignore[assignment]

        self.assertEqual(missing_status, 400)
        self.assertFalse(missing_project["ok"])
        self.assertEqual(unknown_status, 404)
        self.assertFalse(unknown_project["ok"])
        self.assertEqual(opened_status, 200)
        self.assertEqual(opened["project"], "Example Product")
        self.assertIn("Owner review pack", opened["text"])
        self.assertNotIn(".md", str(opened))
        self.assertEqual(calls, ["missing", "Example Product"])


if __name__ == "__main__":
    unittest.main()
