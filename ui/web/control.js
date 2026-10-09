/* GENIE Control Center — vanilla JS, no build step.
 * Answers the three questions the roadmap's Phase 13 exit gate demands:
 *   what do you remember / which agents run / which provider sees my data.
 * Every number comes live from /api/control-center — no placeholders, no fake rows. */
"use strict";

const API = "/api";

/* Canonical fetch lives in ui/web/api.js. These two wrappers no longer
 * implement fetch themselves — they only adapt its result contract
 * (throw on non-2xx) for this console, whose callers catch and render.
 * The old postJSON ignored res.ok, so a failed control action came back
 * as {} and the console reported success. */
const getJSON = (p) => window.GenieApi.apiFetchStrict(p, { cache: "no-store" }, API);
const postJSON = (p, b) => window.GenieApi.apiFetchStrict(p, {
  method: "POST", body: JSON.stringify(b),
}, API);

function pill(text, cls) {
  return `<span class="pill ${cls || ""}">${text}</span>`;
}

/* 1 — what do you remember */
function renderMemory(view) {
  document.getElementById("mem-count").textContent = view.available ? `${view.total}` : "n/a";
  const tb = document.getElementById("mem-rows");
  tb.innerHTML = "";
  if (!view.available) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">unavailable: ${view.reason || "unknown"}</td></tr>`;
    document.getElementById("mem-types").textContent = "";
    return;
  }
  if (!view.records.length) {
    tb.innerHTML = '<tr><td colspan="5" class="muted">GENIE remembers nothing yet.</td></tr>';
  }
  for (const r of view.records) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${r.type || ""}</td><td>${r.entity || ""}</td>` +
      `<td>${r.value || ""}</td><td>${r.confidence != null ? r.confidence : ""}</td>`;
    const td = document.createElement("td");
    const b = document.createElement("button");
    b.className = "danger";
    b.textContent = "forget";
    b.onclick = async () => {
      b.disabled = true;
      const out = await postJSON("/control-center/forget", { record_id: r.record_id });
      if (out && out.ok) { await refresh(); } else { b.disabled = false; alert("could not forget: " + (out && out.error)); }
    };
    td.appendChild(b);
    tr.appendChild(td);
    tb.appendChild(tr);
  }
  const types = Object.entries(view.by_type || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
  document.getElementById("mem-types").textContent = types;
}

/* 2 — which agents run */
function renderAgents(view) {
  document.getElementById("agent-count").textContent = view.available ? `${view.live_agents}` : "n/a";
  const tb = document.getElementById("agent-rows");
  tb.innerHTML = "";
  if (!view.available) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">unavailable: ${view.reason || "unknown"}</td></tr>`;
    return;
  }
  if (!view.teams.length) {
    tb.innerHTML = '<tr><td colspan="5" class="muted">No agent teams are running.</td></tr>';
  }
  for (const t of view.teams) {
    const inner = t.status || {};
    const tasks = (inner.tasks && inner.tasks.tasks) || [];
    const done = tasks.filter(x => x.status === "done").length;
    const running = tasks.filter(x => x.status === "running").length;
    let state = "idle";
    let cls = "";
    if (inner.cancelled) { state = "cancelled"; cls = "bad"; }
    else if (inner.paused) { state = "paused"; cls = "warn"; }
    else if (running) { state = `${running} running`; cls = "ok"; }
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${t.mission_id || ""}</td>` +
      `<td>${(inner.team || []).length}</td>` +
      `<td>${done}/${tasks.length}${running ? ` (${running} live)` : ""}</td>` +
      `<td>${t.provider || "–"}</td>` +
      `<td>${pill(state, cls)}</td>`;
    tb.appendChild(tr);
  }
  const meta = [];
  if (view.running_tasks != null) meta.push(`${view.running_tasks} task(s) running now`);
  if (view.failovers != null) meta.push(`${view.failovers} failover(s)`);
  if (view.experience && view.experience.records != null)
    meta.push(`experience: ${view.experience.records} record(s), ` +
              `success ${view.experience.success_rate}`);
  document.getElementById("agent-meta").textContent = meta.join(" · ");
}

/* 3 — which provider sees my data */
function renderProviders(view) {
  document.getElementById("prov-count").textContent = view.available
    ? `${view.remote} remote · ${view.local} local` : "n/a";
  const tb = document.getElementById("prov-rows");
  tb.innerHTML = "";
  if (!view.available) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">unavailable: ${view.reason || "unknown"}</td></tr>`;
    return;
  }
  for (const p of view.providers) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.name || p.provider_id || ""}</td>` +
      `<td>${pill(p.locality, p.locality === "local" ? "ok" : "")}</td>` +
      `<td>${p.sees_my_data ? pill("YES", "bad") : pill("no", "ok")}</td>` +
      `<td>${p.model_count}</td>` +
      `<td>${p.enabled ? pill("enabled", "ok") : pill("disabled", "")}</td>`;
    tb.appendChild(tr);
  }
  document.getElementById("prov-meta").textContent =
    "'sees_my_data' is YES only for providers that are enabled AND not local.";
}

async function refresh() {
  try {
    const data = await getJSON("/control-center");
    renderMemory(data.memory || {});
    renderAgents(data.agents || {});
    renderProviders(data.providers || {});
    document.getElementById("stamp").textContent = "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("stamp").textContent = "offline: " + e.message;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  refresh();
  setInterval(refresh, 5000);
});
