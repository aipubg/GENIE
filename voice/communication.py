"""Communication Brain (voice/communication).

Conversational behaviour lives here, **not** in a prompt and **not** in the model. The brain
decides length, pace, acknowledgements, humour, interruption timing, proactivity and the
speaking target — so changing the LLM or the voice provider cannot change GENIE's personality
(master spec §34/§36).

It also owns *spoken* shaping: text that reads well in a chat window does not read well out
loud, so markdown, code blocks and long enumerations are converted to speakable form.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("voice.communication")

# Situations where humour must be off regardless of the user's mood
SERIOUS_MARKERS = ("delete", "deleted", "error", "fail", "failed", "urgent", "emergency",
                   "crash", "lost", "corrupt", "mistake", "sorry", "problem")
PLAYFUL_MARKERS = ("haha", "lol", "mazak", "mazaak", "joke", "funny", "😄", "😂")
FRUSTRATION_MARKERS = ("again", "phir se", "not working", "nahi chal", "why", "kyun",
                       "stupid", "useless", "bekar")

ACKNOWLEDGEMENTS = {
    "start": ["Haan", "Theek hai", "Chalo", "Okay"],
    "done": ["Ho gaya", "Done", "Kar diya"],
    "failed": ["Ye nahi ho paya", "Nahi hua"],
    "thinking": ["Ek second", "Dekh raha hoon"],
}


@dataclass
class CommunicationPolicy:
    length: str = "short"            # short | normal | detailed
    pace: str = "normal"             # slow | normal | fast
    humour: int = 1                  # 0 none, 1 light, 2 normal, 3 playful
    acknowledge: bool = True
    proactivity: str = "useful"      # silent | useful | chatty
    speaking_target: str = "pc_main"
    style: str = "default"
    max_spoken_chars: int = 420
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"length": self.length, "pace": self.pace, "humour": self.humour,
                "acknowledge": self.acknowledge, "proactivity": self.proactivity,
                "speaking_target": self.speaking_target, "style": self.style,
                "max_spoken_chars": self.max_spoken_chars, "reason": self.reason}


@dataclass
class CommunicationPlan:
    policy: CommunicationPolicy
    acknowledgement: str = ""
    spoken_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"policy": self.policy.to_dict(), "acknowledgement": self.acknowledgement,
                "spoken_text": self.spoken_text}


class CommunicationBrain:
    """Provider-independent conversational policy engine."""

    def __init__(self, profile_style: str = "default"):
        self.profile_style = profile_style
        self._history: List[Dict[str, Any]] = []
        self.stats = {"turns": 0, "interruptions": 0, "acknowledgements": 0}

    # --------------------------------------------------------------- policy
    def policy_for(self, text: str, *, intent: str = "", interrupted: bool = False,
                   urgency: float = 0.0, relevance: float = 0.5,
                   confidence: float = 0.8, quiet_hours: bool = False) -> CommunicationPolicy:
        low = (text or "").lower()
        policy = CommunicationPolicy()

        if self.profile_style == "concise":
            policy.length, policy.acknowledge = "short", False
        elif self.profile_style == "calm":
            policy.pace, policy.humour = "slow", 0

        # serious situations: no jokes, direct wording
        if any(marker in low for marker in SERIOUS_MARKERS) or urgency > 0.7:
            policy.humour = 0
            policy.length = "short"
            policy.reason = "serious context"
        # repeated frustration: shorter, no humour, no chatter
        elif any(marker in low for marker in FRUSTRATION_MARKERS):
            policy.humour = 0
            policy.length = "short"
            policy.proactivity = "silent"
            policy.reason = "user frustration"
        # playful user: a little more humour, but never in an emergency
        elif any(marker in low for marker in PLAYFUL_MARKERS):
            policy.humour = 2
            policy.reason = "playful user"
        elif quiet_hours:
            policy.proactivity = "silent"
            policy.length = "short"
            policy.reason = "quiet hours"

        if interrupted:
            # after an interruption, be brief and get to the point
            policy.length = "short"
            policy.acknowledge = False
            policy.reason = (policy.reason + "; interrupted").strip("; ")

        if intent in ("application_action", "computer_action", "device_action"):
            policy.length = "short"          # actions confirm, they do not lecture
            policy.acknowledge = False

        # low confidence: say less, offer to clarify rather than guessing loudly
        if confidence < 0.4:
            policy.length = "short"
            policy.reason = (policy.reason + "; low confidence").strip("; ")

        if policy.length == "short":
            policy.max_spoken_chars = 200
        elif policy.length == "detailed":
            policy.max_spoken_chars = 900
        return policy

    # ------------------------------------------------------------------ plan
    def plan(self, text: str, *, intent: str = "", interrupted: bool = False,
             result_ok: Optional[bool] = None, **policy_kwargs) -> CommunicationPlan:
        policy = self.policy_for(text, intent=intent, interrupted=interrupted, **policy_kwargs)
        acknowledgement = ""
        if policy.acknowledge:
            if result_ok is False:
                acknowledgement = ACKNOWLEDGEMENTS["failed"][0]
            elif result_ok is True:
                acknowledgement = ACKNOWLEDGEMENTS["done"][0]
            else:
                acknowledgement = ACKNOWLEDGEMENTS["start"][0]
            self.stats["acknowledgements"] += 1
        return CommunicationPlan(policy=policy, acknowledgement=acknowledgement)

    # ---------------------------------------------------------------- shaping
    def shape_for_speech(self, text: str, policy: CommunicationPolicy) -> str:
        """Convert model output into something a person can listen to."""
        if not text:
            return ""
        out = text
        out = re.sub(r"```.*?```", " code block ", out, flags=re.S)
        out = re.sub(r"`([^`]*)`", r"\1", out)
        out = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", out)          # markdown links
        out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.M)        # bullets
        out = re.sub(r"^\s*#+\s*", "", out, flags=re.M)           # headings
        out = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", out)   # emphasis
        out = re.sub(r"\s{2,}", " ", out).strip()

        if len(out) > policy.max_spoken_chars:
            # cut at a sentence boundary so speech never stops mid-thought
            window = out[:policy.max_spoken_chars]
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
            out = window[:cut + 1] if cut > 60 else window.rsplit(" ", 1)[0] + "…"
        return out

    # --------------------------------------------------------------- proactivity
    def should_speak(self, *, urgency: float, relevance: float, confidence: float,
                     interruption_cost: float = 0.3, busy: bool = False) -> Dict[str, Any]:
        """Scored proactivity (master spec §35) — never a timer."""
        score = (urgency * 0.4) + (relevance * 0.3) + (confidence * 0.2) - (interruption_cost * 0.3)
        if busy:
            score -= 0.15
        if score >= 0.75:
            action = "interrupt"
        elif score >= 0.55:
            action = "speak_now"
        elif score >= 0.35:
            action = "mention_later"
        elif score >= 0.2:
            action = "show_silently"
        else:
            action = "ignore"
        return {"score": round(score, 3), "action": action,
                "humour": 0 if action in ("interrupt", "speak_now") and urgency > 0.6 else 1}

    # -------------------------------------------------------------- bookkeeping
    def record_turn(self, text: str, reply: str, interrupted: bool = False) -> None:
        self.stats["turns"] += 1
        if interrupted:
            self.stats["interruptions"] += 1
        self._history.append({"ts": time.time(), "text": text[:200], "reply": reply[:200],
                              "interrupted": interrupted})
        self._history = self._history[-50:]

    def status(self) -> Dict[str, Any]:
        return {"profile_style": self.profile_style, "stats": dict(self.stats),
                "recent_turns": len(self._history)}
