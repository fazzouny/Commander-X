# Commander X Architecture

Commander X is a local control plane for Codex CLI sessions.

```text
Telegram / Dashboard / future channels
  -> Commander command gateway
  -> allowlist and safety gates
  -> project registry
  -> memory, task queue, project profiles
  -> Codex CLI sessions
  -> Git, logs, approvals, status reports
```

## Design Principles

- Local-first: the laptop owns code access, logs, and process control.
- Registered projects only: Commander refuses to operate outside `projects.json`.
- No raw shell from chat: every remote action must map to an explicit Commander command.
- Evidence-first: replies should include enough proof to distinguish real execution from intent.
- Approval gates: commit and push are prepared first, then require `/approve`.
- Channel-neutral: Telegram is the first channel; dashboard, WhatsApp, and other channels should reuse the same command functions.

## Persistent Stores

These are local runtime files and should not be committed:

- `allowlist.json`: authorized Telegram users.
- `projects.json`: local project paths and aliases.
- `sessions.json`: running and historical Codex session metadata.
- `commander_state.json`: user state, active project, heartbeats.
- `memory.json`: learned preferences and project facts.
- `tasks.json`: task queue and session linkage.
- `project_profiles.json`: optional project-specific stack/check/risk overrides.

Example templates are safe to commit:

- `allowlist.example.json`
- `projects.example.json`
- `project_profiles.example.json`

## Core Commands

- `/start`: starts Codex against a registered project and creates a task record.
- `/status`: shows tracked sessions.
- `/log`: reads the latest session log.
- `/diff`: summarizes Git state.
- `/mission`, `/evidence`, `/replay`, `/playback`: convert managed Codex activity into operator-readable direction, proof, run stories, and next-action briefings.
- `/objective` and `/done`: define the intended outcome and check completion proof before Commander X can call work complete.
- `/commit` and `/push`: create approval requests.
- `/remember`, `/memory`, `/forget`: manage Commander learning.
- `/profile`: shows detected and configured project profile.
- `/queue`: manages the local task queue.
- `/heartbeat`: manages proactive updates.

## Code Layout

- `commander.py`: Telegram gateway, command handlers, Codex process orchestration, approvals, voice handling, and heartbeat loop.
- `dashboard.py`: localhost dashboard API and static-file server.
- `commanderx/`: tested reusable core helpers.
  - `storage.py`: JSON file read/write primitives.
  - `text.py`: command parsing and slug helpers.
  - `projects.py`: alias resolution and project mention detection.
  - `memory.py`: memory ranking.
  - `tasks.py`: task/session status synchronization.
  - `processes.py`: subprocess, Codex command, PID, and stop helpers.
  - `gitops.py`: Git command helpers.
  - `telegram.py`: Telegram Bot API transport.
- `tests/`: unit tests for parsing, project resolution, memory ranking, task/session sync, storage, and command gating.
- `web/`: dashboard frontend.
- `scripts/`: Windows service helpers and smoke tests.

The current split intentionally keeps side-effect-heavy integration code in `commander.py` while moving pure logic into `commanderx/`. Continue extracting modules only when tests cover the behavior being moved.

## Open Source Boundary

Before publishing this project, remove private local files, choose a license intentionally, and review docs for paths, project names, and logs. The `.gitignore` is set up so local runtime state is not included by default.
