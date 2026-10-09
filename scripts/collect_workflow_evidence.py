"""Append this repair's bounded evidence without replacing historical receipts."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def get(path):
    with urlopen("http://127.0.0.1:8787" + path, timeout=15) as response:
        return json.load(response)


def main():
    from computer.service import SCOPE_BY_CAPABILITY
    from computer.tool_bridge import DECLARATIONS
    from voice.live import TOOLS
    destination = ROOT / "artifacts/connectivity-production-evidence.json"
    previous = json.loads(destination.read_text(encoding="utf-8"))
    traces = {
        "trace_fe4577151231": "GUI editor replace/save, exact independently saved file",
        "trace_7f7a67d12568": "GUI exact draft; approval expired, no submission",
        "trace_5238b2faa103": "GUI exact recipient/draft, local owner-confirmed submission",
    }
    records = []
    for path in (Path(os.environ["LOCALAPPDATA"]) / "GENIE/data/logs").glob("genie.log*"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("trace_id") in traces and row.get("module") in (
                    "genie.core.tool_dialogue", "genie.models.gateway", "genie.security.trust"):
                records.append(row)
    history = {}
    for before in (343, 357):
        for row in get(f"/api/chat/history?before={before}")["messages"]:
            if row["id"] in (339, 340, 341, 342, 355, 356):
                history[row["id"]] = row
    old = {row["capability"]: row for row in previous["capabilities"]}
    fresh = {
        "message.prepare": ["Actual Preview GUI trace_5238b2faa103, exact local fixture recipient/draft"],
        "message.send": ["Actual Preview GUI trace_5238b2faa103, approved local submission; not external delivery"],
        "uia.find": ["Actual Preview editor GUI; Live diagnostic separately recorded, including failure"],
        "uia.set_value": ["Actual Preview editor GUI, independent acceptance.txt contents"],
        "uia.invoke": ["Actual Preview editor GUI, Save fixture and independent file"],
    }
    matrix = []
    for cap, scope in sorted(SCOPE_BY_CAPABILITY.items()):
        row = {"capability": cap, "scope": scope,
               "current_acceptance": "UNVERIFIED_THIS_PASS", "evidence_scope": []}
        if cap in old:
            row["historical_acceptance"] = old[cap]
        if cap in fresh:
            row.update(current_acceptance="BOUNDED_GUI_PASS", evidence_scope=fresh[cap])
        if cap.startswith("desktop.visual_"):
            row.update(current_acceptance="IMPLEMENTED_FIXTURE_TESTED_LIVE_PENDING",
                       evidence_scope=["DPI, stale pixels, owner/session, consent and approval fixtures; no live screenshot upload"])
        matrix.append(row)
    suite = ET.parse(ROOT / "artifacts/workflow-final-pytest-20261005.xml").getroot()
    status = get("/api/status")
    voice = get("/api/voice")["live"]
    providers = get("/api/providers")["providers"]
    ui = ROOT / "ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.dll"
    fixture = ROOT / "tests/fixtures/desktop_editor/bin/Release/net8.0-windows/win-x64/publish"
    artifacts = {}
    for name in ("live-workflow-20261004.json", "live-workflow-20261005.json",
                 "browser-workflow-20261004.json", "laya-routing-20261003.json"):
        path = ROOT / "artifacts" / name
        if path.exists():
            artifacts[name] = json.loads(path.read_text(encoding="utf-8"))
    report = {
        "run_id": "workflow-20261005",
        "collected_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
        "runtime": {"ready": status.get("ready"), "build": status.get("build"),
                    "director": status.get("director"), "ui_sha256": hashlib.sha256(ui.read_bytes()).hexdigest()},
        "build_verification": {"final_build_match": "34/34 PASS", "wpf_warnings": 0,
                               "wpf_errors": 0, "earlier_concurrent_probe_failures":
                               ["faster_whisper", "ctranslate2", "tokenizers"],
                               "note": "Final unloaded repeat passed; transient root cause unconfirmed."},
        "counts": {"registered": len(matrix), "shared": len(DECLARATIONS),
                   "live": len(TOOLS[0]["function_declarations"])},
        "pytest": [node.attrib for node in suite.findall("testsuite")],
        "pytest_scope": "Configured full suite; 79 deselected hardware/external tests are not passes.",
        "traces": traces, "tool_receipts": sorted(records, key=lambda row: row["ts"]),
        "gui_history": [history[key] for key in sorted(history)],
        "latency_note": "Trace timestamps exclude UI submission; message confirmation includes human delay. Saved pair timestamps are not response latency.",
        "fixture_checks": {
            "editor_saved_exact": (fixture / "acceptance.txt").read_text(encoding="utf-8-sig") == "Workflow verified 2026-10-03",
            "message_submitted_exact": (fixture / "messages.txt").read_text(encoding="utf-8-sig").splitlines() == ["Fixture recipient: Fixture message 2026-10-04"],
            "external_message_sent": False},
        "voice": {key: voice.get(key) for key in ("state", "error", "settings", "connected")},
        "audio_device_check": {"scope": "Packaged real PortAudio output, 150 ms silent callback",
                               "device": 0, "channels": 1, "rate": 44100, "passed": True,
                               "physical_voice_acceptance": False, "owner_microphone_selection_pending": True},
        "gemini_rest_probe": {"legacy_model": "gemini-2.5-flash", "legacy_status": 404,
                              "new_model": "gemini-3.8-flash", "new_ok": True, "latency_ms": 8485},
        "providers": [{key: p.get(key) for key in ("id", "enabled", "runtime_status", "credential_present")}
                      for p in providers],
        "separate_diagnostics": artifacts, "capabilities": matrix,
        "report_inventory_check": {"missing_ids": [cap for cap in SCOPE_BY_CAPABILITY if not any(
            cap in (ROOT / "docs" / name).read_text(encoding="utf-8") for name in (
                "GENIE_CONNECTED_CAPABILITIES.txt", "GENIE_UNCONNECTED_CAPABILITIES.txt"))]},
    }
    runs = previous.setdefault("workflow_runs", [])
    if any(row.get("run_id") == report["run_id"] for row in runs):
        raise SystemExit("Evidence run already exists; use a new dated run ID, never overwrite it.")
    runs.append(report)
    temp = destination.with_suffix(".tmp")
    temp.write_text(json.dumps(previous, indent=2, ensure_ascii=True), encoding="utf-8")
    temp.replace(destination)
    print(json.dumps({"path": str(destination), "counts": report["counts"],
                      "pytest": report["pytest"], "inventory": report["report_inventory_check"],
                      "fixture_checks": report["fixture_checks"]}, indent=2))


if __name__ == "__main__":
    main()
