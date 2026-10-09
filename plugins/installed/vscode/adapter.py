"""VS Code plugin.

Uses VS Code's **official CLI** (`code`) rather than screen automation: opening a project or a
file is a native, documented operation, so it is reliable and verifiable.

If the CLI is not installed the plugin reports UNAVAILABLE — it never fakes success. GENIE
still operates VS Code through the generic fallback chain (OS automation → UIA → input), so a
missing plugin never blocks the user.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CODE_CANDIDATES = [
    "code",
    "code.cmd",
    "Code.exe",
]

WINDOWS_HINTS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
    r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    r"C:\Program Files (x86)\Microsoft VS Code\bin\code.cmd",
]


def find_code_cli() -> Optional[str]:
    for name in CODE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    for hint in WINDOWS_HINTS:
        if hint and Path(hint).exists():
            return hint
    return None


class Adapter:
    def __init__(self, manifest, workspace=None):
        self.manifest = manifest
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self._cli = find_code_cli()

    # ------------------------------------------------------------- lifecycle
    def initialize(self) -> Dict[str, Any]:
        return {"ok": True, "cli": self._cli,
                "detail": "VS Code CLI found" if self._cli else "VS Code CLI not installed"}

    def health(self) -> Dict[str, Any]:
        self._cli = find_code_cli()
        if not self._cli:
            return {"ok": False, "available": False,
                    "detail": "the `code` CLI is not installed; GENIE will use generic automation"}
        return {"ok": True, "available": True, "detail": f"code CLI: {self._cli}"}

    def shutdown(self) -> None:
        return None

    # --------------------------------------------------------------- invoking
    def invoke(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        name = capability.rsplit(".", 1)[-1]
        if name == "health":
            return self.health()
        if name == "open_project":
            return self._open_project(params)
        if name == "open_file":
            return self._open_file(params)
        if name == "focus":
            return self._focus(params)
        if name == "inspect_workspace":
            return self._inspect(params)
        return self.unavailable(f"unknown capability {name}")

    # ---------------------------------------------------------------- actions
    def _require_cli(self) -> Optional[Dict[str, Any]]:
        if not self._cli:
            return self.unavailable("the `code` CLI is not installed on this machine")
        return None

    def _run(self, args: List[str], timeout_s: float = 15.0) -> Dict[str, Any]:
        try:
            completed = subprocess.run([self._cli] + args, capture_output=True, text=True,
                                       timeout=timeout_s, shell=False)
            return {"ok": completed.returncode == 0, "exit_code": completed.returncode,
                    "stdout": (completed.stdout or "")[-2000:],
                    "stderr": (completed.stderr or "")[-2000:]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"the code CLI did not answer within {timeout_s}s",
                    "error_code": "timeout"}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_code": "cli_failed"}

    def _open_project(self, params: Dict[str, Any]) -> Dict[str, Any]:
        missing = self._require_cli()
        if missing:
            return missing
        raw = str(params.get("path", "")).strip()
        if not raw:
            return {"ok": False, "error": "path is required", "error_code": "bad_params"}
        path = Path(raw).expanduser()
        if not path.exists():
            return {"ok": False, "error": f"folder not found: {path}",
                    "error_code": "not_found", "available": True}
        result = self._run(["--reuse-window", str(path)])
        return {"ok": result["ok"], "path": str(path), "method": "code-cli",
                "detail": f"opened {path.name} in VS Code" if result["ok"]
                else result.get("stderr") or result.get("error", "open failed"),
                "verification": "the CLI accepted the path; the window is verified by GENIE",
                **{k: v for k, v in result.items() if k in ("exit_code", "stderr")}}

    def _open_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        missing = self._require_cli()
        if missing:
            return missing
        raw = str(params.get("path", "")).strip()
        if not raw:
            return {"ok": False, "error": "path is required", "error_code": "bad_params"}
        path = Path(raw).expanduser()
        if not path.exists():
            return {"ok": False, "error": f"file not found: {path}",
                    "error_code": "not_found", "available": True}
        target = str(path)
        line = params.get("line")
        if line:
            target = f"{target}:{int(line)}"
        result = self._run(["-g", target])
        return {"ok": result["ok"], "path": str(path), "line": line, "method": "code-cli",
                "detail": f"opened {path.name}" if result["ok"]
                else result.get("stderr") or result.get("error", "open failed"),
                "verification": "the CLI accepted the file"}

    def _focus(self, params: Dict[str, Any]) -> Dict[str, Any]:
        missing = self._require_cli()
        if missing:
            return missing
        # `--reuse-window` with no path activates the running window
        result = self._run(["--reuse-window"])
        return {"ok": result["ok"], "method": "code-cli",
                "detail": "VS Code window activated" if result["ok"] else "could not focus",
                "verification": "window focus is confirmed by GENIE's window checks"}

    def _inspect(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw = str(params.get("path", "")).strip()
        if not raw:
            return {"ok": False, "error": "path is required", "error_code": "bad_params"}
        root = Path(raw).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"not a folder: {root}", "error_code": "not_found"}
        try:
            entries = []
            file_count = 0
            for item in sorted(root.iterdir()):
                if item.name.startswith(".") and item.name not in (".vscode",):
                    continue
                is_dir = item.is_dir()
                file_count += 0 if is_dir else 1
                entries.append({"name": item.name, "is_dir": is_dir,
                                "size": 0 if is_dir else item.stat().st_size})
            languages = set()
            for pattern, language in (("*.py", "python"), ("*.ts", "typescript"),
                                      ("*.js", "javascript"), ("*.cs", "csharp"),
                                      ("*.go", "go"), ("*.rs", "rust"), ("*.kt", "kotlin")):
                if any(root.glob(f"**/{pattern}")) if False else list(root.glob(pattern)):
                    languages.add(language)
            has_git = (root / ".git").exists()
            has_vscode = (root / ".vscode").exists()
            tasks = []
            if has_vscode and (root / ".vscode" / "tasks.json").exists():
                try:
                    data = json.loads((root / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
                    tasks = [t.get("label", "") for t in data.get("tasks", [])][:20]
                except Exception:
                    tasks = []
            return {"ok": True, "path": str(root), "entries": entries[:60],
                    "file_count": file_count, "languages": sorted(languages),
                    "git_repository": has_git, "vscode_config": has_vscode, "tasks": tasks,
                    "detail": f"{file_count} file(s), languages: {', '.join(sorted(languages)) or 'unknown'}"}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "error_code": "read_failed"}
