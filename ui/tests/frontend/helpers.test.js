/* Helper + normalizer regression tests (RC13 §2 / §3).
 *
 * The pure helpers live in ui/web/helpers.js (UMD) so they can be unit-tested
 * in Node without a browser. These tests lock in the anti-"[object Object]"
 * behaviour: an object / array-of-objects / null / undefined / NaN must never
 * reach the page as "[object Object]" / "undefined" / "NaN" (outside
 * Developer/Advanced JSON), and real epochs must render as a human time.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

const H = require(path.resolve(__dirname, "..", "..", "web", "helpers.js"));
const { esc, humanErr, humanValue, humanList, fmtWhen, groupCapabilities,
        normalizeMission, normalizeSkill, normalizeDevice,
        normalizeSecurityFinding, normalizeAuditEntry, normalizeProvider } = H;

/* ------------------------------------------------------------ esc() */
test("esc() never emits [object Object] / undefined / NaN", () => {
  assert.equal(esc(null), "—");
  assert.equal(esc(undefined), "—");
  assert.equal(esc(NaN), "—");
  assert.equal(esc({}), "—");
  assert.equal(esc({ a: 1 }), "—");
  assert.equal(esc([1, 2]), "—");                 // array is also an object
  assert.equal(esc("hello"), "hello");
  assert.equal(esc(123), "123");
  assert.equal(esc("undefined"), "—");            // the literal strings are scrubbed
  assert.equal(esc("NaN"), "—");
  assert.equal(esc("null"), "—");
  assert.equal(esc(""), "");                       // deliberate blank stays blank
  // HTML is escaped
  assert.equal(esc("<b>&"), "&lt;b&gt;&amp;");
});

/* -------------------------------------------------------- humanErr() */
test("humanErr() turns an error object into readable text, never [object Object]", () => {
  assert.equal(humanErr({ message: "boom" }), "boom");
  assert.equal(humanErr({ error: "denied" }), "denied");
  assert.equal(humanErr({ reason: "nope" }), "nope");
  assert.equal(humanErr({ detail: "det" }), "det");
  assert.equal(humanErr({ code: "E1" }), "E1");
  assert.equal(humanErr("plain string"), "plain string");
  assert.equal(humanErr(42), "42");
  assert.equal(humanErr(null), "");                // empty, caller decides em-dash
  assert.equal(humanErr(undefined), "");
  // nested object with no readable key -> empty, NOT "[object Object]"
  assert.equal(humanErr({}), "");
  assert.equal(humanErr({ weird: true }), "");
});

/* --------------------------------------------------- humanErr() on lists */
/* humanList() is the GENERIC list formatter — it renders people / agents /
 * actors / labels. Error-shaped objects ({message}, {error}, {reason}) are NOT
 * its job; those belong to humanErr(). Keeping the two paths separate is the
 * point of the split (RC13 §1): broadening humanErr() to also cover
 * reported_by / providers / devices would have turned it into a junk drawer.
 * This test locks the error path in through humanErr() itself — same
 * assertion as before, correct owner. */
test("error-object lists render through humanErr(), never [object Object]", () => {
  const out = [
    { message: "first" },
    { error: "second" },
    "third",
    null,
    { noReadableKey: 1 },          // would be "[object Object]" if coerced raw
    42,
  ].map(humanErr).filter(Boolean);
  assert.deepEqual(out, ["first", "second", "third", "42"]);
  assert.ok(!out.includes("[object Object]"));
});

/* --------------------------------------------------------- humanValue() */
test("humanValue() resolves strings, scalars and explicitly human fields", () => {
  assert.equal(humanValue("owner"), "owner");
  assert.equal(humanValue("  padded  "), "padded");
  assert.equal(humanValue(42), "42");
  assert.equal(humanValue(true), "true");
  // null / undefined / NaN / empty -> "" (never "null" / "NaN")
  assert.equal(humanValue(null), "");
  assert.equal(humanValue(undefined), "");
  assert.equal(humanValue(NaN), "");
  assert.equal(humanValue(""), "");
  // human labels win over technical ids (RC13 §4)
  assert.equal(humanValue({ display_name: "Prime RLM", id: "prime_rlm" }), "Prime RLM");
  assert.equal(humanValue({ name: "owner", id: "u-1" }), "owner");
  assert.equal(humanValue({ title: "Weekly report" }), "Weekly report");
  assert.equal(humanValue({ label: "Files" }), "Files");
  // identity-of-origin fields
  assert.equal(humanValue({ who: "system" }), "system");
  assert.equal(humanValue({ actor: "agent-7" }), "agent-7");
  assert.equal(humanValue({ agent: "agent-1" }), "agent-1");
  // `id` / `code` are last-resort identity only
  assert.equal(humanValue({ id: "dev-9" }), "dev-9");
  assert.equal(humanValue({ code: "E1" }), "E1");
  // no meaningful human scalar -> "" and NEVER "[object Object]"
  assert.equal(humanValue({}), "");
  assert.equal(humanValue({ nested: { x: 1 } }), "");
  assert.equal(humanValue({ random: true }), "");
  assert.notEqual(humanValue({ nested: { x: 1 } }), "[object Object]");
  // one level of nesting resolves; deep / unknown trees are not walked
  assert.equal(humanValue({ who: { name: "owner" } }), "owner");
});

