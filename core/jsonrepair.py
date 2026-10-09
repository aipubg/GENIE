"""Structured-output repair (core/jsonrepair.py) — re-audit 14.5.

Capability donor: **AgentScope** (schema-guided JSON/argument repair).

NEDLE2 and remote models emit malformed JSON often enough to matter: code fences around output,
trailing commas, prose before the object, or a response truncated mid-object. Each one currently
becomes a failed tool call.

This applies a series of **increasingly aggressive** repairs and reports which one worked, so the
runtime can log how often a model needs fixing (a quality signal in itself).

Honesty: if nothing works it returns ``ok=False`` rather than inventing a plausible object. A
silently "repaired" object that never appeared in the model output is worse than a clean failure.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.logging_setup import get_logger

log = get_logger("core.jsonrepair")

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _strip_fences(text: str) -> str:
    match = _FENCE.search(text or "")
    if match:
        return match.group(1).strip()
    # an unclosed fence
    return re.sub(r"^```(?:json|JSON)?\s*", "", (text or "").strip()).rstrip("`").strip()


def _remove_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA.sub(r"\1", text or "")


def _extract_block(text: str) -> str:
    """Take the outermost balanced {...} or [...] span, ignoring surrounding prose."""
    start_obj, start_arr = (text or "").find("{"), (text or "").find("[")
    candidates = [i for i in (start_obj, start_arr) if i >= 0]
    if not candidates:
        return (text or "").strip()
    start = min(candidates)
    opener = (text or "")[start]
    closer = "}" if opener == "{" else "]"
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return text[start:]          # truncated — let the closer step fix it


def _close_truncated(text: str) -> str:
    """Close strings/brackets left open by a truncated response."""
    stripped = (text or "").rstrip()
    # an unterminated string: drop the dangling fragment
    if stripped.count('"') % 2 == 1:
        cut = stripped.rfind('"')
        stripped = stripped[:cut].rstrip().rstrip(",")
    open_braces, open_brackets = 0, 0
    in_string, escaped = False, False
    for char in stripped:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            open_braces += 1
        elif char == "}":
            open_braces = max(0, open_braces - 1)
        elif char == "[":
            open_brackets += 1
        elif char == "]":
            open_brackets = max(0, open_brackets - 1)
    return stripped + ("]" * open_brackets) + ("}" * open_braces)


def _attempts(text: str) -> List[Tuple[str, str]]:
    """Ordered (name, transformed) repair attempts, cheapest first."""
    raw = text or ""
    return [
        ("direct", raw),
        ("strip_fences", _strip_fences(raw)),
        ("extract_block", _extract_block(raw)),
        ("trailing_commas", _remove_trailing_commas(_extract_block(_strip_fences(raw)))),
        ("close_truncated", _close_truncated(_extract_block(_strip_fences(raw)))),
    ]


def loads_lenient(text: str) -> Tuple[bool, Any, str]:
    """Parse JSON with repairs. Returns ``(ok, value, strategy_used)``."""
    for name, candidate in _attempts(text):
        if not candidate:
            continue
        try:
            return True, json.loads(candidate), name
        except (ValueError, TypeError):
            continue
    return False, None, ""


def repair(text: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse, repair if needed, and optionally validate against a JSON schema."""
    ok, value, strategy = loads_lenient(text)
    if not ok:
        return {"ok": False, "value": None, "repaired": False, "strategy": "",
                "error": "could not parse the output as JSON"}

    repaired = strategy != "direct"
    errors: List[str] = []
    if schema is not None:
        try:
            import jsonschema  # type: ignore
        except ImportError:
            errors.append("jsonschema not installed — schema not enforced")
        else:
            try:
                jsonschema.validate(value, schema)
            except Exception as exc:
                errors.append(str(exc)[:300])
                return {"ok": False, "value": value, "repaired": repaired,
                        "strategy": strategy, "error": "schema validation failed",
                        "errors": errors}

    return {"ok": True, "value": value, "repaired": repaired, "strategy": strategy,
            "errors": errors}
