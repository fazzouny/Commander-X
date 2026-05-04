const state = {
  dashboard: null,
};
const tokenStorageKey = "commanderDashboardToken";

const qs = (selector) => document.querySelector(selector);

function pill(text, type = "") {
  return `<span class="pill ${type}">${text}</span>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(path, options = {}) {
  const token = localStorage.getItem(tokenStorageKey) || "";
  const headers = { "Content-Type": "application/json", ...(token ? { "X-Commander-Token": token } : {}) };
  const response = await fetch(path, {
    headers,
    ...options,
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.error || payload.text || message;
    } catch {
      message = response.statusText || message;
    }
    throw new Error(message);
  }
  return response.json();
}

function hydrateDashboardToken() {
  const input = qs("#dashboard-token");
  const params = new URLSearchParams(window.location.search);
  const urlToken = params.get("token");
  if (urlToken) {
    localStorage.setItem(tokenStorageKey, urlToken);
    window.history.replaceState({}, "", window.location.pathname);
  }
  input.value = localStorage.getItem(tokenStorageKey) || "";
}

function saveDashboardToken() {
  const value = qs("#dashboard-token").value.trim();
  if (value) {
    localStorage.setItem(tokenStorageKey, value);
  }
  refresh().catch((error) => {
    qs("#metrics").innerHTML = `<div class="metric"><strong>Error</strong><span>${escapeHtml(error.message)}</span></div>`;
  });
}

function clearDashboardToken() {
  localStorage.removeItem(tokenStorageKey);
  qs("#dashboard-token").value = "";
}

function renderMetrics(data) {
  const projects = Object.values(data.projects);
  const sessions = Object.values(data.sessions);
  const tasks = data.tasks || [];
  const changed = projects.reduce((sum, project) => sum + project.changed_count, 0);
  const running = sessions.filter((session) => session.state === "running").length;
  const heartbeat = Object.values(data.heartbeat)[0] || {};
  const autopilot = data.autopilot || [];
  const mode = heartbeat.assistant_mode || "free";
  const doctorScore = data.doctor ? data.doctor.score : "-";
  const cache = data.dashboard_cache || {};
  const cacheStatus = cache.refreshing ? "Refreshing" : cache.stale ? "Stale" : "Fresh";
  qs("#metrics").innerHTML = `
    <div class="metric"><strong>${projects.length}</strong><span>Registered projects</span></div>
    <div class="metric"><strong>${running}</strong><span>Running sessions</span></div>
    <div class="metric"><strong>${tasks.filter((task) => ["queued", "running", "review", "failed"].includes(task.status)).length}</strong><span>Active tasks</span></div>
    <div class="metric"><strong>${changed}</strong><span>Changed files tracked</span></div>
    <div class="metric"><strong>${data.memory_count || 0}</strong><span>Memories</span></div>
    <div class="metric"><strong>${escapeHtml(doctorScore)}</strong><span>Doctor score</span></div>
    <div class="metric"><strong>${escapeHtml(mode)}</strong><span>Assistant mode</span></div>
    <div class="metric"><strong>${autopilot.filter((item) => item.enabled).length}</strong><span>Autopilot projects</span></div>
    <div class="metric"><strong>${heartbeat.enabled ? "On" : "Off"}</strong><span>Heartbeat</span></div>
    <div class="metric"><strong>${escapeHtml(cacheStatus)}</strong><span>Dashboard cache ${cache.age_seconds || 0}s old</span></div>
  `;
}

function renderProjects(data) {
  const projects = Object.values(data.projects);
  qs("#project-count").textContent = `${projects.length} registered`;
  qs("#projects").innerHTML = projects
    .map((project) => {
      const status = project.allowed && project.exists ? pill("enabled", "good") : pill("disabled", "bad");
      const dirty = project.changed_count > 0 ? pill(`${project.changed_count} changed`, "warn") : pill("clean", "good");
      return `
        <div class="row">
          <div class="row-main">
            <div class="row-title">${escapeHtml(project.id)}</div>
            <div class="row-meta">branch ${escapeHtml(project.branch || "-")}</div>
          </div>
          <div>${status} ${dirty}</div>
        </div>
      `;
    })
    .join("");

  const selects = [qs("#start-project"), qs("#evidence-project"), qs("#memory-project")];
  for (const select of selects) {
    const current = select.value;
    select.innerHTML = projects
      .filter((project) => project.allowed)
      .map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.id)}</option>`)
      .join("");
    if (current) select.value = current;
  }
}

function actionButton(item, action) {
  const cls = action.style === "danger" ? " class=\"danger\"" : "";
  const label = escapeHtml(action.label || action.action || "Action");
  if (action.type === "approval") {
    return `<button${cls} data-approval-action="${escapeHtml(action.action)}" data-project="${escapeHtml(item.project || "")}" data-id="${escapeHtml(item.approval_id || "")}">${label}</button>`;
  }
  if (action.type === "task") {
    return `<button${cls} data-task-action="${escapeHtml(action.action)}" data-id="${escapeHtml(item.task_id || "")}">${label}</button>`;
  }
  if (action.type === "work") {
    return `<button${cls} data-work-action="${escapeHtml(action.action)}" data-project="${escapeHtml(item.project || "")}">${label}</button>`;
  }
  return "";
}

function renderActionCenter(data) {
  const items = data.action_center || [];
  qs("#action-center-count").textContent = `${items.length} items`;
  qs("#action-center").innerHTML =
    items
      .slice(0, 12)
      .map((item) => {
        const type = item.priority === "high" ? "bad" : item.priority === "medium" ? "warn" : "good";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.title || "-")}</div>
              <div class="row-meta">${escapeHtml(item.detail || "-")}</div>
              <div class="action-center-actions">
                ${(item.actions || []).map((action) => actionButton(item, action)).join("")}
              </div>
            </div>
            <div>${pill(item.priority || "low", type)}</div>
          </div>
        `;
      })
      .join("") || `<p>No pending action-center items.</p>`;
}

function renderOwnerReviews(data) {
  const items = data.owner_reviews || [];
  qs("#owner-review-count").textContent = `${items.length} saved`;
  qs("#owner-reviews").innerHTML =
    items
      .slice(0, 8)
      .map((item) => {
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.project || "-")}</div>
              <div class="row-meta">${escapeHtml(item.summary || "Saved owner review pack ready.")}</div>
              <div class="row-meta">Saved ${escapeHtml(item.saved_at || "-")} - ${escapeHtml(item.size || "-")}</div>
              <div class="action-center-actions">
                <button data-command="${escapeHtml(item.command || "/reviews")}">Copy Command</button>
              </div>
            </div>
            <div>${pill("review pack", "good")}</div>
          </div>
        `;
      })
      .join("") || `<p>No saved owner review packs yet. Use /review &lt;project&gt; save after a milestone is ready.</p>`;
}

