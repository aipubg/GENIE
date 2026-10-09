"""Regenerate the canonical GENIE multi-resolution Windows ICO.

Source (do NOT edit):
    C:\\Users\\ghostt\\Pictures\\G3 icon\\icon.png
    (transparent PNG, 1254x1254 RGBA)

Targets (both must be the same bytes; they are referenced from different
build surfaces):

    ui/assets/brand/app-icon.ico   <- NSIS MUI_ICON / MUI_UNICON / UNINSTALLER_ICON
    assets/Genie.ico               <- C# csproj <ApplicationIcon>
                                     and embedded as a WPF pack URI for the window

Sizes embedded (Windows-standard):
    16, 24, 32, 48, 64, 128, 256

Method:
    - Open source as RGBA. Do NOT composite on a background.
    - Let Pillow's ICO writer downsample with Lanczos (high quality).
    - Pillow writes BMP/DIB entries for sizes <=256; alpha is preserved through
      the BITMAPINFOHEADER 32bpp BI_BITFIELDS path (Explorer shows alpha on
      Vista+).

Run:
    python scripts/regen_app_icon.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Users\ghostt\Pictures\G3 icon\icon.png")
ROOT = Path(__file__).resolve().parents[1]
OUT_BRAND = ROOT / "ui" / "assets" / "brand" / "app-icon.ico"
OUT_DESKTOP = ROOT / "assets" / "Genie.ico"

# Canonical Windows sizes for application icons.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_one(src: Image.Image, out: Path) -> tuple[int, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    # `sizes` makes Pillow emit a multi-resolution ICO. For Pillow >= 6.0 the
    # internal resize uses Lanczos (highest-quality downsample).
    src.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    return out.stat().st_size, _hash(out)


def main() -> int:
    if not SRC.exists():
        print(f"FAIL: source PNG missing: {SRC}")
        return 2

    src = Image.open(SRC)
    if src.mode != "RGBA":
        print(f"WARN: source mode is {src.mode}; converting to RGBA")
        src = src.convert("RGBA")

    print(f"src  : {SRC}")
    print(f"      size={src.size} mode={src.mode}")

    if src.size[0] < max(SIZES) or src.size[1] < max(SIZES):
        print(f"FAIL: source smaller than required max size {max(SIZES)}")
        return 2

    written = []
    for out in (OUT_BRAND, OUT_DESKTOP):
        size, h = build_one(src, out)
        written.append(out)
        print(f"wrote: {out}  bytes={size:,}  sha256={h}")

    # Sanity: read back and confirm size entries.
    from PIL import IcoImagePlugin
    with Image.open(written[0]) as rb:
        sizes_in_ico = sorted({(s, s) for s, s in rb.ico.sizes()})
        print(f"ico entries readback: {sizes_in_ico}")
    expected = sorted({(s, s) for s in SIZES})
    if sizes_in_ico != expected:
        print(f"FAIL: ICO sizes mismatch. expected={expected} got={sizes_in_ico}")
        return 1

    # Both copies must be byte-identical.
    if OUT_BRAND.read_bytes() != OUT_DESKTOP.read_bytes():
        print("FAIL: brand and desktop ICO copies differ")
        return 1

    print(f"\nOK  {len(written)} ICO files regenerated, {len(SIZES)} sizes, "
          f"alpha preserved, no white background.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())