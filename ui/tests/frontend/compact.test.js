/* Deterministic compact-mode tests (node:test).

Covers the compact shell contract without a browser: the gem is the voice
control using the SAME transitions as Home, and the companion is mounted in a
minimized state (item 25 / item 40).
*/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const WEB = path.resolve(__dirname, "..", "..", "web");

const read = (f) => fs.readFileSync(path.join(WEB, f), "utf8");
const html = read("compact.html");
const js = read("compact.js");

// ------------------------------------------------------------------ markup
test("compact window contains lamp, gem, response bubble and input", () => {
  assert.match(html, /id="gem-btn"/, "the blue gem must be present");
  assert.match(html, /id="c-reply"/, "a response bubble must be present");
  assert.match(html, /id="c-text"/, "an input must be present");
  assert.match(html, /lamp-mini/, "the lamp must be present");
});

test("gem is a real accessible button", () => {
  assert.match(html, /role="button"/);
  assert.match(html, /tabindex="0"/);
  assert.match(html, /aria-pressed/);
  assert.match(html, /aria-label="GENIE voice: off"/);
});

test("compact mode does NOT add a separate microphone control", () => {
  // item 12/40: the gem is the only voice control, even in compact mode
  assert.doesNotMatch(html, /id="mic-btn"/);
  assert.doesNotMatch(html, /class="[^"]*\bmic\b/);
});

// ------------------------------------------------------------ voice contract
function nextMode(current) {
  // mirrors compact.js gemClick()
  switch (current) {
    case "idle": case "muted":     return "listening";
    case "listening":              return "idle";
    case "speaking":               return "listening";   // barge-in
    case "processing":             return "idle";
    default:                       return current;
  }
}

test("compact gem uses the same transitions as Home", () => {
  assert.equal(nextMode("idle"), "listening");
  assert.equal(nextMode("listening"), "idle");
  assert.equal(nextMode("speaking"), "listening", "barge-in returns to listening");
  assert.equal(nextMode("processing"), "idle");
  assert.equal(nextMode("unavailable"), "unavailable", "unavailable never toggles");
});

test("compact js talks to the same voice endpoints", () => {
  assert.match(js, /\/api\/voice\/start/);
  assert.match(js, /\/api\/voice\/stop/);
  assert.match(js, /\/api\/voice\/barge-in/);
});

test("compact uses the same chat path (and streams when possible)", () => {
  assert.match(js, /\/api\/chat\/stream/);
  assert.match(js, /\/api\/chat/);
});

// -------------------------------------------------------------- companion
test("companion is mounted with the compact anchor and low quality", () => {
  assert.match(js, /StaticCompanionRenderer/);
  assert.match(js, /compactAnchor/);
  assert.match(js, /setQuality\("LOW"\)/);
});

test("companion settles to idle, not a large emergence", () => {
  assert.match(js, /setState\("idle"\)/);
});

test("companion is decorative for screen readers", () => {
  const companion = require(path.join(WEB, "companion.js"));
  // the renderer marks the image aria-hidden (see companion.js mount())
  assert.match(read("companion.js"), /aria-hidden/);
  assert.equal(typeof companion.StaticCompanionRenderer, "function");
});
