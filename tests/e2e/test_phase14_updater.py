"""Phase 14.3 — updater & rollback.

The whole point of staging releases in directories is that a bad update is reversible. These
tests prove rollback actually restores the previous version, and — just as important — that the
updater refuses to activate a corrupt release or roll back to a missing one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.updater import MANIFEST_NAME, Updater


@pytest.fixture()
def updater(tmp_path):
    return Updater(tmp_path / "releases")


def _build(tmp_path, name, payload):
    src = tmp_path / f"src-{name}"
    src.mkdir()
    for rel, content in payload.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(content, encoding="utf-8")
    return src


# -------------------------------------------------------------------- staging
def test_stage_copies_files_and_records_hashes(updater, tmp_path):
    src = _build(tmp_path, "v1", {"genie.py": "print('v1')", "core/mod.py": "X = 1"})
    manifest = updater.stage("1.2.3", src, note="first release")
    assert manifest["ok"] is True
    names = {f["name"] for f in manifest["files"]}
    assert names == {"genie.py", "core/mod.py"}
    for f in manifest["files"]:
        assert len(f["sha256"]) == 64 and f["bytes"] > 0
    assert (Path(manifest["path"]) / "genie.py").read_text() == "print('v1')"
    assert manifest["note"] == "first release"


def test_stage_rejects_a_missing_source(updater, tmp_path):
    out = updater.stage("9.9.9", tmp_path / "nope")
    assert out["ok"] is False and "not found" in out["error"]


def test_verify_passes_on_a_fresh_release(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    check = updater.verify("1.0.0")
    assert check["ok"] is True and check["files"] == 1


def test_verify_detects_tampering(updater, tmp_path):
    manifest = updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    target = Path(manifest["path"]) / "genie.py"
    target.write_text("MALICIOUS")
    check = updater.verify("1.0.0")
    assert check["ok"] is False
    assert any("checksum mismatch" in p for p in check["problems"])


# ------------------------------------------------------------------- activate
def test_activate_sets_the_active_version(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    out = updater.activate("1.0.0")
    assert out["ok"] is True
    assert updater.active() == "1.0.0"
    assert updater.previous() is None, "first activation has nothing to roll back to"


def test_activate_records_the_previous_version(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.stage("1.1.0", _build(tmp_path, "b", {"genie.py": "v2"}))
    updater.activate("1.0.0")
    out = updater.activate("1.1.0")
    assert out["previous_version"] == "1.0.0"
    assert updater.active() == "1.1.0"


def test_activate_refuses_a_corrupt_release(updater, tmp_path):
    manifest = updater.stage("2.0.0", _build(tmp_path, "c", {"genie.py": "v3"}))
    (Path(manifest["path"]) / "genie.py").write_text("TAMPERED")
    out = updater.activate("2.0.0")
    assert out["ok"] is False
    assert "refusing to activate" in out["error"]
    assert updater.active() is None, "a corrupt release must never become active"


def test_activate_rejects_an_unknown_version(updater):
    out = updater.activate("does-not-exist")
    assert out["ok"] is False and "unknown version" in out["error"]


# ------------------------------------------------------------------- rollback
def test_rollback_restores_the_previous_version(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.stage("1.1.0", _build(tmp_path, "b", {"genie.py": "v2"}))
    updater.activate("1.0.0")
    updater.activate("1.1.0")          # the update that turns out to be bad
    assert updater.active() == "1.1.0"

    out = updater.rollback()
    assert out["ok"] is True
    assert out["rolled_back_from"] == "1.1.0"
    assert updater.active() == "1.0.0"
    # the good version is genuinely still on disk and is the one now active
    assert (updater.releases_dir / "1.0.0" / "genie.py").read_text() == "v1"


def test_rollback_is_refused_when_there_is_nothing_to_go_back_to(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.activate("1.0.0")
    out = updater.rollback()
    assert out["ok"] is False
    assert "nothing to roll back" in out["error"]
    assert updater.active() == "1.0.0", "a refused rollback must leave the system alone"


def test_rollback_is_refused_if_the_previous_release_vanished(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.stage("1.1.0", _build(tmp_path, "b", {"genie.py": "v2"}))
    updater.activate("1.0.0")
    updater.activate("1.1.0")
    # someone deleted the old release directory
    import shutil
    shutil.rmtree(updater.releases_dir / "1.0.0")
    out = updater.rollback()
    assert out["ok"] is False
    assert "missing from disk" in out["error"]


def test_rollback_refuses_a_corrupt_previous_release(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.stage("1.1.0", _build(tmp_path, "b", {"genie.py": "v2"}))
    updater.activate("1.0.0")
    updater.activate("1.1.0")
    (updater.releases_dir / "1.0.0" / "genie.py").write_text("CORRUPT")
    out = updater.rollback()
    assert out["ok"] is False
    assert "cannot roll back safely" in out["error"]


def test_rollback_can_be_repeated_to_flip_back(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.stage("1.1.0", _build(tmp_path, "b", {"genie.py": "v2"}))
    updater.activate("1.0.0")
    updater.activate("1.1.0")
    assert updater.rollback()["active_version"] == "1.0.0"
    assert updater.rollback()["active_version"] == "1.1.0"


# --------------------------------------------------------------------- status
# ----------------------------------------------------------------- CLI surface
def _cli(tmp_path, monkeypatch):
    """genie.main() with config pointed at a temp data dir."""
    import genie
    from core.config import Config

    cfg = Config({"data_dir": str(tmp_path / "data")})
    cfg.apply()
    monkeypatch.setattr("core.config.get_config", lambda *a, **k: cfg)
    return genie


def test_updater_cli_stages_and_activates(tmp_path, monkeypatch, capsys):
    genie = _cli(tmp_path, monkeypatch)
    v1 = _build(tmp_path, "a", {"genie.py": "v1"})

    assert genie.main(["update-stage", "1.0.0", str(v1), "--note", "first"]) == 0
    assert genie.main(["update-activate", "1.0.0"]) == 0

    capsys.readouterr()
    assert genie.main(["update-status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["active_version"] == "1.0.0"
    assert status["previous_version"] is None


def test_updater_cli_rolls_back_a_bad_release(tmp_path, monkeypatch, capsys):
    genie = _cli(tmp_path, monkeypatch)
    v1 = _build(tmp_path, "a", {"genie.py": "v1"})
    v2 = _build(tmp_path, "b", {"genie.py": "v2"})

    genie.main(["update-stage", "1.0.0", str(v1)])
    genie.main(["update-stage", "1.1.0", str(v2)])
    genie.main(["update-activate", "1.0.0"])
    genie.main(["update-activate", "1.1.0"])   # the update that turns out bad

    capsys.readouterr()
    assert genie.main(["update-rollback"]) == 0
    assert "1.0.0" in capsys.readouterr().out

    capsys.readouterr()
    genie.main(["update-status", "--json"])
    status = json.loads(capsys.readouterr().out)
    assert status["active_version"] == "1.0.0", "rollback must actually restore the old version"
    assert status["previous_version"] == "1.1.0"


def test_updater_cli_refuses_rollback_with_nothing_to_go_back_to(tmp_path, monkeypatch):
    genie = _cli(tmp_path, monkeypatch)
    v1 = _build(tmp_path, "a", {"genie.py": "v1"})
    genie.main(["update-stage", "1.0.0", str(v1)])
    genie.main(["update-activate", "1.0.0"])
    assert genie.main(["update-rollback"]) == 1, "a refused rollback must exit non-zero"


def test_status_reports_releases_and_active_version(updater, tmp_path):
    updater.stage("1.0.0", _build(tmp_path, "a", {"genie.py": "v1"}))
    updater.stage("1.1.0", _build(tmp_path, "b", {"genie.py": "v2"}))
    updater.activate("1.1.0")
    status = updater.status()
    assert status["active_version"] == "1.1.0"
    assert set(status["releases"]) == {"1.0.0", "1.1.0"}
    assert len(updater.releases()) == 2


# ------------------------------------------- install-safety (P0 release blocker)
def _fake_install_root(tmp_path, name="install"):
    """A stand-in for an installed GENIE tree that must never be deleted."""
    root = tmp_path / name
    (root / "resources" / "backend-runtime").mkdir(parents=True)
    (root / "GENIE.exe").write_text("exe", encoding="utf-8")
    (root / "Uninstall GENIE.exe").write_text("uninst", encoding="utf-8")
    (root / "resources" / "app.asar").write_text("asar", encoding="utf-8")
    (root / "resources" / "backend-runtime" / "python312.dll").write_text(
        "dll", encoding="utf-8")
    return root


def test_updater_never_touches_an_install_root(updater, tmp_path):
    """Ordinary updater use cannot delete an installation outside releases/."""
    install = _fake_install_root(tmp_path)
    before = sorted(p.name for p in install.rglob("*"))
    src = _build(tmp_path, "v-inst", {"genie.py": "print(1)"})
    assert updater.stage("1.0.0", src)["ok"] is True
    assert updater.activate("1.0.0")["ok"] is True
    assert updater.active() == "1.0.0"
    assert sorted(p.name for p in install.rglob("*")) == before
    assert (install / "GENIE.exe").exists()
    assert (install / "Uninstall GENIE.exe").exists()


def test_stage_cleanup_is_restricted_to_the_staged_version(updater, tmp_path,
                                                           monkeypatch):
    """Re-staging one version targets only that version, never its siblings.

    rmtree is spied on instead of being allowed to run. The claim under test is
    WHICH path the updater is willing to delete, and spying proves that without
    performing a real deletion - which matters because a bulk-delete guard in
    some sandboxes refuses real deletions once a run has removed enough files,
    and the test must not fail for that reason.
    """
    import core.updater as updater_module

    removed = []

    def spy(path, *a, **k):
        # Record the target, then move the directory aside. Moving emulates the
        # removal without deleting anything, so the test performs no deletion at
        # all and cannot trip a bulk-delete guard.
        removed.append(str(path))
        Path(path).rename(str(path) + ".moved")

    monkeypatch.setattr(updater_module.shutil, "rmtree", spy)

    updater.stage("1.0.0", _build(tmp_path, "v1", {"old.py": "1"}))
    sibling = tmp_path / "releases" / "0.9.0"
    sibling.mkdir(parents=True)
    (sibling / "keepme.txt").write_text("keep", encoding="utf-8")

    updater.stage("1.0.0", _build(tmp_path, "v2", {"new.py": "3"}))

    assert removed == [str(tmp_path / "releases" / "1.0.0")], removed
    assert (tmp_path / "releases" / "1.0.0" / "new.py").exists()
    assert (sibling / "keepme.txt").read_text() == "keep"


def test_arbitrary_roots_cannot_be_recursively_removed(updater, tmp_path):
    """A traversal version string must not escape the releases directory."""
    install = _fake_install_root(tmp_path, name="real-install")
    evil_src = _build(tmp_path, "evil", {"x.py": "1"})
    for evil in ("../../real-install", "..", "../../..", "..\\..\\real-install"):
        result = updater.stage(evil, evil_src)
        assert result["ok"] is False, f"{evil} was accepted"
        assert "outside" in (result.get("error") or "").lower()
    assert (install / "GENIE.exe").exists()
    assert (install / "resources" / "app.asar").exists()
    assert (install / "resources" / "backend-runtime" / "python312.dll").exists()
