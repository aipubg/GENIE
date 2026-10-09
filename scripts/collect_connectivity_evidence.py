"""Collect bounded, non-secret runtime evidence and account for every registered ID."""
import json
import os
import hashlib
from datetime import datetime
from pathlib import Path
import sys
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from computer.service import SCOPE_BY_CAPABILITY
    from computer.tool_bridge import DECLARATIONS
    from voice.live import TOOLS

    with urlopen("http://127.0.0.1:8787/api/status", timeout=15) as response:
        status = json.load(response)
    traces = {
        "trace_f2e5b4044c21": "UIA failure before repair",
        "trace_b5f67aa0449d": "UIA discovery after repair",
        "trace_3af7076fc5a3": "actual WPF Chat navigation selection via HTTP Chat",
        "trace_3c99a2f1f1b7": "constrained app discovery",
        "trace_2972bb49dfe4": "actual Windows default browser discovery",
        "trace_830ac2492010": "public browser navigation and click via HTTP Chat",
        "trace_8b8a7ad8adf8": "actual Preview GUI: WhatsApp open timeout before package-identity repair",
        "trace_e49e3867c77b": "actual Preview GUI: same WhatsApp request after repair; existing window and Search edit",
        "trace_4372a05fe2e7": "actual Preview GUI: independent editor inspect, replace, save, read status",
        "trace_15c17a3aabb3": "actual Preview GUI: local webpage observe, fill, click Go, read changed output",
        "trace_381e184d1026": "actual Preview GUI: Bing field fill and results URL fallback; Search button NOT clicked",
        "trace_a7089beac776": "actual Preview GUI: example.com link click and IANA destination observation",
        "trace_c2ace75fe4b6": "owner persisted failure: multiple tool objects rendered as text",
        "trace_8bf6c32943d8": "actual Preview GUI before bridge repair: prose plus find_application JSON leaked",
        "trace_04f49c535165": "actual Preview GUI exact original WhatsApp replay: execution and natural reply; recipient workflow incomplete",
        "trace_dc4ca25c7cb1": "actual Preview GUI: window list and real WhatsApp content-window Edit control",
        "trace_3605b365d36d": "actual Preview GUI continuation: WhatsApp search set to physics and independently seen on screen; multiple recipients, no send",
        "trace_75fdd51a9c48": "actual Preview GUI: local labeled-field replacement, Apply draft click and fresh resulting output",
        "trace_2196622e9ecd": "actual packaged HTTP SSE: inspect_desktop and desktop_windows, one progress event and natural final",
    }
    records = []
    metrics = {trace: {"first_log_at": None, "last_log_at": None,
                       "successful_model_calls": 0, "failed_provider_attempts": 0,
                       "permission_receipts": []} for trace in traces}
    log_dir = Path(os.environ["LOCALAPPDATA"]) / "GENIE" / "data" / "logs"
    for path in log_dir.glob("genie.log*"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            trace = row.get("trace_id")
            if trace not in traces:
                continue
            metric = metrics[trace]
            ts = row.get("ts", "")
            if ts:
                metric["first_log_at"] = min(metric["first_log_at"] or ts, ts)
                metric["last_log_at"] = max(metric["last_log_at"] or ts, ts)
            module, message = row.get("module"), row.get("msg", "")
            if module == "genie.core.tool_dialogue":
                records.append(row)
            if module == "genie.models.gateway":
                metric["successful_model_calls"] += message.startswith("llm call ")
                metric["failed_provider_attempts"] += message.startswith("provider attempt ")
            if module == "genie.security.trust" and message.startswith("trust.check "):
                metric["permission_receipts"].append({"ts": ts, "message": message})

    # Whitelist acceptance turns; never export the owner's full history.
    gui_ids = {"trace_8b8a7ad8adf8": (309, 310), "trace_e49e3867c77b": (311, 312),
               "trace_4372a05fe2e7": (313, 314), "trace_15c17a3aabb3": (315, 316),
               "trace_381e184d1026": (317, 318), "trace_a7089beac776": (319, 320),
               "trace_8bf6c32943d8": (325, 326), "trace_04f49c535165": (327, 328),
               "trace_dc4ca25c7cb1": (329, 330), "trace_3605b365d36d": (331, 332),
               "trace_75fdd51a9c48": (333, 334)}
    history = {}
    allowed_ids = {item for pair in gui_ids.values() for item in pair}
    for before in (321, 335):
        with urlopen(f"http://127.0.0.1:8787/api/chat/history?before={before}", timeout=15) as response:
            history.update({row["id"]: row for row in json.load(response)["messages"]
                            if row["id"] in allowed_ids})
    gui = []
    for trace, pair in gui_ids.items():
        reply = history.get(pair[1], {})
        metric = metrics[trace]
        elapsed = None
        if metric["first_log_at"] and reply.get("created_at"):
            started = datetime.fromisoformat(metric["first_log_at"]).timestamp()
            elapsed = round(reply["created_at"] / 1000 - started, 2)
        gui.append({"trace_id": trace, "scope": traces[trace],
                    "messages": [history[item] for item in pair if item in history],
                    "first_log_to_persisted_reply_s": elapsed,
                    "latency_note": "Approximate backend interval, not UI-submit or audible end-to-end timing.",
                    "spoken_acceptance": False})

    verified = {
        "window.list": ["actual GUI Chat and HTTP SSE, read-only desktop inventory"],
        "desktop.observe": ["actual packaged HTTP SSE, desktop metadata only; not pixel understanding"],
        "uia.find": ["actual GUI Chat: WhatsApp Search and independent editor; real Gemini Live diagnostic"],
        "uia.set_value": ["actual GUI Chat: independent editor saved file; WhatsApp search value independently seen on screen"],
        "uia.invoke": ["actual GUI Chat: Save fixture button; independently read file"],
        "uia.select": ["actual HTTP Chat selecting GENIE navigation, then reobserved"],
        "uia.state": ["real Gemini Live diagnostic, read-only"],
        "application.resolve": ["actual HTTP Chat, WhatsApp found; WorkBuddy not discovered"],
        "application.open": ["actual GUI Chat: existing WhatsApp package window reused, verified HWND/PID"],
        "browser.installed": ["actual HTTP Chat, HTTP/HTTPS defaults Brave"],
        "browser.navigate": ["actual GUI Chat: isolated fixture, Bing and example.com -> IANA"],
        "browser.detect_gate": ["real fixture and public HTTP Chat"],
        "browser.extract": ["real fixture and public HTTP Chat"],
        "browser.observe": ["actual GUI Chat: local changed output and public IANA destination"],
        "browser.act": ["actual GUI Chat: Go on local fixture; Learn more on example.com"],
        "browser.fill": ["actual GUI Chat: local fixture and Bing textarea; replacement/contenteditable in real CDP fixture"],
    }
    matrix = [{"capability": cap, "scope": scope,
               "current_acceptance": "BOUNDED_PASS" if cap in verified else "UNVERIFIED_THIS_PASS",
               "evidence_scope": verified.get(cap, []),
               "note": "Registration is not live acceptance; fixture coverage is in pytest XML."}
              for cap, scope in sorted(SCOPE_BY_CAPABILITY.items())]
    suite = ROOT / "artifacts" / "tool-bridge-final-pytest.xml"
    suites = ET.parse(suite).getroot() if suite.exists() else None
    ui = ROOT / "ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.dll"
    saved = ROOT / "tests/fixtures/desktop_editor/bin/Release/net8.0-windows/win-x64/acceptance.txt"
    artifacts = {}
    for name in ("window-controls-acceptance.json", "browser_bridge_acceptance.json",
                 "live_tool_bridge_acceptance.json", "tool-stream-acceptance.json"):
        path = ROOT / "artifacts" / name
        if path.exists():
            artifacts[name] = json.loads(path.read_text(encoding="utf-8"))
    report = {
        "collected_at": datetime.now().astimezone().isoformat(),
        "runtime": {"ready": status.get("ready"), "build": status.get("build"),
                    "uia": (status.get("computer") or {}).get("uia"),
                    "ui_dll": str(ui), "ui_sha256": hashlib.sha256(ui.read_bytes()).hexdigest()},
        "counts": {"registered": len(matrix), "shared": len(DECLARATIONS),
                   "live": len(TOOLS[0]["function_declarations"])},
        "pytest": [node.attrib for node in suites.findall("testsuite")] if suites is not None else [],
        "traces": traces, "tool_receipts": sorted(records, key=lambda row: row["ts"]),
        "trace_metrics": metrics, "actual_gui_tests": gui, "separate_diagnostics": artifacts,
        "independent_saved_file": {"path": str(saved), "exists": saved.exists(),
                                   "exact_contents_match": saved.exists() and saved.read_text(encoding="utf-8-sig") == "GENIE desktop interaction verified 2026-09-27"},
        "capabilities": matrix,
        "report_inventory_check": {
            "missing_ids": [cap for cap in SCOPE_BY_CAPABILITY if not any(
                cap in (ROOT / "docs" / filename).read_text(encoding="utf-8")
                for filename in ("GENIE_CONNECTED_CAPABILITIES.txt", "GENIE_UNCONNECTED_CAPABILITIES.txt"))],
            "note": "Historical registrations may occur in both reports; the current matrix assigns each ID exactly once."
        },
    }
    destination = ROOT / "artifacts" / "connectivity-production-evidence.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(destination), "counts": report["counts"],
                      "receipt_count": len(records), "runtime_ready": report["runtime"]["ready"]}))


if __name__ == "__main__":
    main()
