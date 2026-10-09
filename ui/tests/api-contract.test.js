/* Canonical API transport contract (dedup workstream).
 *
 * Six near-duplicate fetch wrappers used to exist across the UI:
 *   pages.js, home.js, compact.js, app.js            -> api()
 *   control.js, ops.js                               -> getJSON() / postJSON()
 *
 * Three of them never checked res.ok, so a 500 parsed as {} and the page
 * rendered an EMPTY state instead of an ERROR state — the worst possible lie,
 * because an owner seeing "no missions" during an outage will act on it.
 * app.js had no offline handling at all (a dead daemon produced an unhandled
 * rejection). control/ops postJSON ignored res.ok, so a FAILED control action
 * came back as {} and the console reported success.
 *
 * ui/web/api.js is now the single transport. These tests lock that:
 *   1. the transport contract itself,
 *   2. that no page re-implements fetch (migration guard, hard failure),
 *   3. that every shell which loads a consumer also loads api.js first.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");

const A = require(path.resolve(__dirname, "..", "web", "api.js"));
const WEB = path.resolve(__dirname, "..", "web");

/* ------------------------------------------------------ transport contract */

test("canonical apiFetch is exported", () => {
  assert.equal(typeof A.apiFetch, "function");
});

test("non-2xx is offline, not empty data", async () => {
  global.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
  const out = await A.apiFetch("/api/anything");
  assert.equal(out.__offline, true);
  assert.equal(out.status, 503);
});

test("network failure is offline", async () => {
  global.fetch = async () => { throw new Error("daemon not running"); };
  assert.equal((await A.apiFetch("/x")).__offline, true);
});

test("2xx returns parsed body", async () => {
  global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ a: 1 }) });
  assert.deepEqual(await A.apiFetch("/x"), { a: 1 });
});

test("2xx bad JSON is empty and NOT offline", async () => {
  global.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new Error("x"); } });
  const out = await A.apiFetch("/x");
  assert.deepEqual(out, {});
  assert.notEqual(out.__offline, true);
});

test("base URL is honoured", async () => {
  let seen;
  global.fetch = async (u) => { seen = u; return { ok: true, json: async () => ({}) }; };
  await A.apiFetch("/p", {}, "http://127.0.0.1:8787");
  assert.equal(seen, "http://127.0.0.1:8787/p");
});

test("caller opts are merged after the default headers", async () => {
  let seen;
  global.fetch = async (_u, o) => { seen = o; return { ok: true, json: async () => ({}) }; };
  await A.apiFetch("/p", { method: "POST", body: "{}" });
  assert.equal(seen.method, "POST");
  assert.equal(seen.headers["Content-Type"], "application/json");
});

/* ------------------------------------------------------------ strict shape */

test("apiFetchStrict throws on non-2xx instead of returning a body", async () => {
  global.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
  await assert.rejects(A.apiFetchStrict("/ops/control", { method: "POST" }), (e) => {
    assert.equal(e.offline, true);
    assert.equal(e.status, 500);
    assert.match(e.message, /500/);
    return true;
  });
});

test("apiFetchStrict throws on network failure", async () => {
  global.fetch = async () => { throw new Error("down"); };
  await assert.rejects(A.apiFetchStrict("/x"), (e) => {
    assert.equal(e.offline, true);
    assert.match(e.message, /unreachable/);
    return true;
  });
});

test("apiFetchStrict returns the body on success", async () => {
  global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ ok: 1 }) });
  assert.deepEqual(await A.apiFetchStrict("/x"), { ok: 1 });
});

/* ------------------------------------------------- MIGRATION GUARD (hard) */

// Pages may interpret the result differently (throw vs __offline), but they
// must NOT implement fetch. Only api.js is allowed to call fetch for data.
// Streaming SSE in pages.js is a different transport and is excluded by name.
const CONSUMERS = ["pages.js", "home.js", "compact.js", "app.js", "control.js", "ops.js"];

test("no page implements its own fetch wrapper", () => {
  const offenders = [];
  for (const f of fs.readdirSync(WEB)) {
    if (!f.endsWith(".js") || f === "api.js") continue;
    const src = fs.readFileSync(path.join(WEB, f), "utf8");
    // strip the SSE streaming block: it is a byte-stream reader, not a wrapper
    const scan = src.replace(/streamInto[\s\S]*?\n  \}\n/, "");
    if (/\bawait\s+fetch\s*\(|\bfetch\s*\(\s*(API|\/?api)/.test(scan)) {
      offenders.push(f);
    }
  }
  assert.deepEqual(offenders, [],
    "local fetch implementation outside ui/web/api.js: " + offenders);
});

test("no page defines a duplicate api/getJSON/postJSON helper", () => {
  const offenders = [];
  for (const f of fs.readdirSync(WEB)) {
    if (!f.endsWith(".js") || f === "api.js") continue;
    const src = fs.readFileSync(path.join(WEB, f), "utf8");
    if (/(async\s+)?function\s+(api|getJSON|postJSON)\s*\(/.test(src)) offenders.push(f);
  }
  assert.deepEqual(offenders, [], "duplicate transport helper: " + offenders);
});

test("every consumer delegates to the canonical module", () => {
  for (const f of CONSUMERS) {
    const src = fs.readFileSync(path.join(WEB, f), "utf8");
    assert.match(src, /GenieApi/,
      `${f} must resolve the canonical module (ui/web/api.js)`);
    assert.match(src, /apiFetch(Strict)?\s*\(/,
      `${f} must call apiFetch/apiFetchStrict instead of fetch`);
  }
});

/* ------------------------------------------------------------- load order */

test("every shell loading a consumer loads api.js BEFORE it", () => {
  const shells = fs.readdirSync(WEB).filter((f) => f.endsWith(".html"));
  let checked = 0;
  for (const file of shells) {
    const src = fs.readFileSync(path.join(WEB, file), "utf8");
    const consumers = CONSUMERS.filter((c) => src.includes("/ui/" + c));
    if (!consumers.length) continue;
    const iApi = src.indexOf("/ui/api.js");
    assert.notEqual(iApi, -1, `${file} uses ${consumers} but never loads api.js`);
    for (const c of consumers) {
      assert.ok(iApi < src.indexOf("/ui/" + c),
        `${file} loads api.js AFTER ${c} — GenieApi would be undefined`);
    }
    checked++;
  }
  assert.ok(checked >= 15, `expected the shells to be covered, saw ${checked}`);
});
