/* Canonical API fetch for every GENIE page.
 *
 * Four near-duplicate api() helpers existed in pages.js / home.js / compact.js
 * / app.js. The ones without an `r.ok` check turned a 500 into `{}`, so pages
 * rendered an EMPTY state instead of an ERROR state - the worst possible lie,
 * because an owner seeing "no missions" during an outage will act on it.
 *
 * Contract:
 *   network failure -> { __offline: true }
 *   non-2xx         -> { __offline: true, status }
 *   2xx, bad JSON   -> {}
 *   2xx, good JSON  -> parsed body
 */
(function (root) {
  "use strict";

  async function apiFetch(path, opts, base) {
    try {
      const r = await fetch((base || "") + path, {
        headers: { "Content-Type": "application/json" },
        ...(opts || {}),
      });
      if (!r.ok) return { __offline: true, status: r.status };
      try { return await r.json(); } catch (_) { return {}; }
    } catch (_) {
      return { __offline: true };
    }
  }

  /* Same transport, different failure contract: throw instead of returning
   * { __offline }. Used by the operator/control consoles, whose callers catch
   * and render the message. It exists so those consoles do NOT re-implement
   * fetch — only the interpretation of the result differs. */
  async function apiFetchStrict(path, opts, base) {
    const out = await apiFetch(path, opts, base);
    if (out && out.__offline) {
      const err = new Error(path + " -> " + (out.status || "unreachable"));
      err.offline = true;
      err.status = out.status;
      throw err;
    }
    return out;
  }

  const api = { apiFetch, apiFetchStrict };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.GenieApi = api;
})(typeof window !== "undefined" ? window : globalThis);
