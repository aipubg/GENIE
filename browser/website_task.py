"""General bounded website-task execution — PLAN -> ACT -> OBSERVE -> VERIFY -> NEXT STEP.

This module is the *plan executor* for website tasks. It is deliberately NOT a
second browser system, NOT a second planner and NOT a second execution authority:

* planning comes from the existing director path (``director.heuristics.plan_web_action``),
* every action is dispatched through the **existing capability system** (the same
  ``browser.*`` capabilities any caller can invoke),
* observation and verification use the reusable primitives ``browser.observe`` /
  ``browser.verify`` / ``browser.detect_gate``.

Its only job is to give a website task the structure the owner asked for:

    an ordered, BOUNDED plan whose steps carry executable capabilities,
    dependencies, expected observations and verification conditions — executed
    step by step with a real receipt per step.

Boundedness:
  * at most ``max_steps`` steps are executed (the rest are reported NOT ATTEMPTED),
  * each step is bounded by ``step_timeout_s``,
  * at most ONE recovery attempt per step, driven by a FRESH observation (so an
    obsolete selector is re-resolved instead of blindly replayed),
  * cancellation is checked BETWEEN steps and belongs to the active run.

A model's statement that something happened is never a receipt: a step is only
``succeeded`` when the capability reports ok AND the verification condition (if
any) holds.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

MAX_STEPS = 8
DEFAULT_STEP_TIMEOUT_S = 90.0

#: capabilities whose params accept an explicit browser choice (never substituted)
_SWITCHABLE = {
    "browser.navigate", "browser.media.play", "browser.fullscreen",
    "browser.chatgpt.image", "browser.website_task",
}

#: capabilities where a stale selector is recoverable from a fresh observation
_RECOVERABLE = {"browser.act", "browser.fill", "browser.click", "browser.type"}


def _verify_of(result: Dict[str, Any]) -> Dict[str, Any]:
    v = result.get("verify")
    return v if isinstance(v, dict) else {}


def _receipt(result: Dict[str, Any]) -> str:
    v = _verify_of(result)
    return str(v.get("detail") or result.get("detail") or result.get("error") or "")[:300]


class WebsiteTaskEngine:
    """Execute an annotated website plan with per-step observation and verification."""

    def __init__(self, dispatch: Callable[[str, Dict[str, Any]], Dict[str, Any]],
                 cancel_event: Optional[threading.Event] = None,
                 max_steps: int = MAX_STEPS,
                 step_timeout_s: float = DEFAULT_STEP_TIMEOUT_S):
        self.dispatch = dispatch
        self.cancel_event = cancel_event
        self.max_steps = int(max_steps)
        self.step_timeout_s = float(step_timeout_s)

    # ------------------------------------------------------------------ helpers
    def _cancel_requested(self) -> bool:
        return bool(self.cancel_event is not None and self.cancel_event.is_set())

    def _call(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch one capability, bounded in time.

        The underlying capabilities already bound their own internal waits; this
        is the outer bound. A call that overruns is reported as a timeout — the
        worker thread is abandoned as a daemon, never awaited forever.
        """
        box: Dict[str, Any] = {}

        def _work() -> None:
            try:
                box["r"] = self.dispatch(capability, params)
            except Exception as exc:  # noqa: BLE001 - a step must never kill the run
                box["r"] = {"ok": False, "error": str(exc)}

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(self.step_timeout_s)
        if t.is_alive():
            return {"ok": False, "error": f"{capability} exceeded {self.step_timeout_s:.0f}s",
                    "timeout": True,
                    "verify": {"verified": False,
                               "detail": f"step timed out after {self.step_timeout_s:.0f}s"}}
        return box.get("r") or {"ok": False, "error": f"{capability} returned no result"}

    def _observe(self) -> Dict[str, Any]:
        """Fresh observation of the current page (never cached, never assumed)."""
        obs = self._call("browser.observe", {}) or {}
        return {
            "url": obs.get("url", ""),
            "title": obs.get("title", ""),
            "controls": len(obs.get("interactive") or []),
            "gate": (obs.get("gate") or {}).get("gate", ""),
            "ok": bool(obs.get("ok")),
        }

    def _with_browser(self, capability: str, params: Dict[str, Any],
                      browser: str) -> Dict[str, Any]:
        p = dict(params or {})
        if browser and capability in _SWITCHABLE and not p.get("browser"):
            p["browser"] = browser
        # Carry the authorized browser MODE (owner-existing vs GENIE-owned) so the
        # executor never silently substitutes one for the other. Only forwarded for
        # capabilities that actually accept a browser choice.
        if capability in _SWITCHABLE and "browser_mode" not in p and "browser_mode" in (params or {}):
            p["browser_mode"] = params["browser_mode"]
        return p

    # --------------------------------------------------------------------- run
    def run(self, plan: List[Dict[str, Any]], browser: str = "",
            timeout_s: Optional[float] = None) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        not_attempted: List[Dict[str, Any]] = []
        status_by_id: Dict[str, str] = {}
        cancelled = False
        failed = False
        cancel_boundary = "none"

        bounded = list(plan or [])[: self.max_steps]
        overflow = list(plan or [])[self.max_steps:]
        for idx, extra in enumerate(overflow, start=self.max_steps + 1):
            not_attempted.append({"index": idx, "capability": extra.get("capability", ""),
                                  "status": "not_attempted",
                                  "reason": f"plan exceeded the {self.max_steps}-step bound"})

        prev_id = ""
        for idx, step in enumerate(bounded, start=1):
            capability = str(step.get("capability", ""))
            sid = str(step.get("id") or f"s{idx}")
            params = dict(step.get("params") or {})
            depends = list(step.get("depends_on") or ([prev_id] if prev_id else []))
            expect = str(step.get("expect", ""))
            verify = dict(step.get("verify") or {})

            # ---- dependent-step failure: never run after an unrecovered step ----
            unmet = [d for d in depends
                     if status_by_id.get(d) in ("failed", "blocked", "skipped",
                                                "cancelled", "timeout")]
            if unmet:
                status_by_id[sid] = "skipped"
                not_attempted.append({"index": idx, "capability": capability,
                                      "status": "not_attempted",
                                      "reason": f"depends on {','.join(unmet)} which did not succeed"})
                continue

            # ---- cancellation belongs to THIS run: checked between steps ----
            if self._cancel_requested():
                cancelled = True
                cancel_boundary = f"before_step_{idx}"
                status_by_id[sid] = "cancelled"
                steps.append({"index": idx, "id": sid, "capability": capability,
                              "status": "cancelled", "ok": False, "verified": False,
                              "receipt": "cancellation requested; this step was NOT started",
                              "expect": expect})
                for j, rest in enumerate(bounded[idx:], start=idx + 1):
                    not_attempted.append({"index": j,
                                          "capability": rest.get("capability", ""),
                                          "status": "not_attempted",
                                          "reason": "cancelled before this step"})
                break

            if capability == "plan.unsupported":
                status_by_id[sid] = "blocked"
                failed = True
                steps.append({"index": idx, "id": sid, "capability": capability,
                              "status": "blocked", "ok": False, "verified": False,
                              "receipt": f"no executor support for this step: "
                                         f"{str(params.get('clause', ''))!r}",
                              "expect": expect})
                prev_id = sid
                continue

            started = time.time()
            # ---- ACT ----
            out = self._call(capability, self._with_browser(capability, params, browser))
            act_ok = bool(out.get("ok"))
            attempts = 1

            # ---- OBSERVE ----
            # A refused attachment must not launch a fallback browser through
            # observation; a timed-out worker may still own the CDP connection.
            obs = self._observe() if act_ok else {}

            # ---- VERIFY ----
            checks: List[Dict[str, Any]] = []
            verify_ok: Optional[bool] = None
            if verify and act_ok:
                vr = self._call("browser.verify", verify) or {}
                checks = list(vr.get("checks") or [])
                verify_ok = bool(vr.get("ok"))

            # ---- bounded RECOVERY: exactly one retry from a fresh observation ----
            if ((not act_ok or verify_ok is False) and capability in _RECOVERABLE
                    and step.get("recovery", True) is not False):
                recovery_params = dict(params)
                # drop a stale selector; re-resolve from the live page text
                recovery_params.pop("selector", None)
                if capability == "browser.click":
                    recovery_params["capability_fallback"] = "browser.act"
                out = self._call(capability if capability != "browser.click" else "browser.act",
                                 self._with_browser(capability, recovery_params, browser))
                act_ok = bool(out.get("ok"))
                attempts = 2
                obs = self._observe()
                if verify:
                    vr = self._call("browser.verify", verify) or {}
                    checks = list(vr.get("checks") or [])
                    verify_ok = bool(vr.get("ok"))

            own = _verify_of(out)
            own_ok = own.get("verified")
            # An explicit expected result that is OBSERVED is stronger evidence
            # than the capability's own self-check (which can only report "no
            # observable change" for an in-page effect).
            if verify and verify_ok is True:
                verified = act_ok
            else:
                verified = act_ok and (verify_ok is not False) and (own_ok is not False)

            if out.get("blocked"):
                status = "blocked"
            elif obs.get("gate") and not act_ok:
                status = "blocked"
            elif out.get("timeout"):
                status = "timeout"
            elif act_ok and verified:
                status = "succeeded"
            elif act_ok:
                status = "succeeded_unverified"
            else:
                status = "failed"

            status_by_id[sid] = status
            receipt = _receipt(out)
            if verify and verify_ok is False:
                receipt = (receipt + " | verification failed: "
                           + "; ".join(f"{c.get('check')}={'ok' if c.get('ok') else 'no'}"
                                       for c in checks))[:300]
            if obs.get("gate") and not act_ok:
                receipt = f"{obs['gate']} gate detected: {receipt}"[:300]

            steps.append({
                "index": idx, "id": sid, "capability": capability,
                "status": status, "ok": act_ok, "verified": verified,
                "receipt": receipt, "expect": expect,
                "observation": obs, "checks": checks, "attempts": attempts,
                "duration_s": round(time.time() - started, 2),
                "blocked": bool(out.get("blocked")) or bool(obs.get("gate") and not act_ok),
                "needs_owner": bool(out.get("needs_owner")),
            })
            if status in ("failed", "blocked", "timeout"):
                failed = True
            prev_id = sid

        cancel_requested = self._cancel_requested()
        if cancel_requested and not cancelled:
            cancel_boundary = "after_all_steps"

        if cancelled:
            state = "CANCELLED"
        elif failed:
            state = "FAILED"
        else:
            state = "COMPLETED"

        return {
            "ok": state == "COMPLETED",
            "state": state,
            "steps": steps,
            "not_attempted": not_attempted,
            "plan": [{"id": s.get("id") or f"s{i}", "capability": s.get("capability"),
                      "depends_on": s.get("depends_on"), "expect": s.get("expect"),
                      "verify": s.get("verify")}
                     for i, s in enumerate(bounded, start=1)],
            "bounded_plan": True,
            "cancel_requested": cancel_requested,
            "cancel_boundary": cancel_boundary,
            "max_steps": self.max_steps,
        }
