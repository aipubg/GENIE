"""Create GENIE Preview shortcuts without COM.

The owner needs a launch path that can never be confused with the frozen rc26
install. COM (WScript.Shell) is unavailable in this environment, so the .lnk is
written directly in the MS-SHLLINK format.

Only two things matter for the shortcut to be correct:

    * it resolves to EXACTLY the latest source-built Genie.Desktop.exe
    * it carries the real GENIE icon

Nothing here touches the installed rc26 release.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

LINK_CLSID = bytes(
    [0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
     0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46]
)

# LinkFlags
HAS_LINK_INFO = 0x00000002
HAS_NAME = 0x00000004
HAS_RELATIVE_PATH = 0x00000008
HAS_WORKING_DIR = 0x00000010
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080


def _u16(v: int) -> bytes:
    return struct.pack("<H", v)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _unicode_str(s: str) -> bytes:
    """StringData entry: 2-byte character count + UTF-16LE (no terminator)."""
    return _u16(len(s)) + s.encode("utf-16-le")


def build_lnk(target: str, working_dir: str, icon: str,
              name: str, serial: int = 0) -> bytes:
    flags = (HAS_LINK_INFO | HAS_NAME | HAS_RELATIVE_PATH
             | HAS_WORKING_DIR | HAS_ICON_LOCATION | IS_UNICODE)

    # ---- header (76 bytes) ---------------------------------------------
    header = b"".join([
        _u32(0x4C),            # HeaderSize
        LINK_CLSID,
        _u32(flags),           # LinkFlags
        _u32(0x00000020),      # FileAttributes (ARCHIVE)
        b"\x00" * 8,           # CreationTime
        b"\x00" * 8,           # AccessTime
        b"\x00" * 8,           # WriteTime
        _u32(0),               # FileSize
        _u32(0),               # IconIndex
        _u32(1),               # ShowCommand (SW_SHOWNORMAL)
        _u16(0),               # HotKey
        _u16(0),               # Reserved1
        _u32(0),               # Reserved2
        _u32(0),               # Reserved3
    ])
    assert len(header) == 76, len(header)

    # ---- LinkInfo (VolumeIDAndLocalBasePath) ---------------------------
    volume_id = b"".join([
        _u32(16),              # VolumeIDSize
        _u32(3),               # DriveType DRIVE_FIXED
        _u32(serial & 0xFFFFFFFF),  # DriveSerialNumber
        _u32(0),               # VolumeLabelOffset (none)
    ])
    assert len(volume_id) == 16

    local_base = target.encode("mbcs") + b"\x00"
    common_suffix = b"\x00"

    header_size = 0x1C
    volume_off = header_size
    local_off = header_size + len(volume_id)
    suffix_off = local_off + len(local_base)

    link_info = b"".join([
        _u32(0),               # LinkInfoSize (patched below)
        _u32(header_size),     # LinkInfoHeaderSize
        _u32(0x00000001),      # LinkInfoFlags VolumeIDAndLocalBasePath
        _u32(volume_off),
        _u32(local_off),
        _u32(0),               # CommonNetworkRelativeLinkOffset (none)
        _u32(suffix_off),
        volume_id,
        local_base,
        common_suffix,
    ])
    link_info = _u32(len(link_info)) + link_info[4:]

    # ---- StringData -----------------------------------------------------
    strings = b"".join([
        _unicode_str(name),                    # NAME_STRING
        _unicode_str(Path(target).name),       # RELATIVE_PATH
        _unicode_str(working_dir),             # WORKING_DIR
        _unicode_str(icon),                    # ICON_LOCATION
    ])

    # ---- ExtraData terminal block ---------------------------------------
    return header + link_info + strings + _u32(0)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    exe = str(root / "ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.exe")
    icon = str(root / "assets/Genie.ico")

    exe_p = Path(exe)
    if not exe_p.is_file():
        print("MISSING EXE:", exe)
        return 1
    if not Path(icon).is_file():
        print("MISSING ICON:", icon)
        return 1

    blob = build_lnk(exe, str(exe_p.parent), icon, "GENIE Preview")

    desktop = Path.home() / "Desktop"
    start_menu = (Path.home() / "AppData" / "Roaming" / "Microsoft"
                  / "Windows" / "Start Menu" / "Programs")
    written = []
    for folder in (desktop, start_menu):
        if not folder.is_dir():
            print("SKIP (missing folder):", folder)
            continue
        out = folder / "GENIE Preview.lnk"
        out.write_bytes(blob)
        written.append(str(out))
        print("WROTE:", out)

    print("target:", exe)
    print("icon:", icon)
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
