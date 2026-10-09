/* Script load-order guarantee (RC13 §9).
 *
 * pages.js throws at runtime when window.GenieHelpers is missing. A forgotten
 * <script> tag in a newly added shell would therefore fail ONLY in the browser,
 * after the build. This test makes the ordering a deterministic repo-wide
 * assertion instead of relying on a manual grep.
 *
 * Shells are discovered, not hardcoded: any HTML file that loads pages.js is
 * covered automatically, so adding a page cannot silently skip the rule.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const WEB = path.resolve(__dirname, "..", "..", "web");
const HELPERS = "/ui/helpers.js";
const PAGES = "/ui/pages.js";

const htmlFiles = fs.readdirSync(WEB).filter((f) => f.endsWith(".html"));
const srcOf = (f) => fs.readFileSync(path.join(WEB, f), "utf8");
const shells = htmlFiles.filter((f) => srcOf(f).includes(PAGES));

// The twelve operational shells known to consume pages.js.
const EXPECTED = ["agents", "chat", "computer", "devices", "forecast", "knowledge",
  "media", "memory", "missions", "security", "settings", "skills"];

test("every known operational shell exists and loads pages.js", () => {
  for (const name of EXPECTED) {
    const file = name + ".html";
    assert.ok(htmlFiles.includes(file), `${file} should exist in ui/web/`);
    assert.ok(srcOf(file).includes(PAGES), `${file} must load pages.js`);
  }
});

test("helpers.js is included BEFORE pages.js in every shell that uses it", () => {
  assert.ok(shells.length > 0, "expected at least one shell loading pages.js");
  for (const file of shells) {
    const src = srcOf(file);
    const iHelpers = src.indexOf(HELPERS);
    const iPages = src.indexOf(PAGES);
    assert.ok(iHelpers !== -1, `${file} must load helpers.js`);
    assert.ok(iPages !== -1, `${file} must load pages.js`);
    assert.ok(iHelpers < iPages,
      `${file} loads helpers.js AFTER pages.js — GenieHelpers would be undefined at runtime`);
  }
});

test("no shell includes helpers.js or pages.js more than once", () => {
  for (const file of shells) {
    const src = srcOf(file);
    const helpers = src.split(HELPERS).length - 1;
    const pages = src.split(PAGES).length - 1;
    assert.equal(helpers, 1, `${file} includes helpers.js ${helpers} times`);
    assert.equal(pages, 1, `${file} includes pages.js ${pages} times`);
  }
});
