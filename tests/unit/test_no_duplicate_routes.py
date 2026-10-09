"""Deduplication regression (section 25).

Duplicate route handlers inside the same HTTP method are dead code: the first
`return` wins and everything after it is unreachable. They also hide bugs,
because an edit to the reachable copy silently leaves a stale twin behind.

This test proves each HTTP method in the IPC server declares every route at
most once, so duplicated authorities cannot reappear.
"""
from __future__ import annotations

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "core" / "ipc" / "server.py"
ROUTE_RE = re.compile(r'path\s*==\s*"([^"]+)"|path\.endswith\(\s*"([^"]+)"\s*\)')


def _method_bodies(source: str):
    for match in re.finditer(r"def (do_GET|do_POST|do_DELETE|do_PATCH|do_PUT)\(self.*?\n(?=    def |\Z)",
                             source, re.S):
        yield match.group(1), match.group(0)


def test_server_source_is_present():
    assert SERVER.exists(), f"missing {SERVER}"


def test_no_duplicate_routes_within_a_method():
    source = SERVER.read_text(encoding="utf-8")
    bodies = list(_method_bodies(source))
    assert bodies, "no HTTP method handlers found - did server.py change shape?"

    problems = []
    for name, body in bodies:
        routes = [a or b for a, b in ROUTE_RE.findall(body)]
        seen = set()
        for route in routes:
            if route in seen:
                problems.append(f"{name}: duplicate route {route}")
            seen.add(route)
    assert not problems, "unreachable duplicate routes:\n  " + "\n  ".join(problems)


def test_no_duplicate_provider_mutation_routes():
    """Only one path may mutate provider settings (section 25)."""
    source = SERVER.read_text(encoding="utf-8")
    for _, body in _method_bodies(source):
        routes = [a or b for a, b in ROUTE_RE.findall(body)]
        mutations = [r for r in routes
                     if r.startswith("/api/providers")
                     and r not in ("/api/providers",)]
        dupes = {r for r in mutations if mutations.count(r) > 1}
        assert not dupes, f"duplicate provider mutation routes: {sorted(dupes)}"
