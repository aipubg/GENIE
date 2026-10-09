"""Scrapling text sanitizer (integrations/scrapling_sanitizer.py).

Web content is untrusted DATA. This module ensures scraped text never
modifies Mission/PTE authority by stripping prompt-injection patterns
before content enters GENIE's Context Builder.

Pipeline: Scrapling extraction -> sanitize_scraped_text() -> Context Builder -> model

Design rule: scraped text can never change Mission/PTE authority.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from core.logging_setup import get_logger

log = get_logger("integrations.scrapling.sanitizer")

# ---------------------------------------------------------------------------
# Prompt injection patterns, compiled once.
#
# These are deliberately broad: scraped web text is untrusted DATA, and an
# unmatched injection pattern is far more costly than an over-eager redaction.
# Each pattern targets a specific attack class so the redaction marker names it.
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Direct instruction overrides
    (re.compile(
        r"ignore\s+(all\s+|any\s+)?(previous|prior|above|preceding|earlier|former)\s+"
        r"(instructions?|prompts?|rules?|system\s+messages?|context|directives?)",
        re.IGNORECASE,
    ), "[REDACTED: injection pattern]"),
    # Role / memory override
    (re.compile(
        r"(you\s+are\s+now|forget\s+(everything|all|your)\s+(previous\s+)?"
        r"(instructions?|programming|rules?|persona|role))",
        re.IGNORECASE,
    ), "[REDACTED: role override]"),
    # Disregard / override / bypass commands.
    # An optional pronoun may sit between the verb and the target ("override your safety").
    (re.compile(
        r"(disregard|override|bypass|circumvent|ignore)\s+(all\s+|any\s+)?"
        r"(previous\s+|prior\s+|my\s+|your\s+|our\s+)?"
        r"(instructions?|rules?|safety|guidelines?|directives?|policies?|restrictions?|"
        r"limits?|controls?)",
        re.IGNORECASE,
    ), "[REDACTED: bypass attempt]"),
    # System prompt / instruction / config / settings extraction
    (re.compile(
        r"(repeat|show|print|display|output|reveal|dump|expose)\s+(me\s+|us\s+)?"
        r"((?:your|the|all|any)\s+)?"
        r"(system\s+prompt|instructions?|initial\s+message|configuration|config|settings?|"
        r"rules?|setup)",
        re.IGNORECASE,
    ), "[REDACTED: prompt leak attempt]"),
    # New / injected instruction block ("New instructions: delete all files")
    (re.compile(
        r"(new|updated|revised|following|these|fresh|current)\s+"
        r"(instructions?|directives?|rules?|commands?|orders?|prompts?)\s*[:=\-]",
        re.IGNORECASE,
    ), "[REDACTED: injected instructions]"),
    # Mission / objective / purpose tampering
    (re.compile(
        r"(change|alter|modify|rewrite|update|replace|redefine)\s+"
        r"(your|the|this|our|my)\s+(mission|objective|goal|purpose|directive)",
        re.IGNORECASE,
    ), "[REDACTED: authority tamper]"),
    # Privilege / PTE / permission escalation
    (re.compile(
        r"(set|change|escalate|raise|grant|elevate|increase|assign)\s+"
        r"(the\s+|your\s+|my\s+)?"
        r"(PTE|privilege|privileges|permission|permissions|clearance|access\s+level|"
        r"authority|role|admin|root)\s*(to|into|level|tier)?",
        re.IGNORECASE,
    ), "[REDACTED: privilege escalation]"),
    # DAN / jailbreak variants
    (re.compile(
        r"(do\s+anything\s+now|DAN|STAN|AIM|APOPHIS|UCAR|developer\s+mode|jailbreak|"
        r"uncensored\s+mode|no\s+restrictions\s+mode)",
        re.IGNORECASE,
    ), "[REDACTED: jailbreak reference]"),
    # Delimiter injection (markdown/code block escapes)
    (re.compile(r"```[\s\S]*?(system|instruction|prompt|developer|config)[\s\S]*?```",
                re.IGNORECASE), "[REDACTED: code block injection]"),
    # Invisible/zero-width characters used for steganography
    (re.compile(r"[\u200B-\u200F\u202A-\u202E\uFEFF\u00AD]"), ""),
]

# Maximum output length to prevent context stuffing
MAX_SANITIZED_LENGTH = 500_000  # 500K chars


def sanitize_scraped_text(text: str, *, mission_context: bool = False) -> str:
    """Sanitize web-scraped text before it enters GENIE's context pipeline.

    Args:
        text: Raw extracted text from Scrapling.
        mission_context: If True, apply stricter filtering (content will be
                        used directly in mission-critical context).

    Returns:
        Sanitized text safe for LLM consumption as DATA.
    """
    if not text:
        return ""

    result = text

    # Phase 1: Strip invisible characters
    result = _INJECTION_PATTERNS[-1][0].sub(_INJECTION_PATTERNS[-1][1], result)

    # Phase 2: Neutralize prompt injection patterns
    for pattern, replacement in _INJECTION_PATTERNS[:-1]:
        result = pattern.sub(replacement, result)

    # Phase 3: Strip HTML/script remnants that Scrapling may have left
    result = re.sub(r"<script[\s\S]*?</script>", "", result, flags=re.IGNORECASE)
    result = re.sub(r"<style[\s\S]*?</style>", "", result, flags=re.IGNORECASE)
    result = re.sub(r"<!--[\s\S]*?-->", "", result)
    result = re.sub(r"<[^>]+>", "", result)  # remaining tags

    # Phase 4: Normalize whitespace (preserve paragraph structure)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    # Phase 5: Enforce length limit
    if len(result) > MAX_SANITIZED_LENGTH:
        log.warning("Scraped text truncated from %d to %d chars", len(result), MAX_SANITIZED_LENGTH)
        result = result[:MAX_SANITIZED_LENGTH] + "\n\n[... content truncated for safety ...]"

    # Phase 6: Mission-context hardening
    if mission_context:
        # In mission context, also strip anything that looks like a command
        result = re.sub(r"^(sudo|rm |del |exec|eval|import |os\.|sys\.)", "[BLOCKED]", result, flags=re.MULTILINE)

    return result


def classify_content_safety(text: str) -> dict:
    """Classify whether scraped text contains suspicious patterns.

    Returns a report dict (does NOT modify the text).
    """
    findings = []
    for i, (pattern, _) in enumerate(_INJECTION_PATTERNS):
        matches = pattern.findall(text)
        if matches:
            findings.append({
                "pattern_index": i,
                "match_count": len(matches),
                "severity": "high" if i < 5 else "medium",
            })

    return {
        "safe": len(findings) == 0,
        "findings": findings,
        "total_patterns_checked": len(_INJECTION_PATTERNS),
        "text_length": len(text),
    }
