"""Run the three test categories and report them separately.

    python tools/test_suites.py              all three categories
    python tools/test_suites.py deterministic
    python tools/test_suites.py real         the serial real-desktop suite
    python tools/test_suites.py owner        the pending owner-acceptance list

Why this exists: a single mixed run cannot be honest. Tests that mutate one real desktop fail
intermittently when another process changes the system volume or a Chrome session is reused, and
mixing them into the deterministic result turns a real signal into noise. Separating them means
the deterministic suite can be *fully green* and the real-machine suite can be *fully green*, and
the owner-only items are reported as pending rather than hidden.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CATEGORIES = {
    "deterministic": ["-m", "not real_machine and not hardware_optional and not owner_acceptance"],
    "real": ["-m", "real_machine or hardware_optional"],
    "owner": ["-m", "owner_acceptance", "-rs"],
}


def run(name: str) -> int:
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *CATEGORIES[name]]
    print(f"\n{'=' * 72}\n{name.upper()} SUITE\n{'=' * 72}")
    completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    tail = [line for line in (completed.stdout or "").splitlines() if line.strip()][-12:]
    for line in tail:
        print(line)
    if completed.returncode != 0 and completed.stdout:
        failures = [line for line in completed.stdout.splitlines() if line.startswith("FAILED")]
        for line in failures:
            print(line)
    return completed.returncode


def main(argv: list[str]) -> int:
    names = argv[1:] or list(CATEGORIES)
    unknown = [n for n in names if n not in CATEGORIES]
    if unknown:
        print(f"unknown suite(s): {', '.join(unknown)}; choose from {', '.join(CATEGORIES)}")
        return 2
    results: dict[str, int] = {}
    for name in names:
        results[name] = run(name)
    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    labels = {"deterministic": "deterministic suite", "real": "real-machine serial suite",
              "owner": "owner acceptance"}
    for name in names:
        verdict = "GREEN" if results[name] == 0 else "NOT GREEN (see above)"
        print(f"{labels[name]:28s}: {verdict}")
    if "owner" in names:
        print("\nowner acceptance items are PENDING by design — see the skipped list above.")
    return max(results.values()) if results else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