/* --------------------------------------------------------- humanList() */
test("humanList() renders generic value lists (RC13 §3 regression cases)", () => {
  assert.deepEqual(humanList(["owner", "agent-1"]), ["owner", "agent-1"]);
  assert.deepEqual(humanList([{ name: "owner" }, { id: "agent-1" }]), ["owner", "agent-1"]);
  assert.deepEqual(humanList([{ display_name: "Prime RLM" }]), ["Prime RLM"]);
  assert.deepEqual(humanList([{ who: "system" }]), ["system"]);
  assert.deepEqual(humanList([null, undefined, NaN, ""]), []);
  assert.deepEqual(humanList([{}]), []);
  assert.deepEqual(humanList([{ nested: { x: 1 } }]), []);
  assert.deepEqual(humanList(["[object Object]"]), []);
  // non-arrays degrade to an empty list instead of throwing
  assert.deepEqual(humanList(null), []);
  assert.deepEqual(humanList(undefined), []);
  assert.deepEqual(humanList("not-an-array"), []);
});

test("humanList() on an array-of-objects step error list yields plain text", () => {
  const steps = [
    { status: "failed", result: { error: "plugin media lacks permissions: application.media.control" } },
  ];
  // this mirrors loadMissions' failure derivation
  const errs = steps.map((s) => humanErr(s.result)).filter(Boolean);
  assert.deepEqual(errs, ["plugin media lacks permissions: application.media.control"]);
  assert.ok(!errs.includes("[object Object]"));
});

/* -------------------------------------------------------- fmtWhen() */
test("fmtWhen() renders a real epoch and rejects null/0/NaN/empty", () => {
  const real = fmtWhen(1789658838143);
  assert.notEqual(real, "—");
  assert.ok(/[0-9]/.test(real), "real epoch should render as a human date string");
  assert.equal(fmtWhen(null), "—");
  assert.equal(fmtWhen(undefined), "—");
  assert.equal(fmtWhen(0), "—");
  assert.equal(fmtWhen(""), "—");
  assert.equal(fmtWhen(NaN), "—");
  assert.equal(fmtWhen("not-a-date"), "—");
});

/* -------------------------------------------------- groupCapabilities() */
test("groupCapabilities() groups ids and never throws on object elements", () => {
  const groups = groupCapabilities(["browser.navigate", "files.write", "clipboard.get"]);
  assert.equal(groups.length, 7);
  const byLabel = Object.fromEntries(groups.map((g) => [g.label, g]));
  assert.equal(byLabel["Browser"].ids.length, 1);
  assert.equal(byLabel["Files"].ids.length, 1);
  assert.equal(byLabel["Browser"].state, "Available");
  assert.equal(byLabel["Screen"].state, "Disabled");
  // object elements must not crash (they just don't match any group)
  assert.doesNotThrow(() => groupCapabilities([{ x: 1 }, null, "window.list"]));
});

/* -------------------------------------------------- normalizers (§2) */
test("normalizeMission() coerces error objects and leaves optional counts null", () => {
  const m = normalizeMission({
    mission_id: "mis_1", goal: "do thing", state: "FAILED",
    steps: [{ status: "failed", result: { error: "boom" } }],
    errors: [{ message: "top-level error" }],
    cost_usd: 0.0, updated_at: 1789799663595,
  });
  assert.equal(m.id, "mis_1");
  assert.equal(m.failure, "top-level error");           // errors win over stepErr
  assert.equal(m.agentCount, null);                      // not fabricated
  assert.equal(m.artifactCount, null);
  assert.equal(typeof m.updatedAt, "number");
});

