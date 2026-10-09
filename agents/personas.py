"""Role templates for the Agent Factory (agents/personas.py) — Phase 11B.

Source: the **agency-agents** corpus vendored into ``data/personas/`` (19 divisions, 295
personas), reused directly under GENIE contracts.

The instruction is explicit: *do NOT hardcode hundreds of permanent agents.* These are
**templates**. The factory turns a template into a task-specific agent on demand:

    role templates  ->  AgentFactory  ->  task-specific agent

so GENIE carries the corpus as data (cheap, searchable) and instantiates only what a mission
actually needs.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("agents.personas")

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "data" / "personas"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def _parse_frontmatter(text: str) -> Dict[str, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    out: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        kv = _KV.match(line.strip())
        if not kv:
            continue
        value = kv.group(2).strip()
        # strip surrounding quotes (descriptions are quoted in the corpus)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[kv.group(1)] = value
    return out


class RoleTemplate:
    """One persona: enough to describe a role, never a running agent."""

    def __init__(self, path: Path, division: str, front: Dict[str, str], body: str):
        self.path = path
        self.division = division
        self.template_id = path.stem
        self.name = front.get("name") or path.stem.replace("-", " ").title()
        self.description = front.get("description", "")
        self.emoji = front.get("emoji", "")
        self.vibe = front.get("vibe", "")
        self.color = front.get("color", "")
        self.body = body

    def to_dict(self) -> Dict[str, Any]:
        return {"template_id": self.template_id, "division": self.division, "name": self.name,
                "description": self.description, "emoji": self.emoji, "vibe": self.vibe,
                "color": self.color}

    def to_agent_spec(self, objective: str = "") -> Dict[str, Any]:
        """The shape AgentFactory.create expects — a task-specific agent, built on demand."""
        return {"role": self.template_id, "capability": self._infer_capability(),
                "quality": "standard", "purpose": objective or self.description[:180],
                "division": self.division, "persona": self.name}

    def _infer_capability(self) -> str:
        """Map a division onto a GENIE capability so routing stays honest."""
        return {
            "engineering": "coding", "testing": "coding", "security": "coding",
            "design": "creative", "marketing": "creative", "paid-media": "creative",
            "product": "research", "research": "research", "academic": "research",
            "finance": "analysis", "gis": "analysis", "specialized": "general",
            "strategy": "research", "support": "general", "sales": "general",
            "healthcare": "research", "spatial-computing": "creative",
            "project-management": "general", "game-development": "coding",
        }.get(self.division, "general")


class PersonaLibrary:
    """Lazy, cached access to the vendored persona corpus."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self._templates: Optional[List[RoleTemplate]] = None

    # ------------------------------------------------------------------ load
    def load(self, *, force: bool = False) -> List[RoleTemplate]:
        if self._templates is not None and not force:
            return self._templates
        templates: List[RoleTemplate] = []
        if not self.root.is_dir():
            log.debug("persona corpus not present at %s", self.root)
            self._templates = templates
            return templates
        for division in sorted(p.name for p in self.root.iterdir() if p.is_dir()):
            # rglob, not glob: several divisions (game-development, strategy) keep personas in
            # nested sub-directories (godot/, unity/, unreal-engine/, playbooks/, runbooks/).
            # Using glob silently dropped 28 of them.
            for path in sorted((self.root / division).rglob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                front = _parse_frontmatter(text)
                if not front.get("name"):
                    continue  # not a persona (README/notes), skip
                body = text[_FRONTMATTER.match(text).end():] if _FRONTMATTER.match(text) else text
                templates.append(RoleTemplate(path, division, front, body))
        self._templates = templates
        return templates

    # ----------------------------------------------------------------- query
    def all(self) -> List[RoleTemplate]:
        return list(self.load())

    def divisions(self) -> List[str]:
        return sorted({t.division for t in self.load()})

    def by_division(self, division: str) -> List[RoleTemplate]:
        return [t for t in self.load() if t.division == division]

    def get(self, template_id: str) -> Optional[RoleTemplate]:
        return next((t for t in self.load() if t.template_id == template_id), None)

    #: words that carry no routing signal but appear in almost every request
    STOPWORDS = frozenset(("a", "an", "the", "for", "of", "to", "in", "on", "and", "or",
                           "with", "that", "this", "me", "my", "please", "build", "create",
                           "make", "write", "need", "want", "using", "use"))

    def search(self, query: str, *, limit: int = 10) -> List[RoleTemplate]:
        """Rank by how well a persona matches the need.

        Deliberately **name/division-weighted**: counting raw term frequency across a long
        description lets verbose personas win on unrelated requests (a compliance persona whose
        text happens to mention "API" outranked a backend engineer). Weighting the name and
        division fixes that without hand-tuning a list of agents.
        """
        q = (query or "").strip().lower()
        if not q:
            return self.load()[:limit]
        terms = [t for t in re.split(r"\W+", q)
                 if len(t) >= 3 and t not in self.STOPWORDS]
        if not terms:
            return self.load()[:limit]
        scored: List[tuple] = []
        for tpl in self.load():
            name, tid = tpl.name.lower(), tpl.template_id.lower()
            div, desc = tpl.division.lower(), tpl.description.lower()
            score = 0
            for term in terms:
                if term in name:
                    score += 6
                if term in tid:
                    score += 4
                if term in div:
                    score += 3
                if term in desc:
                    score += 1
            if score:
                scored.append((score, tpl))
        scored.sort(key=lambda kv: -kv[0])
        return [tpl for _, tpl in scored[:limit]]

    def best(self, query: str) -> Optional[RoleTemplate]:
        found = self.search(query, limit=1)
        return found[0] if found else None

    def statistics(self) -> Dict[str, Any]:
        templates = self.load()
        by_div: Dict[str, int] = {}
        for t in templates:
            by_div[t.division] = by_div.get(t.division, 0) + 1
        return {"root": str(self.root), "templates": len(templates),
                "divisions": len(by_div), "by_division": by_div}


_LIBRARY: Optional[PersonaLibrary] = None


def get_personas(root: Optional[Path] = None) -> PersonaLibrary:
    global _LIBRARY
    if _LIBRARY is None or root is not None:
        _LIBRARY = PersonaLibrary(root)
    return _LIBRARY
