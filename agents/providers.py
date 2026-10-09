"""Model gateway + local executor (agents/providers.py) — Phase 10, §10.9–§10.13, §10.16.

Two things live here:

* :class:`ModelGateway` — agents request a *capability* ("coding/strong"), never a hardcoded
  provider. The gateway maps capability + quality to a priced tier and can **escalate** one tier up
  when evidence says the cheaper model was insufficient. Escalation is recorded, not guessed.

* :class:`LocalExecutor` — a **controlled, offline** model provider that does *real work*: it writes
  actual source files, runs a real ``pytest`` subprocess, and posts real mailbox / blackboard
  messages. It is labelled honestly as ``protocol/behavior verified (controlled local provider)`` —
  it proves the team machinery end to end without needing live API keys. When live providers are
  wired in later, they slide in behind the same gateway interface; the golden missions do not change.

The executor never returns a hardcoded "ok": a broken module produces a failing test, which the
reviewer rejects. That is the whole point — the system must catch real failures, not pretend they
passed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_setup import get_logger
from agents.contracts import ArtifactKind, Message, MessageType

log = get_logger("agents.providers")

PYTHON = sys.executable  # the interpreter running GENIE — used to run the real test subprocess


# ----------------------------------------------------------------------- model gateway
class ModelGateway:
    """Capability → provider tier. No agent says "use provider X forever" (D-090)."""

    #: Ordered cheapest → most capable. price = per model-call + per 1k tokens.
    TIERS: Dict[str, Dict[str, Any]] = {
        "local":  {"rank": 0, "price_per_call": 0.0,   "price_per_1k": 0.0,
                   "supports": {"cheap-fast", "deterministic", "coding", "research"}},
        "cheap":  {"rank": 1, "price_per_call": 0.0004, "price_per_1k": 0.0002,
                   "supports": {"coding", "research", "vision"}},
        "strong": {"rank": 2, "price_per_call": 0.01,   "price_per_1k": 0.002,
                   "supports": {"coding", "reasoning", "research"}},
        "expert": {"rank": 3, "price_per_call": 0.05,   "price_per_1k": 0.01,
                   "supports": {"reasoning", "research"}},
    }

    #: quality word → starting tier
    QUALITY_TIER = {"weak": "local", "cheap": "cheap", "standard": "cheap",
                    "strong": "strong", "high": "expert", "": "cheap"}

    def __init__(self):
        self._failures: Dict[str, int] = {}
        self._escalations: List[Dict[str, Any]] = []

    # -- routing ----------------------------------------------------------------
    def select(self, capability: str, quality: str = "standard", *,
               budget_hint_usd: Optional[float] = None) -> str:
        """Cheapest tier that supports the capability and (roughly) fits the budget hint."""
        start = self.QUALITY_TIER.get(str(quality), "cheap")
        ordered = sorted(self.TIERS, key=lambda t: self.TIERS[t]["rank"])
        for tier in ordered:
            if capability not in self.TIERS[tier]["supports"]:
                continue
            if self.TIERS[tier]["rank"] < self.TIERS[start]["rank"]:
                continue  # do not drop below the requested quality
            if budget_hint_usd is not None and self.price(tier)["per_call"] > budget_hint_usd:
                continue
            return tier
        return start

    # -- escalation (§10.11) ----------------------------------------------------
    def escalate(self, current_tier: str, *, reason: str, evidence: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
        """Move one tier up. Must be measurable — the evidence is recorded."""
        cur_rank = self.TIERS.get(current_tier, self.TIERS["cheap"])["rank"]
        higher = [t for t in self.TIERS if self.TIERS[t]["rank"] == cur_rank + 1]
        target = higher[0] if higher else current_tier
        record = {"from_tier": current_tier, "to_tier": target, "reason": reason,
                  "evidence": evidence or {}, "ts": int(time.time() * 1000)}
        self._escalations.append(record)
        return record

    @property
    def escalations(self) -> List[Dict[str, Any]]:
        return list(self._escalations)

    # -- pricing ----------------------------------------------------------------
    def price(self, tier: str) -> Dict[str, float]:
        spec = self.TIERS.get(tier, self.TIERS["cheap"])
        return {"per_call": spec["price_per_call"], "per_1k_tokens": spec["price_per_1k"]}

    def estimate(self, tier: str, *, model_calls: int = 1, tokens: int = 0) -> float:
        p = self.price(tier)
        return round(model_calls * p["per_call"] + (tokens / 1000.0) * p["per_1k_tokens"], 6)

    # -- failover (§10.12) ------------------------------------------------------
    def next(self, *, exclude: List[str]) -> str:
        """Any other tier — used when a provider dies mid-mission."""
        for tier in sorted(self.TIERS, key=lambda t: self.TIERS[t]["rank"]):
            if tier not in exclude:
                return tier
        return "local"

    def record_failure(self, provider: str, reason: str = "") -> None:
        self._failures[provider] = self._failures.get(provider, 0) + 1
        log.info("circuit breaker: provider %s failure #%d (%s)", provider,
                 self._failures[provider], reason)

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0

    def health(self) -> Dict[str, int]:
        return dict(self._failures)


# ----------------------------------------------------------------------- local executor
@dataclass
class LocalExecutor:
    """A controlled, offline provider that performs real work for the golden missions.

    It writes genuine files, runs a real test subprocess, and posts real structured messages. It is
    NOT a stub returning ``{"ok": True}`` — a malformed module fails the test, which the reviewer
    rejects.
    """

    artifact_root: str
    artifacts: Any = None          # ArtifactService
    blackboard: Any = None         # Blackboard
    mailbox: Any = None            # Mailbox
    budgets: Any = None           # BudgetController
    gateway: Optional[ModelGateway] = None
    mission_id: str = ""
    provider: str = "local"
    #: for golden #7 — fail this many *calls* in, then report provider_unavailable
    fail_after_calls: Optional[int] = None
    _calls: int = field(default=0, init=False)
    _produced: Dict[str, str] = field(default_factory=dict, init=False)  # module -> path

    # ---- the runner contract -------------------------------------------------
    def __call__(self, agent: Any, task: Any, context: Dict[str, Any]) -> Dict[str, Any]:
        self._calls += 1
        if self.fail_after_calls is not None and self._calls > self.fail_after_calls:
            # Provider A just died mid-mission. The orchestrator classifies this and the service
            # performs the failover (snapshot → switch → continue).
            return {"ok": False, "error": f"provider {self.provider} unavailable",
                    "error_code": "provider_unavailable"}

        role = str(agent.role)
        kind = str(task.inputs.get("kind", ""))
        tier = self.provider
        started = time.time()
        tokens = int(task.inputs.get("tokens", 300))

        charge = self._charge(agent.agent_id, tier, tokens=tokens, tool_calls=1)
        if not charge.get("ok"):
            # budget exhausted: do not let the agent keep spending
            return {"ok": False, "error": charge.get("error", "budget exhausted"),
                    "error_code": "budget_exhausted", "provider": tier,
                    "model_calls": 1, "cost_usd": 0.0,
                    "latency_ms": int((time.time() - started) * 1000)}

        if kind == "design" or role in ("architect", "lead"):
            result = self._design(agent, task, context, tier)
        elif kind == "test" or role in ("tester",):
            result = self._test(agent, task, context, tier)
        elif kind == "review" or role == "reviewer":
            result = self._review(agent, task, context, tier)
        else:
            result = self._module(agent, task, context, tier)

        charged_usd = charge.get("charged_usd", 0.0)
        est_usd = self.gateway.estimate(tier, tokens=tokens) if self.gateway else 0.0
        result["provider"] = tier
        result["model_calls"] = 1
        result["cost_usd"] = round(charged_usd + est_usd, 6)
        result["latency_ms"] = int((time.time() - started) * 1000)
        return result

    # ---- budget ---------------------------------------------------------------
    def _charge(self, agent_id: str, tier: str, *, tokens: int, tool_calls: int) -> Dict[str, Any]:
        per_call = self.gateway.price(tier)["per_call"] if self.gateway else 0.0
        per_1k = self.gateway.price(tier)["per_1k_tokens"] if self.gateway else 0.0
        cost = round(per_call + (tokens / 1000.0) * per_1k, 6)
        if self.budgets is None:
            return {"ok": True, "charged_usd": cost}
        return self.budgets.spend(f"agent:{agent_id}", tokens=tokens, cost_usd=cost,
                                  model_calls=1, tool_calls=tool_calls)

    # ---- work ----------------------------------------------------------------
    def _write_artifact(self, agent_id: str, task: Any, artifact_kind: str, name: str,
                        content: str, *, deps: Optional[List[str]] = None):
        artifact_dir = Path(self.artifact_root)
        # create the directory if needed — a missing artifact root is a setup detail, not a
        # task failure, and silently blowing up here looks like "the agent did nothing"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = str(artifact_dir / name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        artifact = self.artifacts.register_file(
            path, producer_agent=agent_id, mission_id=self.mission_id, task_id=task.task_id,
            dependencies=deps or [], kind=artifact_kind) if self.artifacts else None
        art_id = artifact.artifact_id if artifact else name
        if self.mailbox and task.task_id:
            self.mailbox.send(Message(from_agent=agent_id, to_agent="*",
                                      type=MessageType.ARTIFACT_READY.value,
                                      subject=f"{name} ready", body=f"{artifact_kind} {name}",
                                      artifact_id=art_id, task_id=task.task_id))
        return art_id

    def _design(self, agent: Any, task: Any, context: Dict[str, Any], tier: str) -> Dict[str, Any]:
        """The architect/lead writes the interface contract, not code. It lands on the
        blackboard so every downstream agent shares one decision, not a transcript."""
        spec = task.inputs.get("spec") or {
            "module": "genie_app",
            "functions": [{"name": "add", "args": ["a", "b"]},
                          {"name": "subtract", "args": ["a", "b"]}]}
        module = spec["module"]
        lines = [f"# Design — {task.objective}", "",
                 f"Module: `{module}`", "", "Functions:"]
        for fn in spec["functions"]:
            args = ", ".join(fn.get("args", []))
            lines.append(f"- `{fn['name']}({args})`")
        content = "\n".join(lines) + "\n"
        art_id = self._write_artifact(agent.agent_id, task, ArtifactKind.DOCUMENT.value,
                                      "design.md", content)
        if self.blackboard:
            self.blackboard.write("interface", f"{module}.api",
                                  {"module": module,
                                   "functions": [f["name"] for f in spec["functions"]]},
                                  author=agent.agent_id)
        return {"ok": True, "verdict": "accept", "artifacts": [art_id]}

    def _module(self, agent: Any, task: Any, context: Dict[str, Any], tier: str) -> Dict[str, Any]:
        spec = task.inputs.get("spec") or {"module": "genie_app", "functions": [
            {"name": "add", "args": ["a", "b"], "body": "return a + b"},
            {"name": "subtract", "args": ["a", "b"], "body": "return a - b"}]}
        module = spec["module"]
        lines = [f'"""{module} — generated by {agent.role} agent ({tier})."""',
                 "from __future__ import annotations", "", ""]
        for fn in spec["functions"]:
            args = ", ".join(fn["args"])
            lines.append(f"def {fn['name']}({args}):")
            lines.append(f"    {fn['body']}")
            lines.append("")
        content = "\n".join(lines)
        art_id = self._write_artifact(agent.agent_id, task, ArtifactKind.CODE.value,
                                      f"{module}.py", content)
        self._produced[module] = str(Path(self.artifact_root) / f"{module}.py")
        if self.blackboard:
            self.blackboard.write("interface", f"{module}.api",
                                  {"module": module,
                                   "functions": [f["name"] for f in spec["functions"]]},
                                  author=agent.agent_id)
        return {"ok": True, "verdict": "accept", "artifacts": [art_id]}

    def _test(self, agent: Any, task: Any, context: Dict[str, Any], tier: str) -> Dict[str, Any]:
        # consume the dependency artifact (the module) by reference, not by copying a transcript
        deps = self._dependency_artifact_ids(context)
        module = task.inputs.get("module") or "genie_app"
        spec = task.inputs.get("spec")
        if spec is None and self.blackboard:
            api = self.blackboard.read(f"{module}.api")
            spec = {"module": module,
                    "functions": [{"name": f} for f in (api or {}).get("functions", [])]}
        funcs = (spec or {}).get("functions", [{"name": "add"}, {"name": "subtract"}])
        test_lines = ["import sys, pathlib",
                      "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))",
                      f"from {module} import {', '.join(f['name'] for f in funcs)}", "", ""]
        cases = task.inputs.get("cases") or [
            {"fn": funcs[0]["name"], "args": [2, 3], "expect": 5},
            {"fn": funcs[1]["name"] if len(funcs) > 1 else funcs[0]["name"],
             "args": [5, 3], "expect": 2 if len(funcs) > 1 else 5}]
        for i, case in enumerate(cases):
            test_lines.append(f"def test_{case['fn']}_{i}():")
            test_lines.append(f"    assert {case['fn']}({', '.join(str(a) for a in case['args'])})"
                              f" == {case['expect']}")
            test_lines.append("")
        content = "\n".join(test_lines)
        art_id = self._write_artifact(agent.agent_id, task, ArtifactKind.CODE.value,
                                      "test_genie_app.py", content, deps=deps)
        ran = self._run_pytest("test_genie_app.py")
        ok = ran["failed"] == 0 and ran["passed"] > 0
        if self.blackboard:
            self.blackboard.write("decision", f"tests.{module}",
                                  {"passed": ran["passed"], "failed": ran["failed"],
                                   "verdict": "green" if ok else "red"}, author=agent.agent_id)
        return {"ok": ok, "verdict": "accept" if ok else "reject", "artifacts": [art_id],
                "test_passed": ran["passed"], "test_failed": ran["failed"],
                "test_output": ran["output"][:2000]}

    def _review(self, agent: Any, task: Any, context: Dict[str, Any], tier: str) -> Dict[str, Any]:
        # the reviewer reads facts from the blackboard + artifact references — never a transcript
        bb = context.get("blackboard", {})
        test_verdict = "green"
        for d in bb.get("decisions", []):
            if str(d.get("key", "")).startswith("tests."):
                test_verdict = d.get("value", {}).get("verdict", "green")
        refs = context.get("artifact_refs", [])
        modules = [r for r in refs if str(r.get("name", "")).endswith(".py")]
        accepted = test_verdict == "green" and len(modules) >= 1
        reason = ("all tests green and at least one module artifact referenced"
                  if accepted else f"test verdict={test_verdict}, modules={len(modules)}")
        if self.blackboard:
            self.blackboard.write("decision", f"review.{task.task_id}",
                                  {"accepted": accepted, "reason": reason}, author=agent.agent_id)
        return {"ok": accepted, "verdict": "accept" if accepted else "reject",
                "reason": reason, "artifacts": []}

    # ---- helpers -------------------------------------------------------------
    def _dependency_artifact_ids(self, context: Dict[str, Any]) -> List[str]:
        return [r.get("artifact_id") for r in (context.get("artifact_refs", []) or [])
                if r.get("artifact_id")]

    def _run_pytest(self, test_file: str) -> Dict[str, Any]:
        proc = subprocess.run(
            [PYTHON, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider"],
            cwd=self.artifact_root, capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        passed = failed = 0
        for line in out.splitlines():
            if "passed" in line or "failed" in line:
                for m in re.finditer(r"(\d+)\s+passed", line):
                    passed += int(m.group(1))
                for m in re.finditer(r"(\d+)\s+failed", line):
                    failed += int(m.group(1))
        return {"passed": passed, "failed": failed, "output": out,
                "returncode": proc.returncode}
