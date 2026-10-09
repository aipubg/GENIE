"""Cross-session user model: observe, refine, supersede, profile.

Confidence model
----------------
* a new observation starts at a modest base confidence;
* repeating the SAME value adds evidence and raises confidence (bounded);
* a DIFFERENT value for the same trait supersedes the old one and restarts
  confidence — the old value is kept as history, never silently overwritten.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("usermodel.service")

BASE_CONFIDENCE = 0.55
MAX_CONFIDENCE = 0.95
SUPPORT_STEP = 0.12                 # confidence gained per additional observation

# Deterministic extraction patterns. Only explicit self-statements qualify.
# Each entry: (trait, compiled regex with one capture group, normaliser)
_PATTERNS: List[tuple] = [
    ("name", re.compile(
        r"\b(?:call me|my name is|my name's|i am called|i'm called)\s+([A-Z][\w'-]{1,30})\b", re.I),
     lambda m: m.group(1).strip().title()),
    ("role", re.compile(
        r"\b(?:i am|i'm|i work as|i work (?:at|in)|my job is)\s+(?:an?\s+)?([\w][\w \-]{2,40})\b", re.I),
     lambda m: m.group(1).strip().lower()),
    ("preference", re.compile(
        r"\b(?:i prefer|i like|i love|i always use|i usually use)\s+([\w][\w \-]{1,40})\b", re.I),
     lambda m: m.group(1).strip().lower()),
    ("aversion", re.compile(
        r"\b(?:i (?:don't|do not) like|i dislike|i hate|i avoid)\s+([\w][\w \-]{1,40})\b", re.I),
     lambda m: m.group(1).strip().lower()),
    ("language", re.compile(
        r"\b(?:i speak|i (?:am|'m) (?:more )?comfortable in|talk to me in)\s+([\w][\w \-]{1,25})\b", re.I),
     lambda m: m.group(1).strip().lower()),
    ("goal", re.compile(
        r"\b(?:i want to|i'm trying to|i am trying to|my goal is to)\s+([\w][\w \-]{2,60})\b", re.I),
     lambda m: m.group(1).strip().lower()),
]

# Traits that are single-valued (a new value supersedes) vs multi-valued.
SINGLE_VALUED = {"name", "role", "language"}


def _now() -> int:
    return int(time.time())


class UserModelService:
    """Persistent, evidence-weighted model of a person across sessions."""

    def __init__(self, db) -> None:
        self.db = db

    # --------------------------------------------------------------- observe
    def observe(self, person_id: str, trait: str, value: str, *,
                evidence: str = "", source: str = "conversation",
                session_id: str = "") -> Dict[str, Any]:
        """Record or reinforce one fact about a person."""
        if not person_id or not trait or not value:
            raise ValueError("person_id, trait and value are required")
        value = str(value).strip()
        now = _now()

        existing = self.db.query_one(
            "SELECT * FROM user_model_facts WHERE person_id=? AND trait=? "
            "AND value=? AND superseded_at IS NULL", (person_id, trait, value))

        if existing:
            confidence = min(MAX_CONFIDENCE,
                             float(existing["confidence"]) + SUPPORT_STEP)
            self.db.execute(
                "UPDATE user_model_facts SET confidence=?, support=support+1, "
                "updated_at=?, last_evidence=?, last_session=? WHERE fact_id=?",
                (confidence, now, evidence[:300], session_id, existing["fact_id"]))
            return {"fact_id": existing["fact_id"], "trait": trait, "value": value,
                    "confidence": round(confidence, 3), "support": existing["support"] + 1,
                    "updated": True}

        if trait in SINGLE_VALUED:
            # a new value supersedes any previous value for that trait
            self.db.execute(
                "UPDATE user_model_facts SET superseded_at=? WHERE person_id=? "
                "AND trait=? AND superseded_at IS NULL", (now, person_id, trait))

        fid = f"umf_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO user_model_facts(fact_id, person_id, trait, value, "
            "confidence, support, source, evidence, last_evidence, session_id, "
            "last_session, created_at, updated_at, superseded_at) "
            "VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,NULL)",
            (fid, person_id, trait, value, BASE_CONFIDENCE, source,
             evidence[:300], evidence[:300], session_id, session_id, now, now))
        return {"fact_id": fid, "trait": trait, "value": value,
                "confidence": BASE_CONFIDENCE, "support": 1, "updated": False}

    # --------------------------------------------------------------- extract
    def extract(self, text: str) -> List[Dict[str, str]]:
        """Deterministically pull explicit self-statements out of `text`.

        Returns [] when nothing matches. Nothing is guessed or generated.
        """
        out: List[Dict[str, str]] = []
        if not text:
            return out
        for trait, pattern, norm in _PATTERNS:
            for m in pattern.finditer(text):
                try:
                    value = norm(m)
                except Exception:  # noqa: BLE001
                    continue
                if not value or len(value) > 60:
                    continue
                out.append({"trait": trait, "value": value,
                            "evidence": m.group(0).strip()[:200]})
        return out

    def refine_from_text(self, person_id: str, text: str, *,
                         session_id: str = "") -> List[Dict[str, Any]]:
        """Observe every explicit self-statement in one utterance."""
        return [self.observe(person_id, o["trait"], o["value"],
                             evidence=o["evidence"], session_id=session_id)
                for o in self.extract(text)]

    def refine_from_session(self, person_id: str, history: List[Dict[str, Any]],
                            *, session_id: str = "") -> List[Dict[str, Any]]:
        """Refine the model from a whole session's turns (the Hermes idea).

        Only `user` turns are mined: GENIE's own replies are not evidence about
        the person.
        """
        results: List[Dict[str, Any]] = []
        for turn in history or []:
            if (turn.get("role") or "") != "user":
                continue
            results.extend(self.refine_from_text(
                person_id, turn.get("text", ""), session_id=session_id))
        return results

    # ---------------------------------------------------------------- profile
    def profile(self, person_id: str, *, min_confidence: float = 0.0
                ) -> Dict[str, Any]:
        """Current model of the person: best-supported value per trait."""
        rows = self.db.query(
            "SELECT * FROM user_model_facts WHERE person_id=? AND "
            "superseded_at IS NULL AND confidence>=? ORDER BY confidence DESC",
            (person_id, float(min_confidence)))
        traits: Dict[str, Any] = {}
        for r in rows:
            cur = traits.get(r["trait"])
            if cur is None or float(r["confidence"]) > float(cur["confidence"]):
                traits[r["trait"]] = {
                    "value": r["value"],
                    "confidence": round(float(r["confidence"]), 3),
                    "support": r["support"],
                    "updated_at": r["updated_at"],
                }
        return {"person_id": person_id, "traits": traits,
                "fact_count": len(rows)}

    def facts(self, person_id: str, *, include_superseded: bool = False
              ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM user_model_facts WHERE person_id=?"
        if not include_superseded:
            sql += " AND superseded_at IS NULL"
        sql += " ORDER BY updated_at DESC"
        return [dict(r) for r in self.db.query(sql, (person_id,))]

    def forget(self, person_id: str, *, trait: Optional[str] = None) -> int:
        """Remove the model (or one trait) — an owner/privacy action."""
        if trait:
            self.db.execute(
                "DELETE FROM user_model_facts WHERE person_id=? AND trait=?",
                (person_id, trait))
            return 1
        cur = self.db.execute("DELETE FROM user_model_facts WHERE person_id=?",
                              (person_id,))
        return int(cur.rowcount or 0)
