"""Deterministic tests for the real Git-backed evolution backend.

Every test uses a **temporary git repository**, so it never touches the live
GENIE tree and never needs a paid provider.

What these prove (item 37):
  * a separate physical worktree really exists
  * the live tree is unchanged while a candidate edits its own worktree
  * the candidate diff is isolated
  * a failing TEST gate blocks adoption
  * a failing SECURITY gate blocks adoption
  * missing owner approval blocks adoption
  * a passing + approved candidate can really adopt (merge)
  * rejection removes the worktree and the branch
  * branch/commit lineage is recorded
  * rollback restores a known-good commit
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agents.evolution import (APPLIED, APPROVED, CONFORMED, EvolutionEngine,
                              GATE_SECURITY, GATE_TEST)
from agents.evolution_vcs import (EvolutionVcsError, GitEvolutionBackend,
                                  build_default_backend)


# ---------------------------------------------------------------- fixtures
def _git(repo: Path, *args, check: bool = True):
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path):
    """A throwaway git repo with one commit on `main`."""
    r = tmp_path / "genie"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "test@local")
    _git(r, "config", "user.name", "Test")
    (r / "README.md").write_text("# GENIE\n", encoding="utf-8")
    (r / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "baseline")
    return r


@pytest.fixture()
def backend(repo: Path, tmp_path: Path):
    return GitEvolutionBackend(repo_root=repo,
                               worktrees_root=tmp_path / "worktrees",
                               base_branch="main")


def _pass(_payload):
    return True, "ok"


def _fail(_payload):
    return False, "deliberate failure"


# ------------------------------------------------------------- isolation
def test_candidate_gets_a_real_branch_and_worktree(backend, repo):
    cand = backend.create_candidate("chg-aaa111222333")
    assert Path(cand.worktree).is_dir()
    assert (Path(cand.worktree) / "README.md").exists()
    branches = _git(repo, "branch", "--list")
    # hyphen, not slash: git-for-Windows refuses slashed branch names here
    assert "evolution-chg-aaa111222333" in branches
    assert cand.base_commit == _git(repo, "rev-parse", "HEAD")
    # the worktree is NOT inside the live tree
    assert repo.resolve() not in Path(cand.worktree).resolve().parents


def test_live_tree_is_unchanged_while_candidate_edits(backend, repo):
    cand = backend.create_candidate("chg-bbb111222333")
    before = (repo / "core.py").read_text(encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD")

    # candidate edits ONLY its own worktree
    (Path(cand.worktree) / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
    backend.commit_candidate(cand, "candidate: bump VALUE")

    assert (repo / "core.py").read_text(encoding="utf-8") == before, \
        "live tree must not change while the candidate works"
    assert _git(repo, "rev-parse", "HEAD") == head_before
    # the change really landed in the candidate
    assert (Path(cand.worktree) / "core.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_candidate_diff_is_isolated(backend):
    cand = backend.create_candidate("chg-ccc111222333")
    (Path(cand.worktree) / "core.py").write_text("VALUE = 99\n", encoding="utf-8")
    backend.commit_candidate(cand, "candidate change")
    d = backend.diff(cand)
    assert d["ok"] is True
    assert "core.py" in d["files"]
    assert "README.md" not in d["files"]


def test_unsafe_branch_name_is_refused(backend):
    with pytest.raises(EvolutionVcsError):
        backend.create_candidate("bad name with spaces")


def test_duplicate_branch_is_refused(backend):
    backend.create_candidate("chg-ddd111222333")
    with pytest.raises(EvolutionVcsError):
        backend.create_candidate("chg-ddd111222333")


# ------------------------------------------------------------------- gates
def _make_engine(backend, repo):
    eng = EvolutionEngine(audit=None, vcs=backend)
    p = eng.propose(title="bump value", description="test", target="core.py",
                    payload={"file": "core.py"})
    eng.add_conformance(p.proposal_id, "sanity", _pass)
    eng.run_conformance(p.proposal_id)
    return eng, p


def test_failing_test_gate_blocks_approval(backend, repo):
    eng, p = _make_engine(backend, repo)
    eng.add_gate(p.proposal_id, "unit tests", GATE_TEST, _fail)
    eng.run_gates(p.proposal_id)
    # conformance passed, gate failed -> approval must be refused
    assert p.conformance_passed is True
    out = eng.approve(p.proposal_id, by="owner")
    assert out["ok"] is False
    assert "gates" in out["error"]
    assert p.state != APPROVED


def test_failing_security_gate_blocks_approval(backend, repo):
    eng, p = _make_engine(backend, repo)
    eng.add_gate(p.proposal_id, "scanner", GATE_SECURITY, _fail)
    eng.run_gates(p.proposal_id)
    out = eng.approve(p.proposal_id, by="owner")
    assert out["ok"] is False
    assert p.state != APPROVED


def test_unrun_gates_are_not_a_pass(backend, repo):
    eng, p = _make_engine(backend, repo)
    eng.add_gate(p.proposal_id, "unit tests", GATE_TEST, _pass)
    # never called run_gates()
    out = eng.approve(p.proposal_id, by="owner")
    assert out["ok"] is False


def test_missing_owner_approval_blocks_apply(backend, repo):
    eng, p = _make_engine(backend, repo)
    out = eng.apply(p.proposal_id, by="agent")
    assert out["ok"] is False
    assert "not approved" in out["error"]
    assert p.state != APPLIED


def test_failing_conformance_blocks_approval_even_with_gates(backend, repo):
    eng = EvolutionEngine(audit=None, vcs=backend)
    p = eng.propose(title="x", description="y", target="core.py")
    eng.add_conformance(p.proposal_id, "sanity", _fail)
    eng.run_conformance(p.proposal_id)
    eng.add_gate(p.proposal_id, "unit tests", GATE_TEST, _pass)
    eng.run_gates(p.proposal_id)
    assert eng.approve(p.proposal_id, by="owner")["ok"] is False


# ------------------------------------------------------------------ adopt
def test_approved_candidate_really_adopts(backend, repo):
    eng, p = _make_engine(backend, repo)
    wt = Path(p.worktree)
    (wt / "core.py").write_text("VALUE = 42\n", encoding="utf-8")
    backend.commit_candidate(backend.get(p.proposal_id), "candidate: set 42")
    eng.add_gate(p.proposal_id, "unit tests", GATE_TEST, _pass)
    eng.run_gates(p.proposal_id)
    assert eng.approve(p.proposal_id, by="owner")["ok"] is True

    out = eng.apply(p.proposal_id, by="owner")
    assert out["ok"] is True, out
    assert out["merge"]["ok"] is True
    # the change is now really in the live tree
    assert (repo / "core.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert p.state == APPLIED
    # adopting cleans up the worktree
    assert not wt.exists()


def test_adoption_records_lineage(backend, repo):
    eng, p = _make_engine(backend, repo)
    (Path(p.worktree) / "core.py").write_text("VALUE = 7\n", encoding="utf-8")
    backend.commit_candidate(backend.get(p.proposal_id), "candidate: set 7")
    eng.add_gate(p.proposal_id, "unit tests", GATE_TEST, _pass)
    eng.run_gates(p.proposal_id)
    eng.approve(p.proposal_id, by="owner")
    out = eng.apply(p.proposal_id, by="owner")

    assert out["merge"]["previous_commit"] != out["merge"]["merged_commit"]
    # the candidate branch commits are reachable from the base branch now
    log_out = _git(repo, "log", "--oneline")
    assert "candidate: set 7" in log_out


def test_rejection_removes_worktree_and_branch(backend, repo):
    eng, p = _make_engine(backend, repo)
    wt = Path(p.worktree)
    branch = backend.get(p.proposal_id).branch
    (wt / "core.py").write_text("VALUE = 5\n", encoding="utf-8")
    backend.commit_candidate(backend.get(p.proposal_id), "candidate change")

    out = eng.reject(p.proposal_id, by="owner", reason="not wanted")
    assert out["ok"] is True
    assert not wt.exists(), "worktree must be removed on rejection"
    assert branch not in _git(repo, "branch", "--list"), "branch must be deleted"
    # live tree untouched
    assert (repo / "core.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_rejection_preserves_lineage_in_history(backend, repo):
    eng, p = _make_engine(backend, repo)
    eng.reject(p.proposal_id, by="owner", reason="no")
    hist = eng.history()
    assert any(h["proposal_id"] == p.proposal_id for h in hist)
    assert any(h["state"] == "rejected" for h in hist)


# ---------------------------------------------------------------- rollback
def test_rollback_restores_a_known_good_commit(backend, repo):
    good = _git(repo, "rev-parse", "HEAD")
    # make an unwanted change directly on main
    (repo / "core.py").write_text("VALUE = 666\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "unwanted")
    assert (repo / "core.py").read_text(encoding="utf-8") == "VALUE = 666\n"

    eng = EvolutionEngine(audit=None, vcs=backend)
    out = eng.rollback(good)
    assert out["ok"] is True
    assert (repo / "core.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(repo, "rev-parse", "HEAD") == good


def test_rollback_rejects_unknown_commit(backend):
    eng = EvolutionEngine(audit=None, vcs=backend)
    out = eng.rollback("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    assert out["ok"] is False


# ------------------------------------------------------- legacy behaviour
def test_engine_without_vcs_still_works(repo):
    """Backwards compatibility: no backend -> caller-supplied applier."""
    eng = EvolutionEngine(audit=None)
    p = eng.propose(title="legacy", description="", target="x")
    eng.add_conformance(p.proposal_id, "sanity", _pass)
    eng.run_conformance(p.proposal_id)
    assert eng.approve(p.proposal_id, by="owner")["ok"] is True
    out = eng.apply(p.proposal_id, lambda payload: {"ok": True}, by="owner")
    assert out["ok"] is True
    assert p.candidate is None


def test_build_default_backend_points_outside_the_tree():
    b = build_default_backend()
    assert b.worktrees_root.name == "evolution"
    assert b.repo_root.resolve() not in b.worktrees_root.resolve().parents
