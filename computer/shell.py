"""Hardened shell execution (computer/shell).

Two tiers:
  * `safe`     — read-only / informational commands only (allowlist)
  * `elevated` — anything else; requires an explicit PTE scope + confirmation upstream

Every run has a timeout, captured stdout/stderr, exit code and a command hash for the audit
trail. Destructive patterns are refused outright, independent of permissions.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger

log = get_logger("computer.shell")

IS_WINDOWS = sys.platform.startswith("win")

# Refused regardless of grants (defence in depth, not a permission substitute)
FORBIDDEN_PATTERNS = [
    # word-boundary, and never a PowerShell parameter like -Format (A-032)
    r"(?<![-\w])format(?![-\w])", r"(?<![-\w])mkfs(?![-\w])", r"(?<![-\w])diskpart(?![-\w])",
    r"Remove-Item\s+-Recurse\s+-Force\s+[A-Z]:\\?$",
    r"\brm\s+-rf\s+/(?!\w)", r"vssadmin\s+delete", r"bcdedit", r"\bcipher\s+/w",
    r"Stop-Computer", r"Restart-Computer", r"shutdown\s+/[rs]",
    r"Set-ExecutionPolicy\s+Unrestricted", r"reg\s+delete\s+HKLM",
    r"takeown\s+/f\s+[A-Z]:\\Windows", r"icacls\s+[A-Z]:\\Windows",
]

SAFE_ALLOWLIST = [
    r"^Get-\w+", r"^dir\b", r"^ls\b", r"^type\b", r"^where\b", r"^whoami\b",
    r"^echo\b", r"^hostname\b", r"^ipconfig\b", r"^ping\b", r"^tasklist\b",
    r"^systeminfo\b", r"^ver\b", r"^date\b", r"^time\b", r"^set\b$", r"^cd\b",
    r"^Test-Path\b", r"^Select-String\b", r"^Measure-Object\b", r"^ConvertTo-Json\b",
    r"^Get-ChildItem\b", r"^Get-Process\b", r"^Get-Service\b", r"^Get-Item\b",
    r"^Get-Content\b", r"^Get-Volume\b", r"^Get-CimInstance\b", r"^Get-ComputerInfo\b",
]


@dataclass
class ShellResult:
    ok: bool
    command: str
    shell: str
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    refused: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "command": self.command, "shell": self.shell,
                "exit_code": self.exit_code, "stdout": self.stdout[-4000:],
                "stderr": self.stderr[-2000:], "duration_ms": self.duration_ms,
                "refused": self.refused, "reason": self.reason}


def is_forbidden(command: str) -> Optional[str]:
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, command, re.I):
            return f"refused by safety policy: {pattern}"
    return None


def is_safe(command: str) -> bool:
    cmd = command.strip()
    return any(re.match(p, cmd, re.I) for p in SAFE_ALLOWLIST)


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]


def run(command: str, *, shell: str = "powershell", timeout_s: int = 30,
        cwd: str | None = None, tier: str = "safe",
        env: Optional[Dict[str, str]] = None) -> ShellResult:
    """Execute a command. `tier='elevated'` skips the allowlist (permission is checked
    upstream by the PTE) but never skips the forbidden-pattern policy."""
    import time

    blocked = is_forbidden(command)
    if blocked:
        log.warning("shell refused: %s", blocked)
        return ShellResult(ok=False, command=command, shell=shell, refused=True,
                           reason=blocked)

    if tier == "safe" and not is_safe(command):
        return ShellResult(ok=False, command=command, shell=shell, refused=True,
                           reason="command is not in the safe allowlist (needs elevated grant)")

    if shell == "powershell":
        args = ["powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command]
    elif shell == "cmd":
        args = ["cmd", "/c", command]
    else:
        return ShellResult(ok=False, command=command, shell=shell,
                           reason=f"unknown shell {shell}")

    started = time.time()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s,
                              cwd=cwd, env=env)
        return ShellResult(ok=proc.returncode == 0, command=command, shell=shell,
                           exit_code=proc.returncode, stdout=proc.stdout or "",
                           stderr=proc.stderr or "",
                           duration_ms=int((time.time() - started) * 1000))
    except subprocess.TimeoutExpired:
        return ShellResult(ok=False, command=command, shell=shell,
                           duration_ms=int((time.time() - started) * 1000),
                           reason=f"timeout after {timeout_s}s")
    except FileNotFoundError as exc:
        return ShellResult(ok=False, command=command, shell=shell, reason=str(exc))
    except Exception as exc:
        return ShellResult(ok=False, command=command, shell=shell, reason=str(exc))


def run_powershell_json(script: str, timeout_s: int = 30, tier: str = "safe") -> Dict[str, Any]:
    """Run PowerShell and parse JSON output (used by the UIA bridge and diagnostics)."""
    import json
    wrapped = f"{script} | ConvertTo-Json -Depth 6 -Compress"
    res = run(wrapped, shell="powershell", timeout_s=timeout_s, tier=tier)
    if not res.ok:
        return {"ok": False, "error": res.reason or res.stderr, "result": res.to_dict()}
    raw = (res.stdout or "").strip()
    if not raw:
        return {"ok": True, "data": None}
    try:
        return {"ok": True, "data": json.loads(raw)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "non-JSON output", "raw": raw[:500]}
