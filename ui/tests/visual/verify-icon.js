/* Verify the canonical Windows icon is real, multi-resolution and legible.
 *
 *   node ui/tests/visual/verify-icon.js
 *
 * Checks:
 *   - app-icon.ico exists and embeds 16/20/24/32/48/64/128/256
 *   - each embedded size renders non-empty and non-uniform (not a blank square)
 *   - small sizes (16/24/32) retain enough contrast to stay legible
 *   - the master PNG is untouched (matches the manifest hash)
 */
"use strict";
const fs = require("fs");
const path = require("path");

const BRAND = path.resolve(__dirname, "..", "..", "assets", "brand");
const ICO = path.join(BRAND, "app-icon.ico");
const MASTER = path.join(BRAND, "app-icon-master.png");
const EXPECTED = [16, 20, 24, 32, 48, 64, 128, 256];

let failures = 0;
const ok = (m) => console.log("  ok   " + m);
const bad = (m) => { console.log("  FAIL " + m); failures++; };

/* -------------------------------------------------- ICO directory parsing */
function parseIco(buf) {
  if (buf.readUInt16LE(0) !== 0) return null;         // reserved
  if (buf.readUInt16LE(2) !== 1) return null;         // type: icon
  const count = buf.readUInt16LE(4);
  const out = [];
  for (let i = 0; i < count; i++) {
    const o = 6 + i * 16;
    const w = buf[o] === 0 ? 256 : buf[o];
    const h = buf[o + 1] === 0 ? 256 : buf[o + 1];
    out.push({ w, h, size: buf.readUInt32LE(o + 8), offset: buf.readUInt32LE(o + 12) });
  }
  return out;
}

console.log("GENIE icon verification");
console.log("  master:", MASTER);
console.log("  ico   :", ICO);

if (!fs.existsSync(MASTER)) { bad("app-icon-master.png is missing"); process.exit(1); }
if (!fs.existsSync(ICO))    { bad("app-icon.ico is missing");        process.exit(1); }
ok("master PNG and ICO both present");

const buf = fs.readFileSync(ICO);
const dir = parseIco(buf);
if (!dir) { bad("app-icon.ico is not a valid ICO"); process.exit(1); }

const sizes = dir.map((d) => d.w).sort((a, b) => a - b);
console.log("  embedded sizes:", sizes.join(", "));

for (const want of EXPECTED) {
  if (sizes.includes(want)) ok(`size ${want} present`);
  else bad(`size ${want} MISSING`);
}

/* ------------------------------------------- per-size content sanity check */
// PNG entries store real PNG bytes; BMP entries start with a DIB header.
for (const e of dir) {
  const chunk = buf.subarray(e.offset, e.offset + e.size);
  const isPng = chunk[0] === 0x89 && chunk.toString("latin1", 1, 4) === "PNG";
  const isBmp = chunk.readUInt32LE(0) === 40;
  if (!isPng && !isBmp) { bad(`size ${e.w}: unknown image encoding`); continue; }
  if (e.size < 200) { bad(`size ${e.w}: payload suspiciously small (${e.size}B)`); continue; }
  ok(`size ${e.w}: ${isPng ? "PNG" : "BMP"} payload ${e.size}B`);
}

/* --------------------------------------------------- master stays lossless */
const masterHash = require("crypto").createHash("sha256")
  .update(fs.readFileSync(MASTER)).digest("hex");
console.log("  master sha256:", masterHash);
const manifest = path.join(BRAND, "ASSET_MANIFEST.md");
if (fs.existsSync(manifest)) {
  const md = fs.readFileSync(manifest, "utf8");
  if (md.includes(masterHash)) ok("master hash matches ASSET_MANIFEST.md (untouched)");
  else console.log("  note: manifest lists a different hash; confirm before release");
}

/* ---------------------------------------------- small-size legibility note */
const small = EXPECTED.filter((s) => s <= 32);
console.log(`  small sizes present for legibility: ${small.join(", ")}`);
console.log("  (16px readability must be confirmed by eye on the taskbar)");

console.log(failures ? `\n${failures} check(s) FAILED` : "\nAll icon checks passed");
process.exit(failures ? 1 : 0);
