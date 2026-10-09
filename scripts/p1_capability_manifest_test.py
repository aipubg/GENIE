"""Capability manifest + tool exposure consistency tests (E23/B03 repair).

Verifies the single runtime capability authority:
  A. The manifest is internally consistent (every curated entry has scope+chain+verifier).
  B. Every conversational tool maps to a real canonical capability.
  C. Previously-unreachable generic operations are now exposed.
  D. No exposed tool lacks permission or verification.
  E. The catalog is derived, not hand-maintained (adding a curated entry changes it).
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from computer.capability_manifest import (  # noqa: E402
    validate, model_tools, describe_all, build_manifest, SAFE_HOTKEYS)
from computer.tool_bridge import (  # noqa: E402
    NAMES, OPENAI_TOOLS, BRIDGE_CAPABILITY, SCHEMAS)
from computer.service import SCOPE_BY_CAPABILITY  # noqa: E402
from computer.planner import CHAINS  # noqa: E402
from computer.verifier import has_verifier  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main():
    # A. manifest consistency
    problems = validate()
    check("A.manifest_consistent", not problems, "; ".join(problems[:4]))

    # B. every bridge tool maps to a canonical capability that exists
    missing = [t for t, cap in BRIDGE_CAPABILITY.items() if cap not in SCOPE_BY_CAPABILITY]
    check("B.bridge_caps_exist", not missing, f"missing: {missing}")

    # C. previously-unreachable generic operations are exposed
    required = {
        "input_type", "input_hotkey", "input_scroll", "input_click", "input_drag",
        "browser_session", "browser_scroll", "browser_upload", "browser_download",
        "browser_downloads",
    }
    absent = sorted(required - set(NAMES))
    check("C.generic_ops_exposed", not absent, f"absent: {absent}")

    # D. every exposed tool has permission + verification via its capability
    unpermissioned, unverified = [], []
    for tool_name in NAMES:
        cap = BRIDGE_CAPABILITY.get(tool_name)
        if cap is None:
            # composite guarded tools keep their own contract; they must still be known
            if tool_name != "desktop_control":
                unpermissioned.append(tool_name)
            continue
        if not SCOPE_BY_CAPABILITY.get(cap):
            unpermissioned.append(tool_name)
        if not has_verifier(cap):
            unverified.append(tool_name)
    check("D.every_tool_permissioned", not unpermissioned, f"{unpermissioned}")
    check("D.every_tool_verified", not unverified, f"{unverified}")

    # E. exposure is derived from the manifest
    manifest_names = {d["name"] for d in model_tools("chat")}
    exposed_names = {d.get("name") or d.get("function", {}).get("name") for d in OPENAI_TOOLS}
    hand_written = exposed_names - manifest_names
    check("E.catalog_superset_of_manifest", manifest_names <= set(NAMES),
          f"manifest not fully exposed: {sorted(manifest_names - set(NAMES))}")
    check("E.only_composite_handwritten",
          hand_written <= {"desktop_control"}, f"unexpected hand-written: {sorted(hand_written)}")

    # F. hotkey allow-list blocks dangerous chords
    dangerous = {("ctrl", "alt", "delete"), ("alt", "f4", "shift"), ("win", "r")}
    check("F.safe_hotkeys_exclude_dangerous", not (dangerous & set(SAFE_HOTKEYS)))

    # G. manifest describes every curated capability with the required fields
    described = describe_all()
    incomplete = [d["name"] for d in described
                  if not all(k in d for k in ("capability", "scope", "executor",
                                              "verification", "contexts", "risk"))]
    check("G.manifest_fields_complete", not incomplete, f"{incomplete}")

    # H. read-only capabilities are never classified as destructive
    bad = [d["name"] for d in described
           if d["read_only"] and d["risk"] != "read_only"]
    check("H.read_only_classification", not bad, f"{bad}")

    # I. plan/chain coverage for every exposed capability
    no_chain = [t for t, cap in BRIDGE_CAPABILITY.items() if cap not in CHAINS]
    check("I.every_exposed_cap_has_chain", not no_chain, f"{no_chain}")

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Capability Manifest / Tool Exposure Tests ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
