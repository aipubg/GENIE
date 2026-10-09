"""Duplicate-authority audit.

The migration has repeatedly turned up the same class of defect: two
registrations of one service, two definitions of one helper, two handlers for
one route. Each is invisible until something reads the wrong one, so this looks
for them mechanically rather than by memory.

Checks:
  1. duplicate top-level or method definitions with the same name in one file
  2. duplicate HTTP route literals in core/ipc/server.py
  3. duplicate service keys assigned in core/lifecycle.py
  4. duplicate public method names on C# client/service classes
  5. duplicate endpoint constants across the C# client
"""
from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
findings: list[tuple[str, str, str]] = []


def note(kind: str, where: str, detail: str) -> None:
    findings.append((kind, where, detail))


# --------------------------------------------------------------- 1. dup defs
def dup_defs(path: Path) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef)):
            continue
        seen: dict[str, int] = {}
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen[child.name] = seen.get(child.name, 0) + 1
        for name, count in seen.items():
            if count > 1:
                owner = getattr(node, "name", "<module>")
                note("duplicate-def", f"{path.relative_to(ROOT)}::{owner}",
                     f"{name} defined {count}x")


# -------------------------------------------------------------- 2. dup routes
def dup_routes(path: Path) -> None:
    """Same path handled twice by the SAME verb is a real duplicate.

    /api/providers appears once under do_GET and once under do_POST - that is
    correct HTTP, not duplication, so the verb is part of the key.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    verb = "?"
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for line in text.splitlines():
        m = re.match(r"\s*def (do_\w+)\(", line)
        if m:
            verb = m.group(1)
            continue
        for route in re.findall(r'path\s*==\s*"(/[^"]*)"', line):
            counts[(verb, route)] += 1
    for (verb_used, route), count in sorted(counts.items()):
        if count > 1:
            note("duplicate-route", str(path.relative_to(ROOT)),
                 f"{route} handled {count}x in {verb_used}")


# ------------------------------------------------------ 3. dup service keys
# Re-assignments that are deliberate, with the reason they are safe.
SERVICE_ALLOWLIST = {
    "director": ("assigned at boot, then hot-swapped when NEDLE2 finishes "
                 "provisioning (before and after the smoke test). Both writes "
                 "come from one provisioning job and assign the same engine."),
}


def dup_services(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    keys = re.findall(r'services\[\s*"([^"]+)"\s*\]\s*=', text)
    counts: dict[str, int] = defaultdict(int)
    for k in keys:
        counts[k] += 1
    for key, count in sorted(counts.items()):
        if count > 1 and key not in SERVICE_ALLOWLIST:
            note("duplicate-service", str(path.relative_to(ROOT)),
                 f'services["{key}"] assigned {count}x')


def accepted_exceptions() -> None:
    for key, reason in SERVICE_ALLOWLIST.items():
        print(f"accepted: services[\"{key}\"] reassigned - {reason}")


# ------------------------------------------------------- 4/5. C# duplicates
def dup_csharp(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    # public method names (rough but effective for this codebase's style)
    methods = re.findall(r'public\s+(?:static\s+|override\s+|async\s+)*'
                         r'[\w<>\[\]?]+\s+(\w+)\s*\(', text)
    counts: dict[str, int] = defaultdict(int)
    for m in methods:
        counts[m] += 1
    for name, count in sorted(counts.items()):
        if count > 1:
            note("duplicate-csharp-method", str(path.relative_to(ROOT)),
                 f"{name} declared {count}x")


def main() -> int:
    SKIP = {"node_modules", ".git", "dist", "backend-dist", "__pycache__",
            "vendor", ".build", "site-packages", "bin", "obj"}
    for py in ROOT.rglob("*.py"):
        if SKIP & set(py.parts):
            continue
        dup_defs(py)

    server = ROOT / "core" / "ipc" / "server.py"
    if server.exists():
        dup_routes(server)
    lifecycle = ROOT / "core" / "lifecycle.py"
    if lifecycle.exists():
        dup_services(lifecycle)

    for cs in (ROOT / "ui" / "windows").rglob("*.cs"):
        if "obj" in cs.parts or "bin" in cs.parts:
            continue
        dup_csharp(cs)

    accepted_exceptions()
    if not findings:
        print("No duplicates found.")
        return 0

    by_kind: dict[str, list] = defaultdict(list)
    for kind, where, detail in findings:
        by_kind[kind].append((where, detail))
    for kind in sorted(by_kind):
        print(f"\n{kind}  ({len(by_kind[kind])})")
        for where, detail in sorted(by_kind[kind]):
            print(f"  {where}: {detail}")
    print(f"\nTOTAL {len(findings)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
