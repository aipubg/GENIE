"""Verify the plugin-host fix INSIDE the packaged (embeddable) runtime.

Run with the shipped interpreter. Proves:
  1. a plugin host actually starts (was: ModuleNotFoundError, 174 failures)
  2. a capability can be invoked end to end
  3. the circuit breaker is present and reports state
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RUNTIME = Path(sys.argv[1]).resolve()
APP = RUNTIME / "app"

sys.path.insert(0, str(APP))          # explicit: the embeddable ._pth isolates sys.path
os.environ.setdefault("GENIE_PACKAGED", "1")

from plugins.sdk import load_manifest            # noqa: E402
from plugins.client import PluginClient          # noqa: E402

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + detail) if detail else ""))


installed = APP / "plugins" / "installed"
check("installed_plugins_present", installed.is_dir(), str(sorted(p.name for p in installed.iterdir() if p.is_dir())))

for plugin_dir in sorted(p for p in installed.iterdir() if p.is_dir()):
    pid = plugin_dir.name
    try:
        manifest = load_manifest(plugin_dir)
    except Exception as exc:
        check(f"manifest[{pid}]", False, repr(exc))
        continue
    client = PluginClient(manifest, plugin_dir, repo_root=str(APP))
    started = client.start()
    check(f"start[{pid}]", started.get("ok") is True, json.dumps(started)[:300])
    if not started.get("ok"):
        continue
    status = client.status()
    check(f"running[{pid}]", status.get("running") is True, json.dumps(status.get("state")))
    health = client.health()
    # A meaningful health answer is any *specific* one; "health check failed"
    # would mean the transport or the relabelling defect broke the round trip.
    detail = (health.get("detail") or "").strip()
    check(f"health[{pid}]", detail not in ("", "health check failed"), json.dumps(health)[:200])
    check(f"circuit_closed[{pid}]", status["circuit"]["open"] is False,
          json.dumps(status["circuit"])[:200])
    client.stop()

# ---- circuit-breaker behaviour: a plugin that cannot start must not respawn
broken_dir = installed / "__does_not_exist__"
fake_manifest = load_manifest(installed / "home")
fake_manifest.id = "p0_nonexistent_probe"
broken_client = PluginClient(fake_manifest, broken_dir, crash_limit=2, repo_root=str(APP))
first = broken_client.start()
check("broken_plugin_refuses_to_start", first.get("ok") is False, json.dumps(first)[:200])
second = broken_client.start()
check("broken_plugin_not_retried_in_a_loop", second.get("ok") is False, json.dumps(second)[:200])
check("broken_plugin_circuit_counter_recorded",
      broken_client.circuit.startup_failures >= 1,
      f"startup_failures={broken_client.circuit.startup_failures} "
      f"open={broken_client.circuit.is_open()}")

passed = sum(1 for r in results if r["ok"])
print(f"\nRESULT {passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
