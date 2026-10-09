"""Filesystem capability (computer/files).

Rules:
  * workspace-first: writes inside the GENIE workspace need no confirmation
  * anything outside the workspace requires an explicit PTE scope (checked by the caller)
  * writes are atomic (temp file + rename) so a crash never leaves a half-written file
  * every mutating operation returns enough information for the verifier to confirm it
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.logging_setup import get_logger

log = get_logger("computer.files")


def user_locations() -> Dict[str, str]:
    """Resolve redirected Windows folders, including OneDrive Desktop/Documents."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    locations = {name: str(home / name.title())
                 for name in ("desktop", "documents", "downloads", "pictures", "music", "videos")}
    if os.name == "nt":
        import winreg
        names = {"desktop": "Desktop", "documents": "Personal",
                 "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
                 "pictures": "My Pictures", "music": "My Music", "videos": "My Video"}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                for alias, value_name in names.items():
                    try:
                        value, _ = winreg.QueryValueEx(key, value_name)
                        expanded = os.path.expandvars(value)
                        if Path(expanded).is_absolute():
                            locations[alias] = expanded
                    except OSError:
                        continue
        except OSError:
            pass
    return locations


def resolve_user_location(value: str) -> str:
    """Accept a known user folder or an explicit absolute path, never guess a user name."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Choose a user folder or an absolute path.")
    value = value.strip()
    location = user_locations().get(value.lower(), value)
    if not Path(location).is_absolute():
        raise ValueError("Use desktop, documents, downloads, pictures, music, videos, or an absolute path.")
    return location


def _stat(path: Path) -> Dict[str, Any]:
    try:
        st = path.stat()
        return {"exists": True, "size": st.st_size, "mtime": int(st.st_mtime),
                "is_dir": path.is_dir()}
    except OSError:
        return {"exists": False, "size": 0, "mtime": 0, "is_dir": False}


def sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def exists(path: str | Path) -> bool:
    return Path(path).exists()


def read_text(path: str | Path, max_bytes: int = 2_000_000) -> Dict[str, Any]:
    p = Path(path)
    try:
        data = p.read_bytes()[:max_bytes]
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return {"ok": True, "text": data.decode(encoding), "path": str(p),
                        "bytes": len(data)}
            except UnicodeDecodeError:
                continue
        return {"ok": False, "error": "undecodable content", "path": str(p)}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": str(p)}


def write_text(path: str | Path, text: str) -> Dict[str, Any]:
    """Atomic write: temp file in the same directory, then os.replace."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".genie-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        st = _stat(p)
        return {"ok": True, "path": str(p), **st, "sha256": sha256(p)}
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "error": str(exc), "path": str(p)}


def append_text(path: str | Path, text: str) -> Dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(text)
        return {"ok": True, "path": str(p), **_stat(p)}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def delete(path: str | Path, to_recycle_bin: bool = False) -> Dict[str, Any]:
    """Delete a file or directory.

    Safety: for real user paths the caller should prefer `to_recycle_bin=True`, which uses
    the Windows Shell (SHFileOperation) so the item is recoverable.
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "not found", "path": str(p)}

    rc: Optional[int] = None
    if to_recycle_bin and os.name == "nt":
        try:
            rc = _recycle(p)
        except Exception as exc:
            log.warning("recycle-bin delete raised (%s) — falling back", exc)

        # A-033: SHFileOperationW can report a non-zero rc while having actually moved the
        # item to the Recycle Bin. Success is therefore decided by *state*, not by rc.
        if not p.exists():
            return {"ok": True, "path": str(p), "deleted": True, "recycled": True,
                    "rc": rc, "method": "recycle-bin"}
        log.info("recycle-bin delete did not remove %s (rc=%s) — using hard delete", p, rc)

    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"ok": not p.exists(), "path": str(p), "deleted": not p.exists(),
                "recycled": False, "rc": rc, "method": "hard-delete"}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": str(p), "rc": rc}


def _recycle(p: Path) -> int:
    """Move an item to the Recycle Bin via the Windows shell."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004
    FOF_NOERRORUI = 0x0400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = str(p) + "\x00\x00"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    return int(ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)))


def move(src: str | Path, dst: str | Path, overwrite: bool = False) -> Dict[str, Any]:
    s, d = Path(src), Path(dst)
    if not s.exists():
        return {"ok": False, "error": "source not found", "src": str(s)}
    if d.exists() and d.is_dir():
        d = d / s.name
    if d.exists() and not overwrite:
        return {"ok": False, "error": "destination exists", "dst": str(d)}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"ok": True, "src": str(s), "dst": str(d), "src_exists": s.exists(),
                "dst_exists": d.exists(), "size": _stat(d)["size"]}
    except (OSError, shutil.Error) as exc:
        return {"ok": False, "error": str(exc), "src": str(s), "dst": str(d)}


