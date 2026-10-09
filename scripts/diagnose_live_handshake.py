"""Check the selected Gemini Live handshake without opening a microphone.

Reads only GENIE's configured vault. Never prints credentials or imports a
reference ZIP's credentials. This diagnostic does not run tools or send images.
"""
import asyncio
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", help="Use the same data directory as the running desktop backend")
args = parser.parse_args()
if args.data_dir:
    os.environ["GENIE_DATA_DIR"] = str(Path(args.data_dir).resolve())

from core.config import Config
from security.vault import Vault
from voice.live import LiveSession


async def main():
    from google import genai
    config = Config.load()
    voice = LiveSession(vault=Vault(config.vault_path), settings_path=config.data_dir / "voice_live.json")
    key = voice._key()
    if not key:
        print(json.dumps({"ok": False, "error": "No stored Gemini key"}))
        return
    client = genai.Client(api_key=key)
    try:
        async with asyncio.timeout(15):
            async with client.aio.live.connect(model=voice.settings.model, config=voice._config()):
                print(json.dumps({"ok": True, "model": voice.settings.model}))
    except Exception as exc:
        detail = str(exc).replace(key, "[redacted]")
        print(json.dumps({"ok": False, "model": voice.settings.model,
                          "type": type(exc).__name__, "detail": detail[:1000]}))
    finally:
        await client.aio.aclose()
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
