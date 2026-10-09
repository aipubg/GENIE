"""Opt-in Gemini Live function-response test without microphone or screen capture.

Uses the owner's existing credential, production LiveSession tool dispatch and
ComputerService. Only reads the GENIE Chat navigation element and its state.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main(data_dir, output=None):
    from google import genai
    from google.genai import types
    from computer.service import ComputerService
    from core.db import Database
    from core.lifecycle import Daemon
    from security.trust import TrustService
    from security.vault import Vault
    from voice.live import LiveSession

    isolated = Path(tempfile.mkdtemp(prefix="genie-live-bridge-"))
    db = Database(isolated / "test.db")
    computer = ComputerService(db=db, trust=TrustService(db), data_dir=str(isolated), workspace_root=str(isolated / "workspace"))
    context = SimpleNamespace(services={"computer": computer})
    evidence = []

    def handler(name, args, cancel):
        permitted = ((name == "desktop_controls" and args.get("window") == "GENIE"
                      and args.get("name") == "Chat" and args.get("control_type") == "ListItem")
                     or (name == "desktop_control" and args.get("operation") == "state"))
        if not permitted:
            return {"ok": False, "error": "This diagnostic only reads the GENIE Chat navigation control."}
        result = Daemon._live_tool(context, name, args, cancel)
        evidence.append({"tool": name, "ok": result.get("ok"), "verified": result.get("verified"),
                         "error_code": result.get("error_code"), "count": (result.get("data") or {}).get("count"),
                         "selected": (result.get("data") or {}).get("selected")})
        return result

    live = LiveSession(vault=Vault(data_dir / "vault.enc"), settings_path=data_dir / "voice_live.json", tool_handler=handler)
    live._tool_lock = asyncio.Lock()
    key = live._key()
    if not key:
        raise RuntimeError("No configured Gemini key")
    client = genai.Client(api_key=key)
    report = {"ok": False, "scope": "real Gemini Live function calls and reply audio; no microphone, playback or screen frames", "model": live.settings.model}
    audio_bytes = 0
    transcript = []
    try:
        async with asyncio.timeout(60):
            async with client.aio.live.connect(model=live.settings.model, config=live._config()) as session:
                await session.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=
                    "Run this read-only connectivity test. First call desktop_controls with window GENIE, name Chat, control_type ListItem. "
                    "Then call desktop_control operation state for its returned element_id. Do not click or change anything. "
                    "Finally tell me whether that navigation control is selected, based on the receipt.")]), turn_complete=True)
                while True:
                    complete = False
                    async for response in session.receive():
                        if response.tool_call:
                            if len(evidence) >= 4:
                                raise RuntimeError("Diagnostic tool limit reached")
                            await live._tool_calls(session, response.tool_call.function_calls)
                        content = response.server_content
                        if content:
                            if content.output_transcription and content.output_transcription.text:
                                transcript.append(content.output_transcription.text)
                            if content.model_turn:
                                for part in content.model_turn.parts or []:
                                    if part.inline_data and part.inline_data.data:
                                        audio_bytes += len(part.inline_data.data)
                            if content.turn_complete:
                                complete = True
                    if complete and any(not row["ok"] for row in evidence):
                        report["error"] = "A canonical tool returned a failure; see its receipt."
                        break
                    if complete and audio_bytes and any(row["tool"] == "desktop_control" for row in evidence):
                        break
        report["ok"] = (all(row["ok"] for row in evidence) and
                        {row["tool"] for row in evidence} == {"desktop_controls", "desktop_control"} and audio_bytes > 0)
    except Exception as exc:
        report["error"] = (str(exc) or type(exc).__name__).replace(key, "[redacted]")[:500]
    finally:
        await client.aio.aclose()
        client.close()
        computer.desktop_awareness.stop()
        db.close()
    report.update(receipts=evidence, audio_bytes=audio_bytes, transcript="".join(transcript))
    output = output or ROOT / "artifacts" / "live_tool_bridge_acceptance.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return report["ok"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(main(args.data_dir, args.output)) else 1)
