# GENIE — PHASE 4: PLUGINS + BROWSER MATURITY

**Status:** 🟩 COMPLETE — 242 tests passing (1 skipped), real Chrome + real plugin host processes

> Two goals: GENIE gains **reliable application-specific capabilities without app logic in the
> core**, and the browser becomes a **production-grade automation surface** with verified,
> condition-based actions.

---

## 1. Plugin architecture

```text
GENIE daemon
    │  line-delimited JSON over stdio
    ▼
plugin host process  (one per plugin)
    │
    ▼
plugin adapter (plugins/installed/<id>/adapter.py)
```

A crashing or hanging plugin kills **its own** process. The daemon keeps running, reports a
structured error and — after repeated failures — disables the plugin.

| Module | Responsibility |
|---|---|
| `plugins/sdk.py` | manifest schema + validation, capability specs, the host protocol, `PluginAdapter` base |
| `plugins/host.py` | runs ONE plugin in its own process, serves initialize/health/invoke/shutdown |
| `plugins/client.py` | daemon-side supervision: timeouts, crash counter, restart, disable |
| `plugins/registry.py` | discovery, install/uninstall, enable/disable, permission grants (persisted) |
| `plugins/service.py` | capability routing, permission enforcement, audit, status |

### Manifest

```json
{
  "id": "media", "name": "Media & Spotify control", "version": "1.0.0",
  "entry": "adapter.py", "platforms": ["windows"],
  "permissions": ["application.media.control", "system.audio.read"],
  "observations": ["current_track", "playing_state"],
  "capabilities": [
    {"name": "pause", "description": "Pause media playback", "params": {},
     "verification": "media session state observed after the key press",
     "permissions": ["application.media.control"], "timeout_ms": 8000}
  ],
  "dependencies": {}, "health": {"checks": ["windows media interface"]}
}
```

Lifecycle: `discover → validate → install → initialize → health-check → invoke → stop → update → uninstall`.

### Permission model — two independent gates

| Gate | Meaning | Default |
|---|---|---|
| **PTE scope** `plugin:<id>:<capability>` | may this principal invoke plugin capabilities | owner: granted; others: denied |
| **Declared permission** (e.g. `application.spotify.control`) | what the plugin is allowed to touch | **deny until the owner grants it** |

Installing a plugin grants **nothing**. `genie.py plugins-grant <id>` (or
`POST /api/plugins/grant`) is an explicit owner action, and a plugin can only be granted
permissions it actually declared.

Plugins never receive GENIE's database, memory or NEDLE2 state: the host process only speaks
the protocol.

## 2. First real plugins

| Plugin | Capabilities | Method | Notes |
|---|---|---|---|
| **media** | play, pause, toggle, next, previous, current_track, volume_up/down, mute, state | **Windows media-key interface** (`SendInput` + `VK_MEDIA_*`) | native OS media session control — no screen automation; observation reads visible media windows and says so honestly when nothing is playing |
| **vscode** | open_project, open_file, focus, inspect_workspace, health | official `code` CLI | reports UNAVAILABLE when the CLI is absent; `inspect_workspace` reads git/`.vscode/tasks.json`/languages |
| **test_crash** | die, raise, ok | fixture | proves crash isolation |
| **test_timeout** | hang | fixture | proves timeout handling |

Absent applications produce honest `UNAVAILABLE`, never a fake success. Blender/OBS are not
installed on this machine, so their plugins were not written — the fallback chain covers them.

### Plugins are never required

```text
plugin/native  →  OS automation  →  UIA  →  vision  →  raw mouse/keyboard
```

`media.pause` is served by the media plugin when it is available and permitted; otherwise the
worker falls through to the generic chain. A missing plugin never blocks the user.

## 3. Browser maturity

| Capability | Verification |
|---|---|
| `browser.tabs_list` / `tab_new` / `tab_switch` / `tab_close` | tab set and GENIE's bound target are re-read and compared |
| `browser.wait` | **condition-based** — `element`, `element_visible`, `selector_gone`, `url_contains`, `url_equals`, `text`, `dom_ready`, `network_idle`; reports polls and waited_ms; unknown conditions are rejected |
| `browser.navigate` | readyState **and** rendered content (SPAs report complete early) |
| `browser.click` / `type` / `select` / `checkbox` | the resulting DOM state is read back |
| `browser.scroll` | scroll position before/after |
| `browser.upload` | the file input is read back and must hold the exact file |
| `browser.download` | the real file must exist, stop growing, and is recorded as an artifact |
| `browser.history` | URL before/after must differ |
| `browser.accessibility` | full AX tree (roles + names) |
| `browser.cookies` | session cookies, metadata only |
| `browser.dialog` | JavaScript dialog accept/dismiss |
| `browser.leases` | `browser.session:*` leases so two agents cannot fight over one session |

