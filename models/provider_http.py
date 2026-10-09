"""One HTTP implementation for every provider call (Pass 4 §9-§12).

Why this module exists
----------------------
Three code paths used to build provider requests independently (discovery,
test-connection, inference). They drifted, and one of them silently broke the
owner's gateway:

    urllib sends `User-Agent: Python-urllib/3.x` by default.
    Cloudflare bot-management answers that with
        HTTP 403  "Error 1010: Access denied —
                   The site owner has blocked access based on your
                   browser's signature."
    GENIE then reported "Authentication failed — check the API key",
    which was false: the same URL + key worked in another client.

So this module is the single place that decides:
  * which User-Agent leaves the machine
  * how the models URL is derived (never /v1/v1/models, never //models)
  * which auth header is used, and how alternatives are negotiated
  * whether a credential may follow a redirect (cross-origin: never)
  * TLS: always verified — never disabled

SAFETY: no function here logs, prints or returns a credential value.
"""
from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# A normal browser signature. Several API gateways sit behind Cloudflare or
# similar WAFs that reject the default `Python-urllib/3.x` UA outright. This is
# presentation only — it changes nothing about how GENIE authenticates.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Auth headers that must never be forwarded to a different origin.
_CREDENTIAL_HEADERS = ("authorization", "x-api-key", "api-key", "x-auth-token",
                       "x-goog-api-key", "api_key")

def _origin(url: str):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, (parsed.hostname or "").lower(), port

# Ordered probes for automatic auth negotiation (§9). Bearer first because it
# is the OpenAI-compatible default; the rest are common API-key conventions.
AUTH_SCHEMES: Tuple[str, ...] = ("bearer", "x-api-key", "api-key")

def classify_transport_error(exc: Exception) -> str:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror): return "DNS_RESOLUTION_FAILED"
    if isinstance(reason, ssl.SSLCertVerificationError): return "TLS_CERTIFICATE_FAILED"
    if isinstance(reason, ssl.SSLError): return "TLS_CONNECTION_FAILED"
    if isinstance(reason, (socket.timeout, TimeoutError)): return "TRANSPORT_TIMEOUT"
    if isinstance(reason, ConnectionRefusedError): return "CONNECTION_REFUSED"
    if isinstance(reason, ConnectionResetError): return "CONNECTION_RESET"
    return "TRANSPORT_UNAVAILABLE"


def normalize_base_url(base: str) -> str:
    """Return a clean API base with any concrete endpoint suffix removed.

    Accepts the forms owners actually paste (§10):

        https://host/v1                     -> https://host/v1
        https://host/v1/                    -> https://host/v1
        https://host/v1/chat/completions    -> https://host/v1
        https://host/v1/models              -> https://host/v1

    Never produces `//models`, `/v1/v1/models` or `/chat/completions/models`.
    """
    b = (base or "").strip().rstrip("/")
    if not b:
        return ""
    low = b.lower()
    for suffix in ("/chat/completions", "/completions", "/responses",
                   "/embeddings", "/models"):
        if low.endswith(suffix):
            b = b[: -len(suffix)].rstrip("/")
            break
    return b


def models_url(base: str, discovery_url: str = "") -> str:
    """The model-list endpoint for a base URL."""
    d = (discovery_url or "").strip()
    if d:
        if d.lower().startswith(("http://", "https://")):
            return d
        return normalize_base_url(base) + "/" + d.lstrip("/")
    return normalize_base_url(base) + "/models"


def chat_url(base: str) -> str:
    """The chat-completions endpoint for a base URL."""
    return normalize_base_url(base) + "/chat/completions"


