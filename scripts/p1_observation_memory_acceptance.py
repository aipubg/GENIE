"""Track H: REAL observation-memory acceptance through the canonical stack.

Uses the actual Database (with migrations), the real encrypted ObservationStore
(DPAPI), the real MemoryService and the real ContextCompiler. No stubs.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

from core.contracts import CallContext  # noqa: E402
from core.db import get_db  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from context.compiler import ContextCompiler  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def main():
    data_dir = Path(os.environ["GENIE_DATA_DIR"])
    db = get_db(data_dir / "genie.db")
    memory = MemoryService(db)
    store = memory.observations
    obs_mem = memory.observation_memory
    ctx = CallContext(person_id="owner", device_id="pc_main", trace_id="obs-accept")

    # ---- 1. observation is actually stored (encrypted) ---------------------
    now = int(time.time())
    for _ in range(5):
        store.append("desktop", {"process": "notepad", "title": "CONFIDENTIAL DRAFT 42",
                                 "monitor": 0, "source": "desktop_observation",
                                 "inferred_preference": False, "ts": now})
    store.append("desktop", {"process": "1password", "title": "Vault", "ts": now})
    stored = store.status()
    check("observation_stored", stored["events"] >= 6, f"events={stored['events']}")
    check("observation_is_encrypted", stored["encrypted"] is True)

    # the raw archive is NOT plaintext on disk
    raw_bytes = (data_dir / "genie.db").read_bytes()
    check("raw_archive_not_plaintext", b"CONFIDENTIAL DRAFT 42" not in raw_bytes)

    # ---- 2. repeated evidence is summarized --------------------------------
    summary = obs_mem.summarize()
    apps = [a["process"] for a in summary["apps"]]
    check("useful_app_summarized", "notepad" in apps, f"apps={apps}")
    check("repeated_evidence_promoted",
          any(p["value"] == "notepad" for p in summary["preferences"]))
    pref = next(p for p in summary["preferences"] if p["value"] == "notepad")
    check("preference_has_evidence_count", pref["evidence_count"] >= 5)
    check("preference_has_confidence", pref["confidence"] is not None)
    check("preference_has_provenance", pref["source"] == "repeated_observation")

    # ---- 3. sensitive + raw content excluded -------------------------------
    check("sensitive_app_excluded", "1password" not in str(summary))
    check("window_title_not_dumped", "CONFIDENTIAL DRAFT 42" not in str(summary))
    check("no_screenshot_payload", "png" not in str(summary).lower())

    # ---- 4. ContextCompiler retrieves the useful summary -------------------
    compiler = ContextCompiler(memory=memory)
    packet = compiler.compile(ctx, "open notepad for me", environment={"displays": 1})
    text = packet.to_dict() if hasattr(packet, "to_dict") else str(packet)
    blob = str(text)
    check("compiler_has_observational_section", "observational_memory" in blob, blob[:200])
    check("compiler_contains_learned_preference", "notepad" in blob)
    check("compiler_labels_summaries_only", "summaries only" in blob)
    check("compiler_excludes_titles", "CONFIDENTIAL DRAFT 42" not in blob)
    check("compiler_excludes_sensitive_app", "1password" not in blob)

    # ---- 5. irrelevant observations are excluded ---------------------------
    store.append("desktop", {"process": "calc", "title": "Calculator", "ts": now})
    summary2 = obs_mem.summarize()
    check("single_sighting_not_promoted",
          not any(p["value"] == "calc" for p in summary2["preferences"]))
    check("single_sighting_still_listed", any(a["process"] == "calc" for a in summary2["apps"]))

    # ---- 6. explicit owner correction overrides inference ------------------
    memory.observation_memory.record_correction("notepad", "use vscode instead",
                                                "owner preference")
    summary3 = obs_mem.summarize()
    check("correction_recorded", any(c["subject"] == "notepad" for c in summary3["corrections"]))
    check("correction_suppresses_inference",
          not any(p["value"] == "notepad" for p in summary3["preferences"]))
    rows = obs_mem.retrieve("notepad", limit=4)
    check("correction_ranks_first", rows and rows[0]["kind"] == "owner_correction")

    packet2 = compiler.compile(ctx, "what should I use for notes", environment={"displays": 1})
    blob2 = str(packet2.to_dict() if hasattr(packet2, "to_dict") else packet2)
    check("correction_reaches_prompt", "use vscode instead" in blob2)
    check("corrected_preference_not_asserted_as_inference",
          "Frequently used application: notepad" not in blob2)

    # ---- 7. provider change does not erase local learned memory ------------
    from models.registry import ModelRegistry
    ModelRegistry()          # a provider reload must not touch observations
    summary4 = obs_mem.summarize()
    check("provider_change_preserves_memory",
          any(c["subject"] == "notepad" for c in summary4["corrections"]))

    # ---- 8. verified outcome recorded --------------------------------------
    memory.observation_memory.record_outcome("send_whatsapp_message", True,
                                             "submitted in test conversation")
    summary5 = obs_mem.summarize()
    check("verified_outcome_stored",
          any(o["workflow"] == "send_whatsapp_message" for o in summary5["outcomes"]))

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Track H: Observation Memory Real Acceptance ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    print("\n--- compiled observational section (evidence) ---")
    start = blob.find("observational_memory")
    print(blob[max(0, start - 20):start + 400] if start >= 0 else "(section not found)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
