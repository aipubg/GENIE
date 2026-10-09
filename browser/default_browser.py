"""Windows URL handoff, distinct from a controllable GENIE-owned CDP tab."""
import os
import ctypes
from urllib.parse import urlsplit


def _association(protocol, kind):
    from ctypes import wintypes
    query = ctypes.WinDLL("shlwapi").AssocQueryStringW
    query.argtypes = [wintypes.DWORD, ctypes.c_int, wintypes.LPCWSTR, wintypes.LPCWSTR,
                      wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = ctypes.c_long
    size = wintypes.DWORD(0)
    query(0x1000, kind, protocol, "open", None, ctypes.byref(size))
    if not 0 < size.value <= 32768:
        return ""
    value = ctypes.create_unicode_buffer(size.value)
    return value.value if query(0x1000, kind, protocol, "open", value, ctypes.byref(size)) == 0 else ""


def defaults():
    """Resolve per-protocol Windows defaults without launching or changing them."""
    if os.name != "nt":
        return {"available": False, "reason": "Windows associations are unavailable on this platform."}
    associations = {}
    for protocol in ("http", "https"):
        try:
            executable = _association(protocol, 2)  # ASSOCSTR_EXECUTABLE
            name = _association(protocol, 4)  # ASSOCSTR_FRIENDLYAPPNAME
            associations[protocol] = {"name": name, "executable": executable,
                                      "resolved": bool(name or executable)}
        except OSError:
            associations[protocol] = {"resolved": False}
    return {"available": any(row["resolved"] for row in associations.values()),
            "source": "Windows AssocQueryStringW", "associations": associations}


def open_url(params):
    url = str(params.get("url", "")).strip()
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        return {"ok": False, "error": "An HTTP(S) URL without credentials is required."}
    if os.name != "nt":
        return {"ok": False, "error": "Windows default browser is unavailable on this platform."}
    try:
        os.startfile(url)
    except OSError:
        return {"ok": False, "error": "Windows could not open its default browser."}
    return {"ok": True, "url": url, "browser": "windows-default", "page_verified": False,
            "detail": "URL handed to the Windows default browser; page load is not verified.",
            "verify": {"verified": True, "delivery_only": True, "scope": "os-url-handoff",
                       "detail": "Windows accepted the URL handoff, not a page-load verification."}}
