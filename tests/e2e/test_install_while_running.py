"""Install / upgrade over a RUNNING GENIE (directive item 9).

This is the regression test for the owner's report:

    "GENIE cannot be closed. Please close it manually and click Retry."
    "Failed to uninstall old application files ... :2"

It performs a real install into a unique directory while GENIE is running, and
asserts the installer succeeds without asking anyone to kill anything.

WHY IT IS OPT-IN
----------------
It installs and uninstalls software on the machine and it needs a GENIE that
actually stays running, which is not true in the agent environment (the packaged
app exits during GPU-process sandbox start there). So it is marked
`real_machine` and additionally requires GENIE_INSTALL_WHILE_RUNNING=1. In an
environment where GENIE cannot be kept alive it SKIPS with an explicit reason —
it never reports a pass it did not earn.

Run it with:
    GENIE_INSTALL_WHILE_RUNNING=1 python -m pytest tests/e2e/test_install_while_running.py -m real_machine -q
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "dist-electron" / "GENIE Setup 0.1.0.exe"
OPT_IN = "GENIE_INSTALL_WHILE_RUNNING"

pytestmark = [pytest.mark.real_machine]


def _genie_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq GENIE.exe"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return "GENIE.exe" in (out.stdout or "")


def _sha256(path: pathlib.Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def target_dir(tmp_path_factory) -> pathlib.Path:
    return tmp_path_factory.mktemp("genie-install-while-running")


def test_install_while_genie_is_running(target_dir):
    if os.environ.get(OPT_IN) != "1":
        pytest.skip(f"opt-in: set {OPT_IN}=1 to run the install-while-running test")
    if not INSTALLER.exists():
        pytest.skip(f"installer not present: {INSTALLER}")

    sha = _sha256(INSTALLER)

    # 1) install once so there IS an "old application" to replace
    first = subprocess.run([str(INSTALLER), "/S", f"/D={target_dir}"],
                           capture_output=True, text=True, timeout=600)
    assert first.returncode == 0, (
        f"baseline install failed rc={first.returncode}\n{first.stdout}\n{first.stderr}")
    assert (target_dir / "GENIE.exe").exists(), "GENIE.exe missing after baseline install"

    # 2) start GENIE and confirm it is genuinely running
    proc = subprocess.Popen([str(target_dir / "GENIE.exe")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 25
    running = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if _genie_running():
            running = True
            break
        time.sleep(1.0)

    if not running:
        try:
            proc.terminate()
        except Exception:
            pass
        pytest.skip("GENIE did not stay running in this environment; "
                    "this test cannot prove anything here and will not claim a pass")

    try:
        # 3) THE ACTUAL REGRESSION: install again over the running instance.
        #    The installer must ask GENIE to exit and then succeed. Any
        #    "cannot be closed / Retry" dialog would hang or fail here.
        second = subprocess.run([str(INSTALLER), "/S", f"/D={target_dir}"],
                                capture_output=True, text=True, timeout=600)

        # Give the graceful shutdown a moment, then report what we saw.
        time.sleep(2)
        exited_cleanly = proc.poll() is not None

        assert second.returncode == 0, (
            "installer failed while GENIE was running "
            f"(rc={second.returncode}) — this is the reported upgrade bug.\n"
            f"stdout: {second.stdout[-800:]}\nstderr: {second.stderr[-800:]}")
        assert (target_dir / "GENIE.exe").exists(), \
            "GENIE.exe missing after installing over a running instance"
        assert exited_cleanly, \
            "GENIE ignored the installer's shutdown request and is still running"
        assert not _genie_running(), \
            "GENIE.exe processes survive the installer's graceful shutdown request"
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        uninst = None
        for cand in sorted(target_dir.glob("*ninstall*.exe")):
            uninst = cand
            break
        if uninst:
            subprocess.run([str(uninst), "/S"], capture_output=True, timeout=300)
        shutil.rmtree(target_dir, ignore_errors=True)

    print(f"installer sha256: {sha}")
    print("install over a running GENIE: PASS")
