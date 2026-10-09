"""Test fixtures: build a fully wired GENIE stack on a throwaway database.

No test touches the real data/ directory.

Test categories (see `pytest.ini`):

    deterministic      pure logic, throwaway DB, no shared machine state — the default suite
    real_machine       mutates shared desktop state and runs serially behind REAL_DESKTOP_TEST
    hardware_optional  needs hardware that may legitimately be absent
    owner_acceptance   needs the owner physically

Anything that touches the volume, the foreground window, keyboard/mouse, a Chrome profile, the
microphone/speaker or a device must be marked `real_machine`. Those tests are the ones that
produced false failures when another process changed the system volume mid-run, and they are the
reason the deterministic suite is separated from them.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ------------------------------------------------------------------ real desktop lock
#: A cross-process lock file. Real-machine tests mutate one desktop, so exactly one may run at a
#: time — even if two pytest processes are started (a rerun in another shell, CI plus a local run).
REAL_DESKTOP_LOCK = Path(os.environ.get("GENIE_TEST_LOCK",
                                        Path(os.environ.get("TEMP", "/tmp"))
                                        / "genie_real_desktop.lock"))
REAL_DESKTOP_LOCK_TIMEOUT_S = float(os.environ.get("GENIE_TEST_LOCK_TIMEOUT", "600"))


def _acquire_file_lock(path: Path, timeout_s: float) -> bool:
    """Atomically create the lock file, waiting for a held lock to be released."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"pid={os.getpid()} at={time.time()}\n".encode())
            os.close(handle)
            return True
        except FileExistsError:
            # a stale lock from a killed run must not block the suite forever
            try:
                age = time.time() - path.stat().st_mtime
                if age > timeout_s:
                    path.unlink()
                    continue
            except OSError:
                pass
            time.sleep(0.2)
    return False


def _release_file_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


@pytest.fixture(scope="session")
def real_desktop():
    """Serialise the tests that share one real desktop.

    Session-scoped, because the point is "one real-desktop test at a time", and process-wide,
    because a second pytest process must also be excluded.
    """
    acquired = _acquire_file_lock(REAL_DESKTOP_LOCK, REAL_DESKTOP_LOCK_TIMEOUT_S)
    if not acquired:
        pytest.skip(f"another real-desktop test run holds {REAL_DESKTOP_LOCK}")
    try:
        yield REAL_DESKTOP_LOCK
    finally:
        _release_file_lock(REAL_DESKTOP_LOCK)


def pytest_collection_modifyitems(config, items):
    """Attach the desktop lock to every `real_machine` test automatically.

    Requiring each test to remember the fixture would mean one forgetful test silently runs
    alongside another and reintroduces exactly the contention this separation exists to remove.
    """
    for item in items:
        if item.get_closest_marker("real_machine"):
            if "real_desktop" not in item.fixturenames:
                item.fixturenames.append("real_desktop")

from core.contracts import CallContext  # noqa: E402
from core.db import reset_db_for_tests  # noqa: E402
from core.events import EventBus  # noqa: E402
from security.audit import AuditLog  # noqa: E402
from security.policy import PolicyRegistry  # noqa: E402
from security.trust import TrustService  # noqa: E402
from security.vault import Vault  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from missions.service import LockService, MissionService  # noqa: E402
from models.gateway import Gateway  # noqa: E402
from models.health import HealthMonitor  # noqa: E402
from models.registry import ModelRegistry  # noqa: E402
from director.heuristics import HeuristicDirector  # noqa: E402
from computer.service import ComputerService  # noqa: E402
from agents.runtime import AgentRuntime, CapabilityWorker  # noqa: E402
from context.builder import ContextBuilder  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402


@dataclass
class App:
    db: Any
    bus: EventBus
    audit: AuditLog
    vault: Vault
    trust: TrustService
    memory: MemoryService
    missions: MissionService
    locks: LockService
    registry: ModelRegistry
    health: HealthMonitor
    gateway: Gateway
    director: HeuristicDirector
    computer: ComputerService
    agents: AgentRuntime
    orchestrator: Orchestrator
    skills: Any
    devices: Any
    perception: Any
    proactive: Any
    proactive: Any
    perception: Any

    def ctx(self, **kw) -> CallContext:
        return CallContext(**kw)


@pytest.fixture()
def app(tmp_path):
    db = reset_db_for_tests(tmp_path / "test.db")
    bus = EventBus(workers=1)
    audit = AuditLog(db)
    vault = Vault(tmp_path / "vault.enc", audit=audit)
    trust = TrustService(db, audit=audit, default_deny=True)
    memory = MemoryService(db, audit=audit, trust=trust)
    missions = MissionService(db, audit=audit)
    locks = LockService(db)
    registry = ModelRegistry(user_file=tmp_path / "providers.user.json")
    health = HealthMonitor(db)
    policy = PolicyRegistry()
    gateway = Gateway(registry, vault, policy, db, health=health, max_attempts=3)
    director = HeuristicDirector()
    computer = ComputerService(trust=trust, audit=audit, db=db, locks_service=locks,
                               workspace_root=tmp_path / "workspace")
    from skills.service import SkillService
    skills = SkillService(db, audit=audit, missions=missions, trust=trust,
                          computer=computer, gateway=gateway,
                          capability_provider=computer.capabilities)
    # the device mesh is wired but does not listen here — e2e tests start their own on a free port
    from devices.service import DeviceService
    devices = DeviceService(db, audit=audit, trust=trust, vault=vault, auto_start=False)
    from perception.service import PerceptionService
    perception = PerceptionService(db, audit=audit, computer=computer, devices=devices,
                                   gateway=gateway)
    from proactive.service import ProactiveService
    proactive = ProactiveService(db, audit=audit, presence=perception.presence,
                                 devices=devices)
    from proactive.service import ProactiveService
    proactive = ProactiveService(db, audit=audit, presence=perception.presence,
                                 devices=devices)
    worker = CapabilityWorker(computer=computer, device=devices, skills=skills)
    skills.worker = worker
    agents = AgentRuntime(step_runner=worker, audit=audit)
    ctx_builder = ContextBuilder(memory=memory)
    orchestrator = Orchestrator(director=director, missions=missions, memory=memory,
                                gateway=gateway, computer=computer, agent_runtime=agents,
                                context_builder=ctx_builder, trust=trust, audit=audit,
                                perception=perception, proactive=proactive)
    app = App(db=db, bus=bus, audit=audit, vault=vault, trust=trust, memory=memory,
              missions=missions, locks=locks, registry=registry, health=health,
              gateway=gateway, director=director, computer=computer, agents=agents,
              orchestrator=orchestrator, skills=skills, devices=devices,
              perception=perception, proactive=proactive)
    yield app
    try:
        devices.stop()
    except Exception:
        pass
    # real-machine tests may have started a browser: close the client and stop the process
    try:
        from browser.service import get_browser
        browser = get_browser()
        pid = browser.state.pid
        browser.close()
        if pid:
            import subprocess
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        pass
    bus.shutdown()
