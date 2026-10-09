"""E60/B08 observational memory tests."""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("GENIE_DATA_DIR", tempfile.mkdtemp())

from memory.observation_memory import ObservationMemory  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


class FakeStore:
    def __init__(self):
        self.rows = []

    def append(self, kind, evidence):
        self.rows.append({"id": len(self.rows) + 1, "ts": evidence.get("ts", int(time.time())),
                          "kind": kind, "evidence": evidence})

    def recent(self, limit=40, query=""):
        return list(reversed(self.rows))[:limit]


def main():
    now = int(time.time())

    # ---- 1. observation is stored ------------------------------------------
    store = FakeStore()
    mem = ObservationMemory(store, promote_threshold=3)
    mem.record_correction("preferred_browser", "brave", "owner said use Brave")
    mem.record_outcome("send_whatsapp_message", True, "submitted in test conversation")
    for _ in range(4):
        store.append("desktop", {"process": "brave", "title": "Some Private Chat",
                                 "monitor": 0, "ts": now})
    check("observation_stored", len(store.rows) >= 6)

    # ---- 2. useful summary is retrievable -----------------------------------
    summary = mem.summarize()
    check("summary_has_apps", any(a["process"] == "brave" for a in summary["apps"]))
    check("summary_has_preference", any(p["value"] == "brave" for p in summary["preferences"]))
    check("summary_has_correction", any(c["subject"] == "preferred_browser"
                                        for c in summary["corrections"]))
    check("summary_has_outcome", any(o["workflow"] == "send_whatsapp_message"
                                     for o in summary["outcomes"]))
    check("summary_counts_present", "preferences" in summary["counts"])

    rows = mem.retrieve("browser", limit=6)
    check("retrieve_returns_rows", len(rows) > 0)
    check("retrieve_is_bounded", len(rows) <= 6)

    # ---- 3. sensitive raw screen content is not injected --------------------
    check("no_window_titles_in_summary", "Some Private Chat" not in str(summary))
    check("no_raw_screenshot_field", "png" not in str(summary).lower()
          and "screenshot" not in str(summary).lower())
    check("status_declares_no_cloud_upload", mem.status()["cloud_upload"] is False)
    check("status_declares_no_titles", mem.status()["stores_window_titles"] is False)

    # sensitive applications are excluded entirely
    store2 = FakeStore()
    mem2 = ObservationMemory(store2, promote_threshold=2)
    for _ in range(5):
        store2.append("desktop", {"process": "1password", "title": "Vault", "ts": now})
    check("sensitive_app_excluded", mem2.summarize()["apps"] == [])

    # ---- 4. irrelevant observations are excluded ---------------------------
    store3 = FakeStore()
    mem3 = ObservationMemory(store3, promote_threshold=3)
    for _ in range(5):
        store3.append("desktop", {"process": "notepad", "title": "x", "ts": now})
    check("single_sighting_not_promoted",
          ObservationMemory(FakeStore(), promote_threshold=3).summarize()["preferences"] == [])
    check("unrelated_retrieval_still_bounded",
          len(mem3.retrieve("photoshop", limit=3)) <= 3)

    # ---- 5. explicit owner correction overrides an inferred preference ------
    store4 = FakeStore()
    mem4 = ObservationMemory(store4, promote_threshold=3)
    for _ in range(6):
        store4.append("desktop", {"process": "chrome", "title": "t", "ts": now})
    inferred = mem4.summarize()["preferences"]
    check("inference_present_before_correction",
          any(p["value"] == "chrome" for p in inferred))
    mem4.record_correction("chrome", "use brave instead", "owner corrected")
    after = mem4.summarize()
    check("correction_suppresses_inferred_preference",
          not any(p["value"] == "chrome" for p in after["preferences"]))
    check("correction_is_explicit", after["corrections"][0]["explicit"] is True)
    check("correction_confidence_one", after["corrections"][0]["confidence"] == 1.0)

    # corrections rank above inferred preferences in retrieval
    rows4 = mem4.retrieve("chrome", limit=4)
    check("correction_ranks_first", rows4 and rows4[0]["kind"] == "owner_correction")

    # ---- 6. promotion requires repeated evidence ---------------------------
    store5 = FakeStore()
    mem5 = ObservationMemory(store5, promote_threshold=3)
    for _ in range(2):
        store5.append("desktop", {"process": "spotify", "title": "t", "ts": now})
    check("two_sightings_not_promoted", mem5.summarize()["preferences"] == [])
    store5.append("desktop", {"process": "spotify", "title": "t", "ts": now})
    check("three_sightings_promoted",
          any(p["value"] == "spotify" for p in mem5.summarize()["preferences"]))

    # ---- 7. provenance / timestamps / confidence preserved -----------------
    pref = [p for p in mem5.summarize()["preferences"] if p["value"] == "spotify"][0]
    check("preference_has_provenance",
          pref.get("source") == "repeated_observation"
          and pref.get("confidence") is not None
          and pref.get("last_ts"))

    # ---- 8. ContextCompiler contributes a bounded observational section ----
    from context.compiler import ContextCompiler
    import inspect
    src = inspect.getsource(ContextCompiler.compile)
    check("compiler_uses_observational_context", "observational_context" in src)
    check("compiler_labels_section", "observational_memory" in src)
    check("compiler_says_summaries_only", "summaries only" in src)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print("=== Observational Memory Tests (E60/B08) ===")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    print(f"\nResults: {total - len(failed)}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