function renderAutopilot(data) {
  const items = data.autopilot || [];
  qs("#autopilot-count").textContent = `${items.length} configured`;
  qs("#autopilot").innerHTML =
    items
      .slice(0, 10)
      .map((item) => {
        const type = item.enabled && item.can_start ? "good" : item.enabled ? "warn" : "bad";
        const status = item.enabled ? (item.can_start ? "ready" : item.reason || "waiting") : "off";
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.next_criterion || "No open criterion")}</div>
              </div>
              <div>${pill(status, type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Definition of Done</span><strong>${escapeHtml(item.done_criteria || 0)} / ${escapeHtml(item.total_criteria || 0)} complete</strong></div>
              <div><span>Open criteria</span><strong>${escapeHtml(item.open_criteria || 0)}</strong></div>
              <div><span>Blocked criteria</span><strong>${escapeHtml(item.blocked_criteria || 0)}</strong></div>
              <div><span>Interval</span><strong>${escapeHtml(item.interval_minutes || 5)} min</strong></div>
              <div><span>Can start</span><strong>${item.can_start ? "yes" : "no"}</strong></div>
              <div><span>Next action</span><strong>${escapeHtml(item.next_action || "-")}</strong></div>
              <div><span>Last start</span><strong>${escapeHtml(item.last_started_at || "-")}</strong></div>
            </div>
            <div class="work-actions">
              <button data-command="${escapeHtml(item.command || "/autopilot status")}">Copy Command</button>
              <button data-command="/autopilot status">Status</button>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No project autopilot configured yet.</p>`;
}

function renderAuditTrail(data) {
  const audit = data.audit_trail || {};
  const items = audit.items || [];
  qs("#audit-count").textContent = `${items.length} events`;
  qs("#audit-summary").textContent = audit.summary || "No approval audit events recorded yet.";
  qs("#audit-trail").innerHTML =
    items
      .slice(0, 12)
      .map((item) => {
        const status = String(item.status || "").toLowerCase();
        const type = status === "approved" ? "good" : status === "cancelled" ? "warn" : status === "blocked" || status === "failed" ? "bad" : "warn";
        const result = item.result ? `<div class="row-meta">Result: ${escapeHtml(item.result)}</div>` : "";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.type || "action")} - ${escapeHtml(item.status || "recorded")}</div>
              <div class="row-meta">${escapeHtml(item.project || "-")} - ${escapeHtml(item.at || "-")} - approval ${escapeHtml(item.approval_id || "-")}</div>
              <div class="row-meta">${escapeHtml(item.summary || "-")}</div>
              ${result}
            </div>
            <div>${pill(item.status || "recorded", type)}</div>
          </div>
        `;
      })
      .join("") || `<p>No approval audit events recorded yet.</p>`;
}

function renderConversation(data) {
  const conversation = data.conversation || {};
  const items = conversation.items || [];
  qs("#conversation-count").textContent = `${items.length} events`;
  qs("#conversation-summary").textContent = conversation.summary || "No recent Telegram conversation events.";
  qs("#conversation").innerHTML =
    items
      .slice(0, 12)
      .map((item) => {
        const type = item.status === "warn" ? "warn" : item.status === "bad" ? "bad" : "good";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.direction || "Conversation event")}</div>
              <div class="row-meta">${escapeHtml(item.actor || "Commander X")} - ${escapeHtml(item.at || "-")}</div>
              <div class="row-meta">${escapeHtml(item.summary || "-")}</div>
            </div>
            <div>${pill(item.kind || "event", type)}</div>
          </div>
        `;
      })
      .join("") || `<p>No recent Telegram conversation events.</p>`;
}

function renderDecisionSuggestions(data) {
  const items = data.decision_suggestions || [];
  qs("#decision-suggestion-count").textContent = `${items.length} suggestions`;
  qs("#decision-suggestions").innerHTML =
    items
      .slice(0, 6)
      .map((item) => {
        const type = item.confidence === "high" ? "good" : "warn";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.title || "Suggested memory")}</div>
              <div class="row-meta">${escapeHtml(item.note || "-")}</div>
              <div class="row-meta">Evidence: ${escapeHtml(item.evidence || "-")} - Matches: ${escapeHtml(item.matches || 1)}</div>
              <div class="action-center-actions">
                <button data-decision-note="${escapeHtml(item.note || "")}" data-decision-scope="${escapeHtml(item.scope || "user")}">Save Memory</button>
                <button data-command="${escapeHtml(`/remember ${item.scope || "user"} ${item.note || ""}`)}">Copy Command</button>
              </div>
            </div>
            <div>${pill(item.confidence || "medium", type)}</div>
          </div>
        `;
      })
      .join("") || `<p>No new behavior suggestions. Existing memories already cover the current signals.</p>`;
}

