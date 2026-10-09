"""Static security scanner for installed / installable skills (`security/skill_scanner.py`).

This is the check the skill-install flow is supposed to run *before* a skill is trusted or
activated. It scans a skill directory (`SKILL.md` + `scripts/`, `references/`, `assets/`) for
dangerous patterns and reports them with file + line.

It is **static analysis only** — it never executes skill code. It looks for markers that have
historically corresponded to destructive or exfiltrating behaviour:

  * destructive filesystem ops        -> `rm -rf`, `del /S /Q`, `shutil.rmtree`, fork bomb, `dd` to /dev
  * remote code execution            -> `curl ... | sh`, `wget ... | sh`, `os.system`,
                                        `subprocess(..., shell=True)`, `iex` / `Invoke-Expression`
  * committed secrets                -> private-key header, AWS key, hardcoded api/token/password
  * prompt-injection markers         -> "ignore previous instructions" style overrides
  * network egress (informational)   -> raw URLs in code files

Policy (mirrors the install gate):
  * BLOCK   (P0) — must not be installed/activated without an explicit, logged override
  * CONFIRM (P1) — requires explicit user confirmation
  * OK      (P2) — informational only

The scanner is deliberately conservative: it reports *what it matched*, with file + line, and
never asserts a skill is absolutely safe — only "no known-dangerous pattern matched". A clean
scan is necessary, not sufficient: novel or logic-based abuse is out of scope for regex.

Severity vocabulary is shared with `security/findings.py` (info/low/medium/high/critical) so a
scan can be promoted into a canonical `Finding` for the security pipeline.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:  # keep the module usable even if the app logging stack is unavailable
    from core.logging_setup import get_logger
    log = get_logger("security.skill_scanner")
except Exception:  # pragma: no cover - only when run outside the app
    import logging
    log = logging.getLogger("security.skill_scanner")

# --------------------------------------------------------------------------- #
# Policy + severity vocabulary
# --------------------------------------------------------------------------- #
BLOCK = "BLOCK"        # P0
CONFIRM = "CONFIRM"    # P1
OK = "OK"              # P2

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# Files we attempt to read as text. Anything else is treated as binary and skipped.
TEXT_EXTS = {
    ".md", ".py", ".sh", ".ps1", ".js", ".ts", ".jsx", ".tsx", ".yaml", ".yml",
    ".json", ".toml", ".txt", ".cfg", ".ini", ".env", ".bash", ".zsh", ".bat",
    ".rb", ".pl", ".r", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".html",
    ".css", ".sql",
    # secret-bearing extensions that must be scanned, not skipped as "binary"
    ".pem", ".key", ".crt", ".cer", ".pfx", ".der", ".conf", ".env.example",
}
# Extensions we never read (binary / archival).
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".mp3", ".wav", ".mp4",
    ".avi", ".mov", ".mkv", ".zip", ".gz", ".tar", ".bz2", ".7z", ".xz", ".rar",
    ".pdf", ".pkl", ".pickle", ".pyc", ".bin", ".exe", ".dll", ".so", ".dylib",
    ".otf", ".ttf", ".woff", ".woff2", ".db", ".sqlite", ".pack",
}
# Code files where a raw URL is worth flagging as egress (prose in .md is normal).
CODE_EXTS = {
    ".py", ".sh", ".ps1", ".js", ".ts", ".jsx", ".tsx", ".bash", ".zsh", ".bat",
    ".rb", ".pl", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".sql", ".html",
}


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass
class SkillFinding:
    """One matched dangerous pattern in a skill file."""

    rule_id: str
    severity: str
    policy: str
    reason: str
    file: str
    line: int
    snippet: str
    cwe: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "policy": self.policy,
            "reason": self.reason,
            "file": self.file,
            "line": str(self.line),
            "snippet": self.snippet,
            "cwe": self.cwe,
        }


@dataclass
class ScanResult:
    """Outcome of scanning one skill directory."""

    skill_path: str
    findings: List[SkillFinding] = field(default_factory=list)
    scanned_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """True if any P0 (BLOCK) pattern matched -> install must be refused."""
        return any(f.policy == BLOCK for f in self.findings)

    @property
    def requires_confirmation(self) -> bool:
        """True if only P1 (CONFIRM) patterns matched -> install needs explicit OK."""
        return (any(f.policy == CONFIRM for f in self.findings)
                and not self.blocked)

    @property
    def highest_severity(self) -> str:
        if not self.findings:
            return "info"
        return max(self.findings,
                   key=lambda f: SEVERITY_RANK.get(f.severity, 0)).severity

    def verdict(self) -> str:
        if self.blocked:
            return BLOCK
        if self.requires_confirmation:
            return CONFIRM
        return OK


# --------------------------------------------------------------------------- #
# Rule set
# --------------------------------------------------------------------------- #
# Each rule: id, severity, policy, reason, cwe, pattern, flags, code_only.
# `code_only` rules only fire inside CODE_EXTS files (so doc URLs in SKILL.md are ignored).
def _rule(rule_id, severity, policy, reason, pattern, cwe="", flags=0, code_only=False):
    return {
        "id": rule_id, "severity": severity, "policy": policy, "reason": reason,
        "cwe": cwe, "pattern": re.compile(pattern, flags), "code_only": code_only,
    }


RULES = [
    # ---- P0: destructive filesystem / process (BLOCK) ----
    _rule("DESTRUCT_RM_RF", "critical", BLOCK,
          "Recursive force delete (rm -rf) can wipe the workspace or tree.",
          r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*\b|"
          r"\brm\s+-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*\b|"
          r"\brm\s+-[rR]\s+-[fF]\b|\brm\s+-[fF]\s+-[rR]\b",
          cwe="CWE-22"),
    _rule("DESTRUCT_DEL_SQ", "critical", BLOCK,
          "Windows recursive/quiet delete (del /S /Q) can wipe the tree.",
          r"\bdel\s+/[SsQq]\b(?:\s+/[SsQq]\b)?", cwe="CWE-22"),
    _rule("DESTRUCT_RMTREE", "critical", BLOCK,
          "Python recursive delete (shutil.rmtree) wipes directories.",
          r"shutil\s*\.\s*rmtree\s*\(", cwe="CWE-22"),
    _rule("DESTRUCT_FORKBOMB", "critical", BLOCK,
          "Fork bomb (:(){:|:&};:) exhausts process/thread limits.",
          r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}", cwe="CWE-400"),
    _rule("DESTRUCT_DD_DEV", "critical", BLOCK,
          "dd writing to a device node (/dev/...) can destroy disks.",
          r"\bdd\s+if=[^\n]*\bof=/dev/", cwe="CWE-22"),

    # ---- P1: remote code execution (CONFIRM) ----
    _rule("RCE_PIPE_SHELL", "high", CONFIRM,
          "Downloads code and pipes it straight into a shell (curl|sh, wget|sh).",
          r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b", cwe="CWE-494"),
    _rule("RCE_OS_SYSTEM", "high", CONFIRM,
          "os.system() executes a shell string — command injection surface.",
          r"\bos\.system\s*\(", cwe="CWE-78"),
    _rule("RCE_SUBPROCESS_SHELL", "high", CONFIRM,
          "subprocess with shell=True passes a string to the shell.",
          r"subprocess\.[A-Za-z_]+\s*\([^)]*?shell\s*=\s*True", cwe="CWE-78"),
    _rule("RCE_PS_EXEC", "high", CONFIRM,
          "PowerShell code execution (iex / Invoke-Expression / -enc).",
          r"\biex\s*\(|Invoke-Expression\b|powershell\s+-[eE]nc\b", cwe="CWE-78"),
    _rule("RCE_EVAL_EXEC", "medium", CONFIRM,
          "eval()/exec() on a string — dynamic execution surface.",
          r"\b(?:eval|exec)\s*\(", cwe="CWE-95"),

    # ---- P1: committed secrets (CONFIRM) ----
    _rule("SECRET_PRIVATE_KEY", "critical", CONFIRM,
          "Private-key material committed in the skill — must not ship.",
          r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----",
          cwe="CWE-321"),
    _rule("SECRET_AWS_KEY", "high", CONFIRM,
          "Looks like an AWS access key id (AKIA/ASIA + 16 chars).",
          r"(?:AKIA|ASIA)[0-9A-Z]{16}", cwe="CWE-798"),
    _rule("SECRET_HARDCODED", "medium", CONFIRM,
          "Hardcoded credential (api/token/password) >= 16 chars; not a placeholder.",
          r"(?:api[_-]?key|apikey|secret|token|access[_-]?token|password|passwd|pwd|"
          r"client[_-]?secret)\s*[:=]\s*['\"]([A-Za-z0-9_./+=-]{16,})['\"]",
          cwe="CWE-798"),

    # ---- P1: prompt injection (CONFIRM) ----
    _rule("INJECT_IGNORE_INSTRUCTIONS", "medium", CONFIRM,
          "Instruction-override marker (ignore previous/above instructions).",
          r"ignore\s+(?:all\s+|the\s+|any\s+|your\s+)?(?:previous|prior|above|earlier|"
          r"preceding)\s+instructions|disregard\s+(?:the\s+)?(?:previous|above|prior)\s+"
          r"instructions",
          flags=re.IGNORECASE, cwe="CWE-1427"),

    # ---- P2: informational egress (OK) ----
    _rule("EGRESS_URL", "info", OK,
          "Network egress reference in code — review the destination.",
          r"https?://[^\s'\"\)>]+", code_only=True, cwe="CWE-200"),
]

_PLACEHOLDER_TOKENS = {
    "your", "example", "placeholder", "xxxx", "changeme", "todo", "fixme",
    "change", "sample", "demo", "test", "replace", "insert", "none",
}


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def _iter_text_files(skill_dir: str):
    skill_dir = os.path.abspath(skill_dir)
    if not os.path.isdir(skill_dir):
        return
    for root, _dirs, files in os.walk(skill_dir):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXTS:
                continue
            if ext and ext not in TEXT_EXTS:
                continue  # unknown extension -> treat as binary, skip
            yield os.path.join(root, name)


def _rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:  # pragma: no cover - different drives
        return path


def scan_skill(skill_dir: str) -> ScanResult:
    """Scan a skill directory and return a :class:`ScanResult`."""
    base = os.path.abspath(skill_dir)
    result = ScanResult(skill_path=base)

    for path in _iter_text_files(base):
        rel = _rel(path, base)
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError) as exc:
            # A genuinely binary file slipped past extension filtering -> skip silently.
            # A text file we expected to read but couldn't -> record for visibility.
            if ext in TEXT_EXTS:
                result.errors.append(f"{rel}: unreadable ({exc})")
            continue

        result.scanned_files.append(rel)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rule in RULES:
                if rule["code_only"] and ext not in CODE_EXTS:
                    continue
                m = rule["pattern"].search(line)
                if not m:
                    continue
                # For the hardcoded-secret rule, drop obvious placeholders.
                if rule["id"] == "SECRET_HARDCODED" and m.group(1):
                    value = m.group(1).lower()
                    if any(tok in value for tok in _PLACEHOLDER_TOKENS):
                        continue
                snippet = line.strip()[:200]
                result.findings.append(SkillFinding(
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    policy=rule["policy"],
                    reason=rule["reason"],
                    file=rel,
                    line=lineno,
                    snippet=snippet,
                    cwe=rule["cwe"],
                ))
    return result


# Alias used by the install flow.
audit = scan_skill


def format_report(result: ScanResult) -> str:
    """Human-readable report for the install flow / logs."""
    lines = [f"Skill scan: {result.skill_path}",
             f"  verdict: {result.verdict()}  (highest: {result.highest_severity})",
             f"  scanned {len(result.scanned_files)} file(s), "
             f"{len(result.findings)} finding(s)"]
    if result.errors:
        lines.append("  read errors:")
        lines += [f"    - {e}" for e in result.errors]
    for f in sorted(result.findings,
                    key=lambda x: (-SEVERITY_RANK.get(x.severity, 0), x.file, x.line)):
        lines.append(f"  [{f.policy}] {f.severity.upper()} {f.rule_id} "
                     f"{f.file}:{f.line} {f.cwe} — {f.reason}")
        lines.append(f"      > {f.snippet}")
    return "\n".join(lines)


def to_security_findings(result: ScanResult, target: str = ""):
    """Promote scan findings into canonical :class:`security.findings.Finding` records.

    Lazy import so this module stays usable without the full app stack. Returns a list of
    Findings (empty if the security pipeline is unavailable).
    """
    try:
        from security.findings import Finding  # noqa: F401  (imported for type/clarity)
    except Exception:  # pragma: no cover
        log.warning("security.findings unavailable; cannot promote scan findings")
        return []
    from security.findings import Finding

    policy_to_status = {BLOCK: "confirmed", CONFIRM: "triaged", OK: "new"}
    out = []
    for f in result.findings:
        out.append(Finding(
            target=target or result.skill_path,
            title=f.rule_id,
            finding_class="skill-static-scan",
            severity=f.severity,
            cwe=f.cwe,
            evidence=f"{f.file}:{f.line} — {f.snippet}",
            remediation=f.reason,
            status=policy_to_status.get(f.policy, "new"),
        ))
    return out
