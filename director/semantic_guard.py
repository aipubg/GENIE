"""Semantic routing guard (director/semantic_guard.py).

**Why this exists.** NEDLE2 is a 14 MB router. It is fast and usually right, but it is not
authoritative and its confidence is not evidence. It answered

    "mera latest project continue karo"

with `media.play` at ~0.99 confidence. Executing that is not a small error: the owner asked to
continue their work and GENIE would have started playing media on a device. Prompt wording alone
cannot fix a statistical router — so routing is validated *after* the model, against the
semantics of what the owner actually said.

**The rule.** A media/device action requires actual media grounding in the request (or in the
live context). Continuation semantics — project / mission / memory / skill continuation — are
**not** media grounding, and a high model confidence does not override an incompatible intent:

    "phone ka next song"                -> media.next          (media evidence: song, next)
    "Spotify ka song continue karo"     -> media allowed        (media evidence: spotify, song)
    "video resume karo"                 -> media allowed        (media evidence: video)
    "mera latest project continue karo" -> mission/memory/skill or safe escalation
    "continue the previous mission"     -> mission/memory/skill or safe escalation

This is a *deterministic* post-routing validator. It never guesses and never silently drops a
request: a blocked media route is replaced by the correct intent plus an explicit escalation,
and the reason travels with the decision for the audit trail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from core.logging_setup import get_logger

log = get_logger("director.semantic_guard")

# --------------------------------------------------------------------------- media family
# Capability prefixes that mean "this touches media playback on some device".
MEDIA_CAPABILITY_PREFIXES = (
    "media.", "plugin.media.", "plugin.spotify.", "plugin.youtube.", "browser.media.",
)


def is_media_capability(capability: str) -> bool:
    """True when a capability controls media playback (PC, plugin or remote device)."""
    name = (capability or "").lower()
    if not name:
        return False
    if name.startswith(MEDIA_CAPABILITY_PREFIXES):
        return True
    # a device-scoped media action can arrive fully qualified, e.g. "phone.media.next"
    return ".media." in name or name.endswith(".media")


# --------------------------------------------------------------------------- media evidence
# Concrete media nouns, brands and playback verbs. These are what justify a media action.
MEDIA_EVIDENCE_PATTERNS = (
    r"\bsongs?\b", r"\bgaana\b", r"\bgaane\b", r"\bgana\b", r"\bmusic\b", r"\btracks?\b",
    r"\btunes?\b", r"\bmelody\b", r"\bplaylist\b", r"\balbums?\b", r"\bartist\b",
    r"\bsingers?\b", r"\bband\b", r"\baudio\b", r"\bbhajan\b", r"\bkirtan\b", r"\bpodcasts?\b",
    r"\bradio\b", r"\bspotify\b", r"\byoutube\s+music\b", r"\bsaavn\b", r"\bwynk\b",
    r"\bsoundcloud\b", r"\bdeezer\b", r"\bapple\s+music\b", r"\bgaana\.com\b",
    r"\bvideos?\b", r"\bmovies?\b", r"\bfilms?\b", r"\bclips?\b", r"\btrailers?\b",
    r"\bepisodes?\b", r"\bseries\b", r"\bnetflix\b", r"\bprime\s+video\b", r"\bhotstar\b",
    r"\bvlc\b", r"\byoutube\b", r"\bplayback\b", r"\bnow\s+playing\b",
    r"\bmedia\s+session\b", r"\bmedia\s+player\b",
    # playback verbs (the owner's list names play/pause explicitly)
    r"\bplay\b", r"\bpause\b", r"\bunpause\b", r"\bresume\s+playback\b",
    r"\bbajao\b", r"\bbaja\b", r"\bbajana\b", r"\bchalao\b", r"\bchala\b",
)

# ----------------------------------------------------------------- continuation semantics
# Nouns that name *work* rather than media.
WORK_NOUN_PATTERNS = (
    r"\bprojects?\b", r"\bmissions?\b", r"\btasks?\b", r"\bwork\b", r"\bjobs?\b",
    r"\bassignments?\b", r"\btickets?\b", r"\bissues?\b", r"\bthreads?\b",
    r"\bconversations?\b", r"\bchats?\b", r"\bsessions?\b", r"\bcode\b", r"\bbranch\b",
    r"\bkaam\b", r"\bkarya\b", r"\bpitch\b", r"\breport\b",
)

# Verbs / markers that mean "carry on from before".
CONTINUATION_MARKER_PATTERNS = (
    r"\bcontinue\b", r"\bcontinues\b", r"\bcontinuing\b", r"\bcontinuation\b",
    r"\bresume\b", r"\bresumes\b", r"\bresuming\b",
    r"\bcarry\s+on\b", r"\bpick\s+up\b", r"\bwhere\s+i\s+left\b", r"\bwhere\s+we\s+left\b",
    r"\bwahi\s+se\b", r"\bwahin\s+se\b", r"\bwahan\s+se\b",
    r"\baage\b", r"\baage\s+badhao\b", r"\baage\s+badha\b",
    r"\bchalu\s+rakho\b", r"\bchalate\s+raho\b", r"\bphir\s+se\s+shuru\b",
    # Hinglish temporal markers: "kal wala kaam", "pichhla project", "pehle wala task"
    r"\bkal\s+wal[aei]\b", r"\bkal\s+ka\b", r"\bkal\s+ki\b", r"\bkal\s+ke\b",
    r"\bpichhl[aei]\b", r"\bpichl[aei]\b", r"\bpehle\s+wal[aei]\b",
    r"\blast\s+wal[aei]\b", r"\bprevious\b", r"\blast\b", r"\bearlier\b", r"\blatest\b",
)

# Whole phrases that are continuation on their own, with no work noun needed.
CONTINUATION_PHRASE_PATTERNS = (
    r"\bwahi\s+se\s+continue\b", r"\bwahin\s+se\s+continue\b",
    r"\bcontinue\s+from\s+where\b", r"\bpick\s+up\s+where\b", r"\bwhere\s+i\s+left\s+off\b",
    r"\bkal\s+wala\s+kaam\b", r"\bprevious\s+mission\b", r"\blatest\s+project\b",
    r"\bresume\s+task\b", r"\bresume\s+the\s+task\b", r"\bcontinue\s+project\b",
    r"\bresume\s+project\b", r"\bcontinue\s+my\s+work\b", r"\bresume\s+my\s+work\b",
)

_MEDIA_RE = re.compile("|".join(MEDIA_EVIDENCE_PATTERNS), re.IGNORECASE)
_WORK_RE = re.compile("|".join(WORK_NOUN_PATTERNS), re.IGNORECASE)
_MARKER_RE = re.compile("|".join(CONTINUATION_MARKER_PATTERNS), re.IGNORECASE)
_PHRASE_RE = re.compile("|".join(CONTINUATION_PHRASE_PATTERNS), re.IGNORECASE)


# --------------------------------------------------------------------------- public helpers
def has_media_evidence(text: str) -> bool:
    """True when the request itself names something media-specific."""
    return bool(_MEDIA_RE.search(text or ""))


def has_continuation_semantics(text: str) -> bool:
    """True when the request means "carry on with the work I was doing".

    Two shapes count:
      * an explicit continuation phrase ("continue from where", "kal wala kaam"), or
      * a continuation marker together with a *work* noun ("latest project continue",
        "kal wala project resume").
    A media noun is deliberately not a work noun, so "Spotify ka song continue karo" is not
    continuation semantics.
    """
    body = text or ""
    if _PHRASE_RE.search(body):
        return True
    return bool(_WORK_RE.search(body)) and bool(_MARKER_RE.search(body))


def context_has_media_session(context: Optional[Dict[str, Any]]) -> bool:
    """True when live context shows an actual media session (the "stronger grounding")."""
    if not context:
        return False
    for key in ("media_session", "now_playing", "media_session_active", "playback"):
        value = context.get(key)
        if value:
            return True
    return False


# ------------------------------------------------------------------------------- verdict
@dataclass
class GuardVerdict:
    allowed: bool = True
    action: str = "allow"                 # allow | reroute
    blocked_capabilities: List[str] = field(default_factory=list)
    reason: str = ""
    continuation: bool = False
    media_evidence: bool = False
    original_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "action": self.action,
                "blocked_capabilities": self.blocked_capabilities, "reason": self.reason,
                "continuation": self.continuation, "media_evidence": self.media_evidence,
                "original_confidence": round(self.original_confidence, 4)}


def guard_decision(text: str, decision: Any, *,
                   context: Optional[Dict[str, Any]] = None) -> GuardVerdict:
    """Validate a director decision against the semantics of the request.

    Mutates `decision` in place when a media route has to be withdrawn, and returns the
    verdict. Safe to call on every turn: a decision that is already grounded is untouched.

    A blocked media route is never silently dropped. The request is re-pointed at the intent
    it actually expresses — mission / memory / skill retrieval — and escalated so a real
    reasoning model handles it with context.
    """
    body = (text or "").strip()
    verdict = GuardVerdict(original_confidence=float(getattr(decision, "confidence", 0.0) or 0.0))
    if not body or decision is None:
        return verdict

    tasks: Iterable[Any] = getattr(decision, "tasks", None) or []
    tasks = list(tasks)
    app_launches = [t for t in tasks if getattr(t, "capability", "") == "application.open"]
    negated_action = re.search(
        r"\b(?:do\s+not|don't|never)\s+(?:\w+\s+){0,2}(?:open|launch|start|change|click|send|delete|close|install|play)\b|"
        r"\b(?:mat\s+(?:karo|kholo|khol)|(?:open|launch|change|kholna)\s+mat)\b|मत\s+(?:खोल|कर|भेज)", body, re.I)
    launch_verb = re.search(r"\b(?:open|launch|start|kholo|khol|chalu|shuru)\b|खोल|चालू", body, re.I)
    interaction = re.search(
        r"\b(?:interact|click|type|fill|message|draft|send|search|research|notifications?|"
        r"then|after|but|without|bhej(?:o|na|iye|ega)?|likh(?:o|na)?|karke|kar\s+ke|kholke)\b|भेज|लिख|फिर|बिना", body, re.I)
    software_workflow = (re.search(r"\b(?:install|uninstall|download|update)\b", body, re.I)
                         and re.search(r"\b(?:app|software|program|game|store)\b", body, re.I))
    if tasks and (negated_action or software_workflow or (app_launches and (not launch_verb or interaction))):
        verdict.allowed = False
        verdict.action = "reroute"
        verdict.blocked_capabilities = [getattr(t, "capability", "") for t in tasks]
        verdict.reason = "Request constraints or app interaction require full conversational tool reasoning."
        decision.tasks = []
        decision.reasoning_required = True
        decision.provider_category = "reasoning"
        decision.reply_hint = ""
        decision.intent = "constrained_action"
        decision.raw = dict(getattr(decision, "raw", {}) or {})
        decision.raw.pop("web_plan", None)
        decision.raw["semantic_guard"] = verdict.to_dict()
        return verdict
    media_tasks = [t for t in tasks if is_media_capability(getattr(t, "capability", ""))]
    if not media_tasks:
        return verdict

    verdict.continuation = has_continuation_semantics(body)
    verdict.media_evidence = has_media_evidence(body) or context_has_media_session(context)

    if not verdict.continuation or verdict.media_evidence:
        # either it is not a continuation request, or it really is about media
        return verdict

    blocked = [getattr(t, "capability", "") for t in media_tasks]
    remaining = [t for t in tasks if t not in media_tasks]
    verdict.allowed = False
    verdict.action = "reroute"
    verdict.blocked_capabilities = blocked
    verdict.reason = (f"continuation semantics without media grounding — refused "
                      f"{', '.join(blocked)} at confidence {verdict.original_confidence:.2f}; "
                      f"routed to mission/memory retrieval + escalation")

    decision.tasks = remaining
    decision.reasoning_required = True
    if not decision.provider_category or decision.provider_category == "none":
        decision.provider_category = "reasoning"
    if not decision.memory_query:
        decision.memory_query = body[:512]
    # keep the original route + the overrule visible for the audit trail and the UI
    raw = getattr(decision, "raw", None)
    if isinstance(raw, dict):
        raw["semantic_guard"] = verdict.to_dict()
        raw["guard_blocked_tasks"] = [
            {"capability": getattr(t, "capability", ""), "device": getattr(t, "device", "")}
            for t in media_tasks]

    log.warning("semantic guard refused %s for %r (confidence %.2f) — escalating instead",
                blocked, body[:80], verdict.original_confidence)
    return verdict
