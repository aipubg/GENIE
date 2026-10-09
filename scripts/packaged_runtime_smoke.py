"""Smoke the *packaged* backend runtime with its own embedded interpreter.

Run as:
    backend-dist\\backend-runtime\\python\\python.exe artifacts\\packaged_runtime_smoke.py

(executed with cwd = backend-dist/backend-runtime so `app/` is importable via
the embeddable python's ._pth `.` entry)

Checks:
  1. embedded interpreter identity
  2. needle (NEDLE2 / Cactus Needle) import + version + runtime status
  3. the modules added after rc14: core.isolation, agents.competition
  4. the canonical UI transport file shipped under app/ui/web/api.js
  5. every backend package listed by build_backend_runtime.py imports cleanly
"""
from __future__ import annotations

import importlib
import json
import os
import sys

# The embeddable interpreter's ._pth anchors "." at the python/ directory, and
# this smoke script lives outside the tree, so put app/ on sys.path explicitly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNTIME = os.path.dirname(os.path.dirname(sys.executable)) if sys.executable else None
_APP = os.path.join(_RUNTIME, "app") if _RUNTIME else None
if _APP and os.path.isdir(_APP) and _APP not in sys.path:
    sys.path.insert(0, _APP)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        RESULTS.append((name, True, detail))
    except Exception as exc:  # noqa: BLE001 - smoke reports, never raises
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))


def _interp():
    return f"{sys.version.split()[0]} @ {sys.executable}"


def _needle():
    import needle
    ver = getattr(needle, "__version__", "?")
    return f"needle {ver}"


def _needle_status():
    from director.needle_runtime import get_runtime
    st = get_runtime().status()
    if not isinstance(st, dict):
        raise TypeError(f"status() returned {type(st).__name__}, expected dict")
    return f"engine={st.get('engine')} keys={len(st)}"


def _isolation():
    from core.isolation import report, IsolationKind
    r = report()
    if not isinstance(r, dict):
        raise TypeError("report() not a dict")
    eff = r.get("effective")
    if not eff:
        raise AssertionError("report() has no effective kind")
    return f"effective={eff} levels={len(r.get('levels', {}))} enum={len(list(IsolationKind))}"


def _competition():
    """Real end-to-end compete, not just an import check."""
    from agents.competition import CompetitionEngine
    from core.contracts import AgentDefinition
    from agents.contracts import TaskNode

    calls = {"n": 0}

    def runner(agent, task, context):
        calls["n"] += 1
        return {"output": f"{agent.name} answer", "steps": 1}

    def verifier(task, output):
        return {"ok": True, "score": 0.9, "notes": "smoke"}

    eng = CompetitionEngine(runner=runner, verifier=verifier)
    task = TaskNode(objective="smoke")
    cands = [AgentDefinition(name=f"c{i}") for i in range(3)]
    res = eng.compete(task, cands, criteria="smoke criteria")
    if res.outcome != "winner":
        raise AssertionError(f"expected winner, got {res.outcome} ({res.note})")
    return (f"outcome=winner winner={res.winner} "
            f"requested={res.requested} runner_calls={calls['n']}")


def _api_js():
    # must resolve inside the PACKAGED tree, not the source tree
    if not _APP:
        raise AssertionError("could not locate packaged app/")
    p = os.path.join(_APP, "ui", "web", "api.js")
    with open(p, encoding="utf-8") as fh:
        src = fh.read()
    if "apiFetchStrict" not in src:
        raise AssertionError("api.js shipped without apiFetchStrict")
    return f"{os.path.getsize(p):,} bytes, apiFetchStrict present"


PACKAGES = [
    "agents", "browser", "channels", "computer", "context", "core", "devices",
    "director", "evaluation", "experience", "forecast", "integrations", "memory",
    "missions", "models", "perception", "plugins", "proactive", "security", "skills",
    "specs", "teaching", "usermodel", "voice",
]


def main() -> int:
    check("embedded interpreter", _interp)
    check("needle import", _needle)
    check("needle_runtime.status()", _needle_status)
    check("core.isolation (new)", _isolation)
    check("agents.competition (new)", _competition)
    check("app/ui/web/api.js shipped", _api_js)
    for pkg in PACKAGES:
        check(f"import {pkg}", lambda p=pkg: "ok" if importlib.import_module(p) else "?")

    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ::  {detail}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
