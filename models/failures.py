"""Provider failure classification (upgrade spec section 8).

One canonical place that turns a raw provider exception or HTTP status into a
stable failure kind. Everything else - gateway failover, health, UI status,
diagnostics - reads from here instead of guessing from message strings.

Why this exists
---------------
Failover must behave differently per failure class:

    missing_credential   retrying another model on the SAME provider is pointless
    context_limit        fail over to a LARGER context model, not just the next one
    rate_limit / quota   back off or fail over; count separately in health
    timeout              transient - candidate for retry with backoff
    unreachable          endpoint down - fail over to another provider
    malformed            provider contract broken - fail over, do not retry
    partial_stream       output already emitted: NEVER replay from scratch
    model_unavailable    that model is gone on this provider - pick another model

Guidance consumed by callers:
    retryable   worth another attempt (possibly after backoff)
    failover_ok safe to try a different provider/model for the same request
    committed   partial output/actions may already exist; do not blindly replay
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, Optional


class FailureKind(str, Enum):
    MISSING_CREDENTIAL = "missing_credential"
    NO_KEY_LOCAL = "no_key_local"          # intentionally credential-free endpoint
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    CONTEXT_LIMIT = "context_limit"
    UNREACHABLE = "unreachable"
    MALFORMED = "malformed"
    PARTIAL_STREAM = "partial_stream"
    MODEL_UNAVAILABLE = "model_unavailable"
    PERMISSION_DENIED = "permission_denied"
    UNKNOWN = "unknown"


# kind -> (retryable, failover_ok, committed)
_POLICY: Dict[FailureKind, tuple] = {
    FailureKind.MISSING_CREDENTIAL:   (False, True,  False),
    FailureKind.NO_KEY_LOCAL:         (False, False, False),
    FailureKind.TIMEOUT:              (True,  True,  False),
    FailureKind.RATE_LIMIT:           (True,  True,  False),
    FailureKind.QUOTA_EXHAUSTED:      (False, True,  False),
    FailureKind.CONTEXT_LIMIT:        (False, True,  False),
    FailureKind.UNREACHABLE:          (True,  True,  False),
    FailureKind.MALFORMED:            (False, True,  False),
    FailureKind.PARTIAL_STREAM:       (False, True,  True),
    FailureKind.MODEL_UNAVAILABLE:    (False, True,  False),
    FailureKind.PERMISSION_DENIED:    (False, True,  False),
    FailureKind.UNKNOWN:              (True,  True,  False),
}


def retryable(kind: "FailureKind") -> bool:
    return _POLICY.get(kind, (True, True, False))[0]


def failover_ok(kind: "FailureKind") -> bool:
    return _POLICY.get(kind, (True, True, False))[1]


def has_committed_output(kind: "FailureKind") -> bool:
    """True when replaying the request could duplicate work or side effects."""
    return _POLICY.get(kind, (True, True, False))[2]


# ------------------------------------------------------------------ status
def from_status(status: int, *, body: str = "") -> FailureKind:
    if status in (401, 403):
        text = (body or "").lower()
        if "quota" in text or "billing" in text or "credit" in text:
            return FailureKind.QUOTA_EXHAUSTED
        return FailureKind.PERMISSION_DENIED
    if status == 402:
        return FailureKind.QUOTA_EXHAUSTED
    if status == 404:
        return FailureKind.MODEL_UNAVAILABLE
    if status == 408 or status == 504:
        return FailureKind.TIMEOUT
    if status == 429:
        text = (body or "").lower()
        if "quota" in text or "exceeded" in text and "rate" not in text:
            return FailureKind.QUOTA_EXHAUSTED
        return FailureKind.RATE_LIMIT
    if status == 413 or status == 400 and "context" in (body or "").lower():
        return FailureKind.CONTEXT_LIMIT
    if 500 <= status < 600:
        return FailureKind.UNREACHABLE
    return FailureKind.UNKNOWN


# ------------------------------------------------------------------ message
_PATTERNS = [
    (FailureKind.TIMEOUT,
     re.compile(r"timed? ?out|timeout|deadline exceed", re.I)),
    (FailureKind.RATE_LIMIT,
     re.compile(r"rate limit|too many requests|429", re.I)),
    (FailureKind.QUOTA_EXHAUSTED,
     re.compile(r"quota|insufficient (credit|balance)|billing|out of credits", re.I)),
    (FailureKind.CONTEXT_LIMIT,
     re.compile(r"context (length|window|limit)|maximum context|too many tokens|"
                r"reduce the length|prompt is too long", re.I)),
    (FailureKind.MISSING_CREDENTIAL,
     re.compile(r"missing api key|no api key|api key not|api[_ ]key[_ ]invalid|"
                r"invalid api key|valid api key|unauthorized|401|"
                r"secret ref not resolved", re.I)),
    (FailureKind.MODEL_UNAVAILABLE,
     re.compile(r"model .* (not found|does not exist|unavailable)|unknown model|404", re.I)),
    (FailureKind.MALFORMED,
     re.compile(r"malformed|invalid json|expecting value|not valid json|"
                r"unexpected token", re.I)),
    (FailureKind.PARTIAL_STREAM,
     re.compile(r"partial (stream|output)|stream (broke|interrupt|aborted)|"
                r"incomplete chunk", re.I)),
    (FailureKind.UNREACHABLE,
     re.compile(r"connection (refused|reset|aborted|error)|no route to host|"
                r"name or service not known|dns|unreachable|network", re.I)),
    (FailureKind.PERMISSION_DENIED,
     re.compile(r"permission denied|forbidden|403", re.I)),
]


def classify(exc: Any, *, status: Optional[int] = None, body: str = "") -> FailureKind:
    """Classify an exception (and optional HTTP status/body) into a FailureKind."""
    if status is not None:
        kind = from_status(status, body=body)
        if kind is not FailureKind.UNKNOWN:
            return kind

    text = ""
    if exc is not None:
        text = str(exc)
        # urllib HTTPError carries the status; use it when available
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            kind = from_status(code, body=text)
            if kind is not FailureKind.UNKNOWN:
                return kind
        if isinstance(exc, TimeoutError) or exc.__class__.__name__ in (
                "TimeoutError", "socket.timeout", "URLError"):
            name = exc.__class__.__name__
            if "timeout" in name.lower() or "Timeout" in name:
                return FailureKind.TIMEOUT
            return FailureKind.UNREACHABLE

    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return FailureKind.UNKNOWN


def describe(kind: FailureKind) -> Dict[str, object]:
    return {
        "kind": kind.value,
        "retryable": retryable(kind),
        "failover_ok": failover_ok(kind),
        "committed": has_committed_output(kind),
    }


__all__ = [
    "FailureKind", "classify", "describe", "from_status",
    "retryable", "failover_ok", "has_committed_output",
]
