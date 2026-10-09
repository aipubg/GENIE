/* Page state contract: loading / empty / error (PHASE E page audit).
 *
 * Every operational page must tell the owner three things:
 *   loading — something is in flight (otherwise a cold daemon looks broken)
 *   empty   — the backend legitimately has nothing (not "the page is broken")
 *   error   — the backend could not be reached (never rendered as "no data")
 *
 * The error state is enforced by the canonical transport (ui/tests/api-contract.test.js).
 * These tests enforce the other two, per page, by scanning the loader source so a
 * newly added page cannot silently ship without them.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const WEB = path.resolve(__dirname, "..", "..", "web");
const pages = fs.readFileSync(path.join(WEB, "pages.js"), "utf8");
const helpers = fs.readFileSync(path.join(WEB, "helpers.js"), "utf8");

const LOADERS = ["Missions", "Agents", "Computer", "Skills", "Devices", "Memory",
  "Knowledge", "Media", "Forecast", "Security", "Settings"];

// Body of `async function loadX()` — up to the next top-level (2-space) function.
function bodyOf(name) {
  const start = pages.indexOf("async function load" + name + "(");
  assert.notEqual(start, -1, `load${name}() should exist in pages.js`);
  const rest = pages.slice(start);
  const next = rest.search(/\n  (async )?function \w/);
  return next === -1 ? rest : rest.slice(0, next);
}

test("helpers.js exports a loading() state renderer", () => {
  assert.match(helpers, /const loading = \(/, "helpers.js must define loading()");
  assert.match(helpers, /esc, empty, loading,/, "loading() must be exported");
});

test("loading() is announced to assistive technology", () => {
  const at = helpers.indexOf("const loading = (label) =>");
  assert.notEqual(at, -1, "loading() body should be locatable");
  const m = helpers.slice(at, at + 400);
  assert.match(m, /role="status"/, "a loading state must carry role=status");
  assert.match(m, /aria-live/, "a loading state must be announced");
});

test("every page loader paints a loading state before its first await", () => {
  const missing = [];
  for (const name of LOADERS) {
    const body = bodyOf(name);
    const firstAwait = body.indexOf("await api(");
    const firstPaint = body.search(/innerHTML = loading\(/);
    if (firstPaint === -1 || (firstAwait !== -1 && firstPaint > firstAwait)) missing.push(name);
  }
  assert.deepEqual(missing, [],
    "loader waits on the network before showing anything: " + missing);
});

test("every page loader has an empty state for a truthful backend response", () => {
  const missing = [];
  for (const name of LOADERS) {
    const body = bodyOf(name);
    const hasEmpty = /empty\(|class="empty|[>"]No [a-z]|not registered|not enabled/.test(body);
    if (!hasEmpty) missing.push(name);
  }
  assert.deepEqual(missing, [], "loader has no empty state: " + missing);
});

test("every page loader has an error path", () => {
  const missing = [];
  for (const name of LOADERS) {
    const body = bodyOf(name);
    if (!/__offline|offline\(/.test(body)) missing.push(name);
  }
  assert.deepEqual(missing, [], "loader has no offline/error path: " + missing);
});
