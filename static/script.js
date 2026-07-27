/* Todo Manager — frontend (vanilla JS SPA) */
"use strict";

// ------------------------------------------------------------------ state --

const state = {
  token: localStorage.getItem("todoToken") || null,
  expiresAt: localStorage.getItem("todoExpires") || null,
  user: null,
  tasks: [],
  users: [],
  notifications: [],
  unread: 0,
  editId: null,
  page: "dashboard",
  filters: { status: "all", priority: "all", sort: "newest", scope: "mine", search: "" },
};

const $ = (id) => document.getElementById(id);

const PRIORITY_ORDER = { High: 0, Medium: 1, Low: 2 };
const CHART = { High: "#e04a85", Medium: "#cf7d1a", Low: "#119e70", line: "#3987e5" };

// -------------------------------------------------------------------- api --

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401 && state.token) {
    forceLogout("Your session has expired. Please sign in again.");
    throw new Error("Session expired");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// ------------------------------------------------------------------ utils --

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function initials(name) {
  return (name || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? value + "T00:00:00" : value + "Z");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

// ------------------------------------------------------------ auth & boot --

async function boot() {
  bindEvents();
  if (state.token) {
    try {
      const data = await api("/api/auth/me");
      state.user = data.user;
      enterApp();
      return;
    } catch { /* fall through to login */ }
  }
  showLogin();
}

function showLogin() {
  $("login-view").classList.remove("hidden");
  $("app-view").classList.add("hidden");
}

function forceLogout(message) {
  state.token = null;
  state.user = null;
  localStorage.removeItem("todoToken");
  localStorage.removeItem("todoExpires");
  localStorage.removeItem("todoPage");
  clearInterval(state._notifTimer);
  clearInterval(state._sessionTimer);
  showLogin();
  if (message) {
    const el = $("login-error");
    el.textContent = message;
    el.classList.remove("hidden");
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const btn = $("login-button");
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("login-username").value.trim(),
        password: $("login-password").value,
      }),
    });
    state.token = data.token;
    state.user = data.user;
    state.expiresAt = data.expires_at;
    localStorage.setItem("todoToken", data.token);
    localStorage.setItem("todoExpires", data.expires_at);
    $("login-error").classList.add("hidden");
    $("login-form").reset();
    enterApp();
  } catch (err) {
    const el = $("login-error");
    el.textContent = err.message;
    el.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Sign in";
  }
}

async function handleLogout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
  forceLogout();
}

function enterApp() {
  $("login-view").classList.add("hidden");
  $("app-view").classList.remove("hidden");

  $("user-name").textContent = state.user.display_name;
  $("user-role").textContent = state.user.role;
  $("user-avatar").textContent = initials(state.user.display_name);

  const isAdmin = state.user.role === "admin";
  $("nav-admin").classList.toggle("hidden", !isAdmin);
  $("scope-section").classList.toggle("hidden", !isAdmin);

  state.expiresAt = localStorage.getItem("todoExpires");
  startSessionTimer();

  loadUsers();
  loadTasks();
  loadNotifications();
  clearInterval(state._notifTimer);
  state._notifTimer = setInterval(loadNotifications, 30000);
  const saved = localStorage.getItem("todoPage");
  setPage(saved === "admin" && !isAdmin ? "dashboard" : (saved || "dashboard"));
}

// -------------------------------------------------------- session countdown --

function startSessionTimer() {
  clearInterval(state._sessionTimer);
  const tick = () => {
    if (!state.expiresAt) return;
    const ms = new Date(state.expiresAt).getTime() - Date.now();
    const el = $("session-timer");
    if (ms <= 0) {
      forceLogout("Your session has expired. Please sign in again.");
      return;
    }
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    const s = Math.floor((ms % 60000) / 1000);
    el.textContent = h > 0 ? `${h}h ${String(m).padStart(2, "0")}m`
                           : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    el.classList.toggle("warn", ms < 10 * 60000);
  };
  tick();
  state._sessionTimer = setInterval(tick, 1000);
}

