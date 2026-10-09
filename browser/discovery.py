"""Discover registered Windows browsers without assuming the installed brands."""
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


def installed():
    if os.name != "nt":
        return []
    import winreg
    found = {}
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for base in (r"SOFTWARE\Clients\StartMenuInternet", r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet"):
            try:
                with winreg.OpenKey(hive, base) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        key = winreg.EnumKey(root, i)
                        try:
                            with winreg.OpenKey(root, key) as entry:
                                name = winreg.QueryValueEx(entry, "")[0]
                            with winreg.OpenKey(root, key + r"\shell\open\command") as command_key:
                                command = os.path.expandvars(winreg.QueryValueEx(command_key, "")[0]).strip()
                            # Registry launch commands are not sent to a shell. Extract only the executable.
                            exe = command[1:].split('"', 1)[0] if command.startswith('"') else command.split(" ", 1)[0]
                            if Path(exe).is_file() and exe.lower().endswith(".exe"):
                                found[exe.casefold()] = {"name": str(name), "registration": key, "executable": exe}
                        except OSError:
                            continue
            except OSError:
                continue
    return list(found.values())


def open_named(params):
    url = str(params.get("url", ""))
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return {"ok": False, "error": "An HTTP(S) URL without credentials is required."}
    query = str(params.get("browser", "")).strip().casefold()
    matches = [b for b in installed() if query and (query in b["name"].casefold() or
               query == Path(b["executable"]).stem.casefold() or query == b["registration"].casefold())]
    if len(matches) != 1:
        return {"ok": False, "error": "Choose one registered browser by its exact name.",
                "candidates": [b["name"] for b in (matches or installed())]}
    browser = matches[0]
    try:
        subprocess.Popen([browser["executable"], url], close_fds=True)
    except OSError:
        return {"ok": False, "error": "Windows could not start the requested browser."}
    return {"ok": True, "url": url, "browser": browser["name"], "page_verified": False,
            "verify": {"verified": True, "delivery_only": True, "scope": "browser-url-handoff",
                       "detail": "URL handed to the selected browser without a GENIE profile override. Page loading is not verified."}}
