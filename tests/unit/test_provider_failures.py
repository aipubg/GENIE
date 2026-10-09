"""Provider failure classification + failover contracts (spec section 8).

No secret values in fixtures.
"""
from __future__ import annotations

import pytest

from models import failures as F


# ------------------------------------------------------------------ classify
@pytest.mark.parametrize("message,kind", [
    ("connection refused", F.FailureKind.UNREACHABLE),
    ("request timed out", F.FailureKind.TIMEOUT),
    ("rate limit exceeded", F.FailureKind.RATE_LIMIT),
    ("quota exhausted for this billing period", F.FailureKind.QUOTA_EXHAUSTED),
    ("context length exceeded", F.FailureKind.CONTEXT_LIMIT),
    ("missing api key in vault", F.FailureKind.MISSING_CREDENTIAL),
    ("model gpt-x does not exist", F.FailureKind.MODEL_UNAVAILABLE),
    ("partial stream interrupted", F.FailureKind.PARTIAL_STREAM),
    ("malformed response body", F.FailureKind.MALFORMED),
    ("permission denied", F.FailureKind.PERMISSION_DENIED),
])
def test_messages_classify(message, kind):
    assert F.classify(Exception(message)) is kind


@pytest.mark.parametrize("status,kind", [
    (401, F.FailureKind.PERMISSION_DENIED),
    (403, F.FailureKind.PERMISSION_DENIED),
    (402, F.FailureKind.QUOTA_EXHAUSTED),
    (404, F.FailureKind.MODEL_UNAVAILABLE),
    (408, F.FailureKind.TIMEOUT),
    (429, F.FailureKind.RATE_LIMIT),
    (500, F.FailureKind.UNREACHABLE),
    (503, F.FailureKind.UNREACHABLE),
])
def test_status_classifies(status, kind):
    assert F.from_status(status) is kind


def test_unrecognised_is_unknown_and_still_failoverable():
    kind = F.classify(Exception("something entirely new"))
    assert kind is F.FailureKind.UNKNOWN
    assert F.failover_ok(kind) is True


def test_http_error_code_is_used_when_present():
    class HTTPErr(Exception):
        code = 429
    assert F.classify(HTTPErr("ignored text")) is F.FailureKind.RATE_LIMIT


# ------------------------------------------------------------------- policy
def test_partial_stream_is_marked_committed():
    """A stream that already emitted output must never be replayed from scratch."""
    kind = F.FailureKind.PARTIAL_STREAM
    assert F.has_committed_output(kind) is True
    assert F.retryable(kind) is False


def test_retryable_and_failover_flags_are_consistent():
    for kind in F.FailureKind:
        d = F.describe(kind)
        assert set(d) == {"kind", "retryable", "failover_ok", "committed"}
        assert d["kind"] == kind.value
        # anything non-retryable must still be allowed to fail over, or the
        # request would simply die on the first provider
        if not d["retryable"]:
            assert d["failover_ok"] is True or kind is F.FailureKind.NO_KEY_LOCAL


def test_missing_credential_is_not_retried_on_same_provider():
    kind = F.FailureKind.MISSING_CREDENTIAL
    assert F.retryable(kind) is False
    assert F.failover_ok(kind) is True


def test_context_limit_fails_over():
    """Context overflow must move to another (larger-context) candidate."""
    kind = F.FailureKind.CONTEXT_LIMIT
    assert F.retryable(kind) is False
    assert F.failover_ok(kind) is True


def test_describe_is_json_safe():
    import json
    for kind in F.FailureKind:
        json.dumps(F.describe(kind))
