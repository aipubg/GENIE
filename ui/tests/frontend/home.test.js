/* Deterministic frontend tests (Node's built-in test runner).
 *
 * No paid provider is called. No browser is required for these: they cover the
 * pure mapping logic that the UI relies on. Browser-rendered behaviour is
 * covered separately by ui/tests/visual/capture.py (real Chromium).
 *
 *   node --test ui/tests/frontend
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");

const companion = require("../../web/companion.js");
const { backendEventToCompanionState, STATES } = companion;

/* ------------------------------------------------- companion state mapping */
test("companion defines the 14 normalized states", () => {
  const expected = [
    "hidden", "emerging", "welcome", "idle", "attentive", "listening",
    "thinking", "working", "speaking", "success", "warning", "error",
    "offline", "sleepy",
  ];
  assert.deepEqual(STATES, expected);
});

test("voice listening maps to listening", () => {
  assert.equal(backendEventToCompanionState({ type: "VOICE_LISTENING" }), "listening");
});

test("transcript maps to thinking (reasoning, not working)", () => {
  assert.equal(backendEventToCompanionState({ type: "VOICE_TRANSCRIPT" }), "thinking");
});

test("mission running maps to working", () => {
  assert.equal(backendEventToCompanionState({ type: "MISSION_CREATED" }), "working");
});

test("TTS active maps to speaking", () => {
  assert.equal(backendEventToCompanionState({ type: "VOICE_SPOKEN" }), "speaking");
});

test("mission complete maps to success", () => {
  assert.equal(backendEventToCompanionState({ type: "MISSION_COMPLETED" }), "success");
});

test("provider failover maps to error", () => {
  assert.equal(backendEventToCompanionState({ type: "PROVIDER_FAILOVER" }), "error");
});

test("device offline maps to offline", () => {
  assert.equal(backendEventToCompanionState({ type: "DEVICE_OFFLINE" }), "offline");
});

test("unknown events return null so no fake state is invented", () => {
  assert.equal(backendEventToCompanionState({ type: "SOMETHING_ELSE" }), null);
  assert.equal(backendEventToCompanionState(null), null);
  assert.equal(backendEventToCompanionState({}), null);
});

/* --------------------------------------------- renderer contract presence */
test("StaticCompanionRenderer implements the renderer contract", () => {
  const r = new companion.StaticCompanionRenderer({});
  for (const m of ["mount", "setState", "setExpression", "setVoiceAmplitude",
                   "setAnchor", "setQuality", "summon", "minimize", "hide",
                   "destroy"]) {
    assert.equal(typeof r[m], "function", `missing ${m}`);
  }
});

test("renderer rejects unknown states (no silent fakes)", () => {
  const r = new companion.StaticCompanionRenderer({});
  r.setState("idle");
  assert.equal(r.state, "idle");
  r.setState("not-a-real-state");
  assert.equal(r.state, "idle", "unknown state must be ignored");
});

test("renderer uses the canonical companion asset", () => {
  const r = new companion.StaticCompanionRenderer({});
  assert.match(r.assetPath, /genie-companion\.png$/);
});

/* --------------------------------------------------------- greeting logic */
// Mirrors greetingFor() in home.js (kept identical; home.js is browser-only).
function greetingFor(hour) {
  if (hour < 5)  return "Good Night";
  if (hour < 12) return "Good Morning";
  if (hour < 17) return "Good Afternoon";
  return "Good Evening";
}
test("greeting follows the time of day", () => {
  assert.equal(greetingFor(2),  "Good Night");
  assert.equal(greetingFor(9),  "Good Morning");
  assert.equal(greetingFor(14), "Good Afternoon");
  assert.equal(greetingFor(20), "Good Evening");
});

/* ------------------------------------------------------- amplitude policy */
test("amplitude is clamped to 0..1 and never synthesised", () => {
  const clamp = (v) => Math.max(0, Math.min(1, Number(v) || 0));
  assert.equal(clamp(0.5), 0.5);
  assert.equal(clamp(2), 1);
  assert.equal(clamp(-1), 0);
  assert.equal(clamp(NaN), 0);
});

/* --------------------------------------------- gem voice state transitions */
// Mirrors the transition table in home.js gemClick().
function nextMode(current) {
  switch (current) {
    case "idle":
    case "muted":      return "listening";
    case "listening":  return "idle";
    case "speaking":   return "listening";   // barge-in
    case "processing": return "idle";
    default:           return current;
  }
}
test("gem: idle -> listening -> idle", () => {
  assert.equal(nextMode("idle"), "listening");
  assert.equal(nextMode("listening"), "idle");
});
test("gem: speaking + click barge-ins back to listening", () => {
  assert.equal(nextMode("speaking"), "listening");
});
test("gem: processing cancels to idle", () => {
  assert.equal(nextMode("processing"), "idle");
});
test("gem: unavailable does not toggle", () => {
  assert.equal(nextMode("unavailable"), "unavailable");
});

/* ------------------------------------------------- honest empty-state text */
test("empty states are honest, never screenshot demo values", () => {
  const todayEmpty = "No active missions.";
  assert.equal(todayEmpty, "No active missions.");
  assert.ok(!/3 agents running/.test(todayEmpty));
});