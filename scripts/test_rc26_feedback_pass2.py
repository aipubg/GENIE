"""RC26 Owner Feedback PASS 2 — targeted tests (no full matrix).

Covers exactly the changes made in this pass:
  * Point 0  — custom-provider Advanced settings persist + are reused
  * Point 6  — mission intent (conversation vs mission) + orchestrator gate
  * Point 7  — browser ownership semantics + read-only preview contract

Run:  PYTHONPATH="." python scripts/test_rc26_feedback_pass2.py
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


# ===========================================================================
# Point 6 — mission intent classification
# ===========================================================================
def test_mission_intent() -> None:
    from director.heuristics import detect_mission_intent

    should_not = [
        "hello", "hey", "thanks", "how are you", "explain this",
        "rewrite this sentence", "What is 2+2?", "answer: hello",
        "GENIE tum kaise ho?", "what's the capital of France?",
    ]
    for text in should_not:
        mr, _ = detect_mission_intent(text)
        check(f"conversation: {text!r} is not a mission", mr is False)

    should = [
        "Create and grow a YouTube channel for me and keep running it.",
        "Every morning research these websites, prepare a report and email it.",
        "Continuously monitor companies/websites and send me useful findings.",
        "Go to ChatGPT in the browser, generate an image and bring me the result.",
        "Handle this multi-step project until the requested deliverable is complete.",
        "Every day upload/schedule/manage this content.",
    ]
    for text in should:
        mr, _ = detect_mission_intent(text)
        check(f"mission: {text[:40]!r}… is a mission", mr is True)

    # Recurrence is captured as a schedule hint.
    mr, sched = detect_mission_intent(
        "Every morning research these websites, prepare a report and email it.")
    check("schedule hint captured", mr is True and "morning" in sched, sched)

    # Action commands are NOT missions (no durable/autonomous signal).
    for text in ("volume 30", "Chrome kholo", "phone ka next song"):
        mr, _ = detect_mission_intent(text)
        check(f"action stays non-mission: {text!r}", mr is False)


def test_heuristic_director_flags() -> None:
    from core.contracts import CallContext
    from director.heuristics import HeuristicDirector

    d = HeuristicDirector()
    ctx = CallContext()
    conv = d.classify("hello", ctx)
    check("director: hello -> no mission", conv.mission_required is False)
    mis = d.classify("Every morning check these sites and email me a report.", ctx)
    check("director: recurring -> mission", mis.mission_required is True
          and mis.intent == "mission")
    act = d.classify("Chrome kholo", ctx)
    check("director: action -> not a mission", act.mission_required is False)


# ===========================================================================
# Point 6 — orchestrator gate (conversation creates no mission)
# ===========================================================================
class _Comp:
    text = "ok"


class _FakeMissions:
    def __init__(self):
        self.created = []

    def create(self, ctx, goal, **kw):
        from core.contracts import Mission
        self.created.append((goal, kw))
        return Mission(goal=goal, mission_id="mis_test")

    def transition(self, ctx, mission_id, state, reason=""):
        return None

    def update_step(self, *a, **k):
        return None

    def record_error(self, *a, **k):
        return None


class _FakeMemory:
    def write(self, *a, **k):
        return "rec1"

    def query(self, *a, **k):
        return []


class _FakeGateway:
    def complete(self, *a, **k):
        return _Comp()

    def stream(self, *a, **k):
        yield "ok", {}


class _FakeContext:
    def build(self, *a, **k):
        return {"sections": {}}


class _FakeAgents:
    step_runner = None


class _FakeDirector:
    def __init__(self, decision):
        self._d = decision

    def classify(self, text, ctx, hint=None):
        return self._d


def _orch(decision):
    from core.orchestrator import Orchestrator
    missions = _FakeMissions()
    o = Orchestrator(_FakeDirector(decision), missions, _FakeMemory(), _FakeGateway(),
                     computer=None, agent_runtime=_FakeAgents(),
                     context_builder=_FakeContext())
    return o, missions


def test_orchestrator_gate() -> None:
    from core.contracts import CallContext, TaskType
    from director.base import DirectorDecision, DirectorTask

    ctx = CallContext()

    # conversation
    o, m = _orch(DirectorDecision(reasoning_required=True, provider_category="reasoning"))
    out = o.handle_text("hello", ctx)
    check("orchestrator: conversation creates NO mission", m.created == [])
    check("orchestrator: conversation mission_id is None", out.get("mission_id") is None)
    check("orchestrator: conversation state", out.get("state") == "conversation")

    # mission
    o, m = _orch(DirectorDecision(reasoning_required=True, provider_category="reasoning",
                                  mission_required=True, intent="mission",
                                  schedule="every morning"))
    out = o.handle_text("Every morning check these sites and email a report.", ctx)
    check("orchestrator: mission IS created", len(m.created) == 1)
    check("orchestrator: mission carries schedule",
          bool(m.created) and m.created[0][1].get("schedule") == "every morning")
    check("orchestrator: mission_id returned", out.get("mission_id") == "mis_test")

    # action path still records a mission for step tracking
    o, m = _orch(DirectorDecision(tasks=[
        DirectorTask(type=TaskType.APPLICATION_ACTION, capability="application.open",
                     target="chrome")]))
    o.handle_text("open chrome", ctx)
    check("orchestrator: action path still tracks a mission", len(m.created) == 1)


# ===========================================================================
# Point 0 — custom-provider Advanced settings persist + are reused
# ===========================================================================
def test_provider_persistence() -> None:
    from models.registry import ModelRegistry

    tmp = Path(tempfile.mkdtemp(prefix="genie-pp-"))
    defaults = tmp / "providers.json"
    defaults.write_text(json.dumps({"version": 1, "providers": [
        {"id": "deepseek", "display_name": "DeepSeek", "protocol": "openai_chat",
         "base_url": "https://api.deepseek.com", "enabled": True, "priority": 100,
         "models": []}]}), encoding="utf-8")
    user = tmp / "providers.user.json"

    reg = ModelRegistry(defaults_file=defaults, user_file=user)
    reg.add_provider({
        "id": "custom1", "display_name": "My Custom", "protocol": "openai_chat",
        "base_url": "https://api.example.com/v1",
        "auth_scheme": "basic",
        "discovery_url": "https://api.example.com/v1/custom-models",
        "timeout": 7,
        "headers": {"X-Custom": "yes"},
        "custom": True,
        "models": [],
    })

    # Simulate a relaunch: a fresh registry over the same files.
    reg2 = ModelRegistry(defaults_file=defaults, user_file=user)
    p = reg2.provider("custom1") or {}
    check("persist: auth_scheme survives relaunch", p.get("auth_scheme") == "basic")
    check("persist: discovery_url survives relaunch",
          p.get("discovery_url") == "https://api.example.com/v1/custom-models")
    check("persist: timeout survives relaunch", p.get("timeout") == 7)
    check("persist: custom headers survive relaunch",
          p.get("headers") == {"X-Custom": "yes"})

    rows = {r["id"]: r for r in reg2.summary()}
    row = rows.get("custom1", {})
    check("summary exposes advanced fields",
          row.get("auth_scheme") == "basic" and row.get("timeout") == 7
          and row.get("headers") == {"X-Custom": "yes"})
    # Presence only: the summary reports whether a secret EXISTS, never its value.
    check("summary reports credential presence, not the value",
          isinstance(row.get("has_secret_ref"), bool)
          and "SECRET" not in json.dumps(row))


def test_discovery_reuses_persisted_settings() -> None:
    import urllib.request as _u
    from models.gateway import Gateway

    captured = {}

    class _Resp:
        status = 200

        def read(self):
            return b'{"data":[{"id":"m1"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["timeout"] = timeout
        return _Resp()

    orig = _u.urlopen
    _u.urlopen = fake_urlopen
    try:
        prov = {
            "id": "custom1", "base_url": "https://api.example.com/v1",
            "secret_ref": "secret://provider/custom1/key",
            "auth_scheme": "basic",
            "discovery_url": "https://api.example.com/v1/custom-models",
            "timeout": 7,
            "headers": {"X-Custom": "yes"},
            "protocol": "openai_chat", "models": [],
        }

        class _Reg:
            def provider(self, pid):
                return prov if pid == "custom1" else None

        class _Vault:
            def resolve(self, ref):
                return "SECRET"

        class _Policy:
            def check(self, *a, **k):
                return True

        gw = Gateway(_Reg(), _Vault(), _Policy(), db=object())
        out = gw.discover_models(provider_id="custom1")

        check("rediscover uses stored discovery_url",
              captured.get("url") == "https://api.example.com/v1/custom-models")
        check("rediscover uses stored timeout", captured.get("timeout") == 7)
        check("rediscover uses stored custom header",
              captured.get("headers", {}).get("x-custom") == "yes")
        expected_auth = "Basic " + base64.b64encode(b"SECRET").decode("ascii")
        check("rediscover uses stored auth scheme (basic)",
              captured.get("headers", {}).get("authorization") == expected_auth)
        check("rediscover returns models", out.get("ok") is True and out.get("count") == 1)
    finally:
        _u.urlopen = orig


# ===========================================================================
# Point 7 — browser ownership + read-only preview contract
# ===========================================================================
def test_browser_ownership() -> None:
    from browser import cdp

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    proc = _FakeProc(4242)
    cdp._LAUNCHED[proc.pid] = proc
    check("ownership: owned pid is listed", 4242 in cdp.owned_pids())
    stopped = cdp.shutdown_owned(4242)
    check("ownership: owned browser is stopped", stopped is True and proc.terminated)
    check("ownership: registry cleared after shutdown", 4242 not in cdp.owned_pids())
    # A pid GENIE never launched must never be touched.
    check("ownership: unrelated pid is NOT stopped", cdp.shutdown_owned(999999) is False)


def test_preview_contract() -> None:
    from browser.service import BrowserService

    tmp = Path(tempfile.mkdtemp(prefix="genie-bp-"))
    svc = BrowserService(port=9871, profile_dir=tmp / "browser-profile")
    out = svc.preview({})
    check("preview: no session -> honest inactive",
          out.get("ok") is False and out.get("active") is False
          and out.get("reason") == "no_session")
    # The preview endpoint is a GET and the service never forwards input.
    import inspect
    src = inspect.getsource(BrowserService.preview)
    check("preview: read-only (no input forwarding)",
          "Input.dispatch" not in src and "Input.insertText" not in src)


def test_safe_url() -> None:
    from core.ipc.server import _safe_url
    check("safe_url strips credentials",
          _safe_url("https://user:pass@example.com/path?token=abc#frag")
          == "https://example.com/path")
    check("safe_url keeps a bare url", _safe_url("https://example.com/") == "https://example.com/")


def main() -> int:
    print("RC26 feedback pass 2 — targeted tests")
    for fn in (test_mission_intent, test_heuristic_director_flags, test_orchestrator_gate,
               test_provider_persistence, test_discovery_reuses_persisted_settings,
               test_browser_ownership, test_preview_contract, test_safe_url):
        print(f"\n[{fn.__name__}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\nRESULT {_PASS}/{_PASS + _FAIL} passed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
