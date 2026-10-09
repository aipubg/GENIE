"""Read-only production SSE probe; no microphone, screenshots or app input."""
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from core.tool_protocol import looks_like_tool_syntax

    prompt = ("Read-only connectivity test: call inspect_desktop and desktop_windows. "
              "Report only the number of displays and visible windows, not their titles or content. "
              "Do not change anything, launch anything or send any message.")
    started = time.time()
    events = []
    request = Request("http://127.0.0.1:8787/api/chat/stream",
                      data=json.dumps({"text": prompt, "session_id": "owner"}).encode(),
                      headers={"Content-Type": "application/json"})
    event = ""
    with urlopen(request, timeout=120) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[5:])
                events.append({"event": event, "data": payload,
                               "elapsed_s": round(time.time() - started, 3)})
                if event in ("done", "error"):
                    break
    rendered = "".join(e["data"].get("text", "") for e in events if e["event"] == "delta")
    progress = [e for e in events if e["event"] == "progress"]
    report = {"scope": "actual packaged backend HTTP SSE; not GUI or microphone acceptance",
              "started_epoch": started, "prompt": prompt, "events": events,
              "event_contract_ok": (bool(rendered) and len(progress) == 1
                  and events[-1]["event"] == "done" and not looks_like_tool_syntax(rendered)
                  and "Checking the requested action..." not in rendered)}
    output = ROOT / "artifacts/tool-stream-acceptance.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["event_contract_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
