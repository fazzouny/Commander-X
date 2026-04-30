from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from commanderx.memory import relevant_memories
from commanderx.projects import build_project_alias_map, mentioned_projects, resolve_project
from commanderx.storage import read_json_file, write_json_file
from commanderx.tasks import sync_task_records, visible_task_records
from commanderx.text import parse_message, slugify


class TextTests(unittest.TestCase):
    def test_parse_message_preserves_quoted_task(self) -> None:
        self.assertEqual(
            parse_message('/start example-app "Fix onboarding bugs"'),
            ["/start", "example-app", "Fix onboarding bugs"],
        )

    def test_parse_message_falls_back_on_unbalanced_quotes(self) -> None:
        self.assertEqual(parse_message('/start example "unfinished'), ["/start", "example", '"unfinished'])

    def test_slugify_keeps_branch_names_short_and_nonempty(self) -> None:
        slug = slugify("Make it 100% Production Ready!!!", limit=20)
        self.assertLessEqual(len(slug), 20)
        self.assertTrue(slug.startswith("make-it-100-product"))
        self.assertEqual(slugify("!!!"), "task")


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = {
            "example-app": {"aliases": ["example", "app"]},
            "billing-api": {"aliases": ["billing api", "billz"]},
            "taalam-campaigns": {"aliases": ["taalam campaigns", "taalim campaigns", "talim campaigns"]},
        }

    def test_alias_map_includes_ids_space_variants_and_aliases(self) -> None:
        aliases = build_project_alias_map(self.projects)
        self.assertEqual(aliases["example-app"], "example-app")
        self.assertEqual(aliases["example app"], "example-app")
        self.assertEqual(aliases["billz"], "billing-api")

    def test_resolve_project_uses_aliases_and_active_fallback(self) -> None:
        self.assertEqual(resolve_project(self.projects, "billing api"), "billing-api")
        self.assertEqual(resolve_project(self.projects, None, active_project="example-app"), "example-app")
        self.assertIsNone(resolve_project(self.projects, None, active_project="missing"))

    def test_mentioned_projects_dedupes_multiple_alias_hits(self) -> None:
        self.assertEqual(
            mentioned_projects(self.projects, "Run example and billing api until production ready"),
            ["example-app", "billing-api"],
        )

    def test_mentioned_projects_handles_voice_transcription_aliases(self) -> None:
        self.assertEqual(
            mentioned_projects(self.projects, "Focus on Talim campaigns."),
            ["taalam-campaigns"],
        )


class MemoryTests(unittest.TestCase):
    def test_relevant_memories_prioritize_project_and_query_hits(self) -> None:
        memories = [
            {"id": "global", "scope": "global", "user_id": "1", "note": "Always show evidence", "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "project", "scope": "project", "project": "example-app", "user_id": "2", "note": "Run npm build for production", "created_at": "2026-01-02T00:00:00+00:00"},
            {"id": "other", "scope": "project", "project": "other", "user_id": "1", "note": "Unrelated note", "created_at": "2026-01-03T00:00:00+00:00"},
        ]
        result = relevant_memories(memories, user_id="1", project_id="example-app", query="production build")
        self.assertEqual([item["id"] for item in result], ["project"])

    def test_relevant_memories_without_query_returns_user_and_global_context(self) -> None:
        memories = [
            {"id": "global", "scope": "global", "user_id": "9", "note": "global", "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "mine", "scope": "user", "user_id": "1", "note": "mine", "created_at": "2026-01-02T00:00:00+00:00"},
        ]
        self.assertEqual([item["id"] for item in relevant_memories(memories, user_id="1")], ["mine", "global"])


class TaskTests(unittest.TestCase):
    def test_sync_task_records_maps_session_states(self) -> None:
        tasks = [{"id": "a", "status": "queued"}, {"id": "b", "status": "running"}, {"id": "c", "status": "running"}]
        sessions = {
            "example": {"task_id": "a", "state": "completed"},
            "billing": {"task_id": "b", "state": "finished_unknown"},
            "broken": {"task_id": "c", "state": "stop_failed"},
        }
        changed = sync_task_records(tasks, sessions, updated_at="now")
        self.assertTrue(changed)
        self.assertEqual(tasks[0]["status"], "done")
        self.assertEqual(tasks[1]["status"], "review")
        self.assertEqual(tasks[2]["status"], "failed")

    def test_visible_task_records_prefers_active_work(self) -> None:
        tasks = [
            {"id": "done", "status": "done"},
            {"id": "queued", "status": "queued"},
            {"id": "failed", "status": "failed"},
        ]
        self.assertEqual([task["id"] for task in visible_task_records(tasks)], ["queued", "failed"])


class StorageTests(unittest.TestCase):
    def test_read_and_write_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            self.assertEqual(read_json_file(path, {"items": []}), {"items": []})
            write_json_file(path, {"items": [{"id": "1"}]})
            self.assertEqual(read_json_file(path, {"items": []})["items"][0]["id"], "1")


if __name__ == "__main__":
    unittest.main()
