# Open Source Checklist

Use this before publishing Commander X.

## Required

- Choose a license deliberately.
- Commit only example config files, not local runtime files.
- Confirm `.env`, `allowlist.json`, `projects.json`, `memory.json`, `tasks.json`, `sessions.json`, `commander_state.json`, `project_profiles.json`, and `logs/` are ignored.
- Search the repo for private paths, tokens, phone numbers, chat IDs, and project-specific logs.
- Run `python -m py_compile commander.py dashboard.py`.
- Run `python -m compileall -q commanderx`.
- Run `python -m unittest discover -s tests`.
- Run `python commander.py --check` with a sanitized local config.
- Test `/whoami`, `/projects`, `/status`, `/remember`, `/memory`, `/profile`, `/queue`, `/heartbeat status`.

## Nice To Have

- Add CI for Python syntax checks.
- Add unit tests for command parsing, project resolution, memory filtering, and task queue updates.
- Add integration tests for new channel transports before wiring them to real external APIs.
- Add screenshots of the dashboard with fake data.
- Add Docker or uv packaging after the local workflow is stable.
- Split `commander.py` into modules once behavior settles:
  - `config.py`
  - `telegram_gateway.py`
  - `sessions.py`
  - `memory_store.py`
  - `task_queue.py`
  - `dashboard.py`

## Do Not Publish

- Telegram bot tokens.
- OpenAI API keys.
- Local project paths.
- Voice notes.
- Codex logs.
- Git diffs from private repositories.
- Chat IDs or personal Telegram user IDs.
