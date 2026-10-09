# GENIE — PHASE 13: UI POLISH + USER CONTROL CENTER

**Status: 🟩 COMPLETE**

**Exit gate (from `ROADMAP.md`):**
> owner can see "what do you remember / which agents run / which provider sees my data" in 3 clicks.

---

## What was built

### `core/controlcenter.py` — `ControlCenter`

One module that answers exactly the three questions, assembled from **live services** rather than a
cached summary that could drift:

| # | Question | Source | Shows |
|---|---|---|---|
| 1 | **What do you remember?** | `MemoryService.recent()` | every current record, newest first, grouped by type — each one **forgettable** |
| 2 | **Which agents run?** | `AgentService.status()` | live teams, agents per team, tasks done/running, provider, paused/cancelled state, failovers, experience |
| 3 | **Which provider sees my data?** | `ModelRegistry.providers()` | every provider, **local vs remote**, whether it actually receives data, model count, enabled state |

The privacy rule is stated explicitly rather than implied:

    sees_my_data == (locality == "remote") and enabled

So a local model never shows as seeing your data, and a disabled remote provider doesn't either.

### Surfaces

* **API** — `GET /api/control-center` (all three at once), `POST /api/control-center/forget`.
* **UI** — `/ui/control` (`ui/web/control.html` + `control.js`), reachable in **one click** from
  the sidebar button added to `ui/web/index.html`. All three answers render on a single page, so
  the gate ("3 clicks") is met with margin — no drilling through menus.
* The page auto-refreshes every 5s and matches the app's existing dark theme via its CSS variables.

---

## Design rules

**Read-only by default.** Seeing is always safe. The single mutating action is *forgetting* a
memory — the owner reducing what GENIE knows. It is audited (`control_center.forget`).

**Never invent an absence.** If a service is unavailable the section reports
`available: False` with a reason, instead of rendering an empty list. An empty memory list caused
by a broken service would read as *"GENIE remembers nothing"* — which is a lie the owner might
believe. There is a test for this.

**No placeholders.** Every number in the UI comes from the live endpoint.

---

## Verification

```
tests/e2e/test_phase13_controlcenter.py   11   memory listed + grouped by type; missing service
                                               says so instead of looking empty; forget removes a
                                               record AND is audited; agents reported; providers
                                               split local/remote with the sees_my_data rule;
                                               ModelSpec dataclasses normalised; overview has all
                                               three answers
```

Reachable at `/ui/control` — sidebar → **Control Center** → all three answers.

---

## Note on scope

The roadmap names this phase "UI polish + User Control Center". The **Control Center** is done.
General "UI polish" beyond it (and the OpenClaw-derived chat-channel ingress, currently recorded
as deferred in `REPO_UTILIZATION_AUDIT.md`) is not part of the exit gate and remains open for a
later pass — Phase 14 is hardening and daily-use release.
