"""Release-documentation consistency + render-truth checker.

Asserts ONE current truth across the release-facing docs and mechanically
rejects the defects that make a rendered document look broken.

Why this exists: a regex-only pass once reported PASS while the file had, in
fact, been corrupted by an owner prompt pasted into the document (example
pseudo-tables and all). So this checker now also rejects:

    - directive / prompt text inside documentation
    - '=====' directive section dividers
    - the exact pseudo-table pair '|   |' followed by '| - |'
    - empty fenced code blocks
    - 'N)' ordered-list syntax
    - tables without a separator row, orphan rows, empty/dash-only rows

Run:  python scripts/check_release_docs.py
Exit: 0 = consistent, 1 = findings.

Documentation/tooling only. Never touches product code.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CURRENT_RC = "rc13"
CURRENT_SHA = "6d5f900098609f34651916369f01cedbffbe6106c28cb3702d0d91c1fd06d274"
CURRENT_SIZE = "82420646"
CURRENT_SOURCE = "65c6b0af201d4a83c0b826c92ad82eb73e02d390"

DOCS = [
    "docs/ACTIVE_WORK.md",
    "docs/RELEASE_ACCEPTANCE.md",
    "docs/PACKAGED_APP_ACCEPTANCE.md",
    "docs/UI_ACCEPTANCE.md",
    "docs/GOLDEN_GATE.md",
    "docs/PHASE14.md",
    "artifacts/release_matrix.md",
]

# Strings that must NEVER appear outside an explicitly historical section.
BANNED_CURRENT = {
    "14/18": "old golden-gate result",
    "4 UNVERIFIED": "old golden-gate result",
    "UNRESOLVED": "old download investigation",
    "No real-usage baseline yet": "superseded baseline wording",
    "installer packaging outstanding": "installer is built",
    "only verified deterministically": "rollback is verified via real CLI",
    "Current SHA-256: `3c7a4fbb": "rc12 hash presented as current",
    "`c40cae84": "rc11 hash",
    "cannot run acceptance at all": "old agent-environment finding",
    "NOT BUILT": "installer is built",
    "not yet exposed via CLI": "backup CLI is wired",
    "environment-dependent failures": "real-machine is 68 PASS / 0 FAIL / 3 SKIP",
    "Notepad UIA typing": "historical failure, not current",
    "Everything else about the installer is verified": "overclaim while acceptance pending",
    "all 3 skips are Blender": "skips are Blender x2 + Chrome x1",
}

# Owner/developer prompt text must never end up inside release documentation.
DIRECTIVE_MARKERS = [
    "NO PRODUCT CODE CHANGES",
    "NO REBUILD",
    "NO RC14",
    "DO NOT MOVE",
    "THEN STOP",
    "CONTINUE AUTOMATICALLY",
    "DO NOT ADD FEATURES",
    "DOCUMENT RENDER TRUTH",
    "LAST DOC FIXES",
    "VERIFY ACTUAL FILE BYTES",
    "Do NOT trust the existing regex checker",
]
DIRECTIVE_RULE = re.compile(r"^={10,}\s*$")

HISTORICAL_HEADING = re.compile(r"^\s*historical", re.I)


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator(line: str) -> bool:
    cs = cells(line)
    return bool(cs) and all(re.fullmatch(r":?-{3,}:?", c) for c in cs)


def find_pseudo_tables(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for i in range(len(lines) - 1):
        if lines[i].strip() == "|   |" and lines[i + 1].strip() == "| - |":
            out.append((i + 1, "pseudo-table '|   |' followed by '| - |'"))
    return out


def find_empty_code_blocks(lines: list[str]) -> list[int]:
    out, open_at = [], None
    for n, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            if open_at is None:
                open_at = n
            else:
                # open_at/n are 1-based line numbers; the body is the 0-based
                # range between them, excluding BOTH fence lines. Using
                # range(open_at, n) would count the closing fence as content
                # and silently never report an empty block.
                if not any(lines[i].strip() for i in range(open_at, n - 1)):
                    out.append(open_at)
                open_at = None
    return out


def find_bad_tables(lines: list[str]) -> list[tuple[int, str]]:
    out, i = [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            blk = lines[i:j]
            if len(blk) >= 2 and not is_separator(blk[1]):
                out.append((i + 1, "table header without separator row"))
            elif len(blk) == 1:
                out.append((i + 1, "lone/orphan table row"))
            for k, row in enumerate(blk):
                cs = cells(row)
                if not is_separator(row) and (all(not c for c in cs) or any(c == "-" for c in cs)):
                    out.append((i + k + 1, "empty or dash-only table row"))
            i = j
        else:
            i += 1
    return out


def find_paren_ordered(lines: list[str]) -> list[tuple[int, str]]:
    return [(n, "ordered list uses 'N)' syntax") for n, l in enumerate(lines, 1)
            if re.match(r"^\s*\d+\)", l)]


def find_mixed_ordered(lines: list[str]) -> list[tuple[int, str]]:
    out, cur = [], None
    for n, line in enumerate(lines, 1):
        m = re.match(r"^(\s*)([0-9]+)([.)])\s+\S", line)
        if m:
            if cur is None:
                cur = m.group(3)
            elif m.group(3) != cur:
                out.append((n, f"ordered list switches '{cur}' -> '{m.group(3)}'"))
                cur = m.group(3)
        elif line.strip() == "":
            continue
        else:
            cur = None
    return out


def find_directive_text(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(lines, 1):
        for marker in DIRECTIVE_MARKERS:
            if marker in line:
                out.append((n, f"directive/prompt text in documentation: '{marker}'"))
                break
        if DIRECTIVE_RULE.match(line.strip()):
            out.append((n, "directive section divider ('====' rule)"))
    return out


def render_sanity(lines: list[str]) -> tuple[int, int, int]:
    """Structural parse: count tables, fenced blocks and list items a renderer
    would emit. Reported so a structural regression is visible."""
    tables = blocks = items = 0
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            if j - i >= 2 and is_separator(lines[i + 1]):
                tables += 1
            i = j
            continue
        if lines[i].strip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            blocks += 1
            i = j + 1
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+\S", lines[i]):
            items += 1
        i += 1
    return tables, blocks, items


def norm_sha(data: bytes) -> str:
    """SHA-256 with CR stripped, so CRLF/LF differences do not look like edits."""
    return hashlib.sha256(data.replace(b"\r", b"")).hexdigest()


def git_head_bytes(rel: str):
    """Exact committed blob bytes for a path, or None if git is unavailable."""
    try:
        out = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT,
                             capture_output=True, check=False)
        if out.returncode == 0:
            return out.stdout
    except Exception:
        pass
    return None


def file_counts(lines: list[str]) -> tuple[int, int, int]:
    return (len(find_pseudo_tables(lines)),
            len(find_empty_code_blocks(lines)),
            len(find_paren_ordered(lines)))


def main() -> int:
    findings: list[str] = []
    seen_truth = {"rc": False, "sha": False, "size": False, "source": False}
    totals = {"tables": 0, "blocks": 0, "items": 0}
    report: list[tuple[str, str, int, int, int, str]] = []

    for rel in DOCS:
        path = ROOT / rel
        if not path.exists():
            findings.append(f"{rel}: MISSING")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        text = "\n".join(lines)

        # --- exact-byte reporting + HEAD round-trip (§5 / §6) --------------
        # Normalise CR so a CRLF/LF difference is not mistaken for an edit,
        # but any real content difference still fails the round-trip.
        work_sha = norm_sha(path.read_bytes())
        head_bytes = git_head_bytes(rel)
        if head_bytes is None:
            roundtrip = "HEAD-UNAVAILABLE"
        else:
            export = ROOT / "artifacts" / f"verify_{Path(rel).stem}_HEAD.md"
            export.parent.mkdir(parents=True, exist_ok=True)
            export.write_bytes(head_bytes)
            roundtrip = "MATCH" if norm_sha(head_bytes) == work_sha else "DIFFER"
        pc, ec, mc = file_counts(lines)
        report.append((rel, work_sha, pc, ec, mc, roundtrip))
        if roundtrip == "DIFFER":
            findings.append(f"{rel}: working tree differs from committed HEAD — "
                            f"the exported doc would not be the reviewed doc")

        hist = False
        for n, line in enumerate(lines, 1):
            h = re.match(r"^(#{1,6})\s+(.*)$", line)
            if h:
                hist = bool(HISTORICAL_HEADING.match(h.group(2)))
                continue
            if hist:
                continue
            for bad, why in BANNED_CURRENT.items():
                if bad not in line:
                    continue
                lo = max(0, n - 9)
                hi = min(len(lines), n + 2)
                if "historical" in " ".join(lines[lo:hi]).lower():
                    continue
                findings.append(f"{rel}:{n}: stale current language '{bad}' ({why})")

        for n, why in find_pseudo_tables(lines):
            findings.append(f"{rel}:{n}: {why}")
        for n in find_empty_code_blocks(lines):
            findings.append(f"{rel}:{n}: empty fenced code block")
        for n, why in find_bad_tables(lines):
            findings.append(f"{rel}:{n}: {why}")
        for n, why in find_paren_ordered(lines):
            findings.append(f"{rel}:{n}: {why}")
        for n, why in find_mixed_ordered(lines):
            findings.append(f"{rel}:{n}: {why}")
        for n, why in find_directive_text(lines):
            findings.append(f"{rel}:{n}: {why}")

        t, b, it = render_sanity(lines)
        totals["tables"] += t
        totals["blocks"] += b
        totals["items"] += it

        if CURRENT_RC in text:
            seen_truth["rc"] = True
        if CURRENT_SHA in text:
            seen_truth["sha"] = True
        if CURRENT_SIZE in text or "82,420,646" in text:
            seen_truth["size"] = True
        if CURRENT_SOURCE in text or "65c6b0a" in text:
            seen_truth["source"] = True

    if not all(seen_truth.values()):
        missing = [k for k, v in seen_truth.items() if not v]
        findings.append(f"current truth not stated anywhere: {', '.join(missing)}")

    print("PER-FILE (exact committed path, sha256 CR-normalised, defect counts, HEAD round-trip)")
    for rel, sha, pc, ec, mc, rt in report:
        print(f"  {rel}")
        print(f"      sha256 {sha}")
        print(f"      pseudo={pc}  empty={ec}  mixed='N)'={mc}   HEAD==WORKTREE: {rt}")
    print()

    if findings:
        print("RELEASE DOC CHECK — FAILED")
        for f in findings:
            print("  " + f)
        return 1

    print("RELEASE DOC CHECK — PASSED")
    print(f"  current rc     : {CURRENT_RC}")
    print(f"  current sha    : {CURRENT_SHA}")
    print(f"  current size   : {CURRENT_SIZE}")
    print(f"  current source : {CURRENT_SOURCE}")
    print(f"  docs checked   : {len(DOCS)}")
    print(f"  render sanity  : {totals['tables']} tables, "
          f"{totals['blocks']} code blocks, {totals['items']} list items "
          f"(structural parse; no 'markdown' module installed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
