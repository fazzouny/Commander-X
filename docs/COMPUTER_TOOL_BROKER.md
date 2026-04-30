# Commander X Computer Tool Broker

Commander X uses a broker instead of raw shell access from Telegram.

## Current Tools

- `/open url <url>` opens a website in the default browser.
- `/open app <name>` opens an allowlisted local app.
- `/file <project> <relative_path> [lines]` reads a file inside a registered project.
- `/volume up|down|max|mute [steps]` sends Windows media-key volume controls.
- `/computer codex` reports Codex Commander sessions, Codex CLI MCPs, and local Codex processes.
- `/computer processes [name...]` checks local processes by allowlisted-style names.
- `/computer screenshot` captures the current primary screen to `logs/screenshots`.
- `/browser inspect <url>` fetches a web page and reports status, title, description, headings, and link count.
- `/clickup recent [query]` reads recent ClickUp tasks when API credentials are configured.
- `/clickup count [query]` counts matching ClickUp tasks and shows a status breakdown for campaign/lead questions.
- `/mcp` lists Codex CLI MCP servers.
- `/mcp request <docs URL, package search, or install command>` handles MCP install/connect requests without relying on the OpenAI router.
- `/mcp find <package or connector name>` searches npm package metadata for MCP candidates as review leads with a basic source-trust label.
- `/mcp add <server-name> npx -y <package> [args...]` or `/mcp add <server-name> uvx <package> [args...]` prepares an approval-gated `codex mcp add`.
- `/openclaw` reports OpenClaw CLI/config/cache traces and launcher availability without starting it.
- `/openclaw recover` researches GitHub candidates and README install clues without installing anything.
- `/openclaw prepare <github-url>` prepares an approval-gated source clone only.
- `/openclaw start` prepares an approval-gated start of the configured `COMMANDER_OPENCLAW_LAUNCHER` only.
- `/system` reports OS, memory, battery, and disk health.
- `/env` reports which integration keys are configured without printing secret values.
- `/clipboard show|set|clear` provides guarded clipboard utility actions.
- `/cleanup` estimates safe disk-cleanup candidates without deleting files.
- `/doctor` runs a full Commander health check and produces an open-source-friendly readiness score.
- `/service` reports whether the Telegram poller and dashboard are running, plus sanitized recent service signals.
- `/inbox` aggregates approvals, running/failed sessions, queued tasks, and recommendations.
- `/approvals` lists every pending approval with exact approve/cancel commands.
- `/changes` summarizes changed projects by human work area, hiding filenames unless `files/details` is requested.
- `/watch` gives a Codex-app-like plain-English live view of a managed session.
- `/timeline` is an alias for the same live run view and shows the managed session phases.
- `/plan [project] [task]` shows the plain-English goal, approach, risk, expected checks, and approval boundaries before work starts.
- Telegram replies now attach contextual buttons for approval/cancel, watch, plan, stop, diff, and the usual command shortcuts when the message is short enough.
- The dashboard Capabilities card summarizes available brokers, integrations, OpenClaw status, and copyable command shortcuts without exposing local project paths or secrets.

## Guardrails

- No arbitrary `/run` command exists.
- File reads are limited to registered project folders.
- Secret-like files are blocked by filename and suffix.
- App launching is allowlist-based through `computer_tools.json`.
- ClickUp uses direct API credentials for the background Telegram service; Desktop MCP connector access is not assumed.
- MCP install requests are controlled commands. Commander can research web pages and npm package metadata, but it does not execute from shell pipes, redirects, chained commands, unknown runners, or registry search results without an explicit `/mcp add` and approval. NPM trust labels are conservative hints only, not security guarantees.
- OpenClaw recovery is conservative. Candidate repositories and README commands are leads, not proof of official ownership. Cloning source requires approval; starting a launcher requires `COMMANDER_OPENCLAW_LAUNCHER` and approval. Running installer scripts or delegated OpenClaw work should remain separate approval-gated workflows.
- Clipboard reads, screenshot capture, and volume keys can be disabled by `.env` safety flags.
- Disk cleanup is intentionally advice-only from Telegram. Deletion should remain a reviewed local action or a future approval-gated workflow.
- High-impact external actions still require explicit approval.

## OpenClaw-Inspired Direction

OpenClaw's useful architecture is local-first gateway + channels + sessions + tools + skills + memory + cron/heartbeat + control UI.

Commander X now follows that shape:

- Telegram is the first channel.
- `commander.py` is the local gateway.
- Codex CLI sessions are the coding runtime.
- The computer broker is the first device-control layer.
- The dashboard is the control UI and can approve/cancel prepared actions plus start/done/cancel queued tasks through token-gated buttons.
- The dashboard includes a capabilities snapshot so an operator can see what Commander can do before digging into raw logs or file names.
- Session timelines show phases like task received, planned, launched, stopped, failed, or finished.
- Work plans are deterministic and stored with new sessions, so the dashboard can show intent before raw logs.
- Approval cards keep high-impact actions explicit: Commander prepares the action, then Telegram buttons or `/approve` execute it.
- Heartbeats are the first proactive automation layer.

Next high-value additions:

- Browser automation tool with screenshots and extraction.
- ClickUp tool bridge.
- GitHub PR creation.
- Skill execution registry.
- Approval-gated desktop automation for mouse/keyboard only when no API/tool exists.
