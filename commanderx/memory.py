from __future__ import annotations

import re
from typing import Any


def relevant_memories(
    memories: list[dict[str, Any]],
    user_id: str,
    project_id: str | None = None,
    query: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    query_terms = {term.lower() for term in re.findall(r"[a-zA-Z0-9_-]{3,}", query or "")}
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in memories:
        score = 0
        if item.get("scope") == "global":
            score += 2
        if str(item.get("user_id")) == str(user_id):
            score += 2
        if project_id and item.get("project") == project_id:
            score += 4
        text = str(item.get("note", "")).lower()
        if query_terms:
            hits = sum(1 for term in query_terms if term in text)
            if hits == 0:
                continue
            score += hits
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].get("created_at", "")), reverse=True)
    return [item for _, item in scored[:limit]]
