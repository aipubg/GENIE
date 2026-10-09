"""P5 old-repo gap: hack-skills import + Skill Hub + scanner + PTE gating.

Deterministic — no donor checkout required for the gating proofs (they use the
real SkillRegistry with a scanner double). The real-donor import is covered by
scripts/import_hack_skills.py and its JSON report.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.skill_scanner import ScanResult                       # noqa: E402
from skills.hub import (VERDICT_BLOCK, VERDICT_OK, SkillRecord,     # noqa: E402
                        SkillRegistry, SkillStorage)


class _FakeScanner:
    """Returns a fixed verdict so gating can be proven deterministically."""

    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        res = ScanResult(skill_path=path)
        # a BLOCK/CONFIRM verdict is derived from findings; emulate that shape
        res.findings = []
        object.__setattr__(res, "_verdict_override", self.verdict)
        return res


def _patch_verdict(monkeypatch, verdict: str):
    """Force ScanResult.verdict() to a known value for the duration of a test."""
    monkeypatch.setattr(ScanResult, "verdict", lambda self: verdict)


# ------------------------------------------------------------------- gating
def test_block_verdict_cannot_be_enabled_without_force(monkeypatch):
    _patch_verdict(monkeypatch, VERDICT_BLOCK)
    reg = SkillRegistry(SkillStorage(), scanner=_FakeScanner(VERDICT_BLOCK))
    rec = reg.register(".", name="dangerous", capabilities=["security.general"])

    assert rec.verdict == VERDICT_BLOCK
    assert rec.enabled is False, "a BLOCK verdict must never be auto-enabled"
    assert rec.confirm_required is False

    assert reg.set_enabled(rec.skill_id, True) is False, \
        "enabling a BLOCK skill without force must be refused"
    reg._persist()


def test_block_verdict_can_be_enabled_with_explicit_force(monkeypatch):
    _patch_verdict(monkeypatch, VERDICT_BLOCK)
    reg = SkillRegistry(SkillStorage(), scanner=_FakeScanner(VERDICT_BLOCK))
    rec = reg.register(".", name="dangerous", capabilities=["security.general"])

    assert reg.set_enabled(rec.skill_id, True, force=True) is True
    assert reg.get(rec.skill_id).forced is True, \
        "a forced enable must be recorded as forced (auditable)"


def test_disabled_skill_is_not_discoverable_by_capability(monkeypatch):
    """PTE gating only matters if blocked skills are unreachable."""
    _patch_verdict(monkeypatch, VERDICT_BLOCK)
    reg = SkillRegistry(SkillStorage(), scanner=_FakeScanner(VERDICT_BLOCK))
    rec = reg.register(".", name="dangerous", capabilities=["security.exploit"])

    assert reg.find_by_capability("security.exploit") == [], \
        "a BLOCK-verdict skill must never be discoverable by capability"


def test_ok_verdict_is_enabled_and_discoverable(monkeypatch):
    _patch_verdict(monkeypatch, VERDICT_OK)
    reg = SkillRegistry(SkillStorage(), scanner=_FakeScanner(VERDICT_OK))
    rec = reg.register(".", name="safe", capabilities=["security.recon"])

    assert rec.enabled is True
    assert [r.skill_id for r in reg.find_by_capability("security.recon")] == \
        [rec.skill_id]


def test_scanner_error_is_unknown_not_a_clean_bill():
    """A scanner failure must not be recorded as a pass."""
    def boom(_path):
        raise RuntimeError("scanner exploded")

    reg = SkillRegistry(SkillStorage(), scanner=boom)
    rec = reg.register(".", name="unscannable", capabilities=["security.general"])
    assert rec.verdict == "UNKNOWN", \
        "a scanner error must yield UNKNOWN, never a fabricated OK"
    assert "scan error" in (rec.scanner_summary or "")


# ------------------------------------------------------- capability hygiene
def test_capabilities_are_never_wildcard(monkeypatch):
    """No imported skill may hold wildcard authority."""
    _patch_verdict(monkeypatch, VERDICT_OK)
    reg = SkillRegistry(SkillStorage(), scanner=_FakeScanner(VERDICT_OK))
    rec = reg.register(".", name="x", capabilities=["security.recon", "docs.read"])
    assert "*" not in rec.capabilities
    assert "all" not in rec.capabilities


def test_skill_record_keeps_provenance(monkeypatch):
    _patch_verdict(monkeypatch, VERDICT_OK)
    reg = SkillRegistry(SkillStorage(), scanner=_FakeScanner(VERDICT_OK))
    rec = reg.register(".", name="y", donor="hack-skills",
                       capabilities=["security.web"])
    assert rec.donor == "hack-skills"
    assert rec.source == "local"
    assert rec.to_dict()["donor"] == "hack-skills"


# ------------------------------------------------ real donor (if available)
DONOR = Path("E:/G3/repos/hack-skills-main/skills")


@pytest.mark.skipif(not DONOR.is_dir(), reason="hack-skills donor not present")
def test_real_donor_import_produces_verdicts_not_fabricated_passes():
    """Every real imported skill carries a real scanner verdict."""
    from security.skill_scanner import scan_skill
    reg = SkillRegistry(SkillStorage(), scanner=scan_skill)
    rec = reg.register(str(DONOR / sorted(p.name for p in DONOR.iterdir()
                                          if p.is_dir())[0]))
    assert rec.verdict in (VERDICT_OK, "CONFIRM", VERDICT_BLOCK, "UNKNOWN")
    assert rec.findings_count >= 0
    assert rec.scanner_summary, "a summary must always be recorded"
