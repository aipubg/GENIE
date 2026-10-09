/* GENIE operator console — vanilla JS, no build step.
 * Polls /api/ops/dashboard and drives real, audited control actions via /api/ops/control.
 * No fake data: every number comes from the live fleet; every button mutates real state. */
"use strict";
const API = "/api";

function pct(n, d) { return d ? Math.round((n / d) * 100) : 0; }

/* Canonical fetch lives in ui/web/api.js. These two wrappers no longer
 * implement fetch themselves — they only adapt its result contract
 * (throw on non-2xx) for this console, whose callers catch and render.
 * The old postJSON ignored res.ok, so a failed control action came back
 * as {} and the console reported success. */
const getJSON = (p) => window.GenieApi.apiFetchStrict(p, { cache: "no-store" }, API);
const postJSON = (p, b) => window.GenieApi.apiFetchStrict(p, {
  method: "POST", body: JSON.stringify(b),
}, API);

function fmtBudget(b) {
  if (!b) return "–";
  const parts = [];
  if (b.cost_usd != null) parts.push("$" + b.cost_usd.toFixed(4));
  if (b.tokens != null) parts.push(b.tokens + " tok");
  if (b.model_calls != null) parts.push(b.model_calls + " calls");
  return parts.join(" / ") || "–";
}

function teamCard(t) {
  const el = document.createElement("div");
  el.className = "card";
  const flags = [];
  if (t.paused) flags.push('<span class="flag paused">PAUSED</span>');
  if (t.cancelled) flags.push('<span class="flag cancelled">CANCELLED</span>');
  const done = t.tasks.done, total = t.tasks.total;
  const prog = pct(done, total);
  el.innerHTML = `
    <h2><span>${t.mission_id}</span><span>${flags.join(" ") || (t.provider || "–")}</span></h2>
    <div class="kv">
      <b>objective</b><span>${t.objective || "–"}</span>
      <b>tasks</b><span>${done}/${total} done · ${t.tasks.failed} failed · ${t.tasks.pending} pending</span>
      <b>agents</b><span>${t.team_size} total · ${t.working} working · ${t.idle} idle · ${t.orphans.length} orphans</span>
      <b>concurrency</b><span>${t.max_concurrency}</span>
      <b>failovers</b><span>${t.failovers}</span>
      <b>budget left</b><span>${fmtBudget(t.budget_remaining)}</span>
      <b>artifacts</b><span>${t.artifacts} · mb ${t.mailbox_count} · bb ${t.blackboard_count}</span>
      <b>throughput</b><span>~${t.throughput_per_min}/min</span>
    </div>
    <div class="bar"><span style="width:${prog}%"></span></div>`;
  const btns = document.createElement("div");
  btns.className = "btns";
  const mk = (label, action, danger, extra) => {
    const b = document.createElement("button");
    b.textContent = label;
    if (danger) b.className = "danger";
    b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await postJSON("/ops/control", { action, mission_id: t.mission_id, ...(extra || {}) });
        flash(r.ok ? "ok: " + action : "err: " + (r.error || action));
      } finally { b.disabled = false; }
    };
    return b;
  };
  if (!t.paused && !t.cancelled) btns.appendChild(mk("pause", "pause"));
  if (t.paused) btns.appendChild(mk("resume", "resume"));
  btns.appendChild(mk("failover", "force_failover", false, { from_provider: t.provider || "primary", reason: "operator" }));
  btns.appendChild(mk("retire orphans", "retire_orphans"));
  btns.appendChild(mk("cap $0.5", "set_budget_cap", true, { cost_usd: 0.5 }));
  btns.appendChild(mk("throttle 2", "throttle", false, { max_concurrency: 2 }));
  btns.appendChild(mk("cancel", "cancel", true));
  el.appendChild(btns);
  return el;
}

let flashTimer = null;
function flash(msg) {
  let f = document.getElementById("flash");
  if (!f) { f = document.createElement("div"); f.id = "flash"; f.style.cssText =
    "position:fixed;bottom:14px;left:14px;background:#1f2730;border:1px solid #2c3744;padding:8px 12px;border-radius:8px;font-size:12px;"; document.body.appendChild(f); }
  f.textContent = msg;
  f.style.display = "block";
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { f.style.display = "none"; }, 2600);
}

function renderAudit(rows) {
  const tb = document.querySelector("#audit tbody");
  tb.innerHTML = "";
  for (const a of rows || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${a.ts || ""}</td><td>${a.who || ""}</td><td>${a.action || ""}</td>` +
      `<td>${a.result || ""}</td><td class="muted">${(a.why || "").slice(0, 70)}</td>`;
    tb.appendChild(tr);
  }
}

function renderImprove(sugg) {
  const el = document.getElementById("improvements");
  el.innerHTML = "";
  if (!sugg || !sugg.length) { el.innerHTML = '<span class="muted">no suggestions</span>'; return; }
  for (const s of sugg) {
    const d = document.createElement("div");
    d.style.margin = "4px 0";
    d.innerHTML = `<span class="sev-${s.severity}">[${s.severity}]</span> ${s.text}`;
    el.appendChild(d);
  }
}

async function tick() {
  try {
    const dash = await getJSON("/ops/dashboard");
    const g = dash.global;
    document.getElementById("fleet").textContent = `teams: ${g.teams} · paused ${g.paused}`;
    document.getElementById("budget").textContent = "budget: " + fmtBudget(g.global_budget_remaining);
    document.getElementById("exp").textContent = `experience: ${g.experience.records} (${g.experience.success_rate})`;
    const wrap = document.getElementById("teams");
    wrap.innerHTML = "";
    if (!dash.teams.length) wrap.innerHTML = '<span class="muted">no active missions</span>';
    for (const t of dash.teams) wrap.appendChild(teamCard(t));
    renderImprove(dash.improvements);
    renderAudit(dash.audit);
  } catch (e) {
    document.getElementById("fleet").textContent = "offline: " + e.message;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  tick();
  setInterval(() => { if (document.getElementById("auto").checked) tick(); }, 2000);
});
