"""Bounded observational memory (memory/observation_memory).

Bridges verified local ObservationStore events into the canonical memory
authority so typed Chat and Gemini Live retrieve the SAME learned context.

Rules this module enforces:
  * No raw screenshots and no continuous desktop events ever enter a prompt.
  * Only bounded, structured summaries leave this layer.
  * An inferred preference is promoted ONLY after repeated evidence
    (``promote_threshold`` consistent observations) or an explicit owner
    correction. One sighting is not a preference.
  * Every summary carries provenance, timestamps, confidence and source.
  * Window titles are deliberately NOT stored or returned: titles routinely
    contain message text, URLs and document names. Only the owning process,
    counts and timestamps are kept.
  * An explicit owner correction always outranks an inferred preference.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Process names whose mere presence is sensitive. Reused from the desktop
# awareness defaults so there is one exclusion vocabulary, not two.
_SENSITIVE_PROCESS_MARKERS = (
    "1password", "bitwarden", "keepass", "lastpass", "authy",
    "microsoftauthexe", "credential", "wallet",
)


def _is_sensitive_process(process: str) -> bool:
    low = (process or "").lower()
    return any(marker in low for marker in _SENSITIVE_PROCESS_MARKERS)


class ObservationMemory:
    """Structured, bounded memory derived from local observations."""

    def __init__(self, store, promote_threshold: int = 3, retention_s: int = 30 * 86400):
        self.store = store
        self.promote_threshold = max(2, int(promote_threshold))
        self.retention_s = int(retention_s)

    # ------------------------------------------------------------------ write
    def record_correction(self, subject: str, value: str, note: str = "") -> Dict[str, Any]:
        """Record an EXPLICIT owner correction. Highest authority, never inferred."""
        evidence = {"subject": str(subject)[:120], "value": str(value)[:400],
                    "note": str(note)[:400], "source": "owner_correction",
                    "explicit": True, "confidence": 1.0, "ts": int(time.time())}
        self.store.append("owner_correction", evidence)
        return {"ok": True, **evidence}

    def record_outcome(self, workflow: str, verified: bool, detail: str = "") -> Dict[str, Any]:
        """Record a VERIFIED workflow outcome (a real receipt, not a claim)."""
        evidence = {"workflow": str(workflow)[:120], "verified": bool(verified),
                    "detail": str(detail)[:400], "source": "verified_outcome",
                    "confidence": 1.0 if verified else 0.0, "ts": int(time.time())}
        self.store.append("workflow_outcome", evidence)
        return {"ok": True, **evidence}

    # ------------------------------------------------------------------ read
    def summarize(self, limit: int = 400) -> Dict[str, Any]:
        """Aggregate recent observations into bounded structured memory."""
        try:
            events = self.store.recent(limit=limit)
        except Exception:
            return {"apps": [], "preferences": [], "corrections": [], "outcomes": [],
                    "counts": {}, "source": "observation_store", "error": "unavailable"}

        apps: Dict[str, Dict[str, Any]] = {}
        corrections: List[Dict[str, Any]] = []
        outcomes: List[Dict[str, Any]] = []
        now = int(time.time())

        for event in events:
            kind = str(event.get("kind", ""))
            evidence = event.get("evidence") or {}
            ts = int(event.get("ts") or 0)
            if self.retention_s and ts and now - ts > self.retention_s:
                continue
            if kind == "desktop":
                process = str(evidence.get("process", "") or "").strip().lower()
                # Never carry titles or any raw screen content forward.
                if not process or _is_sensitive_process(process):
                    continue
                rec = apps.setdefault(process, {"process": process, "count": 0,
                                                "last_ts": 0, "monitors": set()})
                rec["count"] += 1
                rec["last_ts"] = max(rec["last_ts"], ts)
                if isinstance(evidence.get("monitor"), int):
                    rec["monitors"].add(evidence["monitor"])
            elif kind == "owner_correction":
                corrections.append({"subject": evidence.get("subject", ""),
                                    "value": evidence.get("value", ""),
                                    "note": evidence.get("note", ""),
                                    "ts": ts, "source": "owner_correction",
                                    "explicit": True, "confidence": 1.0})
            elif kind == "workflow_outcome":
                outcomes.append({"workflow": evidence.get("workflow", ""),
                                 "verified": bool(evidence.get("verified")),
                                 "detail": evidence.get("detail", ""),
                                 "ts": ts, "source": "verified_outcome"})

        corrections = sorted(corrections, key=lambda c: -c["ts"])[:20]
        outcomes = sorted(outcomes, key=lambda o: -o["ts"])[:20]

        # Explicit corrections outrank inferred preferences for the same subject.
        corrected_subjects = {c["subject"].lower() for c in corrections}

        preferences: List[Dict[str, Any]] = []
        for process, rec in apps.items():
            if rec["count"] < self.promote_threshold:
                continue
            if process in corrected_subjects:
                continue
            preferences.append({
                "kind": "app_preference", "value": process,
                "confidence": round(min(0.9, 0.3 + 0.1 * rec["count"]), 3),
                "evidence_count": rec["count"], "last_ts": rec["last_ts"],
                "monitors": sorted(rec["monitors"]),
                "source": "repeated_observation", "inferred_preference": True,
            })
        preferences.sort(key=lambda p: (-p["evidence_count"], p["value"]))

        app_rows = [{"process": p, "count": r["count"], "last_ts": r["last_ts"],
                     "monitors": sorted(r["monitors"])} for p, r in apps.items()]
        app_rows.sort(key=lambda r: (-r["count"], r["process"]))

        return {
            "apps": app_rows[:25],
            "preferences": preferences[:12],
            "corrections": corrections,
            "outcomes": outcomes,
            "counts": {"observed_apps": len(apps), "preferences": len(preferences),
                       "corrections": len(corrections), "outcomes": len(outcomes)},
            "source": "observation_store",
            "generated_ts": now,
        }

    def retrieve(self, query: str, limit: int = 4) -> List[Dict[str, Any]]:
        """Return bounded, query-relevant observational memory rows.

        Explicit corrections and verified outcomes always rank above inferred
        preferences, so an owner correction can never be buried by inference.
        """
        summary = self.summarize()
        terms = [t for t in str(query or "").lower().split() if len(t) > 2]
        rows: List[Dict[str, Any]] = []

        for correction in summary["corrections"]:
            rows.append({"kind": "owner_correction",
                         "text": f"Owner correction: {correction['subject']} = "
                                 f"{correction['value']}" + (f" ({correction['note']})" if correction['note'] else ""),
                         "confidence": 1.0, "source": "owner_correction",
                         "ts": correction["ts"], "explicit": True})
        for outcome in summary["outcomes"]:
            rows.append({"kind": "verified_outcome",
                         "text": f"Verified outcome for {outcome['workflow']}: "
                                 f"{outcome['detail']}",
                         "confidence": outcome.get("confidence", 1.0),
                         "source": "verified_outcome", "ts": outcome["ts"]})
        for pref in summary["preferences"]:
            rows.append({"kind": "app_preference",
                         "text": f"Frequently used application: {pref['value']} "
                                 f"(seen {pref['evidence_count']} times)",
                         "confidence": pref["confidence"],
                         "source": "repeated_observation", "ts": pref["last_ts"]})

        if terms:
            matched = [r for r in rows if any(t in r["text"].lower() for t in terms)]
            rows = matched or rows
        return rows[:max(1, int(limit))]

    def status(self) -> Dict[str, Any]:
        summary = self.summarize()
        return {"source": "observation_store", "counts": summary["counts"],
                "promote_threshold": self.promote_threshold,
                "cloud_upload": False, "raw_screenshots": False,
                "stores_window_titles": False}
