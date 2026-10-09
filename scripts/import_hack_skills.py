"""Import the hack-skills donor into GENIE's real Skill Hub (P5 old-repo gap).

This does NOT copy files into the product tree and does NOT invent capability.
Every skill goes through the actual pipeline:

    donor SKILL.md  ->  capability derivation
                    ->  SkillRegistry.register()  (runs the REAL SkillScanner)
                    ->  verdict OK / CONFIRM / BLOCK / UNKNOWN
                    ->  Skill Hub discovery by declared capability
                    ->  PTE gating at execution time

These are offensive-security skills, so many are expected to be scanned as
CONFIRM or BLOCK. That is the governance working correctly, not a failure:
a BLOCK verdict cannot be enabled without an explicit force, and a skill that
is not enabled is never discoverable by capability.

    python scripts/import_hack_skills.py [--limit N] [--donor-root PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from security.skill_scanner import scan_skill                      # noqa: E402
from skills.hub import SkillRegistry, SkillStorage                  # noqa: E402

# Capability taxonomy for this donor. Derived from the skill's own category and
# name — never wildcarded.
CATEGORY_CAPABILITIES = {
    "recon":            ["security.recon"],
    "enumeration":      ["security.recon"],
    "scanning":         ["security.recon"],
    "exploitation":     ["security.exploit"],
    "post-exploitation": ["security.post_exploit"],
    "privilege-escalation": ["security.privesc"],
    "persistence":      ["security.persistence"],
    "evasion":          ["security.evasion"],
    "web":              ["security.web"],
    "api":              ["security.web", "security.api"],
    "cloud":            ["security.cloud"],
    "active-directory": ["security.active_directory"],
    "active directory": ["security.active_directory"],
    "mobile":           ["security.mobile"],
    "android":          ["security.mobile"],
    "ai":               ["security.ai_ml"],
    "ml":               ["security.ai_ml"],
    "cryptography":     ["security.crypto"],
    "forensics":        ["security.forensics"],
    "reporting":        ["security.reporting"],
    "hardening":        ["security.hardening"],
}


def _capabilities_for(name: str, skill_md: str) -> list[str]:
    """Derive declared capabilities from the skill name and its own SKILL.md.

    Falls back to a generic read-only capability when nothing is recognisable,
    so a skill is never granted wildcard authority.

    Matching is WORD-BOUNDARY based. Naive substring matching is unsafe here
    because capability assignment drives PTE gating: e.g. "ai" as a substring
    matches "bypass"/"maintainer" and would have handed every skill the
    security.ai_ml capability.
    """
    # Use the donor's OWN directory name as the category source. Deriving from
    # the SKILL.md body was tried and rejected: security prose mentions "AI"/"ML"
    # incidentally, which granted nearly every skill security.ai_ml.
    haystack = name.lower().replace("-", " ")
    caps: list[str] = []
    for token, mapped in CATEGORY_CAPABILITIES.items():
        pattern = r"\b" + re.escape(token.replace("-", " ")) + r"\b"
        if re.search(pattern, haystack):
            for c in mapped:
                if c not in caps:
                    caps.append(c)
    if not caps:
        caps = ["security.general"]
    caps.append("docs.read")            # every skill is documentation-first
    return caps


def _read_head(skill_dir: Path, limit: int = 4000) -> str:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return ""
    try:
        return md.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--donor-root",
                    default="E:/G3/repos/hack-skills-main/skills")
    ap.add_argument("--limit", type=int, default=0,
                    help="import only the first N skills (0 = all)")
    ap.add_argument("--report", default="docs/hack_skills_import.json")
    args = ap.parse_args()

    donor = Path(args.donor_root)
    if not donor.is_dir():
        print(f"donor root not found: {donor}")
        return 2

    skill_dirs = sorted(p for p in donor.iterdir() if p.is_dir())
    if args.limit:
        skill_dirs = skill_dirs[: args.limit]

    registry = SkillRegistry(SkillStorage(), scanner=scan_skill)
    print(f"Importing {len(skill_dirs)} skills from {donor}")
    print("-" * 66)

    tally: dict[str, int] = {}
    records = []
    for d in skill_dirs:
        head = _read_head(d)
        caps = _capabilities_for(d.name, head)
        try:
            rec = registry.register(str(d), name=d.name, donor="hack-skills",
                                    capabilities=caps, scan=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {d.name}: {exc}")
            tally["error"] = tally.get("error", 0) + 1
            continue
        tally[rec.verdict] = tally.get(rec.verdict, 0) + 1
        records.append(rec.to_dict())

    print()
    for verdict in ("OK", "CONFIRM", "BLOCK", "UNKNOWN", "error"):
        if tally.get(verdict):
            print(f"  {verdict:8s} {tally[verdict]}")
    print("-" * 66)
    print(f"total registered: {len(records)}")

    enabled = [r for r in records if r.get("enabled")]
    print(f"enabled after scan: {len(enabled)}  "
          f"(a BLOCK verdict is never auto-enabled)")

    # ---- Skill Hub discovery by declared capability -------------------
    print("\nSkill Hub discovery by declared capability:")
    for cap in ("security.recon", "security.web", "security.active_directory",
                "security.mobile", "security.ai_ml"):
        found = registry.find_by_capability(cap)
        print(f"  {cap:28s} {len(found)} skill(s)")

    # ---- PTE gating proof ---------------------------------------------
    blocked = [r for r in records if r.get("verdict") == "BLOCK"]
    print("\nPTE gating:")
    if blocked:
        sample = blocked[0]
        sid = sample["skill_id"]
        ok_unforced = registry.set_enabled(sid, True)            # must refuse
        print(f"  enable a BLOCK skill without force -> {ok_unforced} (must be False)")
        forced = registry.set_enabled(sid, True, force=True)
        print(f"  enable with explicit force         -> {forced}")
        registry.set_enabled(sid, False, force=True)             # restore
    else:
        print("  no BLOCK-verdict skills in this batch; gating not exercised")

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "donor": "hack-skills",
        "donor_root": str(donor),
        "license": "MIT",
        "imported": len(records),
        "verdicts": tally,
        "enabled": len(enabled),
        "skills": records,
    }, indent=2), encoding="utf-8")
    print(f"\nreport written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
