"""P0: read the registered GENIE install location (read-only).

The NSIS installer remembers the install directory in the per-user uninstall
key. A later install into a DIFFERENT directory still runs that remembered
uninstaller, which deletes the previously installed tree. Reading this before
an install is how the harness records (rather than discovers after the fact)
which directory is about to be replaced.
"""
from __future__ import annotations

import json
import sys
import winreg
from typing import Any, Dict, List, Optional

UNINSTALL_BASE = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
FIELDS = ("DisplayName", "InstallLocation", "UninstallString",
          "DisplayVersion", "Publisher", "QuietUninstallString")


def _read_values(hive: int, path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with winreg.OpenKey(hive, path) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    out[name] = str(value)
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def genie_entries() -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for hive, label in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                        (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        try:
            with winreg.OpenKey(hive, UNINSTALL_BASE) as base:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(base, i)
                        i += 1
                    except OSError:
                        break
                    values = _read_values(hive, UNINSTALL_BASE + "\\" + name)
                    display = str(values.get("DisplayName", ""))
                    if "genie" not in display.lower() and "genie" not in name.lower():
                        continue
                    found.append({"hive": label, "key": name,
                                  **{f: values.get(f) for f in FIELDS}})
        except OSError:
            continue
    return found


def registered_install_dir() -> Optional[str]:
    """The install directory the next installer will uninstall first.

    InstallLocation is not always populated, so fall back to the directory
    that holds the registered Uninstall executable - that is the tree the
    installer deletes before unpacking the new one.
    """
    for entry in genie_entries():
        loc = entry.get("InstallLocation")
        if loc:
            return loc
    for entry in genie_entries():
        raw = (entry.get("UninstallString") or "").strip()
        # form: "C:\path\Uninstall GENIE.exe" /currentuser
        exe = ""
        if raw.startswith('"'):
            end = raw.find('"', 1)
            if end > 1:
                exe = raw[1:end]
        else:
            idx = raw.lower().find(".exe")
            if idx > 0:
                exe = raw[:idx + 4]
        if exe.lower().endswith(".exe"):
            import os
            return os.path.dirname(exe)
    return None


def main() -> int:
    print(json.dumps({"entries": genie_entries(),
                      "registered_install_dir": registered_install_dir()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
