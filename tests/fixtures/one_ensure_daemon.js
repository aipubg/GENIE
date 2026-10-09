/* Standalone launcher used by the duplicate-daemon test.
 *
 * Spawns as its own OS process, calls the real ensureDaemon() once, and prints
 * a one-line JSON result. Two of these running concurrently is the honest
 * simulation of "two simultaneous desktop launches" (distinct PIDs), which a
 * single Node process cannot reproduce because the spawn lock is PID-based.
 *
 * Usage:
 *     node tests/fixtures/one_ensure_daemon.js <path-to-win-unpacked>
 */
"use strict";
const path = require("path");
const fs = require("fs");
const os = require("os");
const Module = require("module");

const UNPACKED = process.argv[2] || path.resolve(__dirname, "..", "..", "dist-electron-rc14d", "win-unpacked");
const REPO = path.resolve(__dirname, "..", "..");

// All concurrent launchers MUST share one userData dir: the spawn lock lives
// under app.getPath("userData"), so separate temp dirs would silently test
// nothing (each process locks its own file).
const userData = process.env.GENIE_TEST_USERDATA
  || fs.mkdtempSync(path.join(os.tmpdir(), "genie-boot-"));
fs.mkdirSync(userData, { recursive: true });
const stub = { app: { getPath: () => userData } };
const orig = Module._load;
Module._load = function (request) {
  if (request === "electron") return stub;
  return orig.apply(this, arguments);
};

process.resourcesPath = path.join(UNPACKED, "resources");
const backend = require(path.join(REPO, "ui", "electron", "backend.js"));

backend.ensureDaemon().then((res) => {
  process.stdout.write(JSON.stringify({ pid: process.pid, ...res }) + "\n");
  process.exit(0);
}).catch((err) => {
  process.stdout.write(JSON.stringify({ pid: process.pid, ok: false, error: String(err) }) + "\n");
  process.exit(1);
});