def copy(src: str | Path, dst: str | Path, overwrite: bool = True) -> Dict[str, Any]:
    s, d = Path(src), Path(dst)
    if not s.exists():
        return {"ok": False, "error": "source not found", "src": str(s)}
    if d.exists() and d.is_dir():
        d = d / s.name
    if d.exists() and not overwrite:
        return {"ok": False, "error": "destination exists", "dst": str(d)}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=overwrite)
        else:
            shutil.copy2(s, d)
        return {"ok": True, "src": str(s), "dst": str(d), "size": _stat(d)["size"],
                "sha256_match": (not s.is_dir()) and sha256(s) == sha256(d)}
    except (OSError, shutil.Error) as exc:
        return {"ok": False, "error": str(exc)}


def mkdir(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(p), "is_dir": p.is_dir()}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def list_dir(path: str | Path, pattern: str = "*", recursive: bool = False,
             limit: int = 500) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "not found", "path": str(p), "entries": []}
    entries: List[Dict[str, Any]] = []
    iterator: Iterable[Path] = p.rglob(pattern) if recursive else p.glob(pattern)
    for item in iterator:
        if len(entries) >= limit:
            break
        st = _stat(item)
        entries.append({"name": item.name, "path": str(item), "is_dir": st["is_dir"],
                        "size": st["size"], "mtime": st["mtime"]})
    return {"ok": True, "path": str(p), "count": len(entries), "entries": entries}


def find(root: str | Path, name_contains: str = "", extensions: Optional[List[str]] = None,
         min_size: int = 0, limit: int = 100, recursive: bool = True) -> Dict[str, Any]:
    """Search by name/extension/size — the primitive behind
    "Downloads me latest drone manual dhundo"."""
    base = Path(root)
    if not base.exists():
        return {"ok": False, "error": "root not found", "root": str(base), "matches": []}
    needle = (name_contains or "").lower()
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])}
    matches: List[Dict[str, Any]] = []
    iterator = base.rglob("*") if recursive else base.glob("*")
    for item in iterator:
        try:
            if not item.is_file():
                continue
            if needle and needle not in item.name.lower():
                continue
            if exts and item.suffix.lower() not in exts:
                continue
            st = item.stat()
            if st.st_size < min_size:
                continue
            matches.append({"name": item.name, "path": str(item), "size": st.st_size,
                            "mtime": int(st.st_mtime), "ext": item.suffix.lower()})
        except OSError:
            continue
        if len(matches) >= limit * 5:
            break
    matches.sort(key=lambda m: -m["mtime"])
    return {"ok": True, "root": str(base), "count": len(matches), "matches": matches[:limit]}


def newest(paths: Iterable[str]) -> Optional[str]:
    best, best_mtime = None, -1
    for p in paths:
        st = _stat(Path(p))
        if st["exists"] and st["mtime"] > best_mtime:
            best, best_mtime = p, st["mtime"]
    return best


def disk_usage(path: str | Path) -> Dict[str, Any]:
    """Return real filesystem capacity for one path or every mounted Windows volume.

    ``all`` is intentionally an inventory request; inaccessible/unsupported
    volumes are returned with their error so they cannot be mistaken for empty
    disks. A missing path selects the Windows system drive.
    """
    raw = str(path or "").strip()
    if raw.casefold() == "all":
        if os.name != "nt":
            return {"ok": False, "error": "Mounted-volume enumeration is available only on Windows."}
        try:
            import ctypes
            mask = int(ctypes.windll.kernel32.GetLogicalDrives())
            if not mask:
                return {"ok": False, "error": "Windows could not enumerate mounted volumes."}
            volumes = []
            for index in range(26):
                if not mask & (1 << index):
                    continue
                drive = f"{chr(ord('A') + index)}:\\"
                try:
                    total, used, free = shutil.disk_usage(drive)
                    volumes.append({"drive": drive, "ok": True,
                                    "total_gb": round(total / 1e9, 1),
                                    "used_gb": round(used / 1e9, 1),
                                    "free_gb": round(free / 1e9, 1)})
                except OSError as exc:
                    volumes.append({"drive": drive, "ok": False,
                                    "error": str(exc)[:240]})
            readable = sum(1 for item in volumes if item["ok"])
            return {"ok": readable > 0, "volumes": volumes,
                    "inaccessible": [item["drive"] for item in volumes if not item["ok"]],
                    "error": "No mounted volume was readable." if not readable else ""}
        except Exception as exc:
            return {"ok": False, "error": f"Windows volume enumeration failed: {exc}"}

    if not raw:
        raw = os.environ.get("SystemDrive", "C:") + "\\"
    try:
        total, used, free = shutil.disk_usage(raw)
        return {"ok": True, "drive": raw,
                "total_gb": round(total / 1e9, 1), "used_gb": round(used / 1e9, 1),
                "free_gb": round(free / 1e9, 1)}
    except OSError as exc:
        return {"ok": False, "drive": raw, "error": str(exc)}
