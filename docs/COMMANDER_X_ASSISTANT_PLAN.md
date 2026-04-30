# Commander X Assistant Plan

This is the product plan for turning Commander X from a command router into a real local assistant.

## Problem

Commander X currently has useful primitives, but the assistant experience is still too rigid:

- One focused project can trap unrelated requests.
- General computer actions are not represented yet.
- ClickUp, browser, desktop apps, files, MCPs, skills, and plugins are not exposed through one coherent tool layer.
- Some answers are technically true but not useful enough.

## Operating Modes

### Focused Mode

Use this when working deeply on one project.

Behavior:

- Unqualified project commands can use the focused project.
- Natural language like "what changed?" uses the focused project.
- `/focus <project>` or `/mode focused <project>` enters focused mode.

### Free Mode

Use this when Commander should act like a general computer assistant.

Behavior:

- Commander does not silently assume the focused project.
- Ambiguous project actions ask for a project or return a broad overview.
- General actions can route to computer tools, integrations, files, browser, or Codex.
- `/free` or `/mode free` enters free mode.

## Tool Broker

Add a central tool broker instead of letting natural language invent actions.

Tool categories:

- Project tools: Codex sessions, Git, logs, diffs, queues, project profiles.
- Computer tools: open URL, open app, inspect file, volume, screenshots, process status.
- Web/browser tools: visit website, summarize page, screenshot page, check local app.
- Integrations: ClickUp first, then GitHub, Gmail, Calendar, Drive, WhatsApp.
- Codex tools: route work to Codex CLI with configured MCPs.
- Local assistant memory: preferences, repeated workflows, mistakes, project facts.

## Safety Rules

- No raw shell from Telegram.
- Computer actions must be allowlisted.
- File reads default to registered workspaces or explicit safe folders.
- Destructive actions, external sends, paid actions, credentials, deploys, and production data changes require explicit approval.
- Every action response must show evidence, not only intent.

## MVP Sequence

1. Modes:
   - `/mode`
   - `/free`
   - focused/free routing rules
   - dashboard mode indicator

2. Better Assistant Answers:
   - `/updates`
   - `/overview`
   - `/tools`
   - useful summaries from sessions, queue, Git, recent docs, and integrations

3. Computer Tool Broker:
   - `/open url <url>`
   - `/open app <allowlisted app>`
   - `/file <registered project> <path>`
   - `/screenshot`
   - `/volume <up|down|mute|level>`
   - Status: Monster v1 implemented as `/computer`, `/open`, `/file`, and `/volume` with natural-language routing.

4. ClickUp:
   - `/clickup status`
   - `/clickup tasks`
   - `/clickup task <id>`
   - "start Codex from this ClickUp task"
   - Status: API bridge started as `/clickup status` and `/clickup recent [query]`; task-to-Codex dispatch is next.

5. Codex Tool Visibility:
   - `/tools` shows Commander-native tools and `codex mcp list`.
   - Add explicit checks for whether Codex CLI can see required MCPs.
   - Treat Desktop-only skills/plugins as unavailable until exposed through a broker or a Codex CLI session.

6. Model Upgrade:
   - Keep deterministic routing for common workflows.
   - Allow `OPENAI_COMMAND_MODEL` to be upgraded for ambiguous requests.
   - Use the model for interpretation, not as the only control layer.

## Success Standard

Commander X should answer:

> What are the latest updates about the campaigns?

with a useful operating brief:

- running Codex sessions
- campaign status
- leads if ClickUp/CRM is wired
- recent files/docs/logs
- blockers
- recommended next action

It should not force the user to remember slash commands.
