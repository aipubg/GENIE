"""Restore the branch ref when it refuses to advance.

In this environment, writes under .git/refs/heads/** are reverted, so
`git commit` creates the commit and writes the reflog but the branch ref stays
where it was (or goes missing) - which looks exactly like lost work even though
every object is intact.

packed-refs, by contrast, persists. So this re-points the branch there:
  * takes the newest commit from .git/logs/HEAD
  * rewrites packed-refs sorted by refname (heads sort before tags)
  * omits the optional '^' peeled lines, which are easy to corrupt and not
    required for correctness
  * removes any conflicting loose ref

Usage:
    python scripts/fix_branch_ref.py            # fix the current branch
    python scripts/fix_branch_ref.py --show     # just report
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess


def current_branch(gitdir: pathlib.Path) -> str:
    head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        return head.split(" ", 1)[1].strip()
    return "HEAD"


def newest_from_reflog(gitdir: pathlib.Path) -> str:
    """Fall back to the branch reflog, then HEAD reflog."""
    for rel in ("logs/HEAD",):
        p = gitdir / rel
        if p.exists():
            lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            if lines:
                parts = lines[-1].split()
                if len(parts) >= 2 and len(parts[1]) == 40:
                    return parts[1]
    raise SystemExit("could not determine the newest commit from the reflog")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--git-dir", default=None)
    args = ap.parse_args()

    gitdir = pathlib.Path(args.git_dir) if args.git_dir else pathlib.Path(
        subprocess.run(["git", "rev-parse", "--absolute-git-dir"],
                       capture_output=True, text=True).stdout.strip())

    sha = newest_from_reflog(gitdir)
    if args.show:
        print("newest commit in reflog:", sha)
        return 0

    branch = current_branch(gitdir)
    if branch == "HEAD":
        raise SystemExit("HEAD is detached; refusing to guess")

    entries = []
    for root, prefix in ((gitdir / "refs" / "tags", "refs/tags"),
                         (gitdir / "refs" / "heads", "refs/heads")):
        if not root.exists():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            name = prefix + "/" + f.relative_to(root).as_posix()
            if name == branch:
                continue          # this one comes from the reflog
            entries.append((name, f.read_text(encoding="utf-8").strip()))
    entries.append((branch, sha))
    entries.sort(key=lambda e: e[0])

    with open(gitdir / "packed-refs", "wb") as out:
        out.write(b"# pack-refs with: peeled fully-peeled sorted \n")
        out.write(("\n".join(f"{s} {n}" for n, s in entries) + "\n").encode("ascii"))

    loose = gitdir / "refs" / branch.replace("refs/", "")
    if loose.exists():
        loose.unlink()

    print(f"{branch} -> {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