// ------------------------------------------------------------- data loads --

async function loadTasks() {
  const scope = state.filters.scope === "all" ? "?scope=all" : "";
  const data = await api(`/api/tasks${scope}`);
  state.tasks = data.tasks;
  render();
}

async function loadUsers() {
  const data = await api("/api/users");
  state.users = data.users;
  const sel = $("assignee");
  const current = sel.value;
  sel.innerHTML = '<option value="">— Unassigned —</option>' + state.users
    .map((u) => `<option value="${u.id}">${esc(u.display_name)} (@${esc(u.username)})</option>`)
    .join("");
  sel.value = current;
}

async function loadNotifications() {
  try {
    const data = await api("/api/notifications");
    state.notifications = data.notifications;
    state.unread = data.unread;
    renderNotifications();
  } catch { /* polling failure is non-fatal */ }
}

// ------------------------------------------------------------- filtering --

function getFilteredTasks() {
  const { status, priority, search } = state.filters;
  let list = state.tasks.slice();

  if (status === "pending") list = list.filter((t) => t.status === "pending" && !t.overdue);
  else if (status === "completed") list = list.filter((t) => t.status === "completed");
  else if (status === "overdue") list = list.filter((t) => t.overdue);

  if (priority !== "all") list = list.filter((t) => t.priority === priority);

  if (search) {
    const q = search.toLowerCase();
    list = list.filter((t) =>
      [t.title, t.description, t.category, t.priority,
       t.created_by_display, t.assigned_to_display, t.due_date, t.requested_by]
        .some((f) => f && String(f).toLowerCase().includes(q)));
  }

  const { sort } = state.filters;
  list.sort((a, b) => {
    if (sort === "newest") return b.created_at.localeCompare(a.created_at);
    if (sort === "oldest") return a.created_at.localeCompare(b.created_at);
    if (sort === "priority")
      return PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority]
        || b.created_at.localeCompare(a.created_at);
    if (sort === "due")
      return (a.due_date || "9999").localeCompare(b.due_date || "9999");
    if (sort === "title") return a.title.localeCompare(b.title);
    return 0;
  });
  return list;
}

function filterSummary(count) {
  const f = state.filters;
  const bits = [];
  if (f.status !== "all") bits.push(f.status);
  if (f.priority !== "all") bits.push(`${f.priority} priority`);
  if (f.search) bits.push(`matching “${f.search}”`);
  if (f.scope === "all") bits.push("all users");
  return `${count} task${count === 1 ? "" : "s"}${bits.length ? " · " + bits.join(" · ") : ""}`;
}

// -------------------------------------------------------------- rendering --

function render() {
  const filtered = getFilteredTasks();
  renderBoard(filtered);
  renderDashboard(filtered);
}

