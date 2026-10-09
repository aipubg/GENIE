"""Proactivity scoring (proactive/scoring.py) — master spec §7.6.

The decision function is explicitly multiplicative:

    urgency × relevance × confidence × current_task × interruption_cost × user_preference

Multiplication, not addition, is the important part. If *any* factor is near zero the whole score
collapses — and that is exactly the desired behaviour: a genuinely urgent message that is
irrelevant to the owner's goal, or that would land in the middle of deep work, should not be
shouted at them. An additive score would let three mediocre signals outvote one fatal one.

Two factors are inverted on purpose: `current_task` and `interruption_cost` describe *why not to
speak*, so they enter as `1 - value`.
"""
from __future__ import annotations

from typing import Any, Dict

from proactive.contracts import Candidate, Decision, Outcome

#: Score thresholds. Above each threshold the outcome escalates one step.
OUTCOME_THRESHOLDS = (
    (0.80, Outcome.INTERRUPT.value),
    (0.62, Outcome.SPEAK_NOW.value),
    (0.44, Outcome.MENTION_LATER.value),
    (0.26, Outcome.SHOW_SILENTLY.value),
    (0.12, Outcome.SAVE.value),
    (0.00, Outcome.IGNORE.value),
)

#: A safety-relevant candidate may not be dismissed as "ignore" — the floor is a visible warning.
URGENT_FLOOR = Outcome.SHOW_SILENTLY.value


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _preference_factor(user_preference: Any) -> float:
    """Turn a 0..1 preference into a multiplier where **0.5 is neutral (×1.0)**.

    Treating 0.5 as a literal multiplier would halve every score by default, so a neutral owner
    would silently receive half the warnings they should. The owner can *suppress* a class
    (below 0.5 → factor below 1) but cannot inflate one above its merits.
    """
    pref = _clamp(user_preference)
    if pref >= 0.5:
        return 1.0
    return pref * 2.0


def score_candidate(candidate: Candidate) -> Decision:
    """Compute the multiplicative score and map it to an outcome."""
    urgency = _clamp(candidate.urgency)
    relevance = _clamp(candidate.relevance)
    confidence = _clamp(candidate.confidence)
    # inverted: a busy owner and a costly interruption both suppress
    attention = 1.0 - _clamp(candidate.current_task_load)
    interruption = 1.0 - _clamp(candidate.interruption_cost)
    preference = _preference_factor(candidate.user_preference)

    factors = {"urgency": urgency, "relevance": relevance, "confidence": confidence,
               "current_task": attention, "interruption_cost": interruption,
               "user_preference": preference}

    score = urgency * relevance * confidence * attention * interruption * preference
    outcome = Outcome.IGNORE.value
    for threshold, name in OUTCOME_THRESHOLDS:
        if score >= threshold:
            outcome = name
            break

    if candidate.urgent_override:
        # an urgent safety signal must at least be visible; it may still be a silent toast
        if score < OUTCOME_THRESHOLDS[-2][0]:
            outcome = URGENT_FLOOR

    reason = (f"score {score:.3f} = " +
              " × ".join(f"{name}={value:.2f}" for name, value in factors.items()))
    return Decision(candidate_id=candidate.candidate_id, outcome=outcome, score=score,
                    factors=factors, reason=reason)


def describe_decision(decision: Decision) -> str:
    return f"{decision.outcome} ({decision.reason})"


def preference_for(candidate: Candidate, preferences: Dict[str, float]) -> Candidate:
    """Apply a stored per-class preference to a candidate (0.5 is neutral)."""
    if candidate.event_class in preferences:
        candidate.user_preference = _clamp(preferences[candidate.event_class])
    return candidate
