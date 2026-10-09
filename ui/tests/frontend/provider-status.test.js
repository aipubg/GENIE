/* Provider status contract (rc14 section 23).
 *
 * The BACKEND is the authority for provider status. These tests lock the
 * frontend contract: it may ONLY map the canonical enum to a human label. It
 * must never recompute truth from enabled / endpoint / secret_ref /
 * model-count, and it must never fabricate a "Ready".
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

const H = require(path.resolve(__dirname, "..", "..", "web", "helpers.js"));
const { providerStatus, PROVIDER_STATUS } = H;

const CANONICAL = [
  "ready", "configured", "needs_api_key", "needs_configuration",
  "unreachable", "disabled", "local", "test_only",
];

test("every canonical backend status maps to a human label", () => {
  for (const s of CANONICAL) {
    const out = providerStatus(s);
    assert.equal(typeof out.label, "string", `${s} must map to a label`);
    assert.ok(out.label.length > 0, `${s} label must not be empty`);
    assert.notEqual(out.label, "Unknown", `${s} must be a known status`);
  }
});

test("unknown / missing status renders Unknown, never Ready", () => {
  for (const s of ["totally_bogus", "", null, undefined, 42]) {
    const out = providerStatus(s);
    assert.equal(out.label, "Unknown", `${String(s)} must render Unknown`);
    assert.notEqual(out.label, "Ready");
  }
});

test("a provider is never labelled Ready from field combinations", () => {
  // enabled + endpoint + credential, but the backend did not say ready
  for (const s of ["configured", "needs_api_key", "needs_configuration", "unreachable"]) {
    assert.notEqual(providerStatus(s).label, "Ready", `${s} must not render as Ready`);
  }
});

test("mock / test_only is never shown as needing an API key", () => {
  const out = providerStatus("test_only");
  assert.equal(out.label, "Test only");
  assert.notEqual(out.label, "Needs API key");
  assert.equal(providerStatus("local").label, "Local");
});

test("status map covers exactly the canonical enum (no extras)", () => {
  // Guards against the frontend inventing statuses the backend never emits.
  assert.deepEqual(Object.keys(PROVIDER_STATUS).sort(), [...CANONICAL].sort());
});

test("providerStatus() is pure and safe for repeated calls", () => {
  const a = providerStatus("ready");
  const b = providerStatus("ready");
  assert.deepEqual(a, b);
  // must not mutate the shared map
  assert.equal(providerStatus("ready").label, "Ready");
});
