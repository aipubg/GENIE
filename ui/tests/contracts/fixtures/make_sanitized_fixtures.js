/* Builds the committed API/UI contract fixtures (RC13 §7).
 *
 * Pipeline:  live dump -> inspect real schema -> minimal deterministic fixture.
 *
 * These fixtures exist to prove a CONTRACT (which keys/types pages.js reads),
 * not to archive a machine. Every value here is synthetic. Nothing is copied
 * from a real daemon: no owner or personal memory content, no device
 * identifiers, no local/absolute paths, no provider credentials or secret
 * references, no tokens.
 *
 * Run with:  node ui/tests/contracts/fixtures/make_sanitized_fixtures.js
 */
"use strict";
const fs = require("fs");
const path = require("path");

const OUT = __dirname;
const EPOCH = 1700000000000;            // fixed, deterministic timestamp

const F = {
  _api_status: { code: 200, body: {
    instance_id: "sanitized-instance",
    computer_capabilities: ["window.list", "files.read", "browser.navigate", "clipboard.get"],
    // RC13 FIX B: the real binding is computer.workspace.{root,bytes}.
    computer: { workspace: { root: "<sanitized-workspace-root>", bytes: 1048576 } },
    providers: [
      { id: "provider-a", display_name: "Provider A", enabled: true, has_secret_ref: false,
        models: [{ model_id: "model-a-1", enabled: true }] },
      { id: "provider-b", display_name: "Provider B", enabled: false, has_secret_ref: false,
        models: [] },
    ],
  }},

  _api_missions: { code: 200, body: { missions: [
    { mission_id: "mission-1", state: "FAILED", goal: "sanitized example goal",
      steps: [
        { status: "done", capability: "files.read", result: {} },
        { status: "failed", capability: "media.play",
          result: { error: "sanitized example failure" } },
      ],
      targets: ["device-example"],
      errors: [{ message: "sanitized example failure" }],
      cost_usd: 0.0012,
      created_at: EPOCH, updated_at: EPOCH + 60000,
      agent_count: 1, artifact_count: 0 },
  ]}},

  _api_agents: { code: 200, body: {
    factory: { candidates: 3, agents: 0, profiles: { genie_native: "ready" } },
    budgets: { global_remaining: { usd: 1 } },
    artifacts: { count: 2, files: 2, bytes: 2048 },     // Media derives from this
    teams: [],
    failovers: 0,
  }},

  _api_devices: { code: 200, body: {
    count: 1, by_state: { online: 1 }, online: 1,
    devices: [
      { device_id: "device-example", name: "Example Device", type: "pc",
        state: "online", trust_tier: "owner_primary",
        capabilities: ["window.list", "files.read", "browser.navigate"],
        last_seen: EPOCH },
    ],
  }},

  _api_skills: { code: 200, body: { skills: [
    { skill_id: "skill-example", name: "skill-example", version: "1.0.0", status: "active",
      provenance: { type: "builtin", origin: "sanitized" },   // `provenance`, not `source`
      required_capabilities: ["files.read"], permissions: ["files.read"] },
  ]}},

  _api_skills_stats: { code: 200, body: {
    skills: 3, by_status: { active: 2, archived: 1 },
  }},

  // RC13 FIX A: the key is `hits`. One synthetic hit proves the item shape too.
  _api_memory: { code: 200, body: {
    scope: "personal", count: 1,
    hits: [
      { text: "sanitized example memory item", source: { type: "conversation" },
        scope: "personal", confidence: 0.9, ts: EPOCH },
    ],
  }},

  _api_forecast_calibration: { code: 200, body: {
    registered: true, forecasts: 0, numeric: 0, declined: 0,
  }},

  _api_security_findings: { code: 200, body: {
    registered: true, summary: { total: 1, unresolved: 1 },
    findings: [
      // reported_by holds OBJECTS — this is what humanList()/humanValue() must
      // render as labels rather than "[object Object]".
      { severity: "high", title: "sanitized example finding", target: "device-example",
        status: "open",
        reported_by: [{ who: "example-actor" }, { agent: "agent-example" }] },
    ],
  }},

  _api_ops_audit: { code: 200, body: { audit: [
    { seq: 1, ts: EPOCH, who: "system", device: "device-example",
      action: "files.read", result: "allowed", mission_id: null },
  ]}},

  _api_voice: { code: 200, body: {
    mode: "off", language: "en",
    providers: {
      input: { available: false },
      stt: { name: "provider-a" },
      tts: { name: "provider-a" },
    },
  }},

  _api_providers: { code: 200, body: { providers: [
    { id: "provider-a", display_name: "Provider A", enabled: true, has_secret_ref: false,
      models: [{ model_id: "model-a-1", enabled: true }] },
  ]}},

  // No such routes exist (RC13 §6). Knowledge derives from /api/skills +
  // /api/skills/stats; Media derives from /api/agents.artifacts. Keep the 404
  // so nobody invents a contract for a route the backend does not serve.
  _api_knowledge: { code: 404, body: null },
  _api_media: { code: 404, body: null },
};

for (const [name, val] of Object.entries(F)) {
  fs.writeFileSync(path.join(OUT, name + ".json"), JSON.stringify(val, null, 2) + "\n");
}
console.log("wrote " + Object.keys(F).length + " sanitized fixtures to " + OUT);
