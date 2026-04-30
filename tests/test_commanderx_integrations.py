from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any

from commanderx.gitops import git_args, git_safe_path
from commanderx.processes import codex_command_args, pid_running
from commanderx.telegram import TelegramTransport


class ProcessAndGitTests(unittest.TestCase):
    def test_codex_command_args_wraps_on_windows(self) -> None:
        args = codex_command_args(["exec", "-"])
        if os.name == "nt":
            self.assertEqual(args[:4], ["cmd.exe", "/d", "/c", "codex"])
            self.assertEqual(args[-2:], ["exec", "-"])
        else:
            self.assertEqual(args, ["codex", "exec", "-"])

    def test_pid_running_rejects_invalid_pid(self) -> None:
        self.assertFalse(pid_running(0))
        self.assertFalse(pid_running(-1))

    def test_git_args_include_safe_directory(self) -> None:
        path = Path.cwd()
        args = git_args(path, "status", "--short")
        self.assertEqual(args[0], "git")
        self.assertIn(f"safe.directory={git_safe_path(path)}", args)
        self.assertEqual(args[-2:], ["status", "--short"])


class FakeTelegram(TelegramTransport):
    def __init__(self, *args: Any, fail_html_once: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.fail_html_once = fail_html_once

    def request(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 45) -> dict[str, Any]:
        self.calls.append((method, dict(payload) if payload else None))
        if self.fail_html_once and payload and payload.get("parse_mode") == "HTML":
            self.fail_html_once = False
            raise RuntimeError("bad html")
        return {"ok": True, "result": {"file_path": "voice/file.ogg"}}


class TelegramTransportTests(unittest.TestCase):
    def test_configure_commands_sets_menu_and_descriptions(self) -> None:
        bot = FakeTelegram(
            "token",
            commands=[("status", "Show status")],
            short_description="short",
            description="long",
        )
        bot.configure_commands()
        self.assertEqual([call[0] for call in bot.calls], ["setMyCommands", "setMyShortDescription", "setMyDescription"])
        self.assertEqual(bot.calls[0][1]["commands"][0]["command"], "status")

    def test_send_message_uses_html_then_falls_back_plain(self) -> None:
        bot = FakeTelegram(
            "token",
            formatter=lambda text: f"<b>{text}</b>",
            redactor=lambda text: text.replace("secret", "[redacted]"),
            fail_html_once=True,
        )
        bot.send_message(123, "secret")
        self.assertEqual(len(bot.calls), 2)
        self.assertEqual(bot.calls[0][1]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", bot.calls[1][1])
        self.assertEqual(bot.calls[1][1]["text"], "[redacted]")


if __name__ == "__main__":
    unittest.main()
