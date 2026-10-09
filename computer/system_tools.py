"""Supported Windows settings navigation and read-only device inventory."""
import os
import re
import json
import subprocess


def open_settings(page):
    page = str(page or "").removeprefix("ms-settings:")
    if not re.fullmatch(r"[a-z0-9-]{0,80}", page):
        return {"ok": False, "error": "Use a Windows Settings page ID, not a command or script."}
    if os.name != "nt":
        return {"ok": False, "error": "Windows Settings is unavailable on this OS."}
    try:
        os.startfile("ms-settings:" + page)
    except OSError:
        return {"ok": False, "error": "Windows could not open this Settings page."}
    return {"ok": True, "page": page, "settings_changed": False,
            "verify": {"verified": True, "scope": "settings-url-handoff",
                       "detail": "Settings URI accepted; no setting has been changed."}}


def audio_devices():
    try:
        import sounddevice
        devices = sounddevice.query_devices()
        return {"ok": True, "devices": [
            {"id": i, "name": d["name"], "input_channels": d["max_input_channels"],
             "output_channels": d["max_output_channels"], "host_api": d["hostapi"]}
            for i, d in enumerate(devices)], "default": list(sounddevice.default.device)}
    except Exception:
        return {"ok": False, "error": "Audio device enumeration unavailable; check audio drivers and sounddevice."}


def network_state():
    if os.name != "nt":
        return {"ok": False, "error": "Windows network inventory is unavailable on this OS."}
    try:
        script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ConvertTo-Json -InputObject @(Get-NetAdapter | Select-Object Name,Status,LinkSpeed,InterfaceIndex) -Compress"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, encoding="utf-8", timeout=8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return {"ok": False, "error": "Windows network adapter query failed."}
        return {"ok": True, "interfaces": json.loads(result.stdout.lstrip("\ufeff") or "[]"),
                "note": "Link state only; this does not prove internet access or report Wi-Fi credentials."}
    except Exception:
        return {"ok": False, "error": "Network interface discovery unavailable."}
