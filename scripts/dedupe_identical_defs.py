"""Collapse byte-identical duplicate definitions.

A repeated definition is not merely untidy: the second one silently shadows the
first, so if anyone ever edits the first copy the change simply does nothing.
This removes duplicates only when the definitions are BYTE-IDENTICAL, so it can
never change behaviour. Anything that differs is reported and left alone.

    python scripts/dedupe_identical_defs.py            # dry run
    python scripts/dedupe_identical_defs.py --apply
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"node_modules", ".git", "dist", "backend-dist", "__pycache__",
              "vendor", ".build", "bin", "obj", "artifacts", ".pytest_cache"}


def _span(node: ast.AST) -> tuple[int, int]:
    """First and last line of a definition, including its decorators."""
    start = getattr(node, "lineno", 0)
    for dec in getattr(node, "decorator_list", []) or []:
        start = min(start, dec.lineno)
    return start, getattr(node, "end_lineno", start)


def process(path: Path, apply: bool) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0, 0

    # Collect duplicate groups within each scope.
    removals: list[tuple[int, int]] = []
    differing: list[str] = []

    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef))]
    for scope in scopes:
        by_name: dict[str, list[ast.AST]] = {}
        for child in ast.iter_child_nodes(scope):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                by_name.setdefault(child.name, []).append(child)
        for name, defs in by_name.items():
            if len(defs) < 2:
                continue
            segments = []
            for d in defs:
                s, e = _span(d)
                segments.append("".join(lines[s - 1:e]))
            if len(set(segments)) == 1:
                # All identical: keep the first, drop the rest.
                for d in defs[1:]:
                    s, e = _span(d)
                    removals.append((s, e))
            else:
                differing.append(name)

    if not removals:
        return 0, len(differing)

    # Drop from the bottom up so line numbers stay valid.
    for s, e in sorted(removals, reverse=True):
        # Also drop a single following blank line left by the removal.
        end = e
        if end < len(lines) and lines[end].strip() == "":
            end += 1
        del lines[s - 1:end]

    if apply:
        path.write_text("".join(lines), encoding="utf-8", newline="")
    return len(removals), len(differing)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    total_removed = 0
    files_changed: list[str] = []
    differ: list[str] = []

    for py in sorted(ROOT.rglob("*.py")):
        if SKIP_PARTS & set(py.parts):
            continue
        removed, differing = process(py, args.apply)
        if removed:
            total_removed += removed
            files_changed.append(f"{py.relative_to(ROOT)} ({removed})")
        if differing:
            differ.append(f"{py.relative_to(ROOT)}: {', '.join(sorted(set(differing)))}")

    print(f"{'applied' if args.apply else 'dry run'}: "
          f"{total_removed} duplicate definition(s) across {len(files_changed)} file(s)")
    for f in files_changed[:25]:
        print("  ", f)
    if len(files_changed) > 25:
        print(f"   ... and {len(files_changed) - 25} more file(s)")
    if differ:
        print(f"\nNOT touched - duplicates that DIFFER (need a human decision):")
        for d in differ[:20]:
            print("  ", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
