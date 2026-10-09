# Owner acceptance — GENIE rc15c

**What you are testing:** a release *candidate*. It is **unsigned** and **untagged**.
Nothing here is a released version yet.

| | |
|---|---|
| Installer | `E:\G3\GENIE\dist-electron-rc15c\GENIE Setup 0.1.0.exe` |
| Size | 139,547,700 bytes |
| SHA-256 | `9aa757a5b69631af539ca400fd42337360a3d601837c71b5e2e6bddcf6dd3c32` |
| Built | 2026-09-20 16:18 |
| Source commit | see the manifest; product source is `09ee335` plus the rc15c lifecycle change |
| Signed | **No** — you will get a SmartScreen prompt. That is expected. |

---

## 1. Install

Double-click the installer. It installs per-user to
`C:\Users\ghostt\AppData\Local\Programs\GENIE` and creates a **Start Menu**
shortcut and a **Desktop** shortcut.

## 2. Launch from the Start Menu

Start → **GENIE**. The backend starts automatically (takes a few seconds the
first time). You do not need to open a terminal.

## 3. Home — the ghosting check (the one thing I cannot verify for you)

Watch the Home screen immediately after launch and report what you see at:

| Time | What you should see | What you actually see |
|---|---|---|
| t = 0 s | | |
| t = 1 s | | |
| t = 3 s | | |
| t = 5 s | | |

Specifically: does a **greeting or a quote appear and then stay on screen when it
should have been replaced**? That is the "ghosting" symptom. Home ghosting is
**NOT** marked PASS — it needs your eyes.

## 4. Pages to inspect

Open each page and report anything broken, empty, or wrong:

- [ ] **Home**
- [ ] **Missions**
- [ ] **Agents**
- [ ] **Computer**
- [ ] **Settings → provider management** (add/select a provider, confirm it sticks)

## 5. Exit and relaunch

- [ ] Exit GENIE, then relaunch from the Start Menu — it should come back cleanly.
- [ ] After exiting, open Task Manager and confirm **no `pythonw.exe` processes
      remain**. This is the behaviour rc15c fixes; rc15b left six behind.

## 6. If the flashing terminal comes back

This is still open. If you see the rapidly flashing window again, run this
**immediately, while it is happening** — it is observe-only and safe:

```
cd E:\G3\GENIE
python scripts/p0_process_capture.py --seconds 300 --out artifacts/p0_owner_capture.jsonl
```

Then send me `artifacts/p0_owner_capture.jsonl`. It records the executable, full
command line, parent PID, parent executable, the whole parent chain, exit code and
the repetition interval — everything needed to name the root respawner.

Do **not** kill processes or delete scheduled tasks to make it stop; that removes
the evidence we need.

---

## Known and expected

- **Unsigned**: SmartScreen will warn. Choose "Run anyway".
- **Plugin hosts**: five plugin hosts now start correctly (they all failed in
  earlier packaged builds). Nothing to do — just noting it is fixed.
- If an install ever reports the installer "failed", just run it again; this
  machine intermittently makes the NSIS installer exit with `0xC0000005`.

## What I still need from you

1. The Home t = 0/1/3/5 s observations (section 3).
2. Any page that looks wrong (section 4).
3. Confirmation that no `pythonw.exe` survives exit (section 5).

## Known cosmetic issue — read before you judge the greeting

`ui/web/home.html` line 76 ships with a **hardcoded** default:

```html
<h1 id="greet-line">Good Evening</h1>
```

`setGreeting()` replaces it when the page boots, but until then the markup says
"Good Evening" regardless of the actual time. So at 10:00 you may see "Good
Evening" for a moment before it flips to "Good Morning".

That is **not** the ghosting we are hunting — it settles by itself. Please judge
the greeting at t = 3 s and t = 5 s, not at t = 0.

Two related facts, both minor, both deferred under the feature freeze:

* `setGreeting()` runs only once, at boot. `tickClock()` refreshes the date and
  time every 30 s but never the greeting, so a session left running across 12:00
  or 17:00 keeps a stale greeting until reload.
* The two quotes are static by design (`home.html` lines 79 and 203); they are
  not supposed to change.

Neither is fixed in rc15c because the freeze allows changes only for release
blockers, data-loss risk, lifecycle/process defects, installer defects and
harness correctness. Both are one-line fixes if you want them in a rc15d.
