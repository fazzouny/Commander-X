const state = {
  dashboard: null,
};

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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

function renderMetrics(data) {
  const projects = Object.values(data.projects);
  const sessions = Object.values(data.sessions);
  const tasks = data.tasks || [];
  const changed = projects.reduce((sum, project) => sum + project.changed_count, 0);
  const running = sessions.filter((session) => session.state === "running").length;
  const heartbeat = Object.values(data.heartbeat)[0] || {};
  const mode = heartbeat.assistant_mode || "free";
  const doctorScore = data.doctor ? data.doctor.score : "-";
  qs("#metrics").innerHTML = `
    <div class="metric"><strong>${projects.length}</strong><span>Registered projects</span></div>
    <div class="metric"><strong>${running}</strong><span>Running sessions</span></div>
    <div class="metric"><strong>${tasks.filter((task) => ["queued", "running", "review", "failed"].includes(task.status)).length}</strong><span>Active tasks</span></div>
    <div class="metric"><strong>${changed}</strong><span>Changed files tracked</span></div>
    <div class="metric"><strong>${data.memory_count || 0}</strong><span>Memories</span></div>
    <div class="metric"><strong>${escapeHtml(doctorScore)}</strong><span>Doctor score</span></div>
    <div class="metric"><strong>${escapeHtml(mode)}</strong><span>Assistant mode</span></div>
    <div class="metric"><strong>${heartbeat.enabled ? "On" : "Off"}</strong><span>Heartbeat</span></div>
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

function renderTasks(data) {
  const tasks = [...(data.tasks || [])].reverse();
  qs("#task-count").textContent = `${tasks.length} recent`;
  qs("#tasks").innerHTML =
    tasks
      .slice(0, 12)
      .map((task) => {
        const cls = task.status === "running" ? "good" : task.status === "failed" ? "bad" : task.status === "done" ? "good" : "warn";
        return `
          <div class="row">
            <div class="row-main">
              <div class="row-title">[${escapeHtml(task.id)}] ${escapeHtml(task.project)}</div>
              <div class="row-meta">${escapeHtml(task.title || "-")}</div>
            </div>
            <div>${pill(task.status || "queued", cls)}</div>
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
  renderProjects(data);
  renderTasks(data);
  renderSessions(data);
  renderInbox(data);
  renderApprovals(data);
  renderChanges(data);
  renderDoctor(data);
  renderLogs(data);
  renderTools(data);
  renderEnv(data);
  renderSystem(data);
  renderRecommendations(data);
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

refresh().catch((error) => {
  qs("#metrics").innerHTML = `<div class="metric"><strong>Error</strong><span>${escapeHtml(error.message)}</span></div>`;
});
