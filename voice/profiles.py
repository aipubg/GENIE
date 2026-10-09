"""Voice profiles (voice/profiles).

The voice is *presentation*, not personality: changing the voice provider or the profile must
never change how GENIE behaves (that is the Communication Brain's job).

Cloned voices require a recorded consent entry. Without consent the profile cannot be used —
enforced here, not in documentation (master spec §41).
"""
from __future__ import annotations

import dataclasses
import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("voice.profiles")


@dataclass
class ConsentRecord:
    person_id: str
    granted_at: int
    purpose: str
    evidence: str = ""          # where the consent is stored (file/hash/reference)
    revoked_at: Optional[int] = None

    def active(self) -> bool:
        return self.revoked_at is None

    def to_dict(self) -> Dict[str, Any]:
        return {"person_id": self.person_id, "granted_at": self.granted_at,
                "purpose": self.purpose, "evidence": self.evidence,
                "revoked_at": self.revoked_at, "active": self.active()}


@dataclass
class VoiceProfile:
    profile_id: str
    display_name: str
    provider: str = "auto"          # provider name or "auto"
    voice_id: str = ""              # provider-specific voice
    rate: int = 0                   # -10..10
    volume: int = 100               # 0..100
    style: str = "default"          # default | calm | energetic | concise
    language: str = "hinglish"
    cloned: bool = False
    consent: Optional[ConsentRecord] = None

    def usable(self) -> bool:
        """A cloned voice is only usable with an active consent record."""
        return (not self.cloned) or bool(self.consent and self.consent.active())

    def to_dict(self) -> Dict[str, Any]:
        return {"profile_id": self.profile_id, "display_name": self.display_name,
                "provider": self.provider, "voice_id": self.voice_id, "rate": self.rate,
                "volume": self.volume, "style": self.style, "language": self.language,
                "cloned": self.cloned, "usable": self.usable(),
                "consent": self.consent.to_dict() if self.consent else None}


DEFAULT_PROFILES = [
    VoiceProfile("genie-default", "GENIE (default)", provider="auto", rate=0, volume=100,
                 style="default", language="hinglish"),
    VoiceProfile("genie-concise", "GENIE (concise)", provider="auto", rate=1, volume=100,
                 style="concise", language="hinglish"),
    VoiceProfile("genie-calm", "GENIE (calm)", provider="auto", rate=-1, volume=90,
                 style="calm", language="hinglish"),
]


class VoiceProfileRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._profiles: Dict[str, VoiceProfile] = {p.profile_id: p for p in DEFAULT_PROFILES}
        self._active = "genie-default"
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in data.get("profiles", []):
                raw = dict(raw)
                consent = raw.pop("consent", None)
                raw.pop("usable", None)          # derived, not a constructor argument
                allowed = {f.name for f in dataclasses.fields(VoiceProfile)}
                profile = VoiceProfile(**{k: v for k, v in raw.items() if k in allowed})
                if consent:
                    profile.consent = ConsentRecord(**consent)
                self._profiles[profile.profile_id] = profile
            self._active = data.get("active", self._active)
        except Exception as exc:
            log.warning("voice profile file ignored: %s", exc)

    def save(self) -> None:
        if not self.path:
            return
        payload = {"active": self._active,
                   "profiles": [p.to_dict() for p in self._profiles.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # --------------------------------------------------------------------- API
    def all(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self._profiles.values()]

    def get(self, profile_id: str) -> Optional[VoiceProfile]:
        return self._profiles.get(profile_id)

    def active(self) -> VoiceProfile:
        profile = self._profiles.get(self._active)
        if profile is None or not profile.usable():
            return self._profiles["genie-default"]
        return profile

    def set_active(self, profile_id: str) -> Dict[str, Any]:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return {"ok": False, "error": f"unknown profile {profile_id}"}
        if not profile.usable():
            return {"ok": False,
                    "error": "this cloned voice has no active consent record and cannot be used"}
        self._active = profile_id
        self.save()
        return {"ok": True, "active": profile_id}

    def add(self, profile: VoiceProfile) -> Dict[str, Any]:
        """Register a profile.

        A cloned voice may be *registered* without consent, but it stays unusable until an
        active consent record exists — `usable()` and `set_active()` enforce that.
        """
        self._profiles[profile.profile_id] = profile
        self.save()
        return {"ok": True, "profile_id": profile.profile_id, "usable": profile.usable(),
                "consent_required": profile.cloned and not profile.usable()}

    def grant_consent(self, profile_id: str, person_id: str, purpose: str,
                      evidence: str = "") -> Dict[str, Any]:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return {"ok": False, "error": f"unknown profile {profile_id}"}
        profile.consent = ConsentRecord(person_id=person_id, granted_at=int(time.time()),
                                        purpose=purpose, evidence=evidence)
        self.save()
        return {"ok": True, "profile_id": profile_id, "consent": profile.consent.to_dict()}

    def revoke_consent(self, profile_id: str) -> Dict[str, Any]:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.consent is None:
            return {"ok": False, "error": "no consent record"}
        profile.consent.revoked_at = int(time.time())
        if self._active == profile_id:
            self._active = "genie-default"
        self.save()
        return {"ok": True, "revoked": profile_id}

    def status(self) -> Dict[str, Any]:
        return {"active": self.active().profile_id, "profiles": self.all()}