function renderBoard(list) {
  const me = state.user;
  const rows = list.map((task) => {
    const canDelete = me.role === "admin" || task.created_by === me.id;
    const dueCell = task.due_date
      ? `<span class="${task.overdue ? "due-overdue" : ""}">${formatDate(task.due_date)}${task.overdue ? " ⚠" : ""}</span>`
      : "—";
    const assignee = task.assigned_to
      ? `<span class="assignee-tag"><span class="mini-avatar">${esc(initials(task.assigned_to_display))}</span>${esc(task.assigned_to_display)}</span>`
      : '<span style="color: var(--muted)">Unassigned</span>';
    const statusBadge = task.overdue
      ? '<span class="status-badge badge-overdue">⚠ Overdue</span>'
      : task.status === "completed"
        ? '<span class="status-badge badge-completed">✓ Completed</span>'
        : '<span class="status-badge badge-pending">● Pending</span>';
    return `
      <tr class="${task.status === "completed" ? "row-done" : ""}">
        <td><input type="checkbox" class="done-check" data-id="${task.id}"
             ${task.status === "completed" ? "checked" : ""} title="Mark done / undone" /></td>
        <td><span class="task-title">${esc(task.title)}</span>
            ${task.description ? `<span class="task-desc" title="${esc(task.description)}">${esc(task.description)}</span>` : ""}</td>
        <td><span class="task-pill pill-${task.priority.toLowerCase()}">${task.priority}</span></td>
        <td>${esc(task.category)}</td>
        <td><span title="${esc(task.created_at.replace("T", " "))} UTC">${formatDate(task.created_at)}</span></td>
        <td>${dueCell}</td>
        <td>${assignee}</td>
        <td>${esc(task.created_by_display)}</td>
        <td>${esc(task.requested_by) || '<span style="color: var(--muted)">—</span>'}</td>
        <td>${statusBadge}</td>
        <td><div class="actions">
          <button class="action-btn edit" data-id="${task.id}" title="Edit">✏️</button>
          ${canDelete ? `<button class="action-btn delete" data-id="${task.id}" title="Delete">🗑️</button>` : ""}
        </div></td>
      </tr>`;
  }).join("");

  $("task-table-body").innerHTML = rows;
  $("empty-state").classList.toggle("hidden", list.length > 0);
  $("board-note").textContent = state.filters.scope === "all"
    ? "Viewing every user's tasks (admin scope)."
    : "Tasks you created or that are assigned to you.";
}

// ------------------------------------------------------------- dashboard --

function renderDashboard(list) {
  const total = list.length;
  const completed = list.filter((t) => t.status === "completed").length;
  const overdue = list.filter((t) => t.overdue).length;
  const pending = total - completed;
  const pct = total ? Math.round((completed / total) * 100) : 0;

  $("stat-total").textContent = total;
  $("stat-pending").textContent = pending;
  $("stat-completed").textContent = completed;
  $("stat-overdue").textContent = overdue;
  $("stat-pct").textContent = pct + "%";
  $("stat-meter").style.width = pct + "%";
  $("filter-summary").textContent = filterSummary(total);

  renderTrendChart(list);
  renderDonutChart(list);
  renderCategoryChart(list);
}

// --- trend: tasks created per day, last 14 days (single series, no legend) ---

