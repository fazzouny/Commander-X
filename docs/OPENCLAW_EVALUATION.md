# OpenClaw Evaluation

Date checked: 2026-04-30

## Current Install Path

Official OpenClaw docs list these primary install options:

```powershell
iwr -useb https://openclaw.ai/install.ps1 | iex
```

or, if Node is already managed:

```powershell
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

They also list WSL/macOS/Linux install paths, source install, and verification commands:

```powershell
openclaw --version
openclaw doctor
openclaw gateway status
```

Source: https://docs.openclaw.ai/install

## Recommendation

Do not replace Commander X with OpenClaw yet.

Use OpenClaw as an optional sandbox or inspiration layer, while Commander X remains the Codex-specific control plane.

Why:

- Commander X already has the Codex project/session model this workflow needs.
- Telegram UX, project focus/free mode, approvals, logs, dashboard, and heartbeat behavior are already tailored here.
- OpenClaw is broader and more powerful, but that also means a larger local security and supply-chain surface.
- A full-computer assistant should be introduced behind the same approval gates, not as a blind replacement.

## Best Integration Strategy

1. Keep Commander X as the secure gateway.
2. Add OpenClaw as an optional local tool provider only after a sandbox install is verified.
3. Let Commander X call controlled OpenClaw capabilities later through allowlisted commands or APIs.
4. Keep Codex CLI sessions, Git discipline, and project registry inside Commander X.
5. Never expose OpenClaw raw shell or unrestricted local automation directly to Telegram.

## Safe Trial Plan

If testing OpenClaw:

1. Install it in WSL or a separate Windows user profile first.
2. Run `openclaw doctor` and `openclaw gateway status`.
3. Disable broad filesystem access until the trust model is clear.
4. Connect only one low-risk test project.
5. Compare whether it improves Telegram/phone control, browser/device control, MCP discovery, or dashboard UX.

If it proves better at a specific capability, copy the pattern or integrate that capability behind Commander X approval gates.
