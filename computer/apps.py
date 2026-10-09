"""Installed application discovery + launch-method cache (computer/apps).

GENIE does NOT hardcode a handful of apps. It discovers what is actually installed:

    1. Registry `App Paths`   (HKLM/HKCU ...\\CurrentVersion\\App Paths\\*.exe)
    2. Start Menu shortcuts   (.lnk files, launched through the shell)
    3. Common install roots   (Program Files, LocalAppData\\Programs, ...)

Successful launch methods are cached in the DB (`app_launch_cache`) so the second launch of
the same app is fast and reliable. A cached method that fails is demoted, not trusted.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import json
import re
import subprocess
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.logging_setup import get_logger

from . import windows_api as win

log = get_logger("computer.apps")

IS_WINDOWS = sys.platform.startswith("win")

START_MENU_DIRS = [
    r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
]
PROGRAM_ROOTS = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
]
APP_PATHS_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
]

# Friendly aliases -> executable names (still resolved through discovery, never hardcoded paths)
ALIASES: Dict[str, List[str]] = {
    "chrome": ["chrome.exe", "google chrome"],
    "browser": ["chrome.exe", "msedge.exe", "brave.exe", "firefox.exe"],
    "edge": ["msedge.exe"],
    "brave": ["brave.exe"],
    "firefox": ["firefox.exe"],
    "notepad": ["notepad.exe", "notepad"],
    "vscode": ["code.exe", "visual studio code"],
    "code": ["code.exe"],
    "blender": ["blender.exe"],
    "spotify": ["spotify.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "control panel": ["control.exe"],
    "terminal": ["wt.exe", "windowsterminal"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe", "pwsh.exe"],
    "calculator": ["calc.exe", "calculator"],
    "calc": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "steam": ["steam.exe"],
    "discord": ["discord.exe"],
    "vlc": ["vlc.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "outlook": ["outlook.exe"],
    "obs": ["obs64.exe", "obs32.exe"],
    "davinci": ["resolve.exe"],
}


@dataclass
class AppEntry:
    name: str
    target: str                 # exe path or .lnk path
    method: str                 # exe | lnk | appsfolder
    source: str                 # registry | start_menu | program_files | alias
    exe: str = ""               # process image name when known

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "target": self.target, "method": self.method,
                "source": self.source, "exe": self.exe}


_CACHE: Optional[List[AppEntry]] = None
_CACHE_AT = 0.0
CACHE_TTL_S = 300


# ------------------------------------------------------------------- discovery
def _registry_apps() -> List[AppEntry]:
    if not IS_WINDOWS:
        return []
    import winreg
    out: List[AppEntry] = []
    for root, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ),
                       (winreg.HKEY_CURRENT_USER, winreg.KEY_READ)):
        for sub in APP_PATHS_KEYS:
            try:
                with winreg.OpenKey(root, sub, 0, flag) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    for i in range(count):
                        try:
                            exe_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, exe_name) as entry:
                                path, _ = winreg.QueryValueEx(entry, "")
                                if path and Path(path).exists():
                                    out.append(AppEntry(
                                        name=Path(exe_name).stem.lower(), target=path,
                                        method="exe", source="registry",
                                        exe=os.path.basename(path)))
                        except OSError:
                            continue
            except OSError:
                continue
    return out


def _start_menu_apps() -> List[AppEntry]:
    out: List[AppEntry] = []
    for root in START_MENU_DIRS:
        base = Path(root)
        if not base.exists():
            continue
        try:
            for lnk in base.rglob("*.lnk"):
                stem = lnk.stem.lower()
                if any(bad in stem for bad in ("uninstall", "readme", "help", "website",
                                               "license", "remove")):
                    continue
                out.append(AppEntry(name=stem, target=str(lnk), method="lnk",
                                    source="start_menu"))
        except OSError:
            continue
    return out


def _program_files_apps(limit: int = 400) -> List[AppEntry]:
    out: List[AppEntry] = []
    for root in PROGRAM_ROOTS:
        base = Path(root)
        if not base.exists():
            continue
        try:
            for exe in base.glob("*/*.exe"):
                if len(out) >= limit:
                    break
                out.append(AppEntry(name=exe.stem.lower(), target=str(exe), method="exe",
                                    source="program_files", exe=exe.name))
        except OSError:
            continue
    return out


def _registered_start_apps() -> List[AppEntry]:
    """Start registrations include MSIX apps that do not have .lnk shortcuts."""
    if not IS_WINDOWS:
        return []
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ConvertTo-Json -InputObject @(Get-StartApps | Select-Object Name,AppID) -Compress"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, encoding="utf-8", timeout=12,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return []
        rows = json.loads(result.stdout.lstrip("\ufeff") or "[]")
        return [AppEntry(str(row["Name"]), "shell:AppsFolder\\" + str(row["AppID"]),
                         "appsfolder", "start_registration") for row in rows
                if row.get("Name") and row.get("AppID")]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        log.debug("Start application registration unavailable: %s", type(exc).__name__)
        return []


def discover(force: bool = False) -> List[AppEntry]:
    """Full installed-application inventory (cached for CACHE_TTL_S)."""
    global _CACHE, _CACHE_AT
    now = time.time()
    if _CACHE is not None and not force and now - _CACHE_AT < CACHE_TTL_S:
        return _CACHE
    entries: List[AppEntry] = []
    seen: set = set()
    for batch in (_registry_apps(), _start_menu_apps(), _registered_start_apps(), _program_files_apps()):
        for e in batch:
            key = (e.name, e.target.lower())
            if key in seen:
                continue
            seen.add(key)
            entries.append(e)
    _CACHE, _CACHE_AT = entries, now
    log.info("discovered %s installed applications", len(entries))
    return entries


def invalidate_cache() -> None:
    global _CACHE
    _CACHE = None


# -------------------------------------------------------------------- matching
def _score(query: str, candidate: str) -> int:
    query = _normalized(query)
    candidate = _normalized(candidate)
    if candidate == query:
        return 100
    if candidate.startswith(query):
        return 80
    if query in candidate:
        return 60
    # token overlap (handles "visual studio code" vs "code")
    q_tokens = set(query.replace("-", " ").split())
    c_tokens = set(candidate.replace("-", " ").split())
    overlap = len(q_tokens & c_tokens)
    if overlap:
        return 30 + overlap * 5
    ratio = SequenceMatcher(None, query, candidate).ratio()
    return int(ratio * 75) if len(query) >= 5 and ratio >= .84 else 0


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold().removesuffix(".exe")))


def resolve_candidates(name: str, refresh: bool = False) -> List[AppEntry]:
    query = _normalized(name)
    if not query:
        return []
    queries = [query] + [_normalized(Path(a).stem) for a in ALIASES.get(query, [])]
    # Product suffixes such as "AI" are not distinctive enough to choose an app.
    if query.endswith(" ai"):
        queries.append(query[:-3])
    ranked = []
    for entry in discover(force=refresh):
        score = max(_score(q, candidate) for q in queries
                    for candidate in (entry.name, Path(entry.exe).stem if entry.exe else entry.name))
        if score >= 60:
            ranked.append((score, entry))
    ranked.sort(key=lambda pair: (-pair[0], {"lnk": 0, "appsfolder": 1, "exe": 2}.get(pair[1].method, 3)))
    if not ranked:
        return []
    best = ranked[0][0]
    results, seen = [], set()
    for score, entry in ranked:
        if score < best - 5:
            continue
        key = _normalized(entry.name)
        if key not in seen:
            seen.add(key)
            results.append(entry)
    return results[:8]


def resolve(name: str) -> Optional[AppEntry]:
    """Resolve a user-provided app name to a concrete launch target."""
    if not name:
        return None
    query = name.strip().lower()
    query = Path(query).stem if query.endswith(".exe") else query

    matches = resolve_candidates(name)
    if not matches:
        matches = resolve_candidates(name, refresh=True)
    which = _which_exe(query)
    if not which:
        for alias in ALIASES.get(query, []):
            if alias.endswith(".exe"):
                which = _which_exe(Path(alias).stem)
                if which:
                    break
    if which:
        return AppEntry(name=query, target=which, method="exe", source="path", exe=os.path.basename(which))
    if len(matches) == 1:
        return matches[0]
    return None


def _which_exe(name: str) -> str:
    """Windows system directories first, then PATH.

    A-057: the order matters. Git-for-Windows (and similar POSIX toolchains) put a
    `notepad`, `sort`, `find`, `time`, `more` … into `usr/bin`, and those shadow the real
    Windows built-ins for any process launched from that shell. Some of them are console
    programs that block on stdin, so launching one silently hangs the caller forever.
    A Windows system app must therefore always win over a generic PATH hit.
    """
    import shutil
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    for root in (os.path.join(system_root, "System32"), system_root):
        candidate = Path(root) / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    for candidate in (name, f"{name}.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    return ""


# --------------------------------------------------------------------- launching
def launch(entry: AppEntry) -> Dict[str, Any]:
    """Launch using the entry's method. Returns a result dict; verification is the
    verifier's job, not this function's."""
    if not IS_WINDOWS:
        return {"ok": False, "error": "windows only"}
    try:
        if entry.method == "exe":
            # ShellExecuteW gives us UAC/association behaviour without a console window
            rc = ctypes.windll.shell32.ShellExecuteW(None, "open", entry.target, None, None, 1)
            if int(rc) <= 32:
                return {"ok": False, "error": f"ShellExecuteW rc={rc}"}
            return {"ok": True, "method": "exe", "target": entry.target}
        if entry.method == "lnk":
            rc = ctypes.windll.shell32.ShellExecuteW(None, "open", entry.target, None, None, 1)
            if int(rc) <= 32:
                return {"ok": False, "error": f"ShellExecuteW(lnk) rc={rc}"}
            return {"ok": True, "method": "lnk", "target": entry.target}
        if entry.method == "appsfolder":
            rc = ctypes.windll.shell32.ShellExecuteW(None, "open", entry.target, None, None, 1)
            return {"ok": int(rc) > 32, "method": "appsfolder", "target": entry.target}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"unknown method {entry.method}"}


def expected_process(entry: AppEntry) -> str:
    """The process image name we should look for when verifying a launch."""
    if entry.exe:
        return entry.exe
    if entry.method == "exe":
        return os.path.basename(entry.target)
    if entry.method == "lnk":
        return _lnk_target_name(entry.target)
    return ""


def _lnk_target_name(lnk_path: str) -> str:
    """Best-effort: read the .lnk target name from the binary shell-link header.

    We do not implement the full Shell Link format — we only need a process name to look
    for. When it cannot be determined, the verifier falls back to window-title matching.
    """
    try:
        data = Path(lnk_path).read_bytes()
    except OSError:
        return ""
    if len(data) < 76 or data[:4] != b"L\x00\x00\x00":
        return ""
    # LinkTargetIDList follows; the executable name is usually an ASCII/UTF-16 substring
    for enc, needle in (("utf-16-le", ".exe"), ("latin-1", ".exe")):
        try:
            text = data.decode(enc, errors="ignore")
        except Exception:
            continue
        idx = text.lower().find(".exe")
        if idx > 0:
            start = max(text.rfind("\\", 0, idx), text.rfind("\x00", 0, idx))
            name = text[start + 1:idx + 4].strip()
            if name and len(name) < 60:
                return os.path.basename(name)
    return ""


# ---------------------------------------------------------------- launch cache
class LaunchCache:
    """Remembers which launch method actually worked for an app name."""

    def __init__(self, db):
        self.db = db

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one(
            "SELECT * FROM app_launch_cache WHERE name=? AND ok_count > fail_count"
            " ORDER BY ok_count DESC LIMIT 1", (name.lower(),))
        return dict(row) if row else None

    def record(self, name: str, method: str, target: str, ok: bool) -> None:
        name = name.lower()
        row = self.db.query_one(
            "SELECT * FROM app_launch_cache WHERE name=? AND method=? AND target=?",
            (name, method, target))
        if row:
            col = "ok_count" if ok else "fail_count"
            self.db.execute(
                f"UPDATE app_launch_cache SET {col} = {col} + 1,"
                " last_ok = CASE WHEN ? THEN strftime('%s','now') ELSE last_ok END"
                " WHERE name=? AND method=? AND target=?",
                (1 if ok else 0, name, method, target))
        else:
            self.db.execute(
                "INSERT INTO app_launch_cache(name, method, target, ok_count, fail_count,"
                " last_ok) VALUES(?,?,?,?,?,?)",
                (name, method, target, 1 if ok else 0, 0 if ok else 1,
                 int(time.time()) if ok else 0))

    def all(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM app_launch_cache ORDER BY ok_count DESC")]
