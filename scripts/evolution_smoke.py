"""One harmless REAL GENIE smoke test for the Git evolution backend.

Unlike the deterministic tests (which use throwaway repos), this runs against
the actual GENIE repository to prove the backend works in the real tree.

It is deliberately harmless:
  * creates a disposable candidate branch + worktree
  * adds ONE fixture file under docs/ (no production behaviour is changed)
  * verifies the live tree is untouched
  * rejects the candidate, which removes the branch and the worktree

It never adopts, never merges, never touches `main`.

    python scripts/evolution_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import subprocess  # noqa: E402

from agents.evolution import EvolutionEngine  # noqa: E402
from agents.evolution_vcs import (build_default_backend,  # noqa: E402
                                  default_worktrees_root)


def _main_root(any_repo: Path) -> Path:
    """Resolve the checkout that owns the worktree admin data.

    Driving evolution worktree operations from inside a LINKED worktree is
    unsafe on git 2.55.0.windows.3 (see agents/evolution_vcs.py), so this
    smoke deliberately targets the main checkout.
    """
    out = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                          "--git-common-dir"], cwd=str(any_repo),
                         capture_output=True, text=True).stdout.strip()
    p = Path(out)
    return p.parent if p.name == ".git" else p


def main() -> int:
    here = Path(__file__).resolve().parents[1]
    repo = _main_root(here)
    wt_root = default_worktrees_root(repo)
    base = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=str(repo), capture_output=True, text=True).stdout.strip()
    print(f"repo   : {repo}")
    print(f"branch : {base}")
    print(f"wt root: {wt_root}")

    # Survival guard: snapshot registered worktrees so we can prove the smoke
    # did not damage any existing checkout.
    admin = repo / ".git" / "worktrees"
    before = sorted(p.name for p in admin.iterdir()) if admin.is_dir() else []

    backend = build_default_backend(repo_root=repo, base_branch=base)
    engine = EvolutionEngine(audit=None, vcs=backend)

    live_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                                 capture_output=True, text=True).stdout.strip()
    dirty_before = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                                  capture_output=True, text=True).stdout.strip()

    p = engine.propose(title="smoke: harmless fixture",
                       description="Disposable smoke test of the evolution VCS backend.",
                       target="docs/")
    if p.candidate is None:
        print("FAILED: no candidate created —", p.note)
        return 1

    wt = Path(p.worktree)
    print(f"\n1. candidate worktree created : {wt}")
    print(f"   branch                    : {backend.get(p.proposal_id).branch}")
    assert wt.is_dir(), "worktree directory missing"

    # harmless change: a fixture file inside the CANDIDATE worktree only
    fixture = wt / "docs" / "EVOLUTION_SMOKE_FIXTURE.md"
    fixture.write_text("# evolution smoke fixture\n\nDisposable. Safe to delete.\n",
                       encoding="utf-8")
    committed = backend.commit_candidate(backend.get(p.proposal_id),
                                         "smoke: add harmless fixture")
    print(f"2. committed in worktree     : {committed.get('commit', '')[:12]} "
          f"({len(committed.get('changed', []))} file)")

    diff = backend.diff(backend.get(p.proposal_id))
    print(f"3. isolated diff files       : {diff['files']}")

    # live tree must be untouched
    live_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo),
                                capture_output=True, text=True).stdout.strip()
    dirty_after = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                                 capture_output=True, text=True).stdout.strip()
    fixture_in_live = (repo / "docs" / "EVOLUTION_SMOKE_FIXTURE.md").exists()

    print(f"4. live HEAD unchanged       : {live_before == live_after}")
    print(f"   live working tree clean   : {dirty_before == dirty_after}")
    print(f"   fixture leaked to live    : {fixture_in_live} (must be False)")

    # reject -> real cleanup
    out = engine.reject(p.proposal_id, by="smoke", reason="disposable smoke test")
    branch_gone = not subprocess.run(
        ["git", "branch", "--list", backend.get(p.proposal_id).branch],
        cwd=str(repo), capture_output=True, text=True).stdout.strip()

    print(f"5. rejected                  : {out['ok']}")
    print(f"   worktree removed          : {not wt.exists()}")
    print(f"   branch deleted            : {branch_gone}")
    print(f"   lineage kept in history   : "
          f"{any(h['proposal_id'] == p.proposal_id for h in engine.history())}")

    after = sorted(p.name for p in admin.iterdir()) if admin.is_dir() else []
    print(f"6. existing checkouts intact : {before == after}  ({before} -> {after})")

    ok = (live_before == live_after and dirty_before == dirty_after
          and not fixture_in_live and not wt.exists() and branch_gone
          and before == after
          and diff["files"] == ["docs/EVOLUTION_SMOKE_FIXTURE.md"])
    print("\nSMOKE RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
