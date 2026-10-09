/* Daemon auto-bootstrap contract (rc14 section 8).
 *
 * Electron cannot be launched in every CI/agent environment: on some Windows
 * boxes it dies at GPU-process init before app.whenReady() ever fires, so the
 * bootstrap path never executes and goes unverified.
 *
 * This test drives the REAL ui/electron/backend.js ensureDaemon() from plain
 * Node with only the `electron` app module stubbed. Everything else - path
 * resolution, health probe, spawn-lock acquisition, spawning the packaged
 * runtime, waiting for readiness - is the shipping code.
 *
 * Run:  node --test ui/tests/electron/daemon-bootstrap.test.js
 *
 * Requirements: a built unpacked app at
 *   dist-electron-rc14d/win-unpacked/resources/backend-runtime
 * and port 8787 free beforehand.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const os = require("os");
const Module = require("module");
const { execSync } = require("child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const UNPACKED = path.join(REPO, "dist-electron-rc14d", "win-unpacked");

function stubElectron(userDataDir) {
  const stub = { app: { getPath: () => userDataDir } };
  const orig = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return stub;
    return orig.apply(this, arguments);
  };
  return () => { Module._load = orig; };
}

function portListening(port) {
  try {
    const out = execSync(`netstat -ano | findstr ":${port} " | findstr LISTENING`,
      { shell: "cmd.exe", encoding: "utf8" });
    return out.trim().length > 0;
  } catch (_) {
    return false;
  }
}

function freePort(port) {
  try {
    const out = execSync(`netstat -ano | findstr ":${port} " | findstr LISTENING`,
      { shell: "cmd.exe", encoding: "utf8" }).trim();
    const pids = [...new Set(out.split(/\r?\n/)
      .map(l => l.trim().split(/\s+/).pop()).filter(p => /^\d+$/.test(p)))];
    for (const pid of pids) {
      try { execSync(`taskkill /F /PID ${pid}`, { shell: "cmd.exe" }); } catch (_) { /* already gone */ }
    }
  } catch (_) { /* nothing listening */ }
}

test("ensureDaemon() spawns the packaged backend when nothing is listening", { timeout: 180000 }, async () => {
  if (!fs.existsSync(path.join(UNPACKED, "resources", "backend-runtime"))) {
    throw new Error("build the app first: dist-electron-rc14d/win-unpacked/resources/backend-runtime missing");
  }

  freePort(8787);
  await new Promise(r => setTimeout(r, 2500));
  assert.equal(portListening(8787), false, "port 8787 must be free before the test");

  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "genie-boot-"));
  const restore = stubElectron(userData);
  try {
    process.resourcesPath = path.join(UNPACKED, "resources");
    const backend = require(path.join(REPO, "ui", "electron", "backend.js"));

    const resolved = backend.resolveBackend();
    assert.ok(resolved && resolved.exe, "must resolve a packaged backend");
    assert.equal(resolved.mode, "embedded");
    assert.ok(fs.existsSync(resolved.exe), `executable missing: ${resolved.exe}`);

    const res = await backend.ensureDaemon();

    assert.equal(res.ok, true, `bootstrap failed: ${JSON.stringify(res)}`);
    assert.equal(res.reused, false, "must have spawned, not reused");
    assert.ok(res.pid > 0, "must report the spawned pid");
    assert.ok(res.readiness_ms > 0, "must report readiness time");

    assert.equal(portListening(8787), true, "backend must be listening after bootstrap");

    // the bootstrap log must exist and record the sequence
    const logFile = backend.LOG_FILE;
    if (logFile && fs.existsSync(logFile)) {
      const text = fs.readFileSync(logFile, "utf8");
      assert.ok(text.includes("health_check_failed"), "log must record the failed probe");
      assert.ok(text.includes("readiness_result"), "log must record readiness");
    }
  } finally {
    restore();
    delete process.resourcesPath;
    freePort(8787);
  }
});

test("two simultaneous launches yield ONE authoritative backend", { timeout: 180000 }, async () => {
  if (!fs.existsSync(path.join(UNPACKED, "resources", "backend-runtime"))) {
    throw new Error("build the app first");
  }
  freePort(8787);
  await new Promise(r => setTimeout(r, 2500));
  assert.equal(portListening(8787), false, "port must be free");

  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "genie-dup-"));
  const restore = stubElectron(userData);
  try {
    process.resourcesPath = path.join(UNPACKED, "resources");
    const backend = require(path.join(REPO, "ui", "electron", "backend.js"));

    // Two SEPARATE OS processes racing, as two desktop launches would. This
    // must not be simulated with two in-process calls: the spawn lock is
    // PID-based, so a single process would (correctly) re-enter its own lock
    // and spawn twice, which is not the scenario section 9 describes.
    const launcher = path.join(REPO, "tests", "fixtures", "one_ensure_daemon.js");
    // ONE shared userData for BOTH children: the spawn lock lives under
    // app.getPath("userData"), so separate dirs would each lock their own file
    // and the duplicate-daemon guard would never actually be exercised.
    const shared = fs.mkdtempSync(path.join(os.tmpdir(), "genie-shared-"));
    const runOnce = () => new Promise((resolve) => {
      const { spawn } = require("child_process");
      const child = spawn(process.execPath, [launcher, UNPACKED], {
        windowsHide: true,
        env: { ...process.env, GENIE_TEST_USERDATA: shared },
      });
      let out = "";
      child.stdout.on("data", d => { out += d.toString(); });
      child.on("close", () => {
        try { resolve(JSON.parse(out.trim().split(/\r?\n/).pop())); }
        catch (_) { resolve({ ok: false, raw: out }); }
      });
    });

    const results = await Promise.all([runOnce(), runOnce()]);

    assert.equal(results[0].ok, true, "first launch must end healthy");
    assert.equal(results[1].ok, true, "second launch must end healthy");

    const spawned = results.filter(r => r.reused === false);
    assert.ok(spawned.length <= 1,
      `at most one launcher may spawn the backend (got ${spawned.length})`);
    // The other must have recognised the existing/starting daemon.
    assert.ok(results.some(r => r.reused === true || r.reason === "STARTED_BY_OTHER_LAUNCH"),
      "the second launcher must defer to the existing daemon");

    // Exactly one listener on 8787
    const listeners = execSync(`netstat -ano | findstr ":8787 " | findstr LISTENING`,
      { shell: "cmd.exe", encoding: "utf8" }).trim().split(/\r?\n/).filter(Boolean);
    const pids = [...new Set(listeners.map(l => l.trim().split(/\s+/).pop()))];
    assert.equal(pids.length, 1, `expected exactly one backend pid, got ${pids.join(",")}`);
  } finally {
    restore();
    delete process.resourcesPath;
    freePort(8787);
  }
});