function renderMissionTimeline(data) {
  const items = data.mission_timeline || [];
  qs("#mission-count").textContent = `${items.length} items`;
  qs("#mission-timeline").innerHTML =
    items
      .slice(0, 10)
      .map((item) => {
        const type = item.status === "bad" ? "bad" : item.status === "warn" ? "warn" : "good";
        const age = Number.isInteger(item.last_activity_minutes) ? `${item.last_activity_minutes} min ago` : "not available";
        const evidence = (item.evidence || [])
          .slice(0, 4)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.stage || "Tracking")}</div>
              </div>
              <div>${pill(item.freshness || item.status || "tracking", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Direction</span><strong>${escapeHtml(item.direction || "-")}</strong></div>
              <div><span>Work areas</span><strong>${escapeHtml(item.work_areas || "no local changes tracked")} (${escapeHtml(item.changed_count || 0)} changed)</strong></div>
              <div><span>Blocker</span><strong>${escapeHtml(item.blocker || "none reported")}</strong></div>
              <div><span>Last activity</span><strong>${escapeHtml(age)}</strong></div>
              <div><span>Next</span><strong>${escapeHtml(item.next_step || "-")}</strong></div>
              <div><span>Command</span><strong>${escapeHtml(item.command || "-")}</strong></div>
            </div>
            <div class="timeline-mini">${evidence || "<span>No detailed evidence yet</span>"}</div>
            <div class="work-actions">
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
              <button data-work-action="plan" data-project="${escapeHtml(item.project || "")}">Plan</button>
              <button data-work-action="changes" data-project="${escapeHtml(item.project || "")}">Areas</button>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No mission timeline items yet.</p>`;
}

function renderOperatorPlayback(data) {
  const items = data.operator_playback || [];
  qs("#operator-playback-count").textContent = `${items.length} views`;
  qs("#operator-playback").innerHTML =
    items
      .slice(0, 6)
      .map((item) => {
        const confidence = String(item.confidence || "").toLowerCase();
        const type = confidence.includes("blocked") || confidence.includes("decision") ? "warn" : confidence.includes("needs") ? "bad" : "good";
        const checks = (item.checks || [])
          .slice(0, 3)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        const approvals = (item.pending_approvals || [])
          .slice(0, 3)
          .map((approval) => `<span>${escapeHtml(approval.type || "approval")} ${escapeHtml(approval.id || "")}: ${escapeHtml(approval.message || approval.branch || "-")}</span>`)
          .join("");
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.story || "-")}</div>
              </div>
              <div>${pill(item.confidence || "unknown", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Outcome</span><strong>${escapeHtml(item.outcome || "-")}</strong></div>
              <div><span>Work areas</span><strong>${escapeHtml(item.work_areas || "no local changes tracked")} (${escapeHtml(item.changed_count || 0)} changed)</strong></div>
              <div><span>Blocker</span><strong>${escapeHtml(item.blocker || "none reported")}</strong></div>
              <div><span>Next</span><strong>${escapeHtml(item.next_step || "-")}</strong></div>
              <div><span>Primary action</span><strong>${escapeHtml(item.primary_action || "-")}</strong></div>
              <div><span>State</span><strong>${escapeHtml(item.state || "unknown")}</strong></div>
            </div>
            <div class="timeline-mini">${checks || "<span>No checks recorded yet</span>"}</div>
            <div class="timeline-mini">${approvals || "<span>No pending approvals</span>"}</div>
            <div class="work-actions">
              <button data-work-action="playback" data-project="${escapeHtml(item.project || "")}">Playback</button>
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
              <button data-work-action="evidence" data-project="${escapeHtml(item.project || "")}">Evidence</button>
              <button data-work-action="replay" data-project="${escapeHtml(item.project || "")}">Replay</button>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No operator playback views yet.</p>`;
}

function renderProjectCompletion(data) {
  const items = data.project_completion || [];
  qs("#project-completion-count").textContent = `${items.length} checks`;
  qs("#project-completion").innerHTML =
    items
      .slice(0, 6)
      .map((item) => {
        const verdict = String(item.verdict || "").toLowerCase();
        const type = verdict.includes("100") || verdict === "done candidate" ? "good" : verdict.includes("blocked") || verdict.includes("missing") ? "bad" : "warn";
        const criteria = (item.criteria || [])
          .slice(0, 5)
          .map((criterion, index) => `<span>${index + 1}. [${escapeHtml(criterion.status || "open")}] ${escapeHtml(criterion.text || "-")}</span>`)
          .join("");
        const checks = (item.checks || [])
          .slice(0, 3)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.objective || "Objective not set")}</div>
              </div>
              <div>${pill(`${item.completion_percent || 0}% ${item.verdict || "unknown"}`, type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Criteria</span><strong>${escapeHtml(item.done_criteria || 0)} / ${escapeHtml(item.total_criteria || 0)} done</strong></div>
              <div><span>State</span><strong>${escapeHtml(item.state || "unknown")}</strong></div>
              <div><span>Blocker</span><strong>${escapeHtml(item.blocker || "none reported")}</strong></div>
              <div><span>Changed count</span><strong>${escapeHtml(item.changed_count || 0)}</strong></div>
              <div><span>Next</span><strong>${escapeHtml(item.next_step || "-")}</strong></div>
              <div><span>Primary action</span><strong>${escapeHtml(item.primary_action || "-")}</strong></div>
            </div>
            <div class="timeline-mini">${criteria || "<span>No Definition of Done configured</span>"}</div>
            <div class="timeline-mini">${checks || "<span>No verification proof recorded yet</span>"}</div>
            <div class="work-actions">
              <button data-work-action="done" data-project="${escapeHtml(item.project || "")}">Done?</button>
              <button data-work-action="playback" data-project="${escapeHtml(item.project || "")}">Playback</button>
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No completion checks yet.</p>`;
}

function renderSessionEvidence(data) {
  const items = data.session_evidence || [];
  qs("#session-evidence-count").textContent = `${items.length} cards`;
  qs("#session-evidence").innerHTML =
    items
      .slice(0, 8)
      .map((item) => {
        const blocker = String(item.blocker || "").toLowerCase();
        const type = blocker && blocker !== "none reported" ? "warn" : item.state === "failed" ? "bad" : "good";
        const checks = (item.checks || [])
          .slice(0, 4)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        const timeline = (item.timeline || [])
          .slice(0, 4)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.task || "-")}</div>
              </div>
              <div>${pill(item.state || "unknown", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Risk</span><strong>${escapeHtml(item.risk || "unknown")}</strong></div>
              <div><span>Work areas</span><strong>${escapeHtml(item.areas || "no local changes tracked")} (${escapeHtml(item.changed_count || 0)} changed)</strong></div>
              <div><span>Blocker</span><strong>${escapeHtml(item.blocker || "none reported")}</strong></div>
              <div><span>Process</span><strong>${escapeHtml(item.process || "-")}</strong></div>
              <div><span>Task ID</span><strong>${escapeHtml(item.task_id || "-")}</strong></div>
              <div><span>Log age</span><strong>${Number.isInteger(item.log_age_minutes) ? `${item.log_age_minutes} min` : "not available"}</strong></div>
            </div>
            <div class="timeline-mini">${checks || "<span>No checks recorded yet</span>"}</div>
            <div class="timeline-mini">${timeline || "<span>No timeline evidence yet</span>"}</div>
            <div class="work-actions">
              <button data-work-action="evidence" data-project="${escapeHtml(item.project || "")}">Evidence</button>
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
              <button data-work-action="changes" data-project="${escapeHtml(item.project || "")}">Areas</button>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No session evidence cards yet.</p>`;
}

function renderSessionReplay(data) {
  const items = data.session_replay || [];
  qs("#session-replay-count").textContent = `${items.length} stories`;
  qs("#session-replay").innerHTML =
    items
      .slice(0, 6)
      .map((item) => {
        const blocker = String(item.blocker || "").toLowerCase();
        const type = blocker && blocker !== "none reported" ? "warn" : item.state === "failed" ? "bad" : "good";
        const checks = (item.checks || [])
          .slice(0, 3)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        const decisions = (item.decisions || [])
          .slice(0, 3)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        const age = Number.isInteger(item.last_activity_minutes) ? `${item.last_activity_minutes} min ago` : "not available";
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.story || "-")}</div>
              </div>
              <div>${pill(item.state || "unknown", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Outcome</span><strong>${escapeHtml(item.outcome || "-")}</strong></div>
              <div><span>Work areas</span><strong>${escapeHtml(item.work_areas || "no local changes tracked")} (${escapeHtml(item.changed_count || 0)} changed)</strong></div>
              <div><span>Blocker</span><strong>${escapeHtml(item.blocker || "none reported")}</strong></div>
              <div><span>Last activity</span><strong>${escapeHtml(age)}</strong></div>
              <div><span>Next</span><strong>${escapeHtml(item.next_step || "-")}</strong></div>
              <div><span>Freshness</span><strong>${escapeHtml(item.freshness || "unknown")}</strong></div>
            </div>
            <div class="timeline-mini">${checks || "<span>No checks recorded yet</span>"}</div>
            <div class="timeline-mini">${decisions || "<span>No approval decisions recorded yet</span>"}</div>
            <div class="work-actions">
              <button data-work-action="replay" data-project="${escapeHtml(item.project || "")}">Replay</button>
              <button data-work-action="evidence" data-project="${escapeHtml(item.project || "")}">Evidence</button>
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No session replay stories yet.</p>`;
}

function renderSessionBriefs(data) {
  const items = data.session_briefs || [];
  qs("#session-brief-count").textContent = `${items.length} briefs`;
  qs("#session-briefs").innerHTML =
    items
      .slice(0, 8)
      .map((item) => {
        const type = item.needs_attention ? "warn" : item.state === "running" ? "good" : ["failed", "finished_unknown", "stop_failed"].includes(item.state) ? "bad" : "good";
        const age = Number.isInteger(item.last_activity_minutes) ? `${item.last_activity_minutes} min ago` : "not available";
        const timeline = (item.timeline || [])
          .slice(0, 3)
          .map((line) => `<span>${escapeHtml(line)}</span>`)
          .join("");
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.summary || "-")}</div>
              </div>
              <div>${pill(item.state || "unknown", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Task</span><strong>${escapeHtml(item.task || "-")}</strong></div>
              <div><span>Work areas</span><strong>${escapeHtml(item.areas || "no local changes tracked")} (${escapeHtml(item.changed_count || 0)} changed)</strong></div>
              <div><span>Attention</span><strong>${escapeHtml(item.needs_attention ? item.blocker || "review needed" : "no")}</strong></div>
              <div><span>Last activity</span><strong>${escapeHtml(age)}</strong></div>
              <div><span>Next</span><strong>${escapeHtml(item.next_step || "-")}</strong></div>
              <div><span>Phase</span><strong>${escapeHtml(item.phase || "-")}</strong></div>
            </div>
            <div class="timeline-mini">${timeline || "<span>No detailed timeline yet</span>"}</div>
            <div class="work-actions">
              <button data-work-action="brief" data-project="${escapeHtml(item.project || "")}">Brief</button>
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
              <button data-work-action="changes" data-project="${escapeHtml(item.project || "")}">Areas</button>
              ${item.state === "running" ? `<button class="danger" data-work-action="stop" data-project="${escapeHtml(item.project || "")}">Stop</button>` : ""}
            </div>
          </div>
        `;
      })
      .join("") || `<p>No session briefs yet.</p>`;
}

function renderRecentImages(data) {
  const items = data.recent_images || [];
  qs("#recent-image-count").textContent = `${items.length} images`;
  qs("#recent-images").innerHTML =
    items
      .slice(0, 6)
      .map((item) => {
        const risk = String(item.risk || "-").toLowerCase();
        const type = risk.includes("high") || risk.includes("secret") || risk.includes("credential") ? "bad" : risk.includes("medium") || risk.includes("review") ? "warn" : "good";
        const commands = (item.suggested_commands || [])
          .slice(0, 4)
          .map((command) => `<button data-command="${escapeHtml(command)}">${escapeHtml(command)}</button>`)
          .join("");
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.summary || "-")}</div>
                <div class="row-meta">${escapeHtml(item.user || "Telegram user")} - ${escapeHtml(item.at || "-")} - ${escapeHtml(item.kind || "image")}</div>
              </div>
              <div>${pill(item.risk || "review", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Visible text</span><strong>${escapeHtml(item.visible_text || "-")}</strong></div>
              <div><span>Likely intent</span><strong>${escapeHtml(item.likely_intent || "-")}</strong></div>
              <div><span>Safety note</span><strong>Images are context only. Actions still need text, voice, or buttons.</strong></div>
            </div>
            ${commands ? `<div class="command-chips">${commands}</div>` : ""}
          </div>
        `;
      })
      .join("") || `<p>No Telegram image context yet.</p>`;
}

function renderWorkFeed(data) {
  const items = data.work_feed || [];
  qs("#work-feed-count").textContent = `${items.length} items`;
  qs("#work-feed").innerHTML =
    items
      .slice(0, 10)
      .map((item) => {
        const type = item.state === "running" ? "good" : ["failed", "finished_unknown", "stop_failed"].includes(item.state) ? "bad" : item.blocker && item.blocker !== "none reported" ? "warn" : "good";
        const age = Number.isInteger(item.last_activity_minutes) ? `${item.last_activity_minutes} min ago` : "not available";
        return `
          <div class="work-card">
            <div class="work-card-head">
              <div>
                <div class="row-title">${escapeHtml(item.project || "-")}</div>
                <div class="row-meta">${escapeHtml(item.task || "-")}</div>
              </div>
              <div>${pill(item.state || "unknown", type)}</div>
            </div>
            <div class="work-grid">
              <div><span>Now</span><strong>${escapeHtml(item.current_step || "-")}</strong></div>
              <div><span>Direction</span><strong>${escapeHtml(item.detail || item.phase || "-")}</strong></div>
              <div><span>Work areas</span><strong>${escapeHtml(item.areas || "no local changes tracked")} (${escapeHtml(item.changed_count || 0)} changed)</strong></div>
              <div><span>Blocker</span><strong>${escapeHtml(item.blocker || "none reported")}</strong></div>
              <div><span>Last activity</span><strong>${escapeHtml(age)}</strong></div>
              <div><span>Next</span><strong>${escapeHtml(item.next_step || item.command || "-")}</strong></div>
            </div>
            <div class="work-actions">
              <button data-work-action="watch" data-project="${escapeHtml(item.project || "")}">Watch</button>
              <button data-work-action="changes" data-project="${escapeHtml(item.project || "")}">Areas</button>
              <button data-work-action="plan" data-project="${escapeHtml(item.project || "")}">Plan</button>
              ${item.state === "running" ? `<button class="danger" data-work-action="stop" data-project="${escapeHtml(item.project || "")}">Stop</button>` : ""}
            </div>
          </div>
        `;
      })
      .join("") || `<p>No active work feed items.</p>`;
}

function renderTasks(data) {
  const tasks = [...(data.tasks || [])].reverse();
  qs("#task-count").textContent = `${tasks.length} recent`;
  qs("#tasks").innerHTML =
    tasks
      .slice(0, 12)
      .map((task) => {
        const cls = task.status === "running" ? "good" : task.status === "failed" ? "bad" : task.status === "done" ? "good" : "warn";
        const status = task.status || "queued";
        const canStart = status === "queued";
        const canFinish = ["review", "failed", "stopped"].includes(status);
        const canCancel = ["queued", "review", "failed"].includes(status);
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">[${escapeHtml(task.id)}] ${escapeHtml(task.project)}</div>
              <div class="row-meta">${escapeHtml(task.title || "-")}</div>
              ${
                canStart || canFinish || canCancel
                  ? `<div class="task-actions">
                      ${canStart ? `<button data-task-action="start" data-id="${escapeHtml(task.id)}">Start</button>` : ""}
                      ${canFinish ? `<button data-task-action="done" data-id="${escapeHtml(task.id)}">Done</button>` : ""}
                      ${canCancel ? `<button class="danger" data-task-action="cancel" data-id="${escapeHtml(task.id)}">Cancel</button>` : ""}
                    </div>`
                  : ""
              }
            </div>
            <div>${pill(status, cls)}</div>
          </div>
        `;
      })
      .join("") || `<p>No queued tasks.</p>`;
  qs("#memory-count").textContent = `${data.memory_count || 0} saved`;
}

function renderSessions(data) {
  const sessions = Object.values(data.sessions);
  qs("#session-count").textContent = `${sessions.length} tracked`;
  qs("#sessions").innerHTML =
    sessions
      .map((session) => {
        const cls = session.state === "running" ? "good" : session.state === "failed" ? "bad" : "warn";
        const timeline = Array.isArray(session.timeline) ? session.timeline.slice(-3) : [];
        const plan = session.work_plan || {};
        const risk = plan.risk ? `Risk: ${plan.risk}` : "";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(session.project)}</div>
              <div class="row-meta">${escapeHtml(session.task || "-")}</div>
              <div class="row-meta">Phase: ${escapeHtml(session.current_phase || session.state || "unknown")}</div>
              ${risk ? `<div class="row-meta">${escapeHtml(risk)}</div>` : ""}
              ${
                timeline.length
                  ? `<div class="timeline-mini">${timeline
                      .map((item) => `<span>${escapeHtml(item.title || item.phase || "Update")}</span>`)
                      .join("")}</div>`
                  : ""
              }
            </div>
            <div>${pill(session.state || "unknown", cls)}</div>
          </div>
        `;
      })
      .join("") || `<p>No Commander-started sessions.</p>`;
}

function renderInbox(data) {
  const items = data.inbox || [];
  qs("#inbox-count").textContent = `${items.length} active`;
  qs("#inbox").innerHTML =
    items
      .slice(0, 10)
      .map((item) => {
        const type = item.priority === "high" ? "bad" : item.priority === "medium" ? "warn" : "good";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.title)}</div>
              <div class="row-meta">${escapeHtml(item.detail)}</div>
            </div>
            <div>${pill(item.priority, type)}</div>
          </div>
        `;
      })
      .join("") || `<p>No inbox items.</p>`;
}

function renderApprovals(data) {
  const approvals = data.approvals || [];
  qs("#approval-count").textContent = `${approvals.length} pending`;
  qs("#approvals").innerHTML =
    approvals
      .slice(0, 8)
      .map(
        (item) => `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.project)} [${escapeHtml(item.id)}]</div>
              <div class="row-meta">${escapeHtml(item.type)} on ${escapeHtml(item.branch || "-")}</div>
              ${item.message ? `<div class="row-meta">${escapeHtml(item.message)}</div>` : ""}
              <div class="approval-actions">
                <button data-approval-action="approve" data-project="${escapeHtml(item.project)}" data-id="${escapeHtml(item.id)}">Approve</button>
                <button class="danger" data-approval-action="cancel" data-project="${escapeHtml(item.project)}" data-id="${escapeHtml(item.id)}">Cancel</button>
              </div>
            </div>
            <div>${pill("approval", "warn")}</div>
          </div>
        `,
      )
      .join("") || `<p>No pending approvals.</p>`;
}

function renderChanges(data) {
  const changes = data.changes || [];
  const total = changes.reduce((sum, item) => sum + (item.changed_count || 0), 0);
  qs("#changes-count").textContent = `${changes.length} projects, ${total} files`;
  qs("#changes").innerHTML =
    changes
      .slice(0, 10)
      .map((item) => {
        const sensitive = item.sensitive_count > 0 ? pill(`${item.sensitive_count} sensitive`, "bad") : "";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(item.project)}</div>
              <div class="row-meta">${escapeHtml(item.changed_count)} files on ${escapeHtml(item.branch || "-")}</div>
              <div class="row-meta">Use /changes for plain-English summary or /diff ${escapeHtml(item.project)} for technical detail.</div>
            </div>
            <div>${sensitive || pill("changed", "warn")}</div>
          </div>
        `;
      })
      .join("") || `<p>No changed projects.</p>`;
}

function renderDoctor(data) {
  const doctor = data.doctor || { score: "-", checks: [] };
  const checks = doctor.checks || [];
  qs("#doctor-score").textContent = `${doctor.score}/100`;
  qs("#doctor").innerHTML =
    checks
      .slice(0, 12)
      .map((check) => {
        const type = check.status === "good" ? "good" : check.status === "bad" ? "bad" : "warn";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(check.label)}</div>
              <div class="row-meta">${escapeHtml(check.detail)}</div>
            </div>
            <div>${pill(check.status, type)}</div>
          </div>
        `;
      })
      .join("") || `<p>No doctor checks yet.</p>`;
}

function friendlyLogName(name) {
  if (name === "commander-service.out.log") return "Telegram service activity";
  if (name === "commander-service.err.log") return "Telegram service errors";
  if (name === "dashboard.out.log") return "Dashboard activity";
  if (name === "dashboard.err.log") return "Dashboard errors";
  if (name.includes("-")) return "Project run activity";
  return "Commander activity";
}

function renderLogs(data) {
  qs("#logs").innerHTML =
    data.logs
      .map((log) => {
        const kb = Math.round(log.size / 1024);
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">${escapeHtml(friendlyLogName(log.name))}</div>
              <div class="row-meta">${kb} KB</div>
            </div>
          </div>
        `;
      })
      .join("") || `<p>No logs yet.</p>`;
}

function renderTools(data) {
  const tools = data.tools || {};
  const apps = tools.apps || [];
  const skills = tools.skills || [];
  const plugins = tools.plugins || [];
  qs("#tool-count").textContent = `${apps.length} apps, ${skills.length} skills`;
  qs("#tools").innerHTML = `
    <div class="tool-block">
      <h3>Computer Broker</h3>
      <p>${apps.map((item) => escapeHtml(item)).join(", ") || "No allowlisted apps"}</p>
    </div>
    <div class="tool-block">
      <h3>Browser + ClickUp</h3>
      <p>Browser inspect/open: enabled</p>
      <p>ClickUp API: ${tools.clickup_configured ? "configured" : "not configured"}</p>
    </div>
    <div class="tool-block">
      <h3>Codex MCPs</h3>
      <pre>${escapeHtml(tools.mcp || "No MCP output")}</pre>
    </div>
    <div class="tool-block">
      <h3>Skills</h3>
      <p>${skills.map((item) => escapeHtml(item)).join(", ") || "No local skills found"}</p>
    </div>
    <div class="tool-block">
      <h3>Plugins</h3>
      <p>${plugins.map((item) => escapeHtml(item)).join(", ") || "No plugin cache found"}</p>
    </div>
  `;
}

function renderCapabilities(data) {
  const capabilities = data.capabilities || {};
  const highlights = capabilities.highlights || [];
  const commands = capabilities.commands || [];
  const counts = capabilities.counts || {};
  qs("#capability-count").textContent = `${counts.apps || 0} apps, ${counts.skills || 0} skills, ${counts.plugins || 0} plugins`;
  qs("#capabilities").innerHTML = `
    <div class="capability-summary">
      ${
        highlights
          .map(
            (item) => `
              <div class="row">
                <div class="row-main">
                  <div class="row-title">${escapeHtml(item)}</div>
                </div>
              </div>
            `,
          )
          .join("") || `<p>No capability snapshot yet.</p>`
      }
    </div>
    <div class="command-chips">
      ${commands.map((command) => `<button data-command="${escapeHtml(command)}">${escapeHtml(command)}</button>`).join("")}
    </div>
  `;
}

function renderOpenClaw(data) {
  const openclaw = data.openclaw || {};
  const stateText = openclaw.state || "unknown";
  const stateType = stateText === "running" || stateText === "startable" ? "good" : stateText === "traces" || stateText === "launchable" ? "warn" : "bad";
  qs("#openclaw-state").innerHTML = pill(stateText, stateType);
  const processes = openclaw.processes || [];
  const launchers = openclaw.available_launchers || [];
  qs("#openclaw").innerHTML = `
    <div class="row">
      <div class="row-main">
        <div class="row-title">Local traces</div>
        <div class="row-meta">Skills: ${escapeHtml(openclaw.skills_count || 0)}; plugin cache: ${openclaw.plugin_cache ? "yes" : "no"}; legacy checkout: ${openclaw.legacy_checkout ? "yes" : "no"}</div>
      </div>
      <div>${pill(openclaw.skills_count ? "found" : "missing", openclaw.skills_count ? "good" : "warn")}</div>
    </div>
    <div class="row">
      <div class="row-main">
        <div class="row-title">Trusted launcher</div>
        <div class="row-meta">${escapeHtml(openclaw.configured_launcher || openclaw.launcher_error || "not configured")}</div>
      </div>
      <div>${pill(openclaw.configured_launcher ? "configured" : "needed", openclaw.configured_launcher ? "good" : "warn")}</div>
    </div>
    <div class="row">
      <div class="row-main">
        <div class="row-title">Launcher candidates</div>
        <div class="row-meta">${launchers.map((item) => `${escapeHtml(item.label)}: ${escapeHtml(item.path)}`).join("; ") || "none"}</div>
      </div>
    </div>
    <div class="row">
      <div class="row-main">
        <div class="row-title">Running processes</div>
        <div class="row-meta">${processes.map((item) => escapeHtml(item)).join(", ") || "none"}</div>
      </div>
      <div>${pill(processes.length ? "running" : "idle", processes.length ? "good" : "warn")}</div>
    </div>
  `;
}

function renderEnv(data) {
  const env = data.env || {};
  const groups = Object.entries(env);
  const totals = groups.reduce(
    (acc, [, keys]) => {
      const values = Object.values(keys || {});
      acc.total += values.length;
      acc.configured += values.filter((status) => status === "configured").length;
      return acc;
    },
    { configured: 0, total: 0 },
  );
  qs("#env-count").textContent = `${totals.configured}/${totals.total} configured`;
  qs("#env").innerHTML = groups
    .map(([group, keys]) => {
      const values = Object.entries(keys || {});
      const configured = values.filter(([, status]) => status === "configured").length;
      return `
        <div class="row">
          <div class="row-main">
            <div class="row-title">${escapeHtml(group)}</div>
            <div class="row-meta">${configured}/${values.length} configured</div>
          </div>
          <div>${pill(configured === values.length ? "ready" : "missing", configured === values.length ? "good" : "warn")}</div>
        </div>
      `;
    })
    .join("");
}

function renderSystem(data) {
  const system = data.system || {};
  const disks = system.disk || [];
  qs("#system-status").textContent = system.machine || "";
  qs("#system").innerHTML = `
    <div class="row">
      <div class="row-main">
        <div class="row-title">Memory</div>
        <div class="row-meta">${escapeHtml(system.memory || "unknown")}</div>
      </div>
    </div>
    <div class="row">
      <div class="row-main">
        <div class="row-title">Battery</div>
        <div class="row-meta">${escapeHtml(system.battery || "unknown")}</div>
      </div>
    </div>
    ${disks
      .map(
        (disk) => `
        <div class="row">
          <div class="row-main">
            <div class="row-title">${escapeHtml(disk.root || "-")}</div>
            <div class="row-meta">${escapeHtml(disk.free_gb)} GB free of ${escapeHtml(disk.total_gb)} GB</div>
          </div>
          <div>${pill(`${escapeHtml(disk.used_percent)}%`, disk.used_percent > 90 ? "bad" : disk.used_percent > 75 ? "warn" : "good")}</div>
        </div>
      `,
      )
      .join("")}
  `;
}

function renderRecommendations(data) {
  const items = data.recommendations || [];
  qs("#recommendation-count").textContent = `${items.length} active`;
  qs("#recommendations").innerHTML =
    items
      .map(
        (item, index) => `
        <div class="row">
          <div class="row-main">
            <div class="row-title">${index + 1}. ${escapeHtml(item)}</div>
          </div>
        </div>
      `,
      )
      .join("") || `<p>No urgent recommendations.</p>`;
}

async function refresh() {
  const data = await api("/api/dashboard");
  state.dashboard = data;
  renderMetrics(data);
  renderActionCenter(data);
  renderOwnerReviews(data);
  renderAutopilot(data);
  renderAuditTrail(data);
  renderConversation(data);
  renderDecisionSuggestions(data);
  renderMissionTimeline(data);
  renderOperatorPlayback(data);
  renderProjectCompletion(data);
  renderSessionEvidence(data);
  renderSessionReplay(data);
  renderSessionBriefs(data);
  renderRecentImages(data);
  renderWorkFeed(data);
  renderProjects(data);
  renderTasks(data);
  renderSessions(data);
  renderInbox(data);
  renderApprovals(data);
  renderChanges(data);
  renderDoctor(data);
  renderLogs(data);
  renderTools(data);
  renderCapabilities(data);
  renderOpenClaw(data);
  renderEnv(data);
  renderSystem(data);
  renderRecommendations(data);
}

async function openClawRecover() {
  qs("#openclaw-output").textContent = "Researching OpenClaw recovery options...";
  const result = await api("/api/openclaw/recover", {
    method: "POST",
    body: JSON.stringify({}),
  });
  qs("#openclaw-output").textContent = result.text || result.error || JSON.stringify(result, null, 2);
  await refresh();
}

async function openClawStart() {
  qs("#openclaw-output").textContent = "Preparing OpenClaw start approval...";
  const result = await api("/api/openclaw/start", {
    method: "POST",
    body: JSON.stringify({}),
  });
  qs("#openclaw-output").textContent = result.text || result.error || JSON.stringify(result, null, 2);
  await refresh();
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Could not read image file."));
    reader.readAsDataURL(file);
  });
}

async function analyzeDashboardImage() {
  const input = qs("#image-test-file");
  const file = input.files && input.files[0];
  const output = qs("#image-test-output");
  const button = qs("#image-test-button");
  if (!file) {
    output.textContent = "Choose a screenshot or image first.";
    return;
  }
  if (file.type && !file.type.startsWith("image/")) {
    output.textContent = "Choose a JPEG, PNG, WebP, or GIF image.";
    return;
  }
  button.disabled = true;
  output.textContent = "Analyzing image safely. No action will be executed from the image alone...";
  try {
    const dataUrl = await fileToDataUrl(file);
    const result = await api("/api/image/analyze", {
      method: "POST",
      body: JSON.stringify({
        data_url: dataUrl,
        filename: file.name || "dashboard-image",
        caption: qs("#image-test-caption").value.trim(),
      }),
    });
    output.textContent = result.text || result.error || JSON.stringify(result, null, 2);
    await refresh();
  } catch (error) {
    output.textContent = error.message || String(error);
  } finally {
    button.disabled = false;
  }
}

async function generateReport(save = false) {
  const output = qs("#report-output");
  const status = qs("#report-status");
  status.textContent = save ? "Saving..." : "Generating...";
  output.textContent = save ? "Saving sanitized operator report..." : "Generating sanitized operator report...";
  try {
    const result = await api("/api/report", {
      method: "POST",
      body: JSON.stringify({ save }),
    });
    output.textContent = result.text || result.error || JSON.stringify(result, null, 2);
    status.textContent = result.saved ? `Saved ${result.report_id || ""}`.trim() : "Preview";
  } catch (error) {
    output.textContent = error.message || String(error);
    status.textContent = "Failed";
  }
}

async function copyReport() {
  const text = qs("#report-output").textContent || "";
  if (!text.trim()) {
    qs("#report-status").textContent = "Nothing to copy";
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    qs("#report-status").textContent = "Copied";
  } catch {
    qs("#report-status").textContent = "Copy failed";
  }
}

async function handleApprovalClick(event) {
  const button = event.target.closest("[data-approval-action]");
  if (!button) return;
  const action = button.dataset.approvalAction;
  const project = button.dataset.project;
  const approvalId = button.dataset.id;
  if (!action || !project || !approvalId) return;
  button.disabled = true;
  qs("#action-output").textContent = `${action === "approve" ? "Approving" : "Cancelling"} ${approvalId}...`;
  try {
    const result = await api(`/api/approval/${encodeURIComponent(action)}`, {
      method: "POST",
      body: JSON.stringify({ project, approval_id: approvalId }),
    });
    qs("#action-output").textContent = result.text || result.error || JSON.stringify(result, null, 2);
  } finally {
    button.disabled = false;
  }
  await refresh();
}

async function handleTaskClick(event) {
  const button = event.target.closest("[data-task-action]");
  if (!button) return;
  const action = button.dataset.taskAction;
  const taskId = button.dataset.id;
  if (!action || !taskId) return;
  button.disabled = true;
  qs("#action-output").textContent = `${action === "done" ? "Marking done" : action === "cancel" ? "Cancelling" : "Starting"} ${taskId}...`;
  try {
    const result = await api(`/api/task/${encodeURIComponent(action)}`, {
      method: "POST",
      body: JSON.stringify({ task_id: taskId }),
    });
    qs("#action-output").textContent = result.text || result.error || JSON.stringify(result, null, 2);
  } finally {
    button.disabled = false;
  }
  await refresh();
}

async function handleWorkFeedClick(event) {
  const button = event.target.closest("[data-work-action]");
  if (!button) return;
  const action = button.dataset.workAction;
  const project = button.dataset.project;
  if (!action || !project) return;
  button.disabled = true;
  qs("#evidence").textContent = `${action === "stop" ? "Stopping" : "Loading"} ${project}...`;
  try {
    const result =
      action === "stop"
        ? await api("/api/stop", {
            method: "POST",
            body: JSON.stringify({ project }),
          })
        : await api(`/api/work/${encodeURIComponent(action)}/${encodeURIComponent(project)}`);
    qs("#evidence").textContent = result.text || result.error || JSON.stringify(result, null, 2);
  } finally {
    button.disabled = false;
  }
  if (action === "stop") await refresh();
}

async function handleCapabilityClick(event) {
  const button = event.target.closest("[data-command]");
  if (!button) return;
  const command = button.dataset.command;
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    qs("#action-output").textContent = `Copied ${command}`;
  } catch {
    qs("#action-output").textContent = command;
  }
}

async function handleDecisionSuggestionClick(event) {
  const button = event.target.closest("[data-decision-note]");
  if (!button) return;
  const note = button.dataset.decisionNote;
  const scope = button.dataset.decisionScope || "user";
  if (!note) return;
  button.disabled = true;
  qs("#action-output").textContent = "Saving Commander decision memory...";
  try {
    const result = await api("/api/decision-memory", {
      method: "POST",
      body: JSON.stringify({ note, scope }),
    });
    qs("#action-output").textContent = result.text || (result.memory ? `Saved memory ${result.memory.id}` : JSON.stringify(result, null, 2));
  } finally {
    button.disabled = false;
  }
  await refresh();
}

async function showDiff() {
  const project = qs("#evidence-project").value;
  const result = await api(`/api/diff/${encodeURIComponent(project)}`);
  qs("#evidence").textContent = result.text;
}

async function showLog() {
  const project = qs("#evidence-project").value;
  const result = await api(`/api/log/${encodeURIComponent(project)}`);
  qs("#evidence").textContent = result.text;
}

async function showProfile() {
  const project = qs("#evidence-project").value;
  const result = await api(`/api/profile/${encodeURIComponent(project)}`);
  qs("#evidence").textContent = result.text;
}

async function showEvidence() {
  const project = qs("#evidence-project").value;
  const result = await api(`/api/evidence/${encodeURIComponent(project)}`);
  qs("#evidence").textContent = result.text;
}

async function startTask() {
  const project = qs("#start-project").value;
  const task = qs("#start-task").value.trim();
  if (!task) {
    qs("#action-output").textContent = "Task is required.";
    return;
  }
  const result = await api("/api/start", {
    method: "POST",
    body: JSON.stringify({ project, task }),
  });
  qs("#action-output").textContent = result.text || result.error || JSON.stringify(result, null, 2);
  await refresh();
}

async function saveMemory() {
  const project = qs("#memory-project").value;
  const note = qs("#memory-note").value.trim();
  if (!note) {
    qs("#memory-output").textContent = "Memory note is required.";
    return;
  }
  const result = await api("/api/remember", {
    method: "POST",
    body: JSON.stringify({ project, note }),
  });
  qs("#memory-output").textContent = result.memory ? `Saved memory ${result.memory.id}` : result.error || JSON.stringify(result, null, 2);
  qs("#memory-note").value = "";
  await refresh();
}

qs("#refresh").addEventListener("click", refresh);
qs("#show-diff").addEventListener("click", showDiff);
qs("#show-log").addEventListener("click", showLog);
qs("#show-profile").addEventListener("click", showProfile);
qs("#show-evidence").addEventListener("click", showEvidence);
qs("#start-task-button").addEventListener("click", startTask);
qs("#save-memory").addEventListener("click", saveMemory);
qs("#openclaw-recover").addEventListener("click", openClawRecover);
qs("#openclaw-start").addEventListener("click", openClawStart);
qs("#image-test-button").addEventListener("click", analyzeDashboardImage);
qs("#preview-report").addEventListener("click", () => generateReport(false));
qs("#save-report").addEventListener("click", () => generateReport(true));
qs("#copy-report").addEventListener("click", copyReport);
qs("#save-dashboard-token").addEventListener("click", saveDashboardToken);
qs("#clear-dashboard-token").addEventListener("click", clearDashboardToken);
qs("#approvals").addEventListener("click", handleApprovalClick);
qs("#tasks").addEventListener("click", handleTaskClick);
qs("#action-center").addEventListener("click", handleApprovalClick);
qs("#action-center").addEventListener("click", handleTaskClick);
qs("#action-center").addEventListener("click", handleWorkFeedClick);
qs("#decision-suggestions").addEventListener("click", handleDecisionSuggestionClick);
qs("#decision-suggestions").addEventListener("click", handleCapabilityClick);
qs("#owner-reviews").addEventListener("click", handleCapabilityClick);
qs("#autopilot").addEventListener("click", handleCapabilityClick);
qs("#mission-timeline").addEventListener("click", handleWorkFeedClick);
qs("#operator-playback").addEventListener("click", handleWorkFeedClick);
qs("#project-completion").addEventListener("click", handleWorkFeedClick);
qs("#session-evidence").addEventListener("click", handleWorkFeedClick);
qs("#session-replay").addEventListener("click", handleWorkFeedClick);
qs("#session-briefs").addEventListener("click", handleWorkFeedClick);
qs("#recent-images").addEventListener("click", handleCapabilityClick);
qs("#work-feed").addEventListener("click", handleWorkFeedClick);
qs("#capabilities").addEventListener("click", handleCapabilityClick);

hydrateDashboardToken();
refresh().catch((error) => {
  qs("#metrics").innerHTML = `<div class="metric"><strong>Error</strong><span>${escapeHtml(error.message)}</span></div>`;
});
