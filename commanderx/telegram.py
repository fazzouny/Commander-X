from __future__ import annotations

import datetime as dt
import json
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


Formatter = Callable[[str], str]
Splitter = Callable[[str], list[str]]


def default_splitter(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        chunk = remaining[:limit]
        split_at = chunk.rfind("\n")
        if split_at > 1000:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk) :].lstrip()
    return chunks


class TelegramTransport:
    def __init__(
        self,
        token: str,
        *,
        commands: list[tuple[str, str]] | None = None,
        short_description: str = "",
        description: str = "",
        max_download_bytes: int = 25 * 1024 * 1024,
        formatter: Formatter | None = None,
        redactor: Formatter | None = None,
        splitter: Splitter | None = None,
    ) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.commands = commands or []
        self.short_description = short_description
        self.description = description
        self.max_download_bytes = max_download_bytes
        self.formatter = formatter or (lambda text: text)
        self.redactor = redactor or (lambda text: text)
        self.splitter = splitter or default_splitter

    def request(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 45) -> dict[str, Any]:
        url = f"{self.base_url}/{method}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": 30,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self.request("getUpdates", payload=payload, timeout=45)
        if not result.get("ok"):
            raise RuntimeError(str(result))
        return result.get("result", [])

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:180]
        self.request("answerCallbackQuery", payload, timeout=30)

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        html_format: bool = True,
    ) -> None:
        for chunk in self.splitter(text or "(empty)"):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": self.formatter(chunk) if html_format else self.redactor(chunk),
                "disable_web_page_preview": True,
            }
            if html_format:
                payload["parse_mode"] = "HTML"
            if reply_markup:
                payload["reply_markup"] = reply_markup
            try:
                self.request("sendMessage", payload, timeout=30)
            except Exception:
                payload.pop("parse_mode", None)
                payload["text"] = self.redactor(chunk)
                self.request("sendMessage", payload, timeout=30)

    def send_plain(self, chat_id: int | str, text: str) -> None:
        self.send_message(chat_id, text, reply_markup=None, html_format=False)

    def send_message_legacy(self, chat_id: int | str, text: str) -> None:
        for chunk in self.splitter(text or "(empty)"):
            self.request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": self.redactor(chunk),
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )

    def configure_commands(self) -> None:
        self.request(
            "setMyCommands",
            {
                "commands": [
                    {"command": command, "description": description}
                    for command, description in self.commands
                ]
            },
            timeout=30,
        )
        if self.short_description:
            self.request("setMyShortDescription", {"short_description": self.short_description}, timeout=30)
        if self.description:
            self.request("setMyDescription", {"description": self.description}, timeout=30)

    def get_file_path(self, file_id: str) -> str:
        result = self.request("getFile", {"file_id": file_id}, timeout=30)
        if not result.get("ok"):
            raise RuntimeError(str(result))
        file_path = result.get("result", {}).get("file_path")
        if not file_path:
            raise RuntimeError("Telegram did not return a file_path.")
        return str(file_path)

    def download_file(self, file_id: str, destination: Path, preferred_suffix: str = "") -> Path:
        file_path = self.get_file_path(file_id)
        suffix = Path(file_path).suffix or preferred_suffix or ".ogg"
        if suffix.lower() == ".oga":
            suffix = ".ogg"
        destination.mkdir(parents=True, exist_ok=True)
        local_path = destination / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}{suffix}"
        url = f"https://api.telegram.org/file/bot{self.token}/{urllib.parse.quote(file_path)}"
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read(self.max_download_bytes + 1)
        if len(payload) > self.max_download_bytes:
            raise RuntimeError("Downloaded Telegram file is over the configured size limit.")
        local_path.write_bytes(payload)
        return local_path
