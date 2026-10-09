/* API ↔ UI contract regression tests (RC13 §1).
 *
 * Validates that the JSON shapes the operational pages (pages.js) actually
 * read from the backend have not drifted. The fixtures under ./fixtures/ are
 * MINIMAL SANITIZED fixtures: synthetic values that prove the contract and
 * nothing else (see ./fixtures/make_sanitized_fixtures.js). They are committed
 * so this suite is deterministic and needs no running daemon, and they hold no
 * owner data, personal memory, local paths or provider credentials (RC13 §7).
 *
 * To re-validate against a live daemon, set GENIE_LIVE=1 (the suite will fetch
 * the endpoints and assert the live bodies too). Do NOT require fields the
 * backend does not promise: optional/nullable fields are checked only when
 * present, and their absence is treated as a pass.
 *
 * The point of this layer is to catch the silent class of bug where a field is
 * renamed upstream (e.g. `items` -> `hits`) or changes array<->object, which
 * would otherwise make a page render an empty / wrong state with no error.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const FIX = path.resolve(__dirname, "fixtures");

// fixture file name for an endpoint, e.g. "/api/status" -> "_api_status.json"
const fixFile = (ep) => path.join(FIX, ep.replace(/\//g, "_") + ".json");

function loadFixture(ep) {
  const f = fixFile(ep);
  if (!fs.existsSync(f)) return null;
  const j = JSON.parse(fs.readFileSync(f, "utf8"));
  return j; // { code, body }
}

// Optional live fetch (only when GENIE_LIVE=1 and reachable).
async function liveBody(ep) {
  if (process.env.GENIE_LIVE !== "1") return null;
  try {
    const r = await fetch("http://127.0.0.1:8787" + ep, { headers: { accept: "application/json" } });
    return { code: r.status, body: await r.json() };
  } catch (_) { return null; }
}

// ---- tiny shape checker -------------------------------------------------
// rules: array of { path, type, required, item? }
//   type: "object" | "array" | "string" | "number" | "boolean" | "epoch" | "any"
//   item (for array): rules applied to each element
function check(rules, body, file) {
  for (const r of rules) {
    const segs = r.path.split(".");
    let cur = body;
    let ok = true;
    for (const s of segs) {
      if (cur == null || typeof cur !== "object" || !(s in cur)) { ok = false; break; }
      cur = cur[s];
    }
    if (!ok) {
      if (r.required) {
        assert.fail(`${file}: required field "${r.path}" is missing`);
      }
      continue; // optional -> absence is fine
    }
    if (r.type === "epoch") {
      // epoch ms: number, finite, plausibly a date
      assert.equal(typeof cur, "number", `${file}: ${r.path} must be a number (epoch)`);
      assert.ok(isFinite(cur) && cur > 0, `${file}: ${r.path} must be a positive finite epoch`);
      continue;
    }
    if (r.type === "array") {
      assert.ok(Array.isArray(cur), `${file}: ${r.path} must be an array`);
      if (r.item) for (const [i, el] of cur.entries()) check(r.item, el, `${file} ${r.path}[${i}]`);
      continue;
    }
    if (r.type === "object") {
      assert.equal(typeof cur, "object", `${file}: ${r.path} must be an object`);
      assert.ok(!Array.isArray(cur), `${file}: ${r.path} must be an object, not an array`);
      continue;
    }
    assert.equal(typeof cur, r.type, `${file}: ${r.path} must be ${r.type}, got ${typeof cur}`);
  }
}

// For every data endpoint the UI consumes, the top-level body MUST be an object
// (not an array). If the backend ever returned the bare array, pages.js would
// read `.missions` / `.skills` as undefined and silently show an empty page.
function assertTopLevelObject(file, body) {
  assert.equal(typeof body, "object", `${file}: response body must be an object (never a bare array)`);
  assert.ok(!Array.isArray(body), `${file}: response body must not be a bare array`);
}

async function validate(ep, rules, note) {
  const fx = loadFixture(ep);
  const live = await liveBody(ep);
  const srcs = [];
  if (fx && fx.body != null) srcs.push([`fixture(${path.basename(fixFile(ep))})`, fx.body]);
  if (live && live.body != null) srcs.push([`live ${ep}`, live.body]);
  if (!srcs.length) {
    // No fixture and no live daemon: the suite is hermetic, so we skip rather
    // than fail. (Fixtures are committed, so this only happens if someone
    // deleted them.)
    return "skip";
  }
  for (const [label, body] of srcs) {
    if (note && note.allow404 && body == null) continue;
    assertTopLevelObject(label, body);
    if (rules) check(rules, body, label);
  }
  return "ok";
}

// =================================================================== tests
const PROVIDER_ITEM = [
  { path: "id", type: "string", required: false },
  { path: "display_name", type: "string", required: false },
  { path: "enabled", type: "boolean", required: false },
  { path: "has_secret_ref", type: "boolean", required: false },
  { path: "models", type: "array", required: false, item: [
    { path: "model_id", type: "string", required: false },
    { path: "enabled", type: "boolean", required: false },
  ] },
];

const STATUS_RULES = [
  { path: "computer_capabilities", type: "array", required: false, item: [{ path: "", type: "string" }] },
  { path: "instance_id", type: "string", required: false },
  { path: "providers", type: "array", required: false, item: PROVIDER_ITEM },
  // Real workspace binding (RC13 FIX B) — optional, but if present must be right
  { path: "computer.workspace.root", type: "string", required: false },
  { path: "computer.workspace.bytes", type: "number", required: false },
];

test("/api/status shape matches what Settings + Computer read", async () => {
  const r = await validate("/api/status", STATUS_RULES);
  assert.notEqual(r, "skip", "status fixture missing — re-capture from a live daemon");
});

const MISSION_ITEM = [
  { path: "mission_id", type: "string", required: false },
  { path: "state", type: "string", required: false },
  { path: "steps", type: "array", required: false },
  { path: "goal", type: "string", required: false },
  { path: "updated_at", type: "epoch", required: false },
  { path: "errors", type: "array", required: false },
  { path: "cost_usd", type: "number", required: false },
];
test("/api/missions shape matches what Missions reads", async () => {
  const r = await validate("/api/missions", [
    { path: "missions", type: "array", required: true, item: MISSION_ITEM },
  ]);
  assert.notEqual(r, "skip", "missions fixture missing");
});

test("/api/agents shape matches what Agents + Media read", async () => {
  const r = await validate("/api/agents", [
    { path: "factory", type: "object", required: false },
    { path: "factory.candidates", type: "number", required: false },
    { path: "factory.agents", type: "number", required: false },
    { path: "budgets.global_remaining", type: "object", required: false },
    { path: "artifacts", type: "object", required: false },
    { path: "artifacts.count", type: "number", required: false },
    { path: "artifacts.bytes", type: "number", required: false },
    { path: "teams", type: "array", required: false },
    { path: "failovers", type: "number", required: false },
  ]);
  assert.notEqual(r, "skip", "agents fixture missing");
});

const DEVICE_ITEM = [
  { path: "device_id", type: "string", required: false },
  { path: "name", type: "string", required: false },
  { path: "state", type: "string", required: false },
  { path: "trust_tier", type: "string", required: false },
  { path: "capabilities", type: "array", required: false, item: [{ path: "", type: "string" }] },
  { path: "last_seen", type: "epoch", required: false },
];
test("/api/devices shape matches what Devices reads (key is `devices`, not `records`)", async () => {
  const r = await validate("/api/devices", [
    // pages.js reads `d.devices || d.records`; the backend promises `devices`.
    { path: "devices", type: "array", required: true, item: DEVICE_ITEM },
  ]);
  assert.notEqual(r, "skip", "devices fixture missing");
});

const SKILL_ITEM = [
  { path: "skill_id", type: "string", required: false },
  { path: "name", type: "string", required: false },
  { path: "status", type: "string", required: false },
  // backend exposes `provenance` (not `source`) and `required_capabilities`
  { path: "provenance", type: "object", required: false },
  { path: "required_capabilities", type: "array", required: false },
];
test("/api/skills shape matches what Skills + Knowledge read", async () => {
  const r = await validate("/api/skills", [
    { path: "skills", type: "array", required: true, item: SKILL_ITEM },
  ]);
  assert.notEqual(r, "skip", "skills fixture missing");
});

test("/api/skills/stats shape matches what Skills stats panel reads", async () => {
  const r = await validate("/api/skills/stats", [
    { path: "skills", type: "number", required: true },
    { path: "by_status", type: "object", required: true },
    { path: "by_status.active", type: "number", required: false },
    { path: "by_status.archived", type: "number", required: false },
  ]);
  assert.notEqual(r, "skip", "skills/stats fixture missing");
});

test("/api/memory shape matches what Memory reads (key MUST be `hits`)", async () => {
  // RC13 §1 regression: the UI reads m.hits || m.items || m.entries. The
  // backend promises `hits`; if that ever changes the Memory page would go
  // silently blank. Assert the real key is present.
  const fx = loadFixture("/api/memory");
  const live = await liveBody("/api/memory");
  const body = (fx && fx.body) || (live && live.body);
  assert.ok(body != null, "memory fixture missing — re-capture from a live daemon");
  assertTopLevelObject("memory", body);
  assert.ok(Array.isArray(body.hits), "/api/memory must expose `hits` (array); UI reads m.hits");
  // legacy aliases must NOT be the only source — if only `items`/`entries`
  // existed, the UI would be broken; warn if backend drops `hits`.
  assert.ok(!("items" in body) || Array.isArray(body.hits),
    "/api/memory exposes `items` but not `hits` — UI would show empty Memory");
});

test("/api/forecast/calibration shape matches what Forecast reads", async () => {
  const r = await validate("/api/forecast/calibration", [
    { path: "registered", type: "boolean", required: true },
    { path: "forecasts", type: "number", required: false },
    { path: "numeric", type: "number", required: false },
    { path: "declined", type: "number", required: false },
  ]);
  assert.notEqual(r, "skip", "forecast/calibration fixture missing");
});

const FINDING_ITEM = [
  { path: "severity", type: "string", required: false },
  { path: "title", type: "string", required: false },
  { path: "target", type: "string", required: false },
  { path: "status", type: "string", required: false },
  { path: "reported_by", type: "array", required: false },
];
test("/api/security/findings shape matches what Security reads", async () => {
  const r = await validate("/api/security/findings", [
    { path: "registered", type: "boolean", required: true },
    { path: "summary", type: "object", required: false },
    { path: "summary.unresolved", type: "number", required: false },
    { path: "summary.total", type: "number", required: false },
    { path: "findings", type: "array", required: false, item: FINDING_ITEM },
  ]);
  assert.notEqual(r, "skip", "security/findings fixture missing");
});

const AUDIT_ITEM = [
  { path: "action", type: "string", required: false },
  { path: "who", type: "string", required: false },
  { path: "outcome", type: "string", required: false },
  { path: "ts", type: "epoch", required: false },
];
test("/api/ops/audit shape matches what Security audit reads (key is `audit`)", async () => {
  const r = await validate("/api/ops/audit", [
    // pages.js reads `a.audit || a.entries`; backend promises `audit`.
    { path: "audit", type: "array", required: true, item: AUDIT_ITEM },
  ]);
  assert.notEqual(r, "skip", "ops/audit fixture missing");
});

test("/api/voice shape matches what Settings reads", async () => {
  const r = await validate("/api/voice", [
    { path: "mode", type: "string", required: false },
    { path: "language", type: "string", required: false },
    { path: "providers", type: "object", required: false },
    { path: "providers.input.available", type: "boolean", required: false },
    { path: "providers.stt.name", type: "string", required: false },
    { path: "providers.tts.name", type: "string", required: false },
  ]);
  assert.notEqual(r, "skip", "voice fixture missing");
});

test("/api/providers shape matches normalizeProvider (same item shape as status.providers)", async () => {
  const r = await validate("/api/providers", [
    { path: "providers", type: "array", required: true, item: PROVIDER_ITEM },
  ]);
  assert.notEqual(r, "skip", "providers fixture missing");
});

// Derived surfaces: the directive lists /api/knowledge and /api/media, but the
// backend has NO such routes — the UI derives Knowledge from /api/skills +
// /api/skills/stats and Media from /api/agents.artifacts. Document + guard that
// the derived-source fields exist so those pages never go blank.
test("Knowledge surface is derived from /api/skills + /api/skills/stats (no /api/knowledge route)", async () => {
  const know = loadFixture("/api/knowledge");
  assert.ok(!know || know.code === 404,
    "/api/knowledge unexpectedly exists — update the Knowledge loader if it now returns data");
  const sk = loadFixture("/api/skills");
  const st = loadFixture("/api/skills/stats");
  assert.ok(sk && Array.isArray(sk.body.skills), "/api/skills must return skills[] for Knowledge");
  assert.ok(st && typeof st.body.skills === "number", "/api/skills/stats must return a total for Knowledge");
});

test("Media surface is derived from /api/agents.artifacts (no /api/media route)", async () => {
  const media = loadFixture("/api/media");
  assert.ok(!media || media.code === 404,
    "/api/media unexpectedly exists — update the Media loader if it now returns data");
  const ag = loadFixture("/api/agents");
  assert.ok(ag && ag.body.artifacts && typeof ag.body.artifacts.count === "number",
    "/api/agents must return artifacts{count,files,bytes} for Media");
});

module.exports = { validate, check, assertTopLevelObject };