function renderTrendChart(list) {
  const days = [];
  const today = new Date();
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    days.push(d.toISOString().slice(0, 10));
  }
  const counts = days.map((day) =>
    list.filter((t) => t.created_at.slice(0, 10) === day).length);

  const W = 640, H = 220, PADL = 34, PADR = 12, PADT = 14, PADB = 30;
  const max = Math.max(4, ...counts);
  const x = (i) => PADL + (i * (W - PADL - PADR)) / (days.length - 1);
  const y = (v) => PADT + (1 - v / max) * (H - PADT - PADB);

  const gridLines = [];
  const step = Math.max(1, Math.ceil(max / 4));
  for (let v = 0; v <= max; v += step) {
    gridLines.push(`<line x1="${PADL}" y1="${y(v)}" x2="${W - PADR}" y2="${y(v)}"
      stroke="var(--chart-grid)" stroke-width="1"/>
      <text x="${PADL - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11"
        fill="var(--muted)">${v}</text>`);
  }

  const points = counts.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const area = `${PADL},${y(0)} ${points} ${x(days.length - 1)},${y(0)}`;

  const labels = days.map((day, i) => (i % 2 === 0 ? `<text x="${x(i)}" y="${H - 8}"
    text-anchor="middle" font-size="10.5" fill="var(--muted)">${day.slice(5).replace("-", "/")}</text>` : "")).join("");

  const dots = counts.map((v, i) => v > 0
    ? `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${CHART.line}"
        stroke="var(--surface)" stroke-width="2"/>` : "").join("");

  const colW = (W - PADL - PADR) / (days.length - 1);
  const hits = days.map((day, i) => `<rect x="${x(i) - colW / 2}" y="${PADT}"
    width="${colW}" height="${H - PADT - PADB}" fill="transparent"
    class="trend-hit" data-day="${day}" data-count="${counts[i]}"/>`).join("");

  $("trend-chart").innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily tasks entered, last 14 days">
      ${gridLines.join("")}
      <polygon points="${area}" fill="${CHART.line}" opacity="0.12"/>
      <polyline points="${points}" fill="none" stroke="${CHART.line}" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>
      ${dots}${labels}${hits}
    </svg>`;

  $("trend-chart").querySelectorAll(".trend-hit").forEach((hit) => {
    hit.addEventListener("mousemove", (e) =>
      showTooltip(e, formatDate(hit.dataset.day),
        `${hit.dataset.count} task${hit.dataset.count === "1" ? "" : "s"} entered`));
    hit.addEventListener("mouseleave", hideTooltip);
  });
}

function renderDonutChart(list) {
  const counts = {
    High: list.filter((t) => t.priority === "High").length,
    Medium: list.filter((t) => t.priority === "Medium").length,
    Low: list.filter((t) => t.priority === "Low").length,
  };
  const total = counts.High + counts.Medium + counts.Low;
  const R = 70, THICK = 26, C = 100;

  let svgParts = [];
  if (total === 0) {
    svgParts.push(`<circle cx="${C}" cy="${C}" r="${R}" fill="none"
      stroke="var(--chart-grid)" stroke-width="${THICK}"/>`);
  } else {
    let angle = -90;
    for (const key of ["High", "Medium", "Low"]) {
      if (!counts[key]) continue;
      const sweep = (counts[key] / total) * 360;
      if (sweep >= 359.9) {
        // single-priority selection: a full ring, since an arc can't close on itself
        svgParts.push(`<circle cx="${C}" cy="${C}" r="${R}" fill="none"
          stroke="${CHART[key]}" stroke-width="${THICK}" class="donut-seg"
          data-key="${key}" data-count="${counts[key]}" data-pct="100"/>`);
        break;
      }
      const gap = total > 0 && [counts.High, counts.Medium, counts.Low].filter(Boolean).length > 1 ? 2.4 : 0;
      const a0 = ((angle + gap / 2) * Math.PI) / 180;
      const a1 = ((angle + sweep - gap / 2) * Math.PI) / 180;
      const large = sweep - gap > 180 ? 1 : 0;
      const x0 = C + R * Math.cos(a0), y0 = C + R * Math.sin(a0);
      const x1 = C + R * Math.cos(a1), y1 = C + R * Math.sin(a1);
      svgParts.push(`<path d="M ${x0} ${y0} A ${R} ${R} 0 ${large} 1 ${x1} ${y1}"
        fill="none" stroke="${CHART[key]}" stroke-width="${THICK}"
        class="donut-seg" data-key="${key}" data-count="${counts[key]}"
        data-pct="${Math.round((counts[key] / total) * 100)}"/>`);
      angle += sweep;
    }
  }

  const legend = ["High", "Medium", "Low"].map((key) => `
    <div class="legend-item">
      <span class="legend-swatch" style="background:${CHART[key]}"></span>
      <span>${key}</span>
      <span class="legend-value">${counts[key]}${total ? ` · ${Math.round((counts[key] / total) * 100)}%` : ""}</span>
    </div>`).join("");

  $("donut-chart").innerHTML = `
    <svg viewBox="0 0 200 200" style="max-width:200px" role="img" aria-label="Task priority mix">
      ${svgParts.join("")}
      <text x="${C}" y="${C - 4}" text-anchor="middle" font-size="30" font-weight="700"
        fill="var(--text)">${total}</text>
      <text x="${C}" y="${C + 20}" text-anchor="middle" font-size="11"
        fill="var(--muted)">tasks</text>
    </svg>
    <div class="chart-legend">${legend}</div>`;

  $("donut-chart").querySelectorAll(".donut-seg").forEach((seg) => {
    seg.addEventListener("mousemove", (e) =>
      showTooltip(e, `${seg.dataset.key} priority`,
        `${seg.dataset.count} tasks · ${seg.dataset.pct}%`));
    seg.addEventListener("mouseleave", hideTooltip);
  });
}

// --- categories: horizontal bars ---

function renderCategoryChart(list) {
  const byCat = {};
  list.forEach((t) => { byCat[t.category] = (byCat[t.category] || 0) + 1; });
  const cats = Object.entries(byCat).sort((a, b) => b[1] - a[1]).slice(0, 8);

  if (!cats.length) {
    $("category-chart").innerHTML = '<p class="empty-state">No tasks to display.</p>';
    return;
  }

  const W = 640, ROW = 34, PADL = 130, PADR = 46;
  const H = cats.length * ROW + 8;
  const max = Math.max(...cats.map(([, v]) => v));

  const bars = cats.map(([cat, v], i) => {
    const bw = Math.max(4, (v / max) * (W - PADL - PADR));
    const yy = i * ROW + 6;
    return `
      <text x="${PADL - 10}" y="${yy + 15}" text-anchor="end" font-size="12"
        fill="var(--muted)">${esc(cat.length > 16 ? cat.slice(0, 15) + "…" : cat)}</text>
      <rect x="${PADL}" y="${yy}" width="${bw}" height="20" rx="4"
        fill="${CHART.line}" class="cat-bar" data-cat="${esc(cat)}" data-count="${v}"/>
      <text x="${PADL + bw + 8}" y="${yy + 15}" font-size="12" font-weight="600"
        fill="var(--text)">${v}</text>`;
  }).join("");

  $("category-chart").innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Tasks per category">${bars}</svg>`;

  $("category-chart").querySelectorAll(".cat-bar").forEach((bar) => {
    bar.addEventListener("mousemove", (e) =>
      showTooltip(e, bar.dataset.cat, `${bar.dataset.count} task${bar.dataset.count === "1" ? "" : "s"}`));
    bar.addEventListener("mouseleave", hideTooltip);
  });
}