**No arbitrary sleeps as synchronisation.** The only `sleep` calls are sub-second settles
inside an action (e.g. after a click) — every wait for a *state* goes through `browser.wait`.

### Downloads and uploads go through artifact handling

Every completed download is recorded with source URL, destination, filename, MIME type, byte
size, sha256, mission id and timestamp (`browser_downloads` table + the returned artifact).
Chrome occasionally ignores the requested directory; GENIE watches both the requested path and
the profile default and **reports which one was actually used** (A-046).

## 4. Prompt-injection boundary

```text
web page / PDF / email / download  =  DATA, never authority
```

`security/injection_guard.py` implements three enforcement points:

1. **tag** — every browser extraction is marked `trust: untrusted` with its source and a digest
2. **scan** — detects instruction override, exfiltration, destruction, credential theft,
   privilege escalation, role hijack, financial and code-execution attempts
3. **guard_action** — while untrusted content is in play, high-risk capabilities
   (`files.delete`, `files.write`, `shell.run`, `browser.upload`, `process.kill`, …) are
   **blocked** unless the *user* explicitly confirmed them; secret references are blocked
   unconditionally

The worker consults the guard before every capability call, so an injected instruction cannot
change the mission goal, reach secrets, grant permissions or trigger unrelated device actions.
Verified by tests using a real page containing an injection fixture.

## 5. Exit gate

| # | Requirement | Result |
|---|---|---|
| 1 | plugin discovered | ✅ 4 plugins found by scanning `plugins/installed/*/plugin.json` |
| 2 | plugin loaded | ✅ each runs in its own process (different PID from the daemon) |
| 3 | permission denied correctly | ✅ default deny until the owner grants |
| 4 | permitted invocation works | ✅ media keys accepted; `state` reports observable sessions |
| 5 | plugin timeout handled | ✅ hung plugin killed, structured `plugin_timeout` |
| 6 | plugin crash does not crash daemon | ✅ hard `os._exit` in the plugin; daemon + audit unaffected |
| 7 | plugin restart works | ✅ restart after crash, then a normal invoke succeeds |
| 8 | broken plugin can be disabled | ✅ disabled after the crash limit; re-enableable by the owner |
| 9 | Spotify/media play/pause/next | ✅ real native media keys on this machine |
| 10 | browser opens multiple tabs | ✅ verified |
| 11 | tab switching verified | ✅ GENIE's bound target re-read |
| 12 | DOM click without raw mouse | ✅ verified by the resulting DOM state |
| 13 | form typing verified | ✅ value read back; select + checkbox too |
| 14 | SPA wait verified | ✅ condition polling with polls/waited_ms reported |
| 15 | redirect verified | ✅ final URL checked |
| 16 | download verified | ✅ real file + artifact record + DB row |
| 17 | upload verified | ✅ input must hold the exact file |
| 18 | browser cancellation works | ✅ cancelled result, no side effect |
| 19 | two agents cannot mutate the same tab | ✅ session lease refused for the second holder |
| 20 | prompt-injection fixture cannot change mission | ✅ tainted, high-risk action blocked, goal unchanged |
| 21 | audit chain records plugin/browser actions | ✅ `plugin.*` + `computer.browser.*` entries, chain verifies |

## 6. Repository reuse (inspected before building)

| Repo | What was taken | What was not |
|---|---|---|
| **pinchtab** | activity/tab model ideas for long-lived browser sessions | the code — it is a Bun/TS monorepo; embedding it would add a Node runtime to a daemon whose point is being light on low-end Windows (D-040/D-051) |
| **deepseek-harness** | the *declared capability catalogue* + provider lifecycle-event pattern, and the "trusted plugin API" boundary idea | its implementation (different runtime) |
| **openclaw** | noted for Phase 6: Kotlin Android provider/model catalogue + tool-activity presentation | not applicable yet |
| **n8n** | deferred to Phase 11 as an external integration (never the brain) | — |

## 7. Honest caveats

- Plugin hosts are separate **processes**, not OS sandboxes: a plugin can still touch whatever
  the current user can. The declared-permission gate plus the isolated process is the v1
  boundary; OS-level confinement (job objects / restricted tokens) is a Phase 14 hardening item.
- The media plugin controls whatever media session the OS has active. `current_track` is
  observed from window titles, so exact metadata is not always available — it says so instead
  of inventing a track name.
- `browser.download` uses a trusted input event (Chrome blocks gesture-less downloads). It is
  still DOM-derived (the element's own rectangle), not screen coordinates.
- Chrome may place downloads in the profile default directory; GENIE reports the real location.
- Accessibility tree, cookies and dialogs are exposed as capabilities but only lightly tested
  (they are read-mostly); deeper dialog/iframe handling is Phase 14 polish.
