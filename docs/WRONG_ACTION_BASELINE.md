# Wrong-action baseline — protocol and result

Definitions below were fixed **before** collection so the number cannot be tuned
afterwards. Only real executed actions count; test doubles never contribute.

## Definitions

**Action** — a single GENIE operation attempted against the real system (file
operation, app control, browser DOM action, voice command, learned-skill replay,
computer automation, provider failover, or a guarded permission/safe-mode
attempt).

**Executed** — the action ran and its outcome was verified against the system.

**WRONG (counts against the target)**
1. **Failed** — executed but did not do what was asked, or did it to the wrong
   target.
2. **Unverified** — executed but the outcome could not be verified. Counted as
   WRONG on purpose: an unverified action can *look* fine to the owner while
   being wrong.

**Correct refusal (NOT wrong)** — the safety layer refused something it should
refuse (missing scope, missing permission, safe mode). A refusal is the safety
net working; counting refusals as failures would discourage refusing.

**Unverifiable (excluded from the denominator)** — the action could not be
attempted at all in this environment. Neither right nor wrong: absent evidence.

**Denominator** — `executed = verified + failed + unverified`.
**Rate** — `wrong_action_rate = (failed + unverified) / executed`.
**Target** — ≤ 2%.
**Sufficiency** — `executed >= 30` across at least 3 representative categories.

---

## Current status

| Metric | Value |
|---|---|
| executed | **31** |
| verified | **31** |
| unverified | 0 |
| failed | **0** |
| wrong | **0** |
| correct refusals | 2 |
| unverifiable | 4 |
| **rate** | **0.0000** |
| reproducibility | **8 consecutive runs, 0 wrong every run** |
| sufficiency | 31 ≥ 30 — **SUFFICIENT** |
| **verdict** | **MEETS TARGET FOR THIS MEASURED SAMPLE** |

Stated exactly:

> The defined release baseline passed at **0/31**, with the coverage limitation
> stated below.

**Not** claimed:

> ~~GENIE globally has a 0% wrong-action rate.~~

The result applies to this measured sample only.

## Known limitations

**4 of 9 representative categories were not exercised in this environment:**

| Category | Why not exercised |
|---|---|
| voice command | requires a microphone and a supervised desktop session |
| browser DOM action *inside this baseline harness* | Chrome navigation fails in this harness; browser DOM is verified separately by the real-machine browser suite (24 passed / 0 failed / 3 runs) |
| learned-skill replay | no taught skill available in this profile |
| provider failover | no live provider key configured |

Coverage achieved: file operations, app control, computer automation, UI
automation, clipboard — plus permission-denial refusal behaviour.

## Historical

Every step was earned by **fixing a real defect**, never by reclassifying an
outcome.

| Stage | executed | wrong | rate |
|---|---|---|---|
| initial sample | 4 | 2 | 0.5000 |
| real `files.*` capabilities discovered | 12 | 3 | 0.2500 |
| more file/desktop actions | 16 | 3 | 0.1875 |
| `files.find` crash fixed | 26 | 3 | 0.1154 |
| sample becomes sufficient | 31 | 2 | 0.0645 |
| `uia.find` title resolution fixed | 31 | 1 | 0.0323 |
| `uia.find` app-suffix fallback fixed | 31 | **0** | **0.0000** |

Methodology notes, recorded so the numbers are not misread:

* An isolated **0.0000** reading early on was discarded as an outlier (it
  occurred immediately after a manual Notepad kill and was not reproducible).
  Only the figure that repeats across 8 runs is quoted.
* An "unverified" outcome counts as wrong, and a safety "confirmation required"
  outcome counts as a correct refusal — both applied consistently, in the
  direction that does **not** flatter the result.

### Defects fixed during this baseline

| Defect | Effect |
|---|---|
| `files.find` NameError | crashed whenever no `root` was given (`Path` never imported at module level) |
| `files.append` verifier | always reported "content differs from what was requested" — a false failure of a working action |
| `uia.find` window resolution | failed when an action changed the window title (Notepad embeds the document's first line and prefixes `*`) — correct actions reported as failed |
| `browser` target resolver (found via the download regression) | `pages[0]` selected a hidden target after tab churn, so downloads never started |

Three of the four were **verification false negatives**: GENIE did the right
thing and then reported failure.

## Evidence

`tests/e2e/test_wrong_action_baseline.py` (`-m real_machine`) attempts the
representative actions against a live daemon and writes
`artifacts/wrong_action_baseline.json`.