// ------------------------------------------------------------- tooltip ----

let tooltipEl = null;

function showTooltip(event, title, value) {
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.className = "chart-tooltip";
    document.body.appendChild(tooltipEl);
  }
  tooltipEl.innerHTML = `<div class="tt-title">${esc(title)}</div><div class="tt-value">${esc(value)}</div>`;
  tooltipEl.style.left = event.clientX + "px";
  tooltipEl.style.top = event.clientY + "px";
  tooltipEl.style.display = "block";
}

function hideTooltip() {
  if (tooltipEl) tooltipEl.style.display = "none";
}

// -------------------------------------------------------- notifications ---

function renderNotifications() {
  const badge = $("bell-badge");
  badge.textContent = state.unread;
  badge.classList.toggle("hidden", state.unread === 0);

  $("notif-list").innerHTML = state.notifications.length
    ? state.notifications.map((n) => `
        <div class="notif-item ${n.is_read ? "" : "unread"}">
          ${esc(n.message)}
          <span class="notif-time">${timeAgo(n.created_at)}</span>
        </div>`).join("")
    : '<p class="notif-empty">No notifications yet.</p>';
}

// ------------------------------------------------------------ task form ---

function clearTaskForm() {
  $("task-form").reset();
  state.editId = null;
  $("submit-button").textContent = "Add Task";
  $("form-title").textContent = "New task";
}

