from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from commanderx.computer import normalize_url


class PageSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.h1: list[str] = []
        self.links = 0
        self._capture_title = False
        self._capture_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._capture_title = True
        elif tag.lower() == "h1":
            self._capture_h1 = True
        elif tag.lower() == "a" and attrs_dict.get("href"):
            self.links += 1
        elif tag.lower() == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.description = attrs_dict.get("content", "").strip()[:500]

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._capture_title = False
        elif tag.lower() == "h1":
            self._capture_h1 = False

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        if self._capture_title and not self.title:
            self.title = clean[:300]
        elif self._capture_h1 and len(self.h1) < 3:
            self.h1.append(clean[:220])


@dataclass
class BrowserInspection:
    ok: bool
    url: str
    final_url: str = ""
    status: int | None = None
    content_type: str = ""
    title: str = ""
    description: str = ""
    h1: list[str] | None = None
    links: int = 0
    elapsed_ms: int = 0
    error: str = ""


def inspect_url(url: str, timeout: int = 20) -> BrowserInspection:
    clean = normalize_url(url)
    if not clean:
        return BrowserInspection(ok=False, url=url, error="URL is required.")
    started = time.perf_counter()
    request = urllib.request.Request(
        clean,
        headers={
            "User-Agent": "CommanderX/0.1 (+local assistant)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(400_000)
            final_url = response.geturl()
            status = getattr(response, "status", None)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return BrowserInspection(
            ok=False,
            url=clean,
            final_url=exc.geturl(),
            status=exc.code,
            elapsed_ms=elapsed,
            error=f"HTTP {exc.code}: {exc.reason}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return BrowserInspection(ok=False, url=clean, elapsed_ms=elapsed, error=str(exc))

    elapsed = int((time.perf_counter() - started) * 1000)
    text = raw.decode("utf-8", errors="replace")
    parser = PageSummaryParser()
    if "html" in content_type.lower() or "<html" in text[:1000].lower():
        parser.feed(text)
    return BrowserInspection(
        ok=True,
        url=clean,
        final_url=final_url,
        status=status,
        content_type=content_type,
        title=parser.title,
        description=parser.description,
        h1=parser.h1,
        links=parser.links,
        elapsed_ms=elapsed,
    )


def format_inspection(result: BrowserInspection) -> str:
    lines = ["Browser inspection"]
    lines.append(f"URL: {result.url}")
    if result.final_url and result.final_url != result.url:
        lines.append(f"Final URL: {result.final_url}")
    if result.status is not None:
        lines.append(f"Status: {result.status}")
    if result.elapsed_ms:
        lines.append(f"Load: {result.elapsed_ms} ms")
    if result.content_type:
        lines.append(f"Type: {result.content_type}")
    if not result.ok:
        lines.append(f"Error: {result.error}")
        return "\n".join(lines)
    if result.title:
        lines.append(f"Title: {result.title}")
    if result.description:
        lines.append(f"Description: {result.description}")
    headings = result.h1 or []
    if headings:
        lines.append("H1:")
        lines.extend(f"- {heading}" for heading in headings[:3])
    lines.append(f"Links found: {result.links}")
    return "\n".join(lines)
