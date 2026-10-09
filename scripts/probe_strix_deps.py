"""Stage-by-stage Strix dependency probe.

Reports the honest state of every Strix runtime dependency. Nothing is faked:
a stage is OK only if the import/connection actually works.

    python scripts/probe_strix_deps.py [--strix E:/G3/GENIE/vendor/strix-audit/strix-main]

Docker is deliberately NOT installed or configured by this script. If the
daemon is genuinely absent the stage stays BLOCKED — we do not chase a green
badge by reconfiguring the host.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

STAGES: list[tuple[str, str]] = [
    ("openai-agents SDK", "agents"),
    ("litellm", "litellm"),
    ("caido-sdk-client", "caido_sdk_client"),
    ("docker (python lib)", "docker"),
]


def probe(strix_path: Path | None) -> int:
    print("Strix dependency probe")
    print("=" * 62)

    results: list[tuple[str, bool, str]] = []
    notes: list[str] = []

    # --------------------------------------------- name-shadowing hazard
    # GENIE has its own top-level `agents` package. When the repo root is on
    # sys.path (e.g. cwd == repo root) `import agents` resolves to GENIE's
    # package and shadows the `openai-agents` distribution, so every upstream
    # Strix import fails with "No module named 'agents.agent'" even when the
    # dependency is installed. Detect and report it explicitly.
    try:
        agents_mod = importlib.import_module("agents")
        agents_file = getattr(agents_mod, "__file__", "") or ""
        if "site-packages" not in agents_file.replace("\\", "/"):
            notes.append(
                f"HAZARD: `agents` resolves to {agents_file} (GENIE's own package), "
                "shadowing the openai-agents distribution. Upstream Strix imports "
                "will fail from this cwd. Run Strix from a neutral cwd or remove "
                "the repo root from sys.path.")
            # Drop the shadowing path so the probes below measure the real dep.
            for entry in list(sys.path):
                try:
                    p = Path(entry or ".").resolve()
                except Exception:  # noqa: BLE001
                    continue
                if (p / "agents" / "__init__.py").is_file() and (p / "genie.py").is_file():
                    sys.path.remove(entry)
            for mod in [m for m in list(sys.modules) if m == "agents"
                        or m.startswith("agents.")]:
                sys.modules.pop(mod, None)
            notes.append("(repo root removed from sys.path for the probes below)")
    except Exception:  # noqa: BLE001
        pass

    # ------------------------------------------------ pure python imports
    for label, module in STAGES:
        try:
            mod = importlib.import_module(module)
            ver = getattr(mod, "__version__", None) or getattr(mod, "__VERSION__", "unknown")
            results.append((label, True, f"import OK (version {ver})"))
        except Exception as exc:  # noqa: BLE001
            results.append((label, False, f"{type(exc).__name__}: {exc}"))

    # --------------------------------------------- docker daemon liveness
    try:
        import docker
        try:
            client = docker.from_env()
            client.ping()
            results.append(("docker daemon", True, "ping OK"))
        except Exception as exc:  # noqa: BLE001
            results.append(("docker daemon", False, f"{type(exc).__name__}: {exc}"))
    except Exception:  # noqa: BLE001
        results.append(("docker daemon", False, "docker python lib not importable"))

    # ---------------------------------------------------- upstream strix
    if strix_path and strix_path.is_dir():
        sys.path.insert(0, str(strix_path))
        for label, module in [("strix.config", "strix.config"),
                              ("strix.agents.factory", "strix.agents.factory"),
                              ("strix.runtime", "strix.runtime")]:
            try:
                importlib.import_module(module)
                results.append((label, True, "import OK"))
            except Exception as exc:  # noqa: BLE001
                results.append((label, False, f"{type(exc).__name__}: {exc}"))
    else:
        print(f"(upstream strix source not found at {strix_path}; skipping)\n")

    for n in notes:
        print(f"  NOTE   {n}")
    print()

    ok = sum(1 for _, good, _ in results if good)
    for label, good, detail in results:
        mark = "OK    " if good else "BLOCKED"
        print(f"  {mark}  {label:24s} {detail}")
    print("=" * 62)
    print(f"{ok}/{len(results)} stages OK")

    blocked = [l for l, g, _ in results if not g]
    if blocked:
        print("\nRemaining blockers:")
        for b in blocked:
            print(f"  - {b}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strix", default=str(Path(__file__).resolve().parents[1] / "vendor/strix-audit/strix-main"),
                    help="path to the upstream strix source tree")
    args = ap.parse_args()
    return probe(Path(args.strix))


if __name__ == "__main__":
    raise SystemExit(main())
