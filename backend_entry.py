"""
GENIE packaged backend entrypoint.

This is the ONLY thing the frozen executable does:

    GENIEBackend.exe        -> start the production daemon and serve until told to stop

It deliberately does NOT expose the developer CLI surface. `genie.py` has many
subcommands (status, backup, update-*, voice-e2e, ...) that exist for
development and operations; freezing all of it would ship a developer tool to
owners and widen the runtime surface for no reason. One executable, one job.

Path rules (must hold for the packaged build):
  * the current working directory is NEVER trusted — a shortcut or a shell can
    set it to anywhere, and a CWD-relative lookup would silently pick up the
    source checkout;
  * the repository root is NEVER used as runtime state;
  * mutable data lives in the GENIE application-data directory, not in
    Program Files, not in Electron resources, not in app.asar;
  * GENIE_DATA_DIR may override the data location explicitly (canonical).
    GENIE_APP_DATA is a DEPRECATED alias, mapped onto GENIE_DATA_DIR by
    core.paths.data_dir(). Only absolute paths are honoured.
"""
from __future__ import annotations

import os
import site
import signal
import sys
import time


def bootstrap_paths() -> None:
    """Remove any dependency on where the process was launched from.

    sys.path: an embeddable Python carrying a ._pth file IGNORES both the
    script directory and PYTHONPATH - only the paths inside ._pth are used.
    So the backend package root must be added explicitly. This is a no-op
    under a normal interpreter, where the script dir is already on the path.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # The embeddable distribution's ._pth file adds site-packages directly,
    # but that does not process the packages' own .pth files. pywin32 places
    # its extension modules under win32/ and registers that directory there;
    # without processing it, pywinauto is present but desktop control fails at
    # runtime with "No module named win32api".
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    if any(name.endswith("._pth") for name in os.listdir(executable_dir)):
        embedded_site = os.path.join(executable_dir, "site-packages")
        if os.path.isdir(embedded_site):
            site.addsitedir(embedded_site)
    # Drop relative config overrides: only absolute paths are meaningful for a
    # packaged service.
    for var in ("GENIE_CONFIG", "GENIE_DATA_DIR", "GENIE_APP_DATA"):
        value = os.environ.get(var)
        if value and not os.path.isabs(value):
            os.environ.pop(var, None)
    # Anchor the working directory to the executable, never to a caller's CWD.
    try:
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass


def main() -> int:
    bootstrap_paths()

    from core.config import get_config
    from core.ipc.server import IPCServer
    from core.lifecycle import Daemon

    cfg = get_config()
    host = cfg.get("ipc.host", "127.0.0.1")
    port = int(cfg.get("ipc.port", 8787))

    daemon = Daemon(cfg).start()
    server = IPCServer(daemon, host=host, port=port)
    server.start(background=True)
    print("GENIE backend ready -> http://%s:%s" % (host, port), flush=True)

    stop = {"value": False}

    def handle(_signum, _frame):
        stop["value"] = True

    for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), handle)
            except Exception:
                pass

    try:
        while not stop["value"]:
            time.sleep(1)
    finally:
        for closer in (lambda: server.stop(), lambda: daemon.stop()):
            try:
                closer()
            except Exception:
                pass
        print("GENIE backend stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
