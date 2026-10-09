"""Process enumeration: native path must be a real replacement for `tasklist`.

These tests exist because two earlier attempts were wrong in different ways:

  1. The original `tasklist` subprocess was correct but flashed a console window
     (it is a CUI app) every time the computer watcher polled.
  2. A ctypes rewrite using EnumProcesses + OpenProcess + QueryFullProcessImageNameW
     was REJECTED: opening protected processes (System, lsass, winlogon, smss,
     Registry, every service) is denied, so it could name only ~129 of 250 and
     would have silently broken process_running() and the Computer verifier.

The shipped implementation reads the kernel snapshot via
NtQuerySystemInformation(SystemProcessInformation), which needs no per-process
handle. These tests hold it to that standard.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from computer import state


pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"),
                                reason="Windows-only: native process enumeration")

# Protected / privileged processes that an OpenProcess-based implementation
# cannot name. NtQuerySystemInformation must name all of them.
MUST_BE_NAMED = ["System", "smss.exe", "csrss.exe", "winlogon.exe",
                 "services.exe", "lsass.exe"]


def _tasklist_rows():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, timeout=20, creationflags=0x08000000)
    rows = {}
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            rows[int(parts[1])] = parts[0]
    return rows


def test_native_path_is_used_and_returns_rows():
    rows = state._native_processes()
    assert len(rows) > 50, f"suspiciously few processes: {len(rows)}"
    assert all(isinstance(r["pid"], int) for r in rows)
    assert all(isinstance(r["name"], str) for r in rows)


def test_protected_processes_are_named():
    """The specific failure mode of the rejected EnumProcesses implementation."""
    rows = state.list_processes()
    by_name = {r["name"].lower() for r in rows}
    missing = [n for n in MUST_BE_NAMED if n.lower() not in by_name]
    assert not missing, f"protected processes invisible/unnamed: {missing}"


def test_coverage_is_comparable_to_tasklist():
    """Native enumeration must not drop processes tasklist can see.

    Processes may legitimately appear only in one list, because the two
    snapshots are taken microseconds apart and processes churn - but the native
    snapshot must never be missing a large share of what tasklist reports.
    """
    native = {r["pid"]: r["name"] for r in state._native_processes()}
    truth = _tasklist_rows()
    if not truth:
        pytest.skip("tasklist unavailable for comparison")

    missing = set(truth) - set(native)
    # tasklist.exe and its conhost are spawned by the comparison itself and are
    # already gone by the time the native snapshot is taken.
    missing -= {0}
    named_ok = sum(1 for p in set(native) & set(truth)
                   if native[p].lower() == truth[p].lower())

    assert len(native) >= int(len(truth) * 0.9), (
        f"native saw {len(native)} vs tasklist {len(truth)} "
        f"(missing {len(missing)})")
    assert named_ok >= int(len(truth) * 0.9), (
        f"only {named_ok}/{len(truth)} names matched tasklist")


def test_list_processes_does_not_spawn_a_subprocess():
    """Regression: polling must not create console children (the flashing terminal)."""
    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    subprocess.run = spy
    try:
        state.list_processes()
    finally:
        subprocess.run = real_run

    spawned = [c for c in calls if c and c[0] and "tasklist" in str(c[0]).lower()]
    assert not spawned, f"list_processes still shells out: {spawned}"


def test_process_running_still_works_against_real_state():
    """This is the consumer that a short list would silently break."""
    assert state.process_running("System") is True
    assert state.process_running("definitely-not-a-real-process.exe") is False
