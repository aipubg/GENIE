"""Read-only exact-file inventory for relocated GENIE roots; never deletes."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = [ROOT, ROOT.parent / "GENIE-preclean-checkpoint-20261008",
         ROOT.parent / "GENIE-worktrees", ROOT.parent / "models/laya-pinned",
         ROOT.parent / "G3GENIEartifactscaseCGENIE-C",
         ROOT.parent.parent / "G3GENIEartifactscaseCGENIE-C",
         ROOT.parent.parent / "e/G3/GENIE-ui", ROOT.parent.parent / "e/G3/mirofish-env",
         Path(os.environ["LOCALAPPDATA"]) / "GENIE/models"]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    report = []
    for root in ROOTS:
        entry = {"root": str(root), "exists": root.exists(), "bytes": 0,
                 "files": [], "links": [], "errors": []}
        if root.exists():
            for folder, dirs, files in os.walk(root, followlinks=False):
                for name in dirs[:]:
                    p = Path(folder) / name
                    if p.is_symlink() or p.is_junction():
                        entry["links"].append(str(p))
                        dirs.remove(name)
                for name in files:
                    p = Path(folder) / name
                    if p.is_symlink():
                        entry["links"].append(str(p))
                        continue
                    try:
                        size = p.stat().st_size
                        entry["bytes"] += size
                        # The inventory's own output is excluded to avoid self-reference.
                        if p.name == "workspace-inventory.json":
                            continue
                        entry["files"].append({"relative": str(p.relative_to(root)), "bytes": size,
                                               "sha256": digest(p)})
                    except OSError as exc:
                        entry["errors"].append({"path": str(p), "error": str(exc)})
        report.append(entry)
        print(str(root), entry["bytes"], len(entry["files"]), "errors", len(entry["errors"]), flush=True)
    dest = ROOT / "artifacts/workspace-inventory.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
