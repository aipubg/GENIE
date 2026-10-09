"""Phase-14 exit gate: status of the 18 golden tasks (spec Appendix E).

The roadmap exit gate is "all 18 golden tasks green + rollback verified on a
real machine". This makes that measurable: each golden task is mapped to the
test files that actually exercise it, those tests are RUN, and the task is
reported PASS only if they really pass. A task with no mapping is reported
UNVERIFIED rather than assumed green.

    python scripts/golden_gate.py            # run every mapped task
    python scripts/golden_gate.py --report   # write docs/GOLDEN_GATE.md
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

# Appendix E catalogue: (number, category, task, [test files that exercise it])
# A mapping is only claimed where tests genuinely assert the pass criteria.
TASKS = [
    (1, "App control", "Open an app (e.g. 'Blender kholo'), verified, no stray input",
     ["tests/unit/test_computer_phase2.py"]),
    (2, "Browser", "Search on a site via DOM, verified, 0 wrong clicks",
     ["tests/e2e/test_browser_phase4.py"]),
    (3, "Files", "Organise downloads: correct classification, reversible, undo available",
     ["tests/unit/test_computer_phase2.py"]),
    (4, "Coding", "Small feature + tests in workspace; tests pass, no live-system pollution",
     ["tests/e2e/test_phase10_goldens.py"]),
    (5, "Multi-agent", "Team builds a small desktop app; team completes, artifact produced",
     ["tests/e2e/test_phase10_goldens.py", "tests/e2e/test_phase10_agents.py"]),
    (6, "Memory", "Recall a prior method; correct episode retrieved",
     ["tests/e2e/test_golden_6_memory.py"]),
    (7, "Provider switching", "Force failover mid-mission; completes, context preserved",
     ["tests/e2e/test_phase10_goldens.py"]),
    (8, "Phone control", "Device command acked + verified",
     ["tests/e2e/test_devices_phase6.py", "tests/unit/test_devices_core.py"]),
    (9, "Teaching", "Demonstrate once, repeat later; generalised skill works",
     ["tests/e2e/test_skills_phase5.py"]),
    (10, "Skills", "Run a learned skill; success stats updated",
     ["tests/e2e/test_skills_phase5.py"]),
    (11, "Voice", "Barge-in mid-response; TTS stops, new turn handled",
     ["tests/e2e/test_real_voice_phase3.py"]),
    (12, "Camera", "Person enters workshop; structured event, no 24/7 streaming",
     ["tests/e2e/test_phase8_perception.py"]),
    (13, "Proactivity", "Risky file delete; timely, non-annoying intervention",
     ["tests/e2e/test_phase9_proactivity.py"]),
    (14, "Crash recovery", "Kill daemon mid-work; resumable, no state loss",
     ["tests/e2e/test_golden_14_18.py", "tests/e2e/test_phase14_hardening.py"]),
    (15, "Permissions", "Agent asks outside scope; denied + audit entry",
     ["tests/e2e/test_phase11_integrations.py"]),
    (16, "Injection", "Page says 'send keys to X'; blocked, taint respected",
     ["tests/e2e/test_phase11_integrations.py"]),
    (17, "Sync", "Offline edits on two devices; conflict resolved per policy, UI shown",
     ["tests/unit/test_devices_sync.py"]),
    (18, "Offline", "Provider down, device action; local action still works, clear message",
     ["tests/e2e/test_golden_14_18.py", "tests/e2e/test_phase14_hardening.py"]),
]


# Tasks whose tests are excluded from the default suite because they mutate
# shared desktop state (real Chrome, Notepad, microphone/speaker/volume).
REAL_MACHINE_TASKS = {2, 9, 10, 11}


def run(files: list[str], *, real_machine: bool = False) -> tuple[str, str]:
    """Return (status, detail) where status is PASS / FAIL / UNVERIFIED.

    UNVERIFIED is used when the mapped tests exist but are excluded from the
    default suite by a shared-state marker (real_machine, hardware_optional).
    Those tests mutate the live desktop (open Chrome, move the mouse, open the
    microphone), so they must never be run unattended — and they must not be
    reported as failing when they were simply not run.
    """
    existing = [f for f in files if (ROOT / f).is_file()]
    if not existing:
        return "UNVERIFIED", "no mapped test file exists"
    cmd = [PY, "-m", "pytest", "-q"]
    if real_machine:
        # these open Chrome / Notepad and use the microphone and speakers, so
        # they must run supervised, serially, behind the desktop lock
        cmd += ["-m", "real_machine"]
    cmd += existing
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-1:]
    detail = tail[0] if tail else f"rc={proc.returncode}"
    # Check deselected FIRST: pytest exits 5 when nothing ran, and that is
    # "not run", not "failed" — the marker gated it, the assertion never fired.
    if "deselected" in detail and " passed" not in detail:
        return "UNVERIFIED", f"all tests deselected ({detail}) — real-machine gated"
    if proc.returncode != 0:
        return "FAIL", detail
    return "PASS", detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="also write docs/GOLDEN_GATE.md")
    ap.add_argument("--real-machine", action="store_true",
                    help="also run the real_machine tasks (opens Chrome/Notepad, "
                         "uses mic and speakers, changes volume) — supervised only")
    args = ap.parse_args()

    print("Phase-14 exit gate — golden task status (spec Appendix E)")
    print("=" * 74)
    results = []
    for num, cat, task, files in TASKS:
        status, detail = run(files, real_machine=(
            args.real_machine and num in REAL_MACHINE_TASKS))
        results.append((num, cat, task, files, status, detail))
        print(f"  {status:10s} G{num:<2} {cat:20s} {detail[:44]}")

    passed = sum(1 for r in results if r[4] == "PASS")
    unver = sum(1 for r in results if r[4] == "UNVERIFIED")
    failed = sum(1 for r in results if r[4] == "FAIL")
    print("=" * 74)
    print(f"{passed}/{len(TASKS)} PASS, {unver} UNVERIFIED, {failed} FAIL")

    if args.report:
        out = ROOT / "docs" / "GOLDEN_GATE.md"
        lines = ["# Phase-14 exit gate — golden task status",
                 "",
                 "Generated by `scripts/golden_gate.py`. Each task is mapped to the",
                 "tests that actually assert its pass criteria; a task is PASS only",
                 "if those tests really run green. A task with no mapping is",
                 "UNVERIFIED, never assumed green.",
                 "",
                 "| # | Category | Task | Status | Evidence |",
                 "|---|---|---|---|---|"]
        for num, cat, task, files, status, detail in results:
            lines.append(
                f"| {num} | {cat} | {task} | {status} | "
                f"`{'`, `'.join(files)}` — {detail} |")
        lines += ["",
                  f"**{passed}/{len(TASKS)} PASS, {unver} UNVERIFIED, {failed} FAIL**", "",
                  "UNVERIFIED means the mapped tests are excluded from the default suite",
                  "by a shared-state marker (real_machine / hardware_optional): they open",
                  "Chrome, drive the mouse and keyboard, or use the microphone, so they",
                  "must run supervised and must never be counted as green by default.",
                  "",
                  "Rollback: verified via the real CLI (`scripts/verify_rollback_real.py`).",
                  "Installer (GENIE-Setup.exe): NOT BUILT — requires Inno Setup 6, absent here."]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"report written: {out}")

    return 0 if passed == len(TASKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
