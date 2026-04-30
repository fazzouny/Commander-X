# Commander X: OpenClaw for Codex

Commander X is the local-first control layer for Codex sessions.

## Goal

Make Codex controllable from Telegram, voice, and a local dashboard without giving chat users raw access to the laptop.

## OpenClaw Pattern To Copy

- Local runtime on the user's machine
- Chat channels as the main interface
- Persistent memory and context
- Tool and skill layer
- Proactive heartbeat/background workers
- Dashboard for observability and control

## Safety Pattern To Keep

- Registered projects only
- No raw shell from Telegram
- Codex sessions run inside project folders
- Git branches per task
- Evidence logs for every session
- Commit/push approval gates
- Explicit unsupported-media replies

## Commander X Layers

1. Channel Gateway
   - Telegram text
   - Telegram voice
   - Telegram buttons
   - Later: WhatsApp

2. Intent Router
   - Maps natural language to structured Commander commands
   - Multi-project requests must fan out explicitly
   - Unknown projects must not fall back silently

3. Memory Layer
   - Active project per user
   - Heartbeat preferences
   - Project aliases
   - Future: durable user preferences and repeated workflow skills

4. Project Registry
   - Allowlisted project IDs
   - Paths hidden by default
   - Context files per project

5. Session Manager
   - Starts Codex CLI
   - Tracks logs, status, branch, PID
   - Stops sessions
   - Reports evidence

6. Dashboard
   - Real sessions
   - Real diffs
   - Real logs
   - Start/stop controls

## Next Upgrades

- Add persistent learning commands: "remember that..."
- Add skill files per project
- Add image understanding for Telegram screenshots
- Add WhatsApp channel
- Add GitHub PR creation
- Add evidence-based completion checks before Commander says done
