# GENIE — TEST CATEGORIES

A single mixed test run cannot be honest. Tests that mutate one real desktop fail intermittently
when another process changes the system volume or a Chrome session is reused, and mixing them into
the deterministic result turns a real signal into noise.

So the suite is split into three categories, and they are reported separately.

## Categories

| Category | Marker | What it means |
|---|---|---|
| **deterministic** | *(default — no marker)* | Pure logic with a throwaway database. No shared machine state. **Must be fully green.** |
| **real_machine** | `@pytest.mark.real_machine` | Mutates shared desktop state: system volume, foreground window, keyboard/mouse, Chrome profile/session, microphone/speaker, hardware devices. Runs **serially** behind the shared `REAL_DESKTOP_TEST` lock. |
| **hardware_optional** | `@pytest.mark.hardware_optional` | Needs hardware that may legitimately be absent (microphone, camera, GPIO, relay board). |
| **owner_acceptance** | `@pytest.mark.owner_acceptance` | Only the owner can perform it (speaking, watching, confirming). Reported as **pending**, never as a pass. |

## Commands

```bash
python tools/test_suites.py                # all three, reported separately
python tools/test_suites.py deterministic
python tools/test_suites.py real
python tools/test_suites.py owner

# or directly
pytest                                     # deterministic (the default addopts deselect the rest)
pytest -m real_machine                     # serial real-desktop suite
pytest -m hardware_optional
pytest -m owner_acceptance -v              # the pending owner list
pytest -m ""                               # everything
```

## The real-desktop lock

`tests/conftest.py` acquires a **cross-process** lock file (`REAL_DESKTOP_TEST`,
`$TEMP/genie_real_desktop.lock`) for the session and attaches it automatically to every
`real_machine`-marked test — a test cannot forget it. A second pytest process (a rerun in another
shell, CI plus a local run) is excluded rather than allowed to interleave, and a stale lock from a
killed run is broken after the timeout instead of blocking the suite forever.

`GENIE_TEST_LOCK` and `GENIE_TEST_LOCK_TIMEOUT` override the path and the timeout.

## Rules

* **No assertion is weakened to reach green.** The deterministic suite is green because it no
  longer shares a desktop with anything, not because a check was relaxed.
* A test that touches shared desktop state **must** be marked `real_machine`. If it is not, it
  belongs to no category and will be treated as deterministic — and it will eventually flake.
* Owner-only items live in `tests/owner/test_owner_acceptance.py` with the exact command to run
  and what "accepted" means. The list is guarded by a test so it cannot be silently emptied.
