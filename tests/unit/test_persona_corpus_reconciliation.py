"""agency-agents corpus reconciliation — resolves the 302 vs 264 count inconsistency.

Measured against the actual upstream archive (``E:/G3/repos/agency-agents-main``):

    upstream .md files, all top-level dirs ......... 321
      .github/ .....................................   1   not a division
      examples/ ....................................   6   not a division
      scripts/ .....................................   1   not a division
      integrations/ ................................  18   connector docs, not personas
      game-development/ nested (godot, unity, ...) .  15   personas — were DROPPED by glob
      strategy/ nested (playbooks, runbooks) .......  13   NEXUS docs, not personas
    ------------------------------------------------------------------
    vendored files on disk ......................... 295   (19 dirs, rglob)
    strategy/ files without ``name`` frontmatter ... -16   docs, correctly skipped
    ------------------------------------------------------------------
    parsed persona templates ....................... 279   (18 divisions)

The original drift had two causes:
1. the audit's "302" came from an earlier upstream snapshot (repo has since changed);
2. the vendoring step used ``glob("*.md")`` (top-level only), silently dropping 15 real
   game-engine personas nested in sub-directories.

Fix: vendored with rglob and the loader switched to rglob. Net gain +15 templates.
"""
from __future__ import annotations

from agents.personas import PersonaLibrary


def test_corpus_loads_the_full_persona_set():
    templates = PersonaLibrary().load()
    assert len(templates) >= 279, (
        f"expected at least 279 personas, got {len(templates)} — did nested "
        f"sub-directories stop being scanned?")
    assert len(PersonaLibrary().divisions()) >= 18


def test_nested_game_engine_personas_are_present():
    """These lived in godot/, unity/, unreal-engine/ and were silently dropped before."""
    templates = PersonaLibrary().load()
    game = [t for t in templates if t.division == "game-development"]
    assert len(game) >= 21, f"game-development lost nested personas: {len(game)}"

    joined = " ".join(f"{t.template_id} {t.name}".lower() for t in game)
    for engine in ("godot", "unity", "unreal"):
        assert engine in joined, f"nested {engine} persona missing from the corpus"


def test_strategy_files_are_docs_and_are_skipped_not_counted():
    """strategy/ holds NEXUS framework docs without a `name` — correctly not personas."""
    divisions = PersonaLibrary().divisions()
    assert "strategy" not in divisions, (
        "strategy/ contains documentation, not personas — if it now appears, the "
        "frontmatter gate is letting non-personas through")


def test_every_template_has_a_division_and_name():
    for t in PersonaLibrary().load():
        assert t.division and t.name, f"malformed template: {t.template_id}"


def test_corpus_statistics_report_counts():
    stats = PersonaLibrary().statistics()
    assert stats["templates"] >= 279, stats
    assert stats["divisions"] >= 18, stats
    assert "game-development" in stats["by_division"]
