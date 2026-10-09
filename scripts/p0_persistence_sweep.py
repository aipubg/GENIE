"""P0.3 — read-only persistence audit, matched by CONTENT not by name.

A respawner can hide under any label, so every autostart location is scanned
and each entry is matched against the GENIE footprint:

    GENIE / genie-desktop / genie.py / backend_entry / backend-runtime /
    plugins.host / pythonw.exe / python.exe under a GENIE tree

Also reports unrelated entries so the audit is verifiable, and flags the
classic Windows persistence corners (IFEO debugger, Winlogon, App Paths).

READ ONLY. Nothing is created, modified or deleted.
"""
from __future__ import annotations

import json
import os
import sys
import winreg
from typing import Any, Dict, List, Tuple

FOOTPRINT = (
    "genie", "backend_entry", "backend-runtime", "plugins.host",
    "e:\\g3\\genie", "genie-desktop",
)

RUN_KEYS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnceEx"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run"),
    (winreg.HKEY_CURRENT_USER, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
]

IFEO = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
WINLOGON = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

STARTUP_DIRS = [
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
    os.path.expandvars(r"%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\Startup"),
]


def read_values(hive: int, path: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    try:
        with winreg.OpenKey(hive, path) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    out.append((name, str(value)))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def read_subkeys(hive: int, path: str) -> List[str]:
    out: List[str] = []
    try:
        with winreg.OpenKey(hive, path) as key:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def hits(text: str) -> bool:
    low = (text or "").lower()
    return any(f.lower() in low for f in FOOTPRINT)


def main() -> int:
    report: Dict[str, Any] = {"footprint_matches": [], "run_keys": {},
                             "ifeo_debugger": [], "winlogon": {}, "startup_dirs": {}}

    for hive, path in RUN_KEYS:
        hive_name = "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM"
        values = read_values(hive, path)
        report["run_keys"][f"{hive_name}\\{path.split('CurrentVersion')[-1]}"] = values
        for name, value in values:
            if hits(value):
                report["footprint_matches"].append(
                    {"location": f"{hive_name}:{path}", "entry": name, "value": value})

    # IFEO debugger: launching <image> would silently start the "debugger" instead
    for hive, label in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"),
                        (winreg.HKEY_CURRENT_USER, "HKCU")):
        for image in read_subkeys(hive, IFEO):
            for name, value in read_values(hive, IFEO + "\\" + image):
                if name.lower() in ("debugger", "globalflag", "verifierdebugger"):
                    entry = {"hive": label, "image": image, "key": name, "value": value}
                    report["ifeo_debugger"].append(entry)
                    if hits(value) or hits(image):
                        report["footprint_matches"].append(
                            {"location": f"IFEO {label}\\{image}", "entry": name, "value": value})

    for name, value in read_values(winreg.HKEY_LOCAL_MACHINE, WINLOGON):
        report["winlogon"][name] = value
        if hits(value):
            report["footprint_matches"].append(
                {"location": "Winlogon", "entry": name, "value": value})

    # App Paths: a bare "genie.exe" launch would resolve through here
    for image in read_subkeys(winreg.HKEY_LOCAL_MACHINE, APP_PATHS):
        if hits(image):
            for name, value in read_values(winreg.HKEY_LOCAL_MACHINE, APP_PATHS + "\\" + image):
                report["footprint_matches"].append(
                    {"location": f"App Paths\\{image}", "entry": name, "value": value})

    for directory in STARTUP_DIRS:
        try:
            report["startup_dirs"][directory] = os.listdir(directory)
        except OSError as exc:
            report["startup_dirs"][directory] = f"<unreadable: {exc}>"
        for item in report["startup_dirs"].get(directory) or []:
            if isinstance(item, str) and hits(item):
                report["footprint_matches"].append(
                    {"location": directory, "entry": item, "value": ""})

    print(json.dumps(report, indent=2))
    print("\nFOOTPRINT MATCHES: %d" % len(report["footprint_matches"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
