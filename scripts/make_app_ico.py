#!/usr/bin/env python3
"""Regenerate ui/assets/brand/app-icon.ico with Windows-standard BMP entries.

Why this exists
---------------
The desktop shortcut was showing the Electron/atom default icon. Root cause,
proved by scripts/verify_exe_icon.py: the packaged GENIE.exe still carried
Electron's own icon group (16/32/48/256, byte sizes 1320/5160/11560/18963) —
rcedit had not replaced it.

Our ICO stored every size as a PNG-compressed entry. Most EXE icon stamping
tools do not reliably handle PNG-compressed ICO entries, so the icon was
silently never applied. (Historically this was rcedit via electron-builder.) Windows also
resolves shortcut/taskbar icons from BMP (DIB) entries most reliably.

This script rebuilds the ICO from the canonical master PNG using BMP entries at
every size. The master PNG is NOT modified, so its pinned SHA-256 in
ui/assets/brand/ASSET_MANIFEST.md stays valid.

Requires Pillow. Run with an interpreter that has it, e.g.:
    "C:\\Users\\ghostt\\AppData\\Local\\Programs\\Python\\Python312\\python.exe" scripts/make_app_ico.py
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

SIZES = [16, 20, 24, 32, 48, 64, 128, 256]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def describe_ico(path: str):
    data = open(path, "rb").read()
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError("not an ICO")
    (count,) = struct.unpack("<H", data[4:6])
    out = []
    for i in range(count):
        off = 6 + 16 * i
        w = data[off] or 256
        h = data[off + 1] or 256
        bpp, = struct.unpack("<H", data[off + 6:off + 8])
        size, = struct.unpack("<I", data[off + 8:off + 12])
        start, = struct.unpack("<I", data[off + 12:off + 16])
        payload = data[start:start + size]
        out.append({"w": w, "h": h, "bpp": bpp, "bytes": size,
                    "png": payload[:8] == PNG_MAGIC})
    return out


def main(argv=None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Rebuild app-icon.ico with BMP entries")
    ap.add_argument("--master", default=os.path.join(root, "ui", "assets", "brand",
                                                     "app-icon-master.png"))
    ap.add_argument("--out", default=os.path.join(root, "ui", "assets", "brand",
                                                  "app-icon.ico"))
    args = ap.parse_args(argv)

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        print("ERROR: Pillow is required to regenerate the ICO.")
        print("       Run with an interpreter that has it, e.g.")
        print(r'       C:\Users\ghostt\AppData\Local\Programs\Python\Python312\python.exe '
              "scripts/make_app_ico.py")
        return 3

    if not os.path.isfile(args.master):
        print(f"ERROR: master not found: {args.master}")
        return 3

    print(f"master : {args.master}")
    print(f"out    : {args.out}")
    print(f"sizes  : {SIZES}")

    src = Image.open(args.master)
    print(f"source : {src.size} {src.mode}")

    # Square, opaque RGBA so alpha in the ICO is well-defined.
    im = src.convert("RGBA")
    ico_sizes = [(s, s) for s in SIZES]

    try:
        im.save(args.out, format="ICO", sizes=ico_sizes, bitmap_format="bmp")
    except TypeError:
        # Older Pillow without bitmap_format: BMP is the default for ICO.
        im.save(args.out, format="ICO", sizes=ico_sizes)

    entries = describe_ico(args.out)
    print(f"\nwritten ICO: {len(entries)} image(s)")
    all_bmp = True
    for e in entries:
        kind = "PNG" if e["png"] else "BMP"
        if e["png"]:
            all_bmp = False
        print(f"  {e['w']:>3}x{e['h']:<3} bpp={e['bpp']:<3} {e['bytes']:>7}B  {kind}")

    have = sorted(e["w"] for e in entries)
    if have != sorted(SIZES):
        print(f"\nFAIL: expected sizes {sorted(SIZES)}, got {have}")
        return 2
    if not all_bmp:
        print("\nWARN: some entries are still PNG-compressed; rcedit may ignore them.")
        return 2

    print("\nRESULT: PASS — ICO rebuilt with BMP entries at all required sizes.")
    print("        Master PNG untouched; ASSET_MANIFEST hash still valid.")
    print("        Rebuild the installer and re-run scripts/verify_exe_icon.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
