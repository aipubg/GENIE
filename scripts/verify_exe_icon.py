#!/usr/bin/env python3
"""Verify that a packaged Windows EXE actually carries GENIE's brand icon.

Why this exists
---------------
`ui/tests/visual/verify-icon.js` proves the SOURCE .ico is correct. It does not
prove the icon survived packaging. The owner saw the Electron/atom default icon
on the desktop shortcut, which means the icon never made it into the EXE (or was
written in a form Windows refuses to use). This script inspects the PE resource
tree of the built binary and reports exactly what is embedded, then compares it
against the source ICO.

Usage
-----
    python scripts/verify_exe_icon.py
    python scripts/verify_exe_icon.py --exe path/to/GENIE.exe --ico path/to/app-icon.ico
    python scripts/verify_exe_icon.py --json artifacts/exe_icon_report.json

Exit codes
----------
    0 = the EXE's icon group matches the source ICO
    2 = mismatch or no icon group at all
    3 = could not parse
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from typing import Any, Dict, List, Optional, Tuple

RT_ICON = 3
RT_GROUP_ICON = 14

# Retargeted when Electron was retired: the icon now belongs to the
# Windows-native client, not to a dist-electron/win-unpacked build.
DEFAULT_EXE = os.path.join("ui", "windows", "Genie.Desktop", "bin", "Release",
                           "net8.0-windows", "win-x64", "Genie.Desktop.exe")
DEFAULT_ICO = os.path.join("ui", "assets", "brand", "app-icon.ico")


# --------------------------------------------------------------------------- #
# source ICO
# --------------------------------------------------------------------------- #
def read_ico(path: str) -> List[Dict[str, Any]]:
    """Parse an .ico into a list of image descriptors."""
    data = open(path, "rb").read()
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError(f"{path}: not an ICO file")
    (count,) = struct.unpack("<H", data[4:6])
    out: List[Dict[str, Any]] = []
    for i in range(count):
        off = 6 + 16 * i
        w = data[off] or 256
        h = data[off + 1] or 256
        bpp, = struct.unpack("<H", data[off + 6:off + 8])
        size, = struct.unpack("<I", data[off + 8:off + 12])
        start, = struct.unpack("<I", data[off + 12:off + 16])
        payload = data[start:start + size]
        out.append({
            "w": w, "h": h, "bpp": bpp, "bytes": size,
            "png": payload[:8] == b"\x89PNG\r\n\x1a\n",
        })
    return out


# --------------------------------------------------------------------------- #
# PE resources
# --------------------------------------------------------------------------- #
class PE:
    def __init__(self, path: str) -> None:
        self.d = open(path, "rb").read()
        self.path = path
        e_lfanew, = struct.unpack("<I", self.d[0x3C:0x40])
        if self.d[e_lfanew:e_lfanew + 4] != b"PE\0\0":
            raise ValueError(f"{path}: not a PE image")
        self.nsec, = struct.unpack("<H", self.d[e_lfanew + 6:e_lfanew + 8])
        self.optsz, = struct.unpack("<H", self.d[e_lfanew + 20:e_lfanew + 22])
        self.opt = e_lfanew + 24
        self.is64 = struct.unpack("<H", self.d[self.opt:self.opt + 2])[0] == 0x20B
        nrva_off = self.opt + (108 if self.is64 else 92)
        dd_off = nrva_off + 4
        self.rsrc_rva, self.rsrc_sz = struct.unpack("<II", self.d[dd_off + 16:dd_off + 24])
        sec_off = self.opt + self.optsz
        self.sections: List[Tuple[str, int, int, int, int]] = []
        for i in range(self.nsec):
            o = sec_off + i * 40
            name = self.d[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
            vsz, vaddr, rsz, rawptr = struct.unpack("<IIII", self.d[o + 8:o + 24])
            self.sections.append((name, vaddr, vsz, rawptr, rsz))

    def rva2off(self, rva: int) -> Optional[int]:
        for _n, vaddr, vsz, rawptr, rsz in self.sections:
            if vaddr <= rva < vaddr + max(vsz, rsz):
                return rawptr + (rva - vaddr)
        return None

    def resources(self) -> List[Tuple[Tuple[int, ...], int, int]]:
        """Return [(id_path, data_offset, data_size)] for every leaf resource."""
        base = self.rva2off(self.rsrc_rva)
        if base is None:
            return []
        out: List[Tuple[Tuple[int, ...], int, int]] = []
        d = self.d
        n = len(d)

        # IMPORTANT (PE spec): both subdirectory and data-entry offsets are
        # relative to the ROOT resource directory, not to the current one.
        # Getting this wrong silently walks into garbage.
        def walk(b: int, path: Tuple[int, ...], depth: int) -> None:
            if depth > 3 or b + 16 > n:
                return
            named, idc = struct.unpack("<HH", d[b + 12:b + 16])
            for i in range(named + idc):
                e = b + 16 + i * 8
                if e + 8 > n:
                    return
                raw_id, = struct.unpack("<I", d[e:e + 4])
                data, = struct.unpack("<I", d[e + 4:e + 8])
                if data & 0x80000000:
                    walk(base + (data & 0x7FFFFFFF), path + (raw_id,), depth + 1)
                    continue
                entry = base + data
                if entry + 16 > n:
                    continue
                data_rva, = struct.unpack("<I", d[entry:entry + 4])
                size, = struct.unpack("<I", d[entry + 4:entry + 8])
                off = self.rva2off(data_rva)
                if off is None:
                    continue
                out.append((path + (raw_id,), off, size))

        walk(base, (), 0)
        return out


def icon_group(pe: PE) -> List[Dict[str, Any]]:
    """The RT_GROUP_ICON entries: what Windows will actually show."""
    found: List[Dict[str, Any]] = []
    for path, off, size in pe.resources():
        if not path or path[0] != RT_GROUP_ICON:
            continue
        d = pe.d
        if off + 6 > len(d):
            continue
        _res, _typ, cnt = struct.unpack("<HHH", d[off:off + 6])
        images = []
        for i in range(cnt):
            e = off + 6 + i * 14
            if e + 14 > len(d):
                break
            w, h, _cc, _rsv, _pl, bpp, bsz, ordid = struct.unpack("<BBBBHHHI", d[e:e + 14])
            images.append({"w": w or 256, "h": h or 256, "bpp": bpp,
                           "bytes": bsz, "icon_id": ordid})
        found.append({"group_id": path[1] if len(path) > 1 else None,
                      "images": images})
    return found


def icon_images(pe: PE) -> List[Dict[str, Any]]:
    out = []
    for path, off, size in pe.resources():
        if not path or path[0] != RT_ICON:
            continue
        d = pe.d
        if off + 16 > len(d):
            continue
        bih, bw, bh, _pl, bpp = struct.unpack("<IiiHH", d[off:off + 16])
        out.append({"id": path[1] if len(path) > 1 else None, "size": size,
                    "bih": bih, "w": bw, "h": bh // 2, "bpp": bpp})
    return sorted(out, key=lambda x: (x["id"] or 0))


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Verify GENIE EXE icon resources")
    ap.add_argument("--exe", default=os.path.join(root, DEFAULT_EXE))
    ap.add_argument("--ico", default=os.path.join(root, DEFAULT_ICO))
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args(argv)

    print("GENIE packaged EXE icon verification")
    print("=" * 62)
    print(f"exe: {args.exe}")
    print(f"ico: {args.ico}")

    if not os.path.isfile(args.exe):
        print(f"\nFAIL: EXE not found: {args.exe}")
        return 3

    ico = read_ico(args.ico)
    print(f"\nsource ICO: {len(ico)} image(s)")
    for im in ico:
        kind = "PNG" if im["png"] else "BMP"
        print(f"  {im['w']:>3}x{im['h']:<3} bpp={im['bpp']:<3} {im['bytes']:>7}B  {kind}")

    try:
        pe = PE(args.exe)
    except (OSError, ValueError, struct.error) as exc:
        print(f"\nERROR: could not parse PE: {exc}")
        return 3

    groups = icon_group(pe)
    images = icon_images(pe)
    print(f"\nEXE RT_GROUP_ICON groups: {len(groups)}")
    for g in groups:
        print(f"  group {g['group_id']}: {len(g['images'])} image(s)")
        for im in g["images"]:
            print(f"    {im['w']:>3}x{im['h']:<3} bpp={im['bpp']:<3} "
                  f"{im['bytes']:>7}B  -> RT_ICON id {im['icon_id']}")
    print(f"EXE RT_ICON entries: {len(images)}")
    for im in images[:12]:
        print(f"    id={im['id']:<4} size={im['size']:>8} "
              f"w={im['w']} h={im['h']} bpp={im['bpp']}")

    ok = True
    if not groups:
        print("\nFAIL: the EXE has NO RT_GROUP_ICON at all. Windows will show a")
        print("      generic/default icon for the EXE, shortcuts and taskbar.")
        ok = False
    else:
        want = sorted((im["w"], im["h"], im["bpp"]) for im in ico)
        got = sorted((im["w"], im["h"], im["bpp"]) for im in groups[0]["images"])
        print(f"\nwant (from ICO): {want}")
        print(f"got  (from EXE): {got}")
        if want == got:
            print("ok   icon group matches the source ICO")
        else:
            print("FAIL: icon group does not match the source ICO.")
            missing = [w for w in want if w not in got]
            if missing:
                print(f"      missing from EXE: {missing}")
            ok = False

    report = {
        "exe": args.exe,
        "ico": args.ico,
        "ico_images": ico,
        "exe_groups": groups,
        "exe_rt_icons": images,
        "match": ok,
    }
    if args.json_out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".", exist_ok=True)
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2)
            print(f"\nJSON report: {args.json_out}")
        except OSError as exc:
            print(f"could not write JSON: {exc}", file=sys.stderr)

    print("\nRESULT:", "PASS — packaged EXE carries the GENIE icon" if ok
          else "FAIL — packaged EXE icon is wrong or missing")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