function beginEdit(id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!task) return;
  setPage("tasks");
  $("form-toggle").checked = true;
  $("form-panel").classList.remove("hidden");
  $("title").value = task.title;
  $("priority").value = task.priority;
  $("category").value = task.category;
  $("dueDate").value = task.due_date || "";
  $("assignee").value = task.assigned_to || "";
  $("description").value = task.description;
  $("requestedBy").value = task.requested_by || "";
  state.editId = id;
  $("submit-button").textContent = "Update Task";
  $("form-title").textContent = "Edit task";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function submitTask(event) {
  event.preventDefault();
  const payload = {
    title: $("title").value.trim(),
    priority: $("priority").value,
    category: $("category").value.trim(),
    due_date: $("dueDate").value,
    assigned_to: $("assignee").value ? Number($("assignee").value) : null,
    description: $("description").value.trim(),
    requested_by: $("requestedBy").value.trim(),
  };
  try {
    if (state.editId) {
      await api(`/api/tasks/${state.editId}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("Task updated", "success");
    } else {
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      toast("Task added", "success");
    }
    clearTaskForm();
    await loadTasks();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function toggleDone(id) {
  try {
    await api(`/api/tasks/${id}/toggle`, { method: "POST" });
    await loadTasks();
  } catch (err) {
    toast(err.message, "error");
    render();
  }
}

async function deleteTask(id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!task) return;
  if (!confirm(`Delete "${task.title}"? This cannot be undone.`)) return;
  try {
    await api(`/api/tasks/${id}`, { method: "DELETE" });
    toast("Task deleted", "success");
    if (state.editId === id) clearTaskForm();
    await loadTasks();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ----------------------------------------------------------------- admin --

async function loadAdminUsers() {
  try {
    const data = await api("/api/admin/users");
    const me = state.user;
    $("user-table-body").innerHTML = data.users.map((u) => `
      <tr>
        <td><span class="assignee-tag"><span class="mini-avatar">${esc(initials(u.display_name))}</span>${esc(u.display_name)}</span></td>
        <td>@${esc(u.username)}</td>
        <td><span class="status-badge badge-${u.role}">${u.role === "admin" ? "🛡️ Admin" : "User"}</span></td>
        <td><span class="status-badge ${u.is_active ? "badge-completed" : "badge-inactive"}">${u.is_active ? "Active" : "Inactive"}</span></td>
        <td>${u.task_count}</td>
        <td><div class="actions">
          ${u.id === me.id ? '<span style="color:var(--muted);font-size:0.85rem">(you)</span>' : `
            <button class="action-btn u-role" data-id="${u.id}" data-role="${u.role === "admin" ? "user" : "admin"}"
              title="${u.role === "admin" ? "Demote to user" : "Promote to admin"}">${u.role === "admin" ? "⬇️" : "⬆️"}</button>
            <button class="action-btn u-active" data-id="${u.id}" data-active="${u.is_active ? 0 : 1}"
              title="${u.is_active ? "Deactivate" : "Reactivate"}">${u.is_active ? "🚫" : "✅"}</button>
            <button class="action-btn u-pw" data-id="${u.id}" data-name="${esc(u.username)}" title="Reset password">🔑</button>`}
        </div></td>
      </tr>`).join("");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function createUser(event) {
  event.preventDefault();
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        username: $("new-username").value.trim(),
        display_name: $("new-displayname").value.trim(),
        password: $("new-password").value,
        role: $("new-role").value,
      }),
    });
    toast("User created", "success");
    $("user-form").reset();
    loadAdminUsers();
    loadUsers();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function adminPatchUser(id, patch, okMessage) {
  try {
    await api(`/api/admin/users/${id}`, { method: "PUT", body: JSON.stringify(patch) });
    toast(okMessage, "success");
    loadAdminUsers();
    loadUsers();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ------------------------------------------------------------ navigation --

function setPage(page) {
  state.page = page;
  localStorage.setItem("todoPage", page);
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === page));
  ["dashboard", "tasks", "admin"].forEach((p) =>
    $(`page-${p}`).classList.toggle("hidden", p !== page));
  // the form toggle belongs to the Tasks page only
  $("form-toggle-wrap").classList.toggle("hidden", page !== "tasks");
  $("search").classList.toggle("hidden", page === "admin");
  $("page-title").textContent =
    page === "dashboard" ? "Dashboard" : page === "tasks" ? "Tasks" : "Admin";
  if (page === "admin") loadAdminUsers();
}

// --------------------------------------------------------------- events ---

function bindEvents() {
  $("login-form").addEventListener("submit", handleLogin);
  $("logout-button").addEventListener("click", handleLogout);

  document.querySelectorAll(".nav-btn").forEach((btn) =>
    btn.addEventListener("click", () => setPage(btn.dataset.page)));

  // chip groups
  const bindChips = (groupId, key, afterChange) => {
    $(groupId).querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        $(groupId).querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.filters[key] = chip.dataset.value;
        (afterChange || render)();
      });
    });
  };
  bindChips("status-filter", "status");
  bindChips("priority-filter", "priority");
  bindChips("scope-filter", "scope", loadTasks);

  $("sort-select").addEventListener("change", (e) => {
    state.filters.sort = e.target.value;
    render();
  });

  $("search").addEventListener("input", (e) => {
    state.filters.search = e.target.value.trim();
    render();
  });

  $("form-toggle").addEventListener("change", (e) => {
    $("form-panel").classList.toggle("hidden", !e.target.checked);
  });

  $("task-form").addEventListener("submit", submitTask);
  $("task-form").addEventListener("reset", () => setTimeout(clearTaskForm, 0));

  // delegated table actions
  $("task-table-body").addEventListener("click", (e) => {
    const edit = e.target.closest(".action-btn.edit");
    const del = e.target.closest(".action-btn.delete");
    if (edit) beginEdit(Number(edit.dataset.id));
    if (del) deleteTask(Number(del.dataset.id));
  });
  $("task-table-body").addEventListener("change", (e) => {
    if (e.target.classList.contains("done-check")) toggleDone(Number(e.target.dataset.id));
  });

  // notifications
  $("bell-button").addEventListener("click", (e) => {
    e.stopPropagation();
    $("notif-dropdown").classList.toggle("hidden");
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".bell-wrap")) $("notif-dropdown").classList.add("hidden");
  });
  $("mark-read-button").addEventListener("click", async () => {
    try {
      await api("/api/notifications/read", { method: "POST" });
      loadNotifications();
    } catch { /* ignore */ }
  });

  // admin
  $("user-form").addEventListener("submit", createUser);
  $("user-table-body").addEventListener("click", (e) => {
    const roleBtn = e.target.closest(".u-role");
    const activeBtn = e.target.closest(".u-active");
    const pwBtn = e.target.closest(".u-pw");
    if (roleBtn)
      adminPatchUser(Number(roleBtn.dataset.id), { role: roleBtn.dataset.role },
        `Role changed to ${roleBtn.dataset.role}`);
    if (activeBtn)
      adminPatchUser(Number(activeBtn.dataset.id),
        { is_active: activeBtn.dataset.active === "1" },
        activeBtn.dataset.active === "1" ? "User reactivated" : "User deactivated");
    if (pwBtn) {
      const pw = prompt(`New password for @${pwBtn.dataset.name} (min 8 chars):`);
      if (pw) adminPatchUser(Number(pwBtn.dataset.id), { password: pw }, "Password reset");
    }
  });

  // change-password modal
  $("pw-button").addEventListener("click", () => {
    $("pw-form").reset();
    $("pw-error").classList.add("hidden");
    $("pw-modal").classList.remove("hidden");
  });
  $("pw-cancel").addEventListener("click", () => $("pw-modal").classList.add("hidden"));
  $("pw-modal").addEventListener("click", (e) => {
    if (e.target === $("pw-modal")) $("pw-modal").classList.add("hidden");
  });
  $("pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: $("pw-current").value,
          new_password: $("pw-new").value,
        }),
      });
      $("pw-modal").classList.add("hidden");
      toast("Password updated", "success");
    } catch (err) {
      const el = $("pw-error");
      el.textContent = err.message;
      el.classList.remove("hidden");
    }
  });
}

boot();
