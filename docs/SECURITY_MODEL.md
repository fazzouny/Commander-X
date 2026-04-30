# Security Model

Commander X is powerful because it can start local Codex sessions in real repositories. The security model is intentionally conservative.

## Hard Boundaries

- Telegram users must be allowlisted.
- Projects must be registered and enabled in `projects.json`.
- Chat messages cannot execute arbitrary shell commands.
- Secrets are redacted from bot replies and logs where possible.
- Sensitive files are blocked from commit approvals.
- Commits and pushes require a second explicit approval step.
- The dashboard binds to `127.0.0.1` by default.

## High Impact Actions

Commander should prepare but not execute these without explicit approval:

- Pushes.
- Deployments.
- Package installs.
- Production data changes.
- Credential or environment changes.
- External messages.
- Paid campaigns or billing changes.

## Recommended Remote Access

For personal use away from the laptop, prefer:

- Telegram for command control.
- Tailscale or Cloudflare Tunnel for dashboard access.
- `COMMANDER_DASHBOARD_TOKEN` if exposing the dashboard beyond localhost.

Do not expose the dashboard directly to the public internet without authentication.

## Evidence Requirement

Any successful execution claim should include at least some of:

- session state
- PID and whether it is running
- branch
- task ID
- changed file count
- log filename and freshness
- checks run
- blocker or next action
