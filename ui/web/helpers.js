/* GENIE UI — shared pure helpers + field normalizers (single source of truth).
 *
 * Loaded by every operational page as <script src="/ui/helpers.js"></script>
 * BEFORE pages.js. Also require()'d by the Node contract/helper tests (UMD).
 *
 * This file is the ONE place that:
 *   1. maps backend field names to the canonical field names the renderers
 *      consume (see normalizeMission / normalizeSkill / normalizeDevice /
 *      normalizeSecurityFinding / normalizeProvider), and
 *   2. coerces unsafe values (objects / undefined / NaN / raw epochs) into
 *      honest display strings so the UI never shows "[object Object]",
 *      "undefined" or "NaN" outside Developer/Advanced JSON.
 *
 * Renderers in pages.js consume normalized view data and never re-implement
 * this logic (directive RC13 §2).
 */
(function (root, factory) {
  const H = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = H;
  if (root) root.GenieHelpers = H;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this), function () {
  "use strict";

  /**
   * Escape a value for HTML. Safety net for structured data: a plain object,
   * undefined, null or NaN must never reach the page as "[object Object]",
   * "undefined" or "NaN" — they render as an honest em dash instead. This is the
   * single choke point every rendered value passes through.
   */
  const esc = (s) => {
    let v = s;
    if (v == null) return "—";
    if (typeof v === "object") return "—";
    if (typeof v === "number" && !isFinite(v)) return "—";
    v = String(v);
    if (v === "") return "";               // deliberate blank (e.g. no subtitle)
    if (v === "undefined" || v === "null" || v === "NaN") return "—";
    return v.replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  };

  const empty = (big, sub) =>
    `<div class="empty"><div class="big">${esc(big)}</div><div class="sub">${esc(sub || "")}</div></div>`;

  /**
   * In-flight state for a page body. Without it a slow or cold daemon
   * leaves the page blank, which reads as "broken" even though nothing is
   * wrong. role="status" + aria-live announce it to assistive tech.
   */
  const loading = (label) =>
    `<div class="muted" role="status" aria-live="polite" style="padding:18px 0">`
    + `${esc(label || "Loading")}…</div>`;

  /**
   * Render a structured value (provenance / source) as useful text.
   * An object must never reach esc(): implicit coercion yields
   * "[object Object]", which is meaningless to the owner.
   */
  const fmtSource = (v) => {
    if (v == null || v === "") return "Not available";
    if (typeof v !== "object") return String(v);

    const pick = (...keys) => {
      for (const k of keys) {
        const val = v[k];
        if (val != null && val !== "") return val;
      }
      return null;
    };

    const type = pick("type", "source_type", "kind");
    const origin = pick("origin", "source", "name");
    const loc = pick("url", "repository", "repo", "file", "path");
    const version = pick("version", "rev", "revision");
    const imported = pick("imported_at", "imported", "added_at", "timestamp");
    const trust = pick("trust", "state", "provenance_state", "status");

    const parts = [];
    if (type) parts.push(String(type));
    if (origin) parts.push(String(origin));
    if (loc) parts.push(String(loc));
    if (version) parts.push("v" + String(version));
    if (imported) parts.push(String(imported));
    if (trust) parts.push(String(trust));

    return parts.length ? parts.join(" / ") : "Not available";
  };

  /** Human-readable phrase for a machine capability id, e.g.
   *  "computer.files.find" -> "find", "browser.navigate" -> "navigate".
   *  Falls back to the raw id only when it cannot be split, and never
   *  returns undefined/null/NaN.                                          */
  const humanCapability = (cap) => {
    const raw = String((cap == null ? "" : cap)).trim();
    if (!raw) return "step";
    const leaf = raw.split(".").pop() || raw;
    return leaf.replace(/[_-]+/g, " ").trim() || raw;
  };

  /**
   * Group raw computer-capability ids into the human categories GENIE shows
   * on the Computer page. We only know what the backend reports as available,
   * so a group is "Available" when it has at least one capability and
   * "Disabled" otherwise — we never invent a "Restricted" state the backend
   * did not give us. The actual ids stay under "View capabilities".
   */
  const CAP_GROUP_KEYS = [
    ["Apps & Windows",  ["computer.app", "computer.window", "windows"]],
    ["Browser",         ["browser"]],
    ["Files",           ["computer.files", "files"]],
    ["Clipboard",       ["clipboard"]],
    ["Keyboard & Mouse",["keyboard", "mouse"]],
    ["Screen",          ["screen"]],
    ["System Audio",    ["audio", "system.audio"]],
  ];
  function groupCapabilities(caps) {
    const lower = (c) => String(c).toLowerCase();
    return CAP_GROUP_KEYS.map(([label, keys]) => {
      const ids = (caps || []).filter((c) => keys.some((k) => lower(c).includes(k)));
      const cls = ids.length ? "ok" : "mute";
      return {
        label,
        cls,
        state: ids.length ? "Available" : "Disabled",
        caps: ids.map((c) => humanCapability(c)),
        ids,
      };
    });
  }

  /** Format an epoch-millisecond timestamp. Returns "—" for anything that is
   *  missing or not a real date, so the UI never prints "undefined"/"NaN".  */
  const fmtWhen = (ms) => {
    const n = Number(ms);
    if (!ms || !isFinite(n) || n <= 0) return "—";
    const d = new Date(n);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString();
  };

  /** Money. Returns "—" when no cost was recorded instead of "$NaN". */
  const fmtMoney = (usd) => {
    const n = Number(usd);
    if (usd == null || !isFinite(n)) return "—";
    return "$" + n.toFixed(4);
  };

  /** Coerce a recorded error to a readable human string.  We never want
   *  "[object Object]" to leak into a FAILED-mission banner — that string is
   *  what String({}) produces.  Returns "" when nothing readable is present so
   *  callers can decide between empty and em-dash. */
  const humanErr = (e) => {
    if (e == null) return "";
    if (typeof e === "string") return e.trim();
    if (typeof e === "object") {
      return String(
        e.message || e.error || e.reason || e.detail ||
        (typeof e.code === "string" ? e.code : "") ||
        ""
      ).trim();
    }
    return String(e).trim();
  };

  /* --- generic human values ------------------------------------------------
   * humanErr() is deliberately NARROW: it formats ERROR-like objects. It must
   * not be stretched to render people, agents, providers or devices — those
   * are not errors and have their own vocabulary. humanValue() is that
   * separate, generic formatter.
   * --------------------------------------------------------------------- */

  /** Plain scalars -> text. null / undefined / NaN -> "" (never "null"/"NaN"). */
  const scalarText = (v) => {
    if (v == null) return "";
    if (typeof v === "string") return v.trim();
    if (typeof v === "number") return isFinite(v) ? String(v) : "";
    if (typeof v === "boolean") return v ? "true" : "false";
    return "";
  };

  /** Keys we will read off an arbitrary object, in priority order.
   *  Human labels win over technical ids: a record that has a display_name or
   *  name must never be labelled by its raw id in Normal UI (RC13 §4).
   *  `id` / `code` are last-resort identity — fine for technical rows only. */
  const HUMAN_VALUE_KEYS = [
    "display_name", "name", "title", "label", "who", "actor", "agent", "id", "code",
  ];

  /** Strings that are never real content — they are what String({}) and
   *  String(undefined) produce, and they must not survive into the UI. */
  const BANNED_LITERALS = ["[object Object]", "undefined", "null", "NaN"];

  /** Coerce ANY backend value to honest display text.
   *  string -> trimmed; number/boolean -> readable scalar;
   *  null/undefined/NaN -> ""; object -> an explicitly human field, else "".
   *  We never JSON.stringify() an arbitrary object into normal UI. */
  const humanValue = (v, depth) => {
    // `depth` is internal (recursion guard). Ignore anything that is not a
    // number so callers can safely use it in .map(humanValue) position, where
    // Array#map would otherwise pass the element index as the depth.
    const d = typeof depth === "number" ? depth : 0;
    if (v == null) return "";
    if (Array.isArray(v)) {
      if (d >= 3) return "";
      return v.map((x) => humanValue(x, d + 1)).filter(Boolean).join(", ");
    }
    if (typeof v !== "object") return scalarText(v);
    if (d >= 3) return "";                 // never walk deep / unknown trees
    for (const k of HUMAN_VALUE_KEYS) {
      if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
      const inner = v[k];
      if (inner == null) continue;
      const text = (inner !== null && typeof inner === "object")
        ? humanValue(inner, d + 1)
        : scalarText(inner);
      if (text && BANNED_LITERALS.indexOf(text) === -1) return text;
    }
    return "";
  };

  /** Like humanErr() but for a whole array — used wherever the backend may hand
   *  us a list of objects (blockers, constraints, reported_by, actors) that we
   *  want to render as plain text. Uses humanValue(), NOT humanErr(), so a
   *  person/agent object carrying only {who} or {name} still renders as its
   *  label. Drops unreadable / empty entries so the caller never has to
   *  .filter(Boolean).map(String) again. */
  const humanList = (arr) => (Array.isArray(arr) ? arr : [])
    .map((x) => humanValue(x))
    .map((s) => String(s == null ? "" : s).trim())
    .filter((s) => s && BANNED_LITERALS.indexOf(s) === -1);

  /** Byte sizes. Returns "—" for a missing value rather than "NaN B". */
  const fmtBytes = (n) => {
    const b = Number(n);
    if (n == null || !isFinite(b) || b < 0) return "—";
    if (b < 1024) return b + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let v = b / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return v.toFixed(v >= 10 ? 0 : 1) + " " + units[i];
  };

  const notLive = (what, why) =>
    `<div class="notlive"><strong>${esc(what)} is not live on this daemon.</strong><br>${esc(why)}</div>`;

  const offline = () => empty("Backend unreachable.", "Start GENIE with: python genie.py daemon");

  // Label/value pairs rendered as a real two-column definition list.
  function rows(pairs, opts) {
    const dense = opts && opts.dense ? " kv-dense" : "";
    return `<dl class="kv${dense}">` + pairs.map(([k, v]) =>
      `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("") + `</dl>`;
  }

  /* ===================================================================== *
   *  FIELD NORMALIZERS — the single choke point that turns a raw backend  *
   *  record into the canonical view data the renderers consume. Backend    *
   *  field names live ONLY here; renderers read the canonical names.        *
   * ===================================================================== */

  /** Mission record → canonical view fields. Resolves backend field names and
   *  coerces the error list so a backend object never renders as
   *  "[object Object]". agent_count / artifact_count are optional and left
   *  null when the backend does not provide them (never fabricated). */
  function normalizeMission(x) {
    const steps = Array.isArray(x.steps) ? x.steps : [];
    const goal = String(x.goal || "").trim();
    const errors = (Array.isArray(x.errors) ? x.errors : []).map(humanErr).filter(Boolean);
    const failedStep = steps.find((s) => String(s.status) === "failed");
    const res = failedStep && failedStep.result ? failedStep.result : null;
    const stepErr = res
      ? humanErr({ message: res.error, error: res.error, reason: res.reason, code: res.code })
      : "";
    const failure = errors.join(" · ") || stepErr || "";
    return {
      id: x.mission_id || "",
      goal,
      state: String(x.state || "UNKNOWN"),
      steps,
      targets: Array.isArray(x.targets) ? x.targets : [],
      costUsd: x.cost_usd,
      createdAt: x.created_at,
      updatedAt: x.updated_at,
      agentCount: typeof x.agent_count === "number" ? x.agent_count : null,
      artifactCount: typeof x.artifact_count === "number" ? x.artifact_count : null,
      errors,
      failure,
    };
  }

  /** Skill record → canonical view fields. Backend exposes `provenance`
   *  (not `source`) and `required_capabilities` (no `capabilities` key on most
   *  records); both are resolved here. */
  function normalizeSkill(s) {
    const source = s.source || s.provenance;
    const caps = [...(s.capabilities || []), ...(s.required_capabilities || [])];
    return {
      id: s.skill_id || s.name || "",
      name: s.name || s.skill_id || "",
      version: s.version || "",
      status: String(s.status || "—"),
      sourceText: fmtSource(source),
      permissions: Array.isArray(s.permissions) ? s.permissions : [],
      capabilities: caps,
    };
  }

  /** Device record → canonical view fields. Resolves trust_tier / trust,
   *  capabilities array, and renders last_seen through fmtWhen() so a raw
   *  epoch never reaches the page. */
  function normalizeDevice(d) {
    return {
      id: d.device_id || d.id || "",
      name: d.name || d.device_id || "",
      type: d.type || d.kind || "",
      state: String(d.status || d.state || "—"),
      trustTier: d.trust_tier || d.trust || "",
      capabilities: Array.isArray(d.capabilities) ? d.capabilities : [],
      lastSeen: fmtWhen(d.last_seen),
    };
  }

  /** Security finding → canonical view fields. reported_by may arrive as a
   *  list of objects; humanList() renders it as plain text. */
  function normalizeSecurityFinding(f) {
    return {
      severity: f.severity || "unrated",
      title: f.title || "",
      target: f.target || "",
      status: f.status || "",
      reportedBy: humanList(f.reported_by).join(", "),
    };
  }

  /** Audit entry → canonical view fields. The actor may arrive as a plain
   *  string OR as an object under who / actor / agent; humanValue() resolves
   *  either, so an actor object never renders as "[object Object]" and never
   *  collapses to an em dash just because it was nested one level deeper. */
  function normalizeAuditEntry(e) {
    const raw = (e && e.who !== undefined && e.who !== null) ? e.who
      : ((e && e.actor !== undefined && e.actor !== null) ? e.actor
        : (e ? e.agent : undefined));
    return {
      action: (e && e.action) || "",
      actor: humanValue(raw) || "",
      outcome: (e && (e.outcome || e.result)) || "",
      when: fmtWhen(e && (e.ts || e.timestamp)),
    };
  }

  /** Provider record → canonical view fields + precise human status.
   *
   * "enabled" only means the provider is switched on in configuration. It does
   * NOT mean a credential exists, and it definitely does not mean the endpoint
   * is reachable or healthy. A provider is never called Ready merely because a
   * record exists.
   *
   *   Disabled            explicitly switched off
   *   Local               no external API credential required
   *   Test only           mock/developer provider; no credential; Advanced only
   *   Needs API key       requires a credential and none is stored
   *   Needs configuration endpoint/model incomplete
   *   Unreachable         configuration present, latest connection check failed
   *   Ready               enabled + credential + usable model + no failing check
   */
  function normalizeProvider(p) {
    const on = !!p.enabled;
    const cred = !!p.has_secret_ref;
    const kind = p.kind || (p.protocol === "mock" ? "test" : "remote");
    const local = p.local === undefined
      ? (kind === "local" || kind === "test")
      : !!p.local;
    const requiresKey = !local && kind !== "test";
    const baseUrl = p.base_url || "";
    const reachable = p.reachable;          // undefined = never measured
    let statusCls, statusLabel;
    if (!on) { statusCls = "mute"; statusLabel = "Disabled"; }
    else if (kind === "test") { statusCls = "mute"; statusLabel = "Test only"; }
    else if (local) { statusCls = "mute"; statusLabel = "Local"; }
    else if (requiresKey && !cred) { statusCls = "warn"; statusLabel = "Needs API key"; }
    else if (!baseUrl) { statusCls = "warn"; statusLabel = "Needs configuration"; }
    else if (reachable === false) { statusCls = "bad"; statusLabel = "Unreachable"; }
    else { statusCls = "ok"; statusLabel = "Ready"; }
    return {
      id: p.id || p.display_name || "",
      displayName: p.display_name || p.id || "unnamed provider",
      enabled: on,
      hasSecret: cred,
      kind,
      local,
      // Test-only providers are a developer detail: hidden from Normal mode.
      advanced: kind === "test",
      modelCount: Array.isArray(p.models) ? p.models.length : 0,
      statusCls,
      statusLabel,
    };
  }

  /** Canonical provider status -> human label + badge class.
   *
   * The BACKEND is the authority for provider status. The frontend must ONLY
   * map the enum to a label; it must never recompute truth from enabled /
   * endpoint / secret_ref / model-count combinations. An unrecognised value
   * renders "Unknown" — never a fabricated "Ready".
   */
  const PROVIDER_STATUS = {
    ready: ["Ready", "ok"],
    configured: ["Configured", ""],
    needs_api_key: ["Needs API key", "warn"],
    needs_configuration: ["Needs configuration", "warn"],
    unreachable: ["Unreachable", "bad"],
    disabled: ["Disabled", "mute"],
    local: ["Local", "mute"],
    test_only: ["Test only", "mute"],
  };

  function providerStatus(status) {
    const pair = PROVIDER_STATUS[status];
    return { label: pair ? pair[0] : "Unknown", cls: pair ? pair[1] : "mute" };
  }

  return {
    esc, empty, loading, fmtSource, humanCapability, CAP_GROUP_KEYS, groupCapabilities,
    fmtWhen, fmtMoney, humanErr, humanValue, scalarText, HUMAN_VALUE_KEYS,
    humanList, fmtBytes, notLive, offline, rows,
    normalizeMission, normalizeSkill, normalizeDevice, normalizeSecurityFinding,
    normalizeAuditEntry, normalizeProvider,
    PROVIDER_STATUS, providerStatus,
  };
});
