# Codex Commander

Codex Commander is a local Telegram-controlled controller for Codex CLI sessions.

It is intentionally narrow:

- no raw shell execution from Telegram
- only registered projects in `projects.json`
- Codex runs through `codex exec`
- natural-language messages are routed through OpenAI into safe Commander actions
- Telegram voice notes are transcribed with OpenAI, then routed through the same command parser
- Telegram responses use HTML formatting and inline action buttons
- automatic heartbeat summaries can be sent back to Telegram
- Commander memory stores durable preferences and project facts
- project profiles detect stack, useful scripts, verification commands, and risk notes
- a task queue tracks queued/running/review/done work
- a local dashboard exposes sessions, Git state, queue, memory, and evidence
- a safe computer tool broker can open URLs, open allowlisted apps, read registered-project files, adjust volume, capture screenshots, and inspect Codex processes
- a browser broker can inspect websites without opening an unsafe raw shell
- a ClickUp API bridge can read recent tasks when `CLICKUP_API_TOKEN` and `CLICKUP_WORKSPACE_ID` are configured
- logs are stored locally under `logs/`
- `/commit` and `/push` require a second `/approve`
- obvious secret files are blocked from commit

## Setup

1. Create a Telegram bot with BotFather and copy the bot token.
2. Copy `.env.example` to `.env`.
3. Put the bot token in `.env` as `TELEGRAM_BOT_TOKEN=...`.
4. Add your OpenAI API key in `.env` as `OPENAI_API_KEY=...` if you want voice notes.
5. Start Commander:

```powershell
cd C:\path\to\codex-commander
python .\commander.py --poll
```

Or start both the Telegram poller and dashboard as background services while archiving previous logs:

```powershell
.\scripts\start-services.ps1 -Restart
```

6. Send the bot:

```text
/whoami
```

7. Add the returned Telegram user ID to `allowlist.json` or `TELEGRAM_ALLOWED_USER_IDS` in `.env`.
8. Restart Commander.

## Check

```powershell
cd C:\path\to\codex-commander
python .\commander.py --check
```

Run the local smoke test:

```powershell
.\scripts\smoke-test.ps1
```

Run only unit tests:

```powershell
python -m unittest discover -s .\tests
```

## Optional Startup Task

After `.env` and `allowlist.json` are configured, register Commander to start when Windows logs in:

```powershell
cd C:\path\to\codex-commander
.\scripts\register-startup-task.ps1
```

Remove it later with:

```powershell
.\scripts\unregister-startup-task.ps1
```

## Commands

```text
/whoami
/help
/projects
/status
/service
/doctor
/inbox
/approvals
/changes
/watch
/timeline
/plan
/brief
/morning
/next
/updates
/mode
/mode free
/mode focused example-app
/free
/tools
/computer
/computer codex
/computer screenshot
/browser inspect https://example.com
/clickup status
/clickup recent campaigns
/skills
/skills playwright
/plugins
/mcp
/mcp help
/mcp request https://example.com/mcp-docs
/mcp find meta ads
/mcp add example-server npx -y @vendor/mcp-server
/env
/system
/clipboard show
/cleanup
/open url https://example.com
/open app notepad
/file example-app README.md 80
/volume down 5
/focus example-app
/context
/context full
/start example-app "Audit onboarding and fix production blockers"
/log
/diff
/stop
/commit example-app "Fix onboarding bugs"
/approve example-app <approval_id>
/push example-app
/cancel example-app <approval_id>
/heartbeat on 30
/heartbeat quiet 23:00 08:00
/heartbeat now
/heartbeat off
/remember global "Always include evidence before saying work is done"
/remember project example-app "Use npm run typecheck, lint, test, and build before release claims"
/memory
/forget <memory_id>
/profile
/profile example-app
/queue
/queue add example-app "Audit production readiness"
/queue start <task_id>
/check
```

You can also type natural language:

```text
Make Example App the active project.
What is Codex doing right now?
Show me the latest log for this project.
Send me updates every 30 minutes.
Start working on Example App and audit the onboarding flow first.
Continue Example App and make it usable for the team.
Check what is left in this project.
Give me my morning brief.
What should I do next?
Visit example.com.
Inspect example.com.
Check ClickUp for campaign tasks.
What keys are missing?
Show system status.
Run Commander doctor.
What needs my attention?
Show pending approvals.
What changed across projects?
Watch the current project.
Show me a cleanup plan.
Open Notepad.
Lower the volume.
Check Codex on this computer.
```

## Voice Commands

