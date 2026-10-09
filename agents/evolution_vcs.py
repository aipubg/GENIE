"""Real Git-backed VCS backend for governed self-evolution (agents/evolution_vcs).

Why this module exists
----------------------
Before this, candidate isolation was *conceptual*: `EvolutionEngine.apply()`
took an arbitrary `applier` callable, so a candidate agent could in principle
write straight into the live GENIE tree. That is not acceptable for a system
that modifies itself.

This module makes isolation **physical**:

    propose    -> real branch  -> real worktree (separate directory on disk)
    candidate  -> edits land ONLY in that worktree
    gates      -> tests / security / benchmark / owner approval
    adopt      -> real merge into the base branch
    reject     -> worktree + branch removed, lineage preserved
    rollback   -> real revert to a known-good commit

Nothing silently self-modifies. A candidate cannot touch the live tree because
the live tree is a different directory.

Platform lessons baked in (both cost real debugging time)
---------------------------------------------------------
1. Git-for-Windows 2.55.0.windows.3 refuses slashed branch names in this
   context: `git branch evolution/x <base>` exits 0 but creates NOTHING, and
   `git worktree add -b a/b` fails with "invalid reference". Candidate branches
   therefore use a HYPHEN: `evolution-<proposal_id>`.

2. `git worktree add|remove|prune` must be run from the MAIN checkout, never
   from inside a linked worktree. Running
   `git worktree remove --force <other>` from inside a linked worktree deletes
   the admin directory of the worktree you are STANDING IN, breaking its .git
   pointer ("fatal: not a git repository (NULL)"). `_main_root()` resolves the
   owning repo and all worktree commands are dispatched from there.

3. Git-for-Windows does not strip a cygwin `/e/...` prefix, so absolute
   Windows paths are always passed through verbatim.

Backend contract
----------------
    EvolutionVcsBackend
        create_candidate(proposal_id, title)  -> create branch + worktree
        create_branch(name)                   -> real branch
        create_worktree(branch, path)         -> real worktree
        diff(candidate)                       -> isolated diff
        commit_candidate(candidate, message)  -> real commit in the worktree
        status(candidate)                     -> real git status
        adopt(candidate)                      -> merge into base branch
        reject(candidate)                     -> remove worktree + branch
        cleanup(candidate)                    -> remove worktree only
        rollback(base_commit)                 -> restore a known-good commit
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.evolution_vcs")

# Branch names must be safe: no spaces, no '..', no control chars.
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")


class EvolutionVcsError(RuntimeError):
    """Raised when a VCS operation cannot be completed safely."""


@dataclass
class Candidate:
    """A physically isolated workspace for one proposed change."""

    proposal_id: str
    branch: str
    worktree: str
    base_branch: str
    base_commit: str = ""
    commits: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"proposal_id": self.proposal_id, "branch": self.branch,
                "worktree": self.worktree, "base_branch": self.base_branch,
                "base_commit": self.base_commit, "commits": list(self.commits)}


class EvolutionVcsBackend:
    """Abstract contract. Implementations must provide real isolation."""

    def create_candidate(self, proposal_id: str, title: str = "") -> Candidate:
        raise NotImplementedError

    def create_branch(self, name: str) -> None:
        raise NotImplementedError

    def create_worktree(self, branch: str, path: str) -> str:
        raise NotImplementedError

    def diff(self, candidate: Candidate) -> Dict[str, Any]:
        raise NotImplementedError

    def commit_candidate(self, candidate: Candidate, message: str) -> Dict[str, Any]:
        raise NotImplementedError

    def status(self, candidate: Candidate) -> Dict[str, Any]:
        raise NotImplementedError

    def adopt(self, candidate: Candidate) -> Dict[str, Any]:
        raise NotImplementedError

    def reject(self, candidate: Candidate) -> Dict[str, Any]:
        raise NotImplementedError

    def cleanup(self, candidate: Candidate) -> Dict[str, Any]:
        raise NotImplementedError

    def rollback(self, commit: str) -> Dict[str, Any]:
        raise NotImplementedError


class GitEvolutionBackend(EvolutionVcsBackend):
    """Real Git implementation: real branches, real worktrees, real commits.

    Args:
        repo_root:       the git repository (the live GENIE tree).
        worktrees_root:  where candidate worktrees are created. Kept OUTSIDE
                         repo_root so a candidate can never be confused with
                         the live tree and so cleanup on the live tree can
                         never delete candidate work.
        base_branch:     branch that candidates are branched from and merged
                         back into.
    """

    def __init__(self, repo_root: str | Path, worktrees_root: str | Path,
                 base_branch: str = "main"):
        self.repo_root = Path(repo_root).resolve()
        self.worktrees_root = Path(worktrees_root).resolve()
        self.base_branch = base_branch
        self._candidates: Dict[str, Candidate] = {}
        self._main_root_cache: Optional[Path] = None
        self._validate_repo()

    # ------------------------------------------------------------- internals
    def _validate_repo(self) -> None:
        if not (self.repo_root / ".git").exists():
            raise EvolutionVcsError(f"{self.repo_root} is not a git repository")
        try:
            self._git(["rev-parse", "--git-dir"])
        except Exception as exc:  # noqa: BLE001
            raise EvolutionVcsError(f"git repository unusable: {exc}") from exc

    def _main_root(self) -> Path:
        """The repository that OWNS the worktree admin data (the main checkout).

        `git worktree add|remove|prune` MUST be run from here. Verified on
        git 2.55.0.windows.3: running `git worktree remove --force <other>`
        from inside a LINKED worktree deletes the admin directory of the
        worktree you are standing in, breaking its .git pointer.
        """
        if self._main_root_cache is not None:
            return self._main_root_cache
        try:
            out = self._git(["rev-parse", "--path-format=absolute",
                             "--git-common-dir"]).stdout.strip()
        except Exception:  # noqa: BLE001 - older git without --path-format
            out = self._git(["rev-parse", "--git-common-dir"]).stdout.strip()
        p = Path(out)
        self._main_root_cache = p.parent if p.name == ".git" else p
        return self._main_root_cache

    def _git(self, args: List[str], cwd: Optional[Path] = None,
             check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", *args], cwd=str(cwd or self.repo_root),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=120)
        if check and proc.returncode != 0:
            raise EvolutionVcsError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}")
        return proc

    @staticmethod
    def _safe_name(name: str) -> str:
        if not name or not _SAFE_BRANCH.match(name):
            raise EvolutionVcsError(f"unsafe branch/identifier name: {name!r}")
        return name

    # --------------------------------------------------------------- branch
    def create_branch(self, name: str) -> None:
        self._safe_name(name)
        existing = self._git(["branch", "--list", name], check=False).stdout.strip()
        if existing:
            raise EvolutionVcsError(f"branch {name} already exists")
        self._git(["branch", name, self.base_branch])
        # git 2.55.0.windows.3 silently no-ops on slashed names — verify.
        if not self._git(["branch", "--list", name]).stdout.strip():
            raise EvolutionVcsError(
                f"branch {name} was not created (git refused the name)")

    # ------------------------------------------------------------- worktree
    def create_worktree(self, branch: str, path: str) -> str:
        self._safe_name(branch)
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise EvolutionVcsError(f"worktree path already exists: {target}")
        # Absolute Windows path + run from the MAIN repo (see module docstring).
        self._git(["worktree", "add", str(target), branch], cwd=self._main_root())
        return str(target)

    # ----------------------------------------------------------- candidates
    def create_candidate(self, proposal_id: str, title: str = "") -> Candidate:
        """Create a real branch + real worktree. The live tree is untouched."""
        pid = self._safe_name(proposal_id)
        # HYPHEN, not slash: git-for-Windows refuses slashed branch names here.
        branch = f"evolution-{pid}"
        path = self.worktrees_root / pid

        base_commit = self._git(["rev-parse", "HEAD"]).stdout.strip()
        self.create_branch(branch)
        try:
            wt = self.create_worktree(branch, str(path))
        except Exception:
            self._git(["branch", "-D", branch], check=False)   # no dangling branch
            raise

        cand = Candidate(proposal_id=pid, branch=branch, worktree=wt,
                         base_branch=self.base_branch, base_commit=base_commit)
        self._candidates[pid] = cand
        log.info("evolution candidate %s isolated at %s (branch %s)", pid, wt, branch)
        return cand

    def commit_candidate(self, candidate: Candidate, message: str) -> Dict[str, Any]:
        """Commit whatever the candidate agent changed, inside its worktree."""
        wt = Path(candidate.worktree)
        status = self._git(["status", "--porcelain"], cwd=wt).stdout.strip()
        if not status:
            return {"ok": False, "error": "no changes to commit", "changed": []}
        self._git(["add", "-A"], cwd=wt)
        self._git(["commit", "-m", message], cwd=wt)
        sha = self._git(["rev-parse", "HEAD"], cwd=wt).stdout.strip()
        candidate.commits.append(sha)
        changed = [l[3:].strip() for l in status.splitlines() if len(l) > 3]
        return {"ok": True, "commit": sha, "changed": changed}

    def diff(self, candidate: Candidate) -> Dict[str, Any]:
        """The candidate's diff against its base — isolated, never the live tree."""
        wt = Path(candidate.worktree)
        name_status = self._git(
            ["diff", "--name-status", candidate.base_commit, "HEAD"], cwd=wt).stdout.strip()
        stat = self._git(["diff", "--stat", candidate.base_commit, "HEAD"], cwd=wt).stdout.strip()
        files = [l.split("\t", 1)[-1] for l in name_status.splitlines() if l.strip()]
        return {"ok": True, "files": files, "name_status": name_status, "stat": stat}

    def status(self, candidate: Candidate) -> Dict[str, Any]:
        wt = Path(candidate.worktree)
        porcelain = self._git(["status", "--porcelain"], cwd=wt).stdout.strip()
        branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt).stdout.strip()
        head = self._git(["rev-parse", "HEAD"], cwd=wt).stdout.strip()
        return {"ok": True, "branch": branch, "head": head,
                "dirty": bool(porcelain),
                "changes": [l for l in porcelain.splitlines() if l.strip()]}

    # --------------------------------------------------------------- adopt
    def adopt(self, candidate: Candidate) -> Dict[str, Any]:
        """Merge the candidate branch into the base branch — a REAL merge.

        The caller (EvolutionEngine) must have already passed every gate.
        """
        head_before = self._git(["rev-parse", "HEAD"], cwd=self._main_root()).stdout.strip()
        try:
            self._git(["checkout", self.base_branch], cwd=self._main_root())
            self._git(["merge", "--no-ff", candidate.branch, "-m",
                       f"evolution: adopt {candidate.proposal_id}"],
                      cwd=self._main_root())
        except EvolutionVcsError as exc:
            self._git(["merge", "--abort"], cwd=self._main_root(), check=False)
            self._git(["checkout", self.base_branch], cwd=self._main_root(), check=False)
            return {"ok": False, "error": str(exc)}
        head_after = self._git(["rev-parse", "HEAD"], cwd=self._main_root()).stdout.strip()
        self.cleanup(candidate)
        return {"ok": True, "merged_commit": head_after, "previous_commit": head_before}

    # ------------------------------------------------------ reject / cleanup
    def cleanup(self, candidate: Candidate) -> Dict[str, Any]:
        """Remove the worktree directory only (branch survives for lineage)."""
        wt = Path(candidate.worktree)
        removed = False
        if wt.exists():
            # DELIBERATELY NOT using "git worktree remove".
            # On git 2.55.0.windows.3 it destroys admin directories belonging
            # to OTHER registered worktrees (observed repeatedly: removing a
            # candidate silently deleted .git/worktrees/ui-final-genie-companion
            # and bricked that checkout with "not a git repository (NULL)").
            # Removing the directory and its own admin dir directly is
            # deterministic and touches nothing else.
            shutil.rmtree(wt, ignore_errors=True)
            removed = not wt.exists()
        self._drop_admin_dir(wt)
        return {"ok": True, "worktree_removed": removed, "branch": candidate.branch}

    def _drop_admin_dir(self, worktree_path: Path) -> None:
        """Delete only THIS worktree's admin dir (git names it after the dir)."""
        admin_root = self._main_root() / ".git" / "worktrees"
        if not admin_root.is_dir():
            return
        target = Path(worktree_path).resolve().name
        candidate_dir = admin_root / target
        if candidate_dir.is_dir() and candidate_dir != admin_root:
            shutil.rmtree(candidate_dir, ignore_errors=True)

    def reject(self, candidate: Candidate) -> Dict[str, Any]:
        """Remove the worktree AND delete the branch. Lineage stays in audit."""
        out = self.cleanup(candidate)
        self._git(["branch", "-D", candidate.branch], cwd=self._main_root(), check=False)
        gone = not self._git(["branch", "--list", candidate.branch],
                             cwd=self._main_root()).stdout.strip()
        return {"ok": True, **out, "branch_deleted": gone}

    # -------------------------------------------------------------- rollback
    def rollback(self, commit: str) -> Dict[str, Any]:
        """Restore the base branch to a known-good commit (real reset)."""
        self._safe_name(commit)
        exists = self._git(["cat-file", "-e", f"{commit}^{{commit}}"], check=False)
        if exists.returncode != 0:
            return {"ok": False, "error": f"unknown commit {commit}"}
        current = self._git(["rev-parse", "HEAD"], cwd=self._main_root()).stdout.strip()
        self._git(["checkout", self.base_branch], cwd=self._main_root())
        self._git(["reset", "--hard", commit], cwd=self._main_root())
        return {"ok": True, "restored_to": commit, "previous_head": current}

    # ---------------------------------------------------------------- query
    def get(self, proposal_id: str) -> Optional[Candidate]:
        return self._candidates.get(proposal_id)

    def candidates(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self._candidates.values()]


def default_worktrees_root(root: Path) -> Path:
    """Where candidate worktrees go for a given repo.

    If the repo already lives inside a `*-worktrees` directory (i.e. it IS a
    worktree), place candidates beside it rather than nesting another
    `GENIE-worktrees` level.
    """
    root = Path(root).resolve()
    if root.parent.name.endswith("worktrees"):
        return root.parent / "evolution"
    return root.parent / "GENIE-worktrees" / "evolution"


def build_default_backend(repo_root: str | Path | None = None,
                          base_branch: str | None = None) -> GitEvolutionBackend:
    """Backend for the real GENIE repo, with worktrees kept outside the tree.

    `base_branch` defaults to the branch currently checked out, so running from
    inside a worktree does not silently target `main`.
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    if base_branch is None:
        proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                              cwd=str(root), capture_output=True, text=True)
        base_branch = (proc.stdout.strip() or "main")
    return GitEvolutionBackend(repo_root=root,
                               worktrees_root=default_worktrees_root(root),
                               base_branch=base_branch)