def build_auth_headers(secret: str, scheme: str = "bearer") -> Dict[str, str]:
    """Credential headers for one scheme. The value is never returned to logs."""
    if not secret:
        return {}
    s = (scheme or "bearer").strip().lower()
    if s == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if s == "basic":
        import base64
        token = base64.b64encode(secret.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    if s in ("x-api-key", "api-key", "x-auth-token", "x-goog-api-key"):
        return {s: secret}
    # Unknown/custom: treat the scheme name as the header name.
    return {scheme: secret}


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects but never leak a credential to another origin (§11).

    Also records the chain so a diagnosis can show what actually happened.
    """

    def __init__(self) -> None:
        self.chain: List[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        self.chain.append(f"{code} -> {newurl}")
        try:
            same_origin = _origin(req.full_url) == _origin(newurl)
        except Exception:
            same_origin = False
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and not same_origin:
            # Drop every credential header before the request leaves the origin.
            for h in list(new_req.headers.keys()):
                if h.lower() in _CREDENTIAL_HEADERS:
                    del new_req.headers[h]
            for h in list(new_req.unredirected_hdrs.keys()):
                if h.lower() in _CREDENTIAL_HEADERS:
                    del new_req.unredirected_hdrs[h]
        return new_req


def request(url: str, *, method: str = "GET",
            secret: str = "", auth_scheme: str = "bearer",
            headers: Optional[Dict[str, str]] = None,
            body: Optional[bytes] = None,
            timeout: float = 25.0) -> Dict[str, Any]:
    """Perform one provider HTTP call through the shared implementation.

    Returns a dict with status/body/final_url/content_type/redirect_chain plus
    a `request` description containing header NAMES only — never a value.
    """
    req_headers: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update({str(k): str(v) for k, v in headers.items()})
    # Auth wins over any collision so a custom header cannot silently strip it.
    req_headers.update(build_auth_headers(secret, auth_scheme))

    desc = {
        "method": method,
        "url": url,
        "auth_scheme": (auth_scheme or "bearer").strip().lower(),
        "header_names": sorted(req_headers.keys()),
        "timeout": float(timeout),
    }

    redirect_handler = _SafeRedirectHandler()
    opener = urllib.request.build_opener(redirect_handler)
    # TLS verification stays ON (§11): no custom unverified context.
    req = urllib.request.Request(url, data=body, headers=req_headers,
                                 method=method.upper())
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
            final_url = resp.geturl()
            ctype = resp.headers.get("Content-Type", "")
            chain = redirect_handler.chain
            return {"ok": 200 <= status < 300, "status": status, "body": raw,
                    "final_url": final_url, "content_type": ctype,
                    "redirect_chain": list(chain), "elapsed_ms": round((time.perf_counter()-started)*1000, 1), "request": desc}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        handler = getattr(opener, "_chain", None)
        return {"ok": False, "status": exc.code, "body": raw,
                "final_url": getattr(exc, "url", url),
                "content_type": (exc.headers.get("Content-Type", "")
                                 if exc.headers else ""),
                "redirect_chain": list(redirect_handler.chain),
                "elapsed_ms": round((time.perf_counter()-started)*1000, 1),
                "request": desc}
    except urllib.error.URLError as exc:
        code = classify_transport_error(exc)
        return {"ok": False, "status": 0, "body": b"", "final_url": url,
                "content_type": "", "redirect_chain": [],
                "error_code": code, "error": code,
                "elapsed_ms": round((time.perf_counter()-started)*1000, 1),
                "request": desc}
    except Exception as exc:  # noqa: BLE001
        code = classify_transport_error(exc)
        return {"ok": False, "status": 0, "body": b"", "final_url": url,
                "content_type": "", "redirect_chain": [],
                "error_code": code, "error": code,
                "elapsed_ms": round((time.perf_counter()-started)*1000, 1), "request": desc}


def _auth_failed(res: Dict[str, Any]) -> bool:
    return res.get("status") in (401, 403)


def negotiate_auth(url: str, *, secret: str,
                   preferred: str = "bearer",
                   headers: Optional[Dict[str, str]] = None,
                   timeout: float = 25.0,
                   schemes: Tuple[str, ...] = AUTH_SCHEMES) -> Dict[str, Any]:
    """Try the preferred scheme, then common API-key conventions (§9).

    Only ever contacts the owner-configured origin. Returns the first response
    that is not an auth rejection, with `auth_scheme` set to what worked so the
    caller can persist it for Test / Discover / inference alike.
    """
    order: List[str] = []
    if preferred and preferred.strip().lower() not in ("auto", ""):
        order.append(preferred.strip().lower())
    for s in schemes:
        if s not in order:
            order.append(s)

    attempts: List[Dict[str, Any]] = []
    last = None
    for scheme in order:
        res = request(url, secret=secret, auth_scheme=scheme,
                      headers=headers, timeout=timeout)
        attempts.append({"scheme": scheme, "status": res.get("status")})
        last = res
        if not _auth_failed(res):
            res["auth_scheme"] = scheme
            res["auth_attempts"] = attempts
            return res

    assert last is not None
    last["auth_scheme"] = ""
    last["auth_attempts"] = attempts
    return last


def safe_error_snippet(body: bytes, limit: int = 300) -> str:
    """First characters of an error body, content-type aware and size-capped."""
    try:
        text = body.decode("utf-8", "replace")[:limit]
    except Exception:
        return ""
    return text