Voice notes are downloaded locally under `logs\voice\`, transcribed with `OPENAI_TRANSCRIBE_MODEL`, and normalized into normal slash commands.

Examples you can say:

```text
status
projects
diff example app
log example app
start example app audit the onboarding flow and report issues first
stop example app
```

The transcript is sent back before Commander executes the normalized command.

## Telegram Command Menu

Commander configures the Telegram slash-command menu on startup by default.

Manual setup:

```powershell
cd C:\path\to\codex-commander
python .\commander.py --set-telegram-commands
```

When you type `/` in Telegram, the command list should appear.

## Telegram Buttons

Most bot replies include inline buttons:

- `Status`
- `Projects`
- `Context`
- `Watch`
- `Plan`
- `Log`
- `Diff`
- `Heartbeat Now`
- `Heartbeat Off`

When a session is running, Commander can also show `Watch`, `Plan`, and `Stop`. When a commit or push approval is pending, Commander shows `Approve`, `Cancel`, `Show diff`, and `Watch`.

Buttons are contextual. Commander avoids adding them to long logs, long context dumps, and noisy transcript/debug outputs.

## Heartbeats

Enable periodic status summaries:

```text
/heartbeat on 30
```

Send one immediately:

```text
/heartbeat now
```

Stop updates:

```text
/heartbeat off
```

Quiet hours are enabled by default from `23:00` to `08:00` local laptop time. Commander will not send automatic heartbeat messages during quiet hours, but it will still reply immediately if you message it.

Change quiet hours:

```text
/heartbeat quiet 00:00 08:30
```

Disable quiet hours:

```text
/heartbeat quiet off
```

## Assistant Modes

Commander supports two operating modes:

```text
/mode
/mode free
/mode focused example-app
/free
```

Focused mode uses the focused project when a project is not mentioned. Free mode avoids assuming the focused project and is the right mode for general computer, integration, browser, or file requests.

Use `/tools` to see what Commander can access directly and what still needs to be wired.

## Computer Tool Broker

Commander X intentionally does not expose a raw `/run` shell over Telegram. Device control goes through explicit tools:

```text
/open url <url>
/open app <allowlisted_app>
/file <project> <relative_path> [lines]
/volume up|down|mute [steps]
/computer codex
/computer processes [name...]
/computer screenshot
```

Default allowlisted apps are `notepad`, `calculator`, `paint`, and `explorer`. To add more, copy `computer_tools.example.json` to `computer_tools.json` and add app commands there.

File reads stay inside registered project folders and block secret-like files such as `.env`, private keys, and credential files.

Device and readiness checks:

```text
/env
/system
/clipboard show
/clipboard set <text>
/clipboard clear
/cleanup
```

`/cleanup` is non-destructive. It estimates safe cleanup candidates such as Commander archived logs, voice-note downloads, temp files, NPX cache, pip cache, Playwright cache, and Windows Update downloads. It does not delete files from Telegram.

Clipboard reads, screenshots, and volume keys can be disabled with:

```text
COMMANDER_ALLOW_CLIPBOARD_READ=false
COMMANDER_ALLOW_SCREENSHOT=false
COMMANDER_ALLOW_VOLUME_KEYS=false
```

## Browser And ClickUp Brokers

Website checks:

```text
/browser inspect <url>
/browser open <url>
/browser screenshot
```

ClickUp checks:

```text
/clickup status
/clickup recent [query]
```

Commander can see Codex Desktop's ClickUp connector only inside this Codex session. For the always-on Telegram service, configure direct API access in `.env`:

```text
CLICKUP_API_TOKEN=...
CLICKUP_WORKSPACE_ID=...
```

The ClickUp bridge uses ClickUp's filtered Workspace tasks endpoint and filters query terms locally for simple mobile briefs.

## MCP Setup

MCP setup is controlled and approval-gated:

```text
/mcp
/mcp help
/mcp request https://example.com/mcp-docs
/mcp find meta ads
/mcp add example-server npx -y @vendor/mcp-server
```

Commander treats URLs as setup/research requests, not raw install commands. If you send a docs URL, it fetches the page, looks for explicit `codex mcp add`, `npx -y`, or `uvx` install commands, and prepares an approval only when it finds a single safe candidate. If the page does not contain an install command, Commander can search npm package metadata with `/mcp find <connector name>` and show candidate packages as review leads with a basic source-trust label.

Running `codex mcp add` always requires an explicit `/approve commander <approval_id>`.

## Memory, Profiles, and Queue

Commander can learn simple durable facts without changing code:

```text
/remember global "Do not show local paths unless I ask for full details"
/remember project example-app "Production readiness requires typecheck, lint, build, and smoke checks"
/memory
/memory project
/forget <memory_id>
```

Project profiles combine explicit `project_profiles.json` settings with detected repo facts such as `package.json` scripts and stack markers:

```text
/profile example-app
```

Task queue commands let Commander track work separately from running processes:

```text
/queue
/queue add example-app "Fix onboarding bugs"
/queue start <task_id>
/queue done <task_id>
/queue cancel <task_id>
```

Every `/start` creates a task record and links it to the Codex session.

## Dashboard

Start the local dashboard:

```powershell
cd C:\path\to\codex-commander
python .\dashboard.py
```

Open:

```text
http://127.0.0.1:8787
```

The dashboard shows registered projects, sessions, task queue, memory count, Git evidence, logs, and profiles. It binds to localhost by default. If you expose it through Tailscale, Cloudflare Tunnel, or another remote path, set `COMMANDER_DASHBOARD_TOKEN` in `.env`.

## Project Registry

Copy `projects.example.json` to `projects.json`, then add or disable projects. Commander will not operate outside enabled project IDs.

Use `/projects full` only when you need local paths. Normal `/projects` output intentionally hides paths.

## Safety Notes

Telegram can start and stop local Codex tasks, but it cannot run arbitrary shell commands.

High-impact actions should stay outside this MVP unless they are represented as explicit Commander actions with an approval step. The current high-impact actions are:

- `/commit`: prepares a local Git commit, then requires `/approve`
- `/push`: prepares a Git push, then requires `/approve`

Do not put Telegram tokens, OpenAI keys, Supabase tokens, or production credentials in Telegram messages.

## Open Source Notes

This project is structured so local runtime files can stay private. Before publishing, review:

- `docs/ARCHITECTURE.md`
- `docs/SECURITY_MODEL.md`
- `docs/OPEN_SOURCE_CHECKLIST.md`

Commit the example config files, not your real local runtime files:

- `allowlist.example.json`
- `projects.example.json`
- `project_profiles.example.json`

The repository includes a basic GitHub Actions workflow at `.github/workflows/ci.yml` for Python compile and local command smoke checks.

The tested reusable core lives in `commanderx/`. Keep Telegram/OpenAI/Codex side effects in integration code and move pure logic into `commanderx/` when adding new behavior.
