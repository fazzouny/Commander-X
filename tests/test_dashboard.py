from __future__ import annotations

import unittest

import dashboard


class DashboardApprovalTests(unittest.TestCase):
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