test("normalizeSkill() resolves provenance + required_capabilities (not source/capabilities)", () => {
  const s = normalizeSkill({
    skill_id: "s1", name: "Skill One", status: "active", version: 3,
    provenance: { derived_from: "s1@1" },
    required_capabilities: ["files.write"],
    permissions: ["p.a"],
  });
  assert.equal(s.name, "Skill One");
  assert.equal(s.status, "active");
  assert.equal(s.sourceText, "Not available");          // provenance has no readable key
  assert.deepEqual(s.capabilities, ["files.write"]);     // required_capabilities unioned in
  assert.deepEqual(s.permissions, ["p.a"]);
});

test("normalizeDevice() resolves trust_tier/trust and formats last_seen", () => {
  const d = normalizeDevice({
    device_id: "pc_main", name: "Main PC", type: "pc", state: "online",
    trust_tier: "owner_primary", capabilities: ["browser.navigate"], last_seen: 1789658838143,
  });
  assert.equal(d.trustTier, "owner_primary");
  assert.equal(d.state, "online");
  assert.notEqual(d.lastSeen, "—");                      // epoch rendered
  assert.deepEqual(d.capabilities, ["browser.navigate"]);
  // legacy `trust` alias
  const d2 = normalizeDevice({ name: "x", trust: "guest" });
  assert.equal(d2.trustTier, "guest");
});

test("normalizeSecurityFinding() stringifies reported_by objects via humanList", () => {
  const f = normalizeSecurityFinding({
    severity: "high", title: "t", target: "pc_main", status: "open",
    reported_by: [{ who: "owner" }, { agent: "agent-1" }],
  });
  assert.equal(f.severity, "high");
  assert.equal(f.reportedBy, "owner, agent-1");
  const f2 = normalizeSecurityFinding({ severity: "low", title: "t2" });
  assert.equal(f2.reportedBy, "");                        // no reported_by -> empty
});

test("normalizeAuditEntry() resolves string OR object actors and formats time", () => {
  // plain string actor (the common case)
  assert.deepEqual(
    normalizeAuditEntry({ action: "files.read", who: "owner", outcome: "allowed", ts: 1789658838143 }),
    { action: "files.read", actor: "owner", outcome: "allowed", when: fmtWhen(1789658838143) });
  // an object actor must not become "[object Object]" nor collapse to an em dash
  assert.equal(normalizeAuditEntry({ actor: { name: "prime_rlm" } }).actor, "prime_rlm");
  assert.equal(normalizeAuditEntry({ who: { who: "system" } }).actor, "system");
  // `agent` alias still resolves
  assert.equal(normalizeAuditEntry({ agent: "agent-1" }).actor, "agent-1");
  // nothing readable -> "" (the caller renders the em dash)
  assert.equal(normalizeAuditEntry({ action: "x" }).actor, "");
  // the timestamp goes through fmtWhen(), never a raw epoch
  assert.equal(normalizeAuditEntry({ ts: null }).when, "—");
});

test("normalizeProvider() maps provider state to precise human status", () => {
  // Ready: enabled, credential present, endpoint present, no failing check.
  assert.deepEqual(
    normalizeProvider({ id: "a", enabled: true, has_secret_ref: true, base_url: "https://x/v1" }),
    { id: "a", displayName: "a", enabled: true, hasSecret: true, kind: "remote",
      local: false, advanced: false, modelCount: 0, statusCls: "ok", statusLabel: "Ready" });

  // Needs API key: remote provider, no credential stored.
  assert.equal(
    normalizeProvider({ id: "b", enabled: true, has_secret_ref: false, base_url: "https://x/v1" }).statusLabel,
    "Needs API key");

  // Needs configuration: credential present but the endpoint is incomplete.
  // A provider is never Ready merely because a record exists.
  assert.equal(
    normalizeProvider({ id: "d", enabled: true, has_secret_ref: true }).statusLabel,
    "Needs configuration");

  // Disabled: explicitly switched off.
  assert.equal(
    normalizeProvider({ id: "c", enabled: false, has_secret_ref: false }).statusLabel,
    "Disabled");

  // Unreachable: configuration present, latest connection check failed.
  assert.equal(
    normalizeProvider({ id: "e", enabled: true, has_secret_ref: true, base_url: "https://x/v1", reachable: false }).statusLabel,
    "Unreachable");
});

test("normalizeProvider() never demands a credential for a test/local provider", () => {
  const mock = normalizeProvider({ id: "mock", enabled: true, has_secret_ref: false, kind: "test" });
  assert.equal(mock.statusLabel, "Test only",
    "the mock provider must not be labelled 'Needs API key'");
  assert.equal(mock.advanced, true, "test-only providers are hidden from Normal mode");

  const local = normalizeProvider({ id: "needle2", enabled: true, has_secret_ref: false, kind: "local" });
  assert.equal(local.statusLabel, "Local");
  assert.equal(local.advanced, false);
});
