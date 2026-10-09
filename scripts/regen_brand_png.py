"""Regenerate the canonical transparent brand PNG for the GENIE core.

Source (do NOT edit):
    C:\\Users\\ghostt\\Pictures\\G3 icon\\icon.png
    (1254x1254 RGBA, alpha preserved, true transparent background)

Output (overwritten in place):
    assets/Genie_brand_256.png   (256x256 RGBA, used by HomeView's central core)
    assets/Genie_brand_512.png   (512x512 RGBA, used by Settings/About & startup splash)

Method:
    Lanczos resample from the high-res source. No compositing on white.
    Alpha is preserved end-to-end.

Run:
    python scripts/regen_brand_png.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Users\ghostt\Pictures\G3 icon\icon.png")
ROOT = Path(__file__).resolve().parents[1]
OUT_256 = ROOT / "assets" / "Genie_brand_256.png"
OUT_512 = ROOT / "assets" / "Genie_brand_512.png"


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    if not SRC.exists():
        print(f"FAIL: source PNG missing: {SRC}")
        return 2
    src = Image.open(SRC)
    if src.mode != "RGBA":
        print(f"WARN: source mode is {src.mode}; converting to RGBA")
        src = src.convert("RGBA")
    print(f"src  : {SRC}  size={src.size}  mode={src.mode}")
    for size, out in ((256, OUT_256), (512, OUT_512)):
        out.parent.mkdir(parents=True, exist_ok=True)
        # Lanczos gives the highest-quality downsample.
        img = src.resize((size, size), Image.LANCZOS)
        img.save(out, format="PNG", optimize=True)
        h = _hash(out)
        # read back to confirm alpha is preserved end-to-end
        with Image.open(out) as rb:
            corners = [rb.getpixel((0, 0)), rb.getpixel((size - 1, 0)),
                       rb.getpixel((0, size - 1)), rb.getpixel((size - 1, size - 1))]
            transparent = sum(1 for px in corners if px[3] == 0)
        print(f"wrote: {out}  size={out.stat().st_size:,}  sha256={h}  "
              f"alpha={transparent}/4 transparent corners")
        if transparent != 4:
            print(f"FAIL: {out} does not have fully transparent corners")
            return 1
    print("\nOK: brand PNG regenerated from canonical transparent source, "
          "alpha preserved, no white background.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())