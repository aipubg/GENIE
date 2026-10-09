"""Read-only post-purge checks for the native GENIE workspace."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "FINAL_CLEANUP_DELETION_MANIFEST.md"
EXPECTED_LAYA_SOURCE = "1e28ac20c0896b1c37a744cd11f740eb98f8b178"
EXPECTED_LAYA_WEIGHTS = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ready_paths() -> list[str]:
    result = []
    if not MANIFEST.is_file():
        return result
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[-1] == "READY_FOR_OWNER_PURGE":
            result.append(cells[0].strip("`").replace("/", os.sep))
    return result


def walk_product_source() -> list[Path]:
    roots = ("agents", "browser", "channels", "computer", "config", "context", "core",
             "devices", "director", "integrations", "memory", "missions", "models",
             "perception", "plugins", "proactive", "security", "voice", "ui/windows",
             "installer", "backend_entry.py", "genie.py")
    items: list[Path] = []
    for rel in roots:
        root = ROOT / rel
        if root.is_file():
            items.append(root)
        elif root.is_dir():
            items.extend(p for p in root.rglob("*") if p.is_file() and
                         not any(part in {"bin", "obj", "__pycache__", ".pytest_cache"}
                                 for part in p.relative_to(ROOT).parts))
    return items


def private_pattern_findings() -> list[tuple[str, str]]:
    # Detect likely credential material without printing or retaining values.
    patterns = (
        re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]([A-Za-z0-9_./+=:-]{20,})['\"]"),
        re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
    )
    excluded = {"tests", "docs", "artifacts", "vendor", ".git", "backend-dist", ".build"}
    findings = []
    for path in walk_product_source():
        if any(part in excluded for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() not in {".py", ".json", ".toml", ".yaml", ".yml", ".ini", ".ps1", ".nsi", ".cs", ".xaml", ".xml"}:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            findings.append((str(path), "unreadable"))
            continue
        for pattern in patterns:
            if pattern.search(data):
                findings.append((str(path), "credential-shaped literal (value suppressed)"))
                break
    return findings


def main() -> int:
    print(f"Final GENIE workspace verification: {ROOT}")
    cache_roots = ("agents", "browser", "channels", "computer", "context", "core", "devices", "director",
                   "evaluation", "experience", "forecast", "integrations", "knowledge", "memory", "missions",
                   "models", "observability", "perception", "plugins", "proactive", "scripts", "security",
                   "skills", "specs", "teaching", "tests", "tools", "ui", "usermodel", "voice")
    for rel in ready_paths():
        if rel.replace("\\", "/").rstrip("/") == "**/__pycache__":
            caches = []
            root_cache = ROOT / "__pycache__"
            if root_cache.exists():
                caches.append(root_cache)
            for cache_root in cache_roots:
                base = ROOT / cache_root
                if base.is_dir():
                    caches.extend(p for p in base.rglob("__pycache__") if p.is_dir())
            check("approved generated Python caches absent", not caches,
                  ", ".join(str(p) for p in caches) if caches else str(ROOT))
            continue
        path = ROOT / rel
        check(f"purge target absent: {rel}", not path.exists(), str(path))

    check("no retired Electron source", not (ROOT / "ui/electron").exists(), str(ROOT / "ui/electron"))
    check("no dist-electron output", not any(ROOT.glob("dist-electron*")), str(ROOT / "dist-electron*"))
    check("no root Node dependency tree", not (ROOT / "node_modules").exists(), str(ROOT / "node_modules"))
    check("native NSIS installer is sole authority", (ROOT / "installer/genie_native.nsi").is_file() and
          not (ROOT / "installer/GENIE.iss").exists(), str(ROOT / "installer/genie_native.nsi"))
    check("current WPF source present", (ROOT / "ui/windows/Genie.Desktop/Genie.Desktop.csproj").is_file(),
          str(ROOT / "ui/windows/Genie.Desktop/Genie.Desktop.csproj"))
    check("current Python backend present", (ROOT / "backend_entry.py").is_file() and
          (ROOT / "core/orchestrator.py").is_file(), str(ROOT / "backend_entry.py"))

    app = ROOT / "backend-dist/backend-runtime/app"
    bundled_user_config = app / "config/user.json"
    check("packaged runtime carries no mutable config/user.json", not bundled_user_config.exists(), str(bundled_user_config))
    check("packaged runtime has no Node/Electron binary", not any(app.rglob("*.node")) and
          not any(app.rglob("electron.exe")), str(app))
    local_profiles = [ROOT / "browser-profile", ROOT / "browser-profile-2", ROOT / "browser-profile-3",
                      ROOT / "browser-profile-abs", ROOT / "data/workspace/browser-profile"]
    check("no repository browser profiles", not any(p.exists() for p in local_profiles),
          ", ".join(str(p) for p in local_profiles if p.exists()) or str(ROOT))
    check("no repository private database/vault", not any((ROOT / "data").glob("*.db*")) and
          not (ROOT / "data/vault.enc").exists() and not (ROOT / "data/providers.user.json").exists(),
          str(ROOT / "data"))
    check("no repository screenshots/logs", not (ROOT / "ui/screenshots").exists() and
          not (ROOT / "data/logs").exists() and not (ROOT / "capture_run.log").exists() and
          not (ROOT / "daemon_run.log").exists() and not (ROOT / "data/workspace/page.png").exists() and
          not (ROOT / "data/workspace/artifacts/capture_1789590073.bmp").exists() and
          not list((ROOT / "data/voice").glob("*.wav")), str(ROOT))

    model_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GENIE" / "models"
    laya = model_root / "laya"
    try:
        manifest = json.loads((laya / "provision.json").read_text(encoding="utf-8"))
        source = laya / "source-1e28ac20c089"
        weights = laya / "weights/model.safetensors"
        head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        canonical_laya = (manifest.get("source", {}).get("revision") == EXPECTED_LAYA_SOURCE and
                         manifest.get("weights", {}).get("revision") == EXPECTED_LAYA_WEIGHTS and
                         manifest.get("verified", {}).get("ok") is True and
                         manifest.get("paths", {}).get("root") == str(laya) and
                         head.returncode == 0 and head.stdout.strip() == EXPECTED_LAYA_SOURCE and
                         weights.is_file() and weights.stat().st_size > 0)
        for name, expected in (manifest.get("weights", {}).get("files") or {}).items():
            path = laya / "weights" / name
            if (not path.is_file() or path.stat().st_size != int(expected.get("bytes") or -1) or
                    sha256_file(path) != expected.get("sha256")):
                canonical_laya = False
                break
    except (OSError, ValueError):
        canonical_laya = False
    check("canonical external Laya provision verified", canonical_laya, str(laya))
    check("no duplicate repository Laya weights", not (ROOT / "data/models/laya").exists(), str(ROOT / "data/models/laya"))
    vosk = model_root / "vosk-model-small-en-us-0.15"
    try:
        vm = json.loads((vosk / "GENIE_MODEL_MANIFEST.json").read_text(encoding="utf-8"))
        vosk_ok = vm.get("verified") is True and int(vm.get("file_count") or 0) >= 10 and int(vm.get("total_bytes") or 0) > 0
        total = 0
        for entry in vm.get("files", []):
            path = vosk / entry["path"]
            if (not path.is_file() or path.stat().st_size != int(entry["bytes"]) or
                    sha256_file(path) != entry["sha256"]):
                vosk_ok = False
                break
            total += path.stat().st_size
        if total != int(vm.get("total_bytes") or -1):
            vosk_ok = False
    except (OSError, ValueError):
        vosk_ok = False
    check("canonical external Vosk model verified", vosk_ok, str(vosk))
    check("no duplicate repository Vosk model", not (ROOT / "data/models/vosk-model-small-en-us-0.15").exists(),
          str(ROOT / "data/models/vosk-model-small-en-us-0.15"))

    paths_source = (ROOT / "core/paths.py").read_text(encoding="utf-8")
    config_source = (ROOT / "core/config.py").read_text(encoding="utf-8")
    check("canonical per-user model path is configured", "GENIE" in paths_source and "models" in paths_source and
          "MODEL_DIR / \"laya\"" in config_source, str(ROOT / "core/paths.py"))
    forbidden_paths = (b"e:\\g3\\genie", b"e:\\\\g3\\\\genie")
    hardcoded = []
    for path in walk_product_source():
        if path.suffix.lower() not in {".py", ".json", ".nsi", ".cs", ".xaml"}:
            continue
        try:
            content = path.read_bytes().lower()
        except OSError:
            hardcoded.append(path)
            continue
        if any(value in content for value in forbidden_paths):
            hardcoded.append(path)
    check("production sources contain no workspace-root dependency", not hardcoded,
          ", ".join(str(p) for p in hardcoded) if hardcoded else str(ROOT))

    findings = private_pattern_findings()
    check("secret scan below allowed threshold", len(findings) == 0,
          f"{len(findings)} finding(s); values suppressed" if findings else "0 credential-shaped literals in scanned product sources")
    for path, kind in findings:
        print(f"  FINDING {path}: {kind}")

    print(f"RESULT {_passed}/{_passed + _failed} passed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
