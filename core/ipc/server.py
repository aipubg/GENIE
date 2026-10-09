"""Local API server (core/ipc/server).

UI (Electron / any future 3D frontend) talks ONLY to this API over HTTP + SSE.
Assumption A-014: stdlib http.server + SSE instead of FastAPI/WebSockets, so the daemon
stays dependency-free and light on low-end PCs. Swap later behind the same contract.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from core.events import get_bus
from core.logging_setup import get_logger

log = get_logger("core.ipc")

MAX_BODY = 2_000_000
UI_HTML = """
<!doctype html><html><head><meta charset="utf-8"><title>GENIE</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font-family:Segoe UI,system-ui,sans-serif;background:#12131a;color:#e6e7ee}
 .app{display:flex;height:100vh}
 aside{width:240px;background:#171823;border-right:1px solid #262837;padding:14px;overflow:auto}
 main{flex:1;display:flex;flex-direction:column}
 #log{flex:1;overflow:auto;padding:18px}
 .msg{margin:8px 0;padding:10px 12px;border-radius:10px;max-width:760px;white-space:pre-wrap}
 .user{background:#24304a;margin-left:auto}
 .genie{background:#1d2130;border:1px solid #2b3040}
 .meta{font-size:11px;opacity:.55;margin-top:4px}
 form{display:flex;gap:8px;padding:12px;border-top:1px solid #262837;background:#14151d}
 input{flex:1;padding:10px;border-radius:8px;border:1px solid #2b3040;background:#1b1d29;color:#e6e7ee}
 button{padding:10px 16px;border-radius:8px;border:0;background:#4f7cff;color:#fff;cursor:pointer}
 h3{font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.6;margin:14px 0 6px}
 .pill{display:inline-block;font-size:11px;padding:3px 8px;border-radius:999px;background:#232838;margin:2px 4px 2px 0}
 .ok{color:#5ddc9a}.bad{color:#ff7b7b}
</style></head><body>
<div class="app">
 <aside>
  <div style="font-weight:700;font-size:18px">GENIE</div>
  <div class="meta" id="status">loading…</div>
  <h3>Providers</h3><div id="providers"></div>
  <h3>Missions</h3><div id="missions"></div>
  <h3>Actions</h3>
  <div><button onclick="dryRun()">Dry-run: Chrome kholo</button></div>
 </aside>
 <main>
   <div id="log"></div>
   <form onsubmit="send(event)">
     <input id="text" placeholder="GENIE se poochho… (Hinglish supported)" autocomplete="off"/>
     <button>Send</button>
   </form>
 </main>
</div>
<script>
const log = document.getElementById('log');
function add(role, text, meta){const d=document.createElement('div');d.className='msg '+role;
 d.textContent=text; if(meta){const m=document.createElement('div');m.className='meta';m.textContent=meta;d.appendChild(m);}
 log.appendChild(d); log.scrollTop=log.scrollHeight;}
async function send(e){e.preventDefault();const i=document.getElementById('text');const t=i.value.trim();
 if(!t)return; i.value=''; add('user',t);
 const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({text:t})}).then(r=>r.json());
 add('genie', r.reply || r.error || '(no reply)', r.mission_id? 'mission '+r.mission_id+' · '+(r.state||'')+' · trace '+r.trace_id : '');
 refresh();}
async function dryRun(){const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({text:'Chrome kholo', dry_run:true})}).then(r=>r.json());
 add('genie', '[dry-run] '+(r.reply||r.error), r.mission_id||''); refresh();}
async function refresh(){
 const s=await fetch('/api/status').then(r=>r.json());
 document.getElementById('status').innerHTML =
   'ready: '+s.ready+' · director: '+s.director.engine+' ('+(s.director.runtime||'')+')';
 document.getElementById('providers').innerHTML = (s.providers||[]).map(p=>
   '<span class="pill">'+p.display_name+': '+p.models.length+'</span>').join('');
 const m=await fetch('/api/missions').then(r=>r.json());
 document.getElementById('missions').innerHTML = (m.missions||[]).slice(0,8).map(x=>
   '<span class="pill '+(x.state==='COMPLETED'?'ok':(x.state==='FAILED'?'bad':''))+'">'+x.goal.slice(0,22)+' · '+x.state+'</span>').join('') || '<span class="meta">none</span>';
}
refresh();
const es=new EventSource('/api/events');
es.onmessage=(e)=>{try{const d=JSON.parse(e.data); if(['MISSION_COMPLETED','PERMISSION_DENIED','PROVIDER_FAILOVER','MEMORY_UPDATED'].includes(d.type)) console.log('event',d.type,d.payload);}catch(_){}};
</script></body></html>
"""


def _human_plan(steps: Any) -> list:
    """A human-readable plan for the dashboard — never raw internal JSON."""
    out = []
    for s in steps or []:
        try:
            deps = json.loads(s.get("depends_on") or "[]")
        except Exception:
            deps = []
        out.append({
            "step_id": s.get("step_id", ""),
            "objective": s.get("objective") or s.get("capability") or "(step)",
            "status": s.get("status", "pending"),
            "role": s.get("required_role", "worker"),
            "criteria": s.get("completion_criteria", ""),
            "depends_on": deps,
            "attempt": int(s.get("attempt") or 0),
            "provider": s.get("provider_used", "") or "",
        })
    return out


def _safe_url(url: str) -> str:
    """Strip credentials and query/fragment so a preview never leaks a token."""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        if not parts.scheme:
            return url[:200]
        netloc = parts.netloc.split("@")[-1]
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:300]
    except Exception:
        return url[:200]


class Handler(BaseHTTPRequestHandler):
    daemon = None  # injected

    # ------------------------------------------------------------- plumbing
    def log_message(self, fmt: str, *args) -> None:  # silence default access log
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, code: int, data: Any) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length or length > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _query(self) -> Dict[str, list]:
        return parse_qs(urlparse(self.path).query)

    def _plugins(self):
        service = (self.daemon.services or {}).get("plugins") if self.daemon else None
        if service is None:
            raise RuntimeError("plugin service is not available")
        return service

    def _voice(self):
        voice = (self.daemon.services or {}).get("voice") if self.daemon else None
        if voice is None:
            raise RuntimeError("voice service is not enabled on this daemon")
        return voice

    def _skills(self):
        service = (self.daemon.services or {}).get("skills") if self.daemon else None
        if service is None:
            raise RuntimeError("skill service is not available")
        return service

    # ----------------------------------------------- dedicated browser (Point 7)
    def _browser_service(self):
        """The GENIE-owned browser service (same workspace root the executor
        uses), or None when the runtime is unavailable."""
        try:
            from browser.service import get_browser
            from core.paths import data_dir
            return get_browser(workspace_root=str(data_dir() / "workspace"))
        except Exception:
            return None

    def _browser_payload(self) -> Dict[str, Any]:
        """Honest browser status in owner language. Never reports 'Not detected'
        as if GENIE were randomly probing — it states the real session state."""
        try:
            from browser import cdp
            installed = cdp.find_browser() or ""
        except Exception:
            installed = ""
        svc = self._browser_service()
        if svc is None:
            return {"state": "unavailable", "available": False,
                    "message": "The browser runtime is not available.",
                    "chrome": installed}
        try:
            st = svc.status()
        except Exception as exc:
            return {"state": "failed", "available": False,
                    "message": "The browser failed to start.", "error": str(exc)}
        pages = int(st.get("pages") or 0)
        pid = int(st.get("pid") or 0)
        if not installed:
            state = "unavailable"
            message = "No Chrome or Edge installation was found on this PC."
        elif pages > 0 or pid:
            state = "working"
            message = "GENIE's browser session is active."
        else:
            state = "inactive"
            message = "No browser task is active."
        title = url = ""
        if state == "working":
            try:
                info = svc.page_info()
                title = str(info.get("title") or "")[:200]
                url = _safe_url(str(info.get("url") or ""))
            except Exception:
                pass
        return {"state": state, "available": bool(installed), "message": message,
                "title": title, "url": url, "profile": st.get("profile", ""),
                "pid": pid, "pages": pages, "chrome": installed}

    def _browser_frame(self) -> None:
        """Read-only PNG frame of GENIE's current page. 204 when no session."""
        svc = self._browser_service()
        if svc is None:
            self._send(204, b"", "image/png")
            return
        try:
            out = svc.preview({})
        except Exception:
            self._send(204, b"", "image/png")
            return
        if not out.get("ok"):
            self._send(204, b"", "image/png")
            return
        try:
            with open(out["path"], "rb") as fh:
                data = fh.read()
        except Exception:
            self._send(204, b"", "image/png")
            return
        self._send(200, data, "image/png")

    def _agents(self):
        service = (self.daemon.services or {}).get("agents_service") if self.daemon else None
        if service is None:
            raise RuntimeError("agent service is not available")
        return service

    def _control_center(self):
        """Phase 13 — the three answers the owner is entitled to, from live services."""
        from core.controlcenter import ControlCenter
        services = (self.daemon.services or {}) if self.daemon else {}
        return ControlCenter(memory=services.get("memory"),
                             registry=services.get("registry"),
                             agents=services.get("agents_service"),
                             audit=services.get("audit"),
                             gateway=services.get("gateway"))

    def _proactive(self):
        service = (self.daemon.services or {}).get("proactive") if self.daemon else None
        if service is None:
            raise RuntimeError("proactive service is not available")
        return service

    def _perception(self):
        service = (self.daemon.services or {}).get("perception") if self.daemon else None
        if service is None:
            raise RuntimeError("perception service is not available")
        return service

    def _devices(self):
        service = (self.daemon.services or {}).get("devices") if self.daemon else None
        if service is None:
            raise RuntimeError("device service is not available")
        return service

    def _experience(self):
        """Experience bank state. Reports unavailable rather than empty+confident
        when the authority is not registered, so "no lessons" is never confused
        with "no evidence system"."""
        bank = (self.daemon.services or {}).get("experience") if self.daemon else None
        if bank is None:
            return {"available": False, "reason": "experience bank is not registered",
                    "lessons": [], "task_types": []}
        try:
            lessons = bank.all_lessons(limit=50)
        except Exception as exc:
            return {"available": False, "reason": f"experience bank read failed: {exc}",
                    "lessons": [], "task_types": []}
        task_types = sorted({str(x.get("task_type") or "") for x in lessons if x.get("task_type")})
        return {"available": True, "lessons": lessons, "task_types": task_types,
                "lesson_count": len(lessons)}

    def _knowledge_service(self):
        """The service itself (for writes). Returns None when unregistered so a
        caller can answer honestly instead of raising a 500."""
        return (self.daemon.services or {}).get("knowledge") if self.daemon else None

    def _knowledge(self):
        """Imported sources. Reports unavailable rather than an empty list that
        reads as 'nothing imported' when no store is registered."""
        svc = self._knowledge_service()
        if svc is None:
            return {"available": False, "reason": "knowledge service is not registered",
                    "sources": [], "source_count": 0, "indexed_item_count": 0}
        try:
            return svc.status()
        except Exception as exc:
            log.debug("knowledge status failed: %s", exc)
            return {"available": False, "reason": f"knowledge read failed: {exc}",
                    "sources": [], "source_count": 0, "indexed_item_count": 0}

    def _media(self):
        """Produced artifacts: what missions and agents actually made.

        Backed by the artifact registry, so an item carries its type, producing
        mission, producing agent, timestamp, path and hash. Files are never
        copied just to be displayed - the reference is stored, not the bytes.
        """
        svc = (self.daemon.services or {}).get("artifacts") if self.daemon else None
        if svc is None:
            return {"available": False, "reason": "artifact service is not registered",
                    "items": [], "count": 0}
        q = self._query()
        try:
            return svc.media(mission_id=(q.get("mission") or [""])[0],
                             kind=(q.get("kind") or [""])[0],
                             limit=int((q.get("limit") or ["100"])[0] or 100))
        except Exception as exc:
            log.debug("media read failed: %s", exc)
            return {"available": False, "reason": f"media read failed: {exc}",
                    "items": [], "count": 0}

    def _experience_digest(self, *, mission_id="", capability="", agent="",
                          provider=""):
        """Durable learning, filterable by mission / capability / agent / provider.

        Every number is a count of recorded rows. When there are none, the
        digest is honestly empty rather than dressed up with plausible advice.
        """
        bank = (self.daemon.services or {}).get("experience") if self.daemon else None
        if bank is None:
            return {"available": False,
                    "reason": "experience bank is not registered"}
        try:
            digest = bank.digest(mission_id=mission_id, capability=capability,
                                 agent=agent, provider=provider)
        except Exception as exc:                        # noqa: BLE001
            log.debug("experience digest failed: %s", exc)
            return {"available": False, "reason": f"experience digest failed: {exc}"}
        digest["available"] = True
        digest["filters"] = {"mission": mission_id, "capability": capability,
                             "agent": agent, "provider": provider}
        return digest

    def _competition_status(self):
        """Competition engine state + its recorded outcomes.

        The engine is invoked per task and holds no long-lived store, so the
        honest history source is the audit trail the engine itself writes
        (action=agent.competition). Nothing here is synthesised.
        """
        from agents.competition import (MAX_CANDIDATES, OUTCOME_CANCELLED,
                                        OUTCOME_NEEDS_REVISION, OUTCOME_NO_CANDIDATES,
                                        OUTCOME_WINNER)
        agents = (self.daemon.services or {}).get("agents_service") if self.daemon else None
        audit = (self.daemon.services or {}).get("audit") if self.daemon else None
        engine = getattr(agents, "competition", None) if agents else None
        recent: list = []
        if audit is not None:
            try:
                recent = [e for e in audit.tail(limit=200)
                          if e.get("action") == "agent.competition"][:25]
            except Exception as exc:
                log.debug("competition history read failed: %s", exc)
        return {
            "available": engine is not None,
            "reason": None if engine is not None else "agent service is not available",
            "max_candidates": MAX_CANDIDATES,
            "outcomes": [OUTCOME_WINNER, OUTCOME_NEEDS_REVISION,
                         OUTCOME_CANCELLED, OUTCOME_NO_CANDIDATES],
            # The safety rule the client must be able to show the owner:
            # only the winner commits, so no side effect can happen twice.
            "commit_policy": "winner_only",
            "recent": recent,
        }

    def _static(self, rel: str) -> None:
        """Serve the UI bundle from ui/web. The UI is a separate, replaceable layer."""
        from pathlib import Path
        ui_dir = Path(__file__).resolve().parents[2] / "ui" / "web"
        target = (ui_dir / rel).resolve()
        if not str(target).startswith(str(ui_dir.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".webp": "image/webp",
        }.get(target.suffix, "application/octet-stream")
        return self._send(200, target.read_bytes(), ctype)

    def _brand_asset(self, rel: str) -> None:
        """Serve owner brand assets from ui/assets/brand (path-traversal guarded).

        The brand tree lives outside ui/web so the canonical owner assets and
        their manifest stay separate from the replaceable UI bundle.
        """
        from pathlib import Path
        brand_dir = Path(__file__).resolve().parents[2] / "ui" / "assets" / "brand"
        target = (brand_dir / rel).resolve()
        if not str(target).startswith(str(brand_dir.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = {
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".md": "text/markdown; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        return self._send(200, target.read_bytes(), ctype)

    # ----------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        d = self.daemon
        try:
            if path == "/health":
                return self._json(200, {"ok": d.ready if d else False})
            if path == "/ui" or path == "/":
                return self._static("index.html")
            if path == "/ui/ops":
                return self._static("ops.html")
            if path == "/ui/control":
                return self._static("control.html")
            if path.startswith("/ui/assets/brand/"):
                return self._brand_asset(path[len("/ui/assets/brand/"):])
            if path.startswith("/ui/"):
                return self._static(path[len("/ui/"):])
            if path == "/api/status":
                return self._json(200, d.status())
            if path == "/api/action/approvals":
                if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                    return self._json(403, {"error": "Action confirmation is available in the desktop app."})
                return self._json(200, {"pending": d.services["computer"].approvals.pending()})
            if path == "/api/chat/history":
                query = parse_qs(urlparse(self.path).query)
                before = max(0, int(query.get("before", ["0"])[0]))
                messages = d.services["session_store"].history(before=before)
                return self._json(200, {"messages": messages})
            if path == "/api/missions":
                # Pass 3 — the dashboard needs progress + next run, not just state.
                ms = d.services["missions"]
                out = []
                for m in ms.list(50):
                    item = m.to_dict()
                    try:
                        item["progress"] = ms.progress(m.mission_id)
                        sched = ms.schedule_of(m.mission_id)
                        item["next_run_ms"] = (sched or {}).get("next_run_ms", 0)
                    except Exception:
                        pass
                    out.append(item)
                return self._json(200, {"missions": out})
            if path.startswith("/api/missions/") and path.endswith("/plan"):
                mid = path.split("/")[3]
                ms = d.services["missions"]
                if not ms.get(mid):
                    return self._json(200, {"error": "not found"})
                return self._json(200, {"mission_id": mid, "plan": _human_plan(ms.steps(mid)),
                                        "progress": ms.progress(mid)})
            if path.startswith("/api/missions/") and path.endswith("/progress"):
                mid = path.split("/")[3]
                ms = d.services["missions"]
                if not ms.get(mid):
                    return self._json(200, {"error": "not found"})
                return self._json(200, {"mission_id": mid, "progress": ms.progress(mid),
                                        "schedule": ms.schedule_of(mid)})
            if path.startswith("/api/missions/"):
                mid = path.rsplit("/", 1)[1]
                ms = d.services["missions"]
                m = ms.get(mid)
                if not m:
                    return self._json(200, {"error": "not found"})
                item = m.to_dict()
                try:
                    item["progress"] = ms.progress(mid)
                    item["schedule_row"] = ms.schedule_of(mid)
                    item["plan"] = _human_plan(ms.steps(mid))
                except Exception:
                    pass
                return self._json(200, item)
            if path == "/api/memory":
                q = (self._query().get("q") or [""])[0]
                from core.contracts import CallContext
                hits = d.services["memory"].query(CallContext(), q, limit=10)
                return self._json(200, {"hits": [h.to_dict() for h in hits]})
            if path == "/api/audit":
                return self._json(200, {"entries": d.services["audit"].tail(50)})
            if path == "/api/providers":
                reg = d.services["registry"]
                elig = d.services.get("eligibility")
                prows = reg.summary()
                if elig is not None:
                    # Point 1 — surface the automatic runtime eligibility state so
                    # Settings can show a status badge instead of an owner toggle.
                    for p in prows:
                        st = elig.status(p["id"])
                        p["runtime_status"] = st["status"]
                        p["runtime_detail"] = st.get("detail", "")
                        p["runtime_eligible"] = st["eligible"]
                return self._json(200, {"providers": prows})

            # Point 1.6 — Advanced routing diagnostics only. Not shown in Normal
            # mode. Reveals the last selection + current provider eligibility.
            if path == "/api/routing/diagnostics":
                gw = d.services.get("gateway")
                elig = d.services.get("eligibility")
                return self._json(200, {
                    "last_selection": (gw.last_selection() if gw else {}),
                    "eligibility": (elig.snapshot() if elig else []),
                })

            # Built-in provider catalog for the Add Provider picker. Single
            # source of truth: the shipped config defaults, so the client never
            # duplicates provider URLs. Test/stub providers are excluded here —
            # they are not something an owner should be offered.
            if path == "/api/providers/catalog":
                try:
                    from pathlib import Path
                    raw = json.loads(
                        Path(d.services["registry"].defaults_file).read_text(encoding="utf-8")
                    )
                except Exception:
                    raw = {"providers": []}
                items = []
                for p in raw.get("providers", []):
                    if str(p.get("kind", "")).lower() == "test":
                        continue
                    if str(p.get("protocol", "")).lower() == "mock":
                        continue
                    items.append({
                        "id": p.get("id", ""),
                        "display_name": p.get("display_name") or p.get("name") or p.get("id", ""),
                        "base_url": p.get("base_url", ""),
                        "protocol": p.get("protocol", "openai_chat"),
                        "requires_user_config": bool(p.get("requires_user_config", False)),
                    })
                return self._json(200, {"catalog": items})

            # Deprecated: legacy role preferences (everyday/fast/deep_work).
            # The Model Gateway is the active routing authority; these roles are
            # retained only as compatibility metadata and not used for routing.
            if path == "/api/models/roles":
                return self._json(200, {"roles": d.services["registry"].roles(), "deprecated": True})
            if path == "/api/events":
                return self._sse()
            if path == "/api/locks":
                return self._json(200, {"locks": d.services["locks"].holders()})
            if path == "/api/plugins":
                return self._json(200, self._plugins().status())
            if path == "/api/plugins/health":
                q = self._query().get("plugin")
                return self._json(200, self._plugins().health((q or [None])[0]))
            if path == "/api/voice":
                return self._json(200, self._voice().status())
            if path == "/api/desktop":
                computer = d.services.get("computer")
                if computer is None:
                    return self._json(503, {"ok": False, "error": "computer service unavailable"})
                awareness = computer.desktop_awareness
                return self._json(200, {"ok": True, "status": awareness.status,
                                        "settings": awareness.settings.to_dict()})
            if path == "/api/owner-policy":
                if (self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site")
                        or self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost")):
                    return self._json(403, {"ok": False, "error": "Use native desktop owner settings."})
                computer = d.services.get("computer")
                if computer is None:
                    return self._json(503, {"ok": False, "error": "computer service unavailable"})
                return self._json(200, computer.owner_policy.status())
            if path == "/api/desktop/frame":
                computer = d.services.get("computer")
                if computer is None:
                    return self._json(503, {"ok": False})
                index = int((self._query().get("index") or ["0"])[0])
                frame = computer.desktop_awareness.preview_frame(index)
                return self._send(200 if frame else 404, frame, "image/bmp")
            if path == "/api/voice/devices":
                from voice import devices as voice_devices
                return self._json(200, voice_devices.summary())
            if path == "/api/voice/levels":
                return self._json(200, self._voice().levels())
            if path == "/api/voice/setup":
                return self._json(200, self._voice().live.setup_status())
            if path == "/api/local-awareness":
                return self._json(200, d.services["local_monitor"].status())
            if path == "/api/local-awareness/events":
                query = (self._query().get("q") or [""])[0][:200]
                return self._json(200, {"events": d.services["local_monitor"].observations.recent(40, query)})
            if path == "/api/local-awareness/frame":
                monitor = d.services["local_monitor"]
                frame = monitor.preview()
                return self._send(200 if frame else 404, frame, "image/jpeg")

            # Where GENIE is actually running and what confines it (§19-§20).
            # Reported, never assumed: container/VM are heuristics and an
            # isolated desktop can only be declared, not detected.
            if path == "/api/isolation":
                from core import isolation as isolation_mod
                from core.paths import data_dir
                # Point 7 — the dedicated browser profile lives UNDER the
                # workspace (the executor launches it there). Reporting a
                # different path made a real profile read as "Not detected".
                return self._json(200, isolation_mod.report(
                    workspace_root=str(data_dir() / "workspace"),
                    browser_profile=str(data_dir() / "workspace" / "browser-profile")))

            # Point 7 — dedicated GENIE browser: honest status + a read-only
            # preview frame. The preview is observation only; no owner input is
            # ever forwarded to the browser.
            if path == "/api/browser/status":
                return self._json(200, self._browser_payload())
            if path == "/api/browser/frame":
                return self._browser_frame()
            if path == "/api/voice/metrics":
                v = self._voice()
                return self._json(200, {"summary": v.metrics.summary(),
                                        "recent": v.metrics.recent(20),
                                        "db": v.metrics.db_summary()})
            if path == "/api/voice/profiles":
                return self._json(200, self._voice().profiles.status())
            if path == "/api/skills":
                q = self._query()
                return self._json(200, {"skills": self._skills().list(
                    status=(q.get("status") or [None])[0],
                    scope=(q.get("scope") or [None])[0])})
            if path == "/api/skills/stats":
                return self._json(200, self._skills().stats())
            if path == "/api/skills/search":
                from core.contracts import CallContext
                q = (self._query().get("q") or [""])[0]
                return self._json(200, self._skills().search(CallContext(), q))
            if path == "/api/skills/duplicates":
                sid = (self._query().get("skill_id") or [""])[0]
                return self._json(200, self._skills().duplicates(sid))
            if path == "/api/skills/corrections":
                sid = (self._query().get("skill_id") or [""])[0]
                return self._json(200, {"corrections": self._skills().corrections(sid)})
            if path == "/api/teaching":
                return self._json(200, self._skills().teaching_status())
            if path == "/api/devices":
                return self._json(200, self._devices().status())
            if path == "/api/devices/health":
                q = self._query().get("device")
                return self._json(200, self._devices().health((q or [""])[0]))
            if path == "/api/devices/commands":
                q = self._query().get("device")
                return self._json(200, {"commands": self._devices().recent_commands(
                    (q or [""])[0])})
            if path == "/api/agents":
                return self._json(200, self._agents().status())
            # Pass 3 — mission-scoped specialist agents. These are the real roles
            # the mission's plan spawned (AgentService creates one per role), not
            # invented rows. Empty when no mission is live.
            if path == "/api/agents/missions":
                ms = d.services.get("missions")
                out = []
                if ms is not None:
                    for m in ms.list(50):
                        if m.state.value in ("COMPLETED", "FAILED", "CANCELLED"):
                            continue
                        steps = ms.steps(m.mission_id)
                        if not steps:
                            continue
                        roles, seen = [], set()
                        for s in steps:
                            r = (s.get("required_role") or "worker")
                            if r in seen:
                                continue
                            seen.add(r)
                            current = next((x for x in steps
                                            if (x.get("required_role") or "worker") == r
                                            and x.get("status") in ("running", "ready",
                                                                    "pending")), None)
                            roles.append({
                                "agent": f"{r} specialist",
                                "role": r,
                                "mission_id": m.mission_id,
                                "mission": (m.goal or "")[:120],
                                "task": (current or {}).get("objective", "")[:120],
                                "state": m.state.value,
                                "provider": (current or {}).get("provider_used", "") or "",
                            })
                        out.extend(roles)
                return self._json(200, {"agents": out})

            if path == "/api/agents/teams":
                return self._json(200, {"teams": [a.to_dict()
                                                  for a in self._agents()._teams.values()]})
            if path.startswith("/api/agents/teams/"):
                mission_id = path[len("/api/agents/teams/"):]
                orchestrator = self._agents().team(mission_id)
                return self._json(200, orchestrator.status() if orchestrator
                                  else {"error": "not found"})
            if path.startswith("/api/agents/continuation/"):
                mission_id = path[len("/api/agents/continuation/"):]
                packet = self._agents().continuation_packet(mission_id)
                return self._json(200, {**packet.to_dict(), "text": packet.render()})
            # --- operator observability & control surface (Phase 11 §11) ---
            if path == "/api/ops/dashboard":
                return self._json(200, self._agents().ops_dashboard())
            if path == "/api/security/findings":
                svc = (d.services or {}).get("security_findings")
                if svc is None:
                    return self._json(200, {"registered": False, "findings": [],
                                            "note": "findings store not registered on this daemon"})
                sev = (self._query().get("severity") or [None])[0]
                items = ([f for f in svc.all() if f.severity == sev]
                         if sev else svc.all())
                return self._json(200, {"registered": True,
                                        "summary": svc.summary(),
                                        "findings": [f.to_dict() for f in items]})

            if path == "/api/forecast/calibration":
                svc = (d.services or {}).get("forecast")
                if svc is None:
                    return self._json(200, {"registered": False,
                                            "note": "forecast service not registered"})
                return self._json(200, {"registered": True,
                                        **svc.calibration_report()})

            if path == "/api/ops/audit":
                q = self._query()
                return self._json(200, {"audit": self._agents().ops_audit(
                    limit=int((q.get("limit") or ["25"])[0] or 25))})
            if path == "/api/ops/outcomes":
                q = self._query()
                return self._json(200, {"outcomes": self._agents().ops_outcomes(
                    limit=int((q.get("limit") or ["50"])[0] or 50))})
            if path == "/api/ops/improve":
                return self._json(200, {"improvements": self._agents().ops_improve()})
            # --- user control center (Phase 13): memory / agents / providers ---
            if path == "/api/control-center":
                q = self._query()
                return self._json(200, self._control_center().overview(
                    memory_limit=int((q.get("memory_limit") or ["25"])[0] or 25)))
            # --- wrong-action rate (Phase 14 exit gate) ---
            if path == "/api/metrics/actions":
                services = (self.daemon.services or {}) if self.daemon else {}
                computer = services.get("computer")
                if computer is None:
                    return self._json(200, {"available": False,
                                            "reason": "computer service not available"})
                status = computer.action_meter.status()
                status["available"] = True
                status["recent"] = computer.action_meter.recent(limit=25)
                return self._json(200, status)
            if path == "/api/knowledge":
                return self._json(200, self._knowledge())
            if path == "/api/knowledge/search":
                q = self._query()
                svc = self._knowledge_service()
                hits = []
                if svc is not None:
                    hits = svc.search((q.get("q") or [""])[0],
                                      limit=int((q.get("limit") or ["50"])[0] or 50))
                return self._json(200, {"available": svc is not None,
                                        "query": (q.get("q") or [""])[0],
                                        "hits": hits})
            if path == "/api/media":
                return self._json(200, self._media())
            if path == "/api/experience":
                return self._json(200, self._experience())
            if path == "/api/experience/digest":
                q = self._query()
                return self._json(200, self._experience_digest(
                    mission_id=(q.get("mission") or [""])[0],
                    capability=(q.get("capability") or [""])[0],
                    agent=(q.get("agent") or [""])[0],
                    provider=(q.get("provider") or [""])[0]))
            if path == "/api/competition":
                return self._json(200, self._competition_status())
            if path == "/api/proactive":
                return self._json(200, self._proactive().status())
            if path == "/api/proactive/history":
                q = self._query()
                return self._json(200, {"history": self._proactive().notifier.history(
                    limit=int((q.get("limit") or ["50"])[0] or 50),
                    delivered_only=bool(q.get("delivered")))})
            if path.startswith("/api/proactive/explain/"):
                return self._json(200, self._proactive().explain(
                    path[len("/api/proactive/explain/"):]))
            if path == "/api/perception":
                return self._json(200, self._perception().status())
            if path == "/api/perception/zones":
                return self._json(200, {"zones": [z.to_dict()
                                                  for z in self._perception().zones()]})
            if path == "/api/perception/events":
                q = self._query()
                return self._json(200, {"events": self._perception().recent_events(
                    limit=int((q.get("limit") or ["50"])[0] or 50),
                    zone_id=(q.get("zone") or [""])[0])})
            if path == "/api/perception/environment":
                q = self._query()
                return self._json(200, self._perception().environment(
                    (q.get("zone") or [""])[0]))
            if path == "/api/peripherals":
                return self._json(200, self._devices().peripherals.status())
            if path == "/api/devices/sync":
                return self._json(200, self._devices().sync.status())
            if path == "/api/devices/sync/records":
                q = self._query()
                return self._json(200, {"records": self._devices().sync.records(
                    (q.get("namespace") or [""])[0],
                    since_cursor=int((q.get("since") or ["0"])[0] or 0))})
            if path == "/api/devices/sync/conflicts":
                q = self._query()
                return self._json(200, {"conflicts": self._devices().sync.conflicts(
                    unresolved_only=bool(q.get("unresolved")),
                    namespace=(q.get("namespace") or [""])[0])})
            if path.startswith("/api/devices/"):
                record = self._devices().registry.get(path[len("/api/devices/"):])
                return self._json(200, record.to_dict() if record
                                  else {"error": "not found"})
            if path.startswith("/api/skills/"):
                rest = path[len("/api/skills/"):]
                if rest.endswith("/versions"):
                    return self._json(200, {"versions": self._skills().versions(rest[:-9])})
                skill = self._skills().get(rest)
                return self._json(200, skill if skill else {"error": "not found"})
            return self._json(404, {"error": "not found"})
        except Exception as exc:
            log.exception("GET %s failed", path)
            return self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        d = self.daemon
        body = self._body()
        try:
            if path == "/api/action/approvals/resolve":
                if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                    return self._json(403, {"error": "Use the desktop action confirmation."})
                ok = d.services["computer"].approvals.resolve(str(body.get("id", "")), body.get("allow"))
                return self._json(200 if ok else 409, {"ok": ok})
            if path == "/api/chat":
                out = d.chat(body.get("text", ""), session_id=body.get("session_id", "owner"),
                             dry_run=bool(body.get("dry_run", False)),
                             person_id=body.get("person_id", "owner"))
                return self._json(200, out)

            if path == "/api/chat/stream":
                return self._chat_stream(body)

            if path == "/api/missions/create":
                from core.contracts import CallContext
                ctx = CallContext()
                m = d.services["missions"].create(ctx, body.get("goal", ""))
                return self._json(200, m.to_dict())

            if path.startswith("/api/missions/") and path.endswith("/cancel"):
                from core.contracts import CallContext
                mid = path.split("/")[3]
                m = d.services["missions"].cancel(CallContext(), mid)
                d.services["missions"].clear_schedule(mid)
                return self._json(200, m.to_dict())

            if path.startswith("/api/missions/") and path.endswith("/delete"):
                from core.contracts import CallContext
                from missions.service import MissionError
                try:
                    deleted = d.services["missions"].delete(CallContext(), path.split("/")[3])
                    return self._json(200, {"ok": True, "deleted": deleted})
                except MissionError as exc:
                    return self._json(200, {"ok": False, "error": str(exc)})

            # Pass 3 — mission control (also reachable by chat/voice).
            if path.startswith("/api/missions/") and path.endswith("/run"):
                mid = path.split("/")[3]
                runner = d.services.get("mission_runner")
                if runner is None:
                    return self._json(200, {"ok": False,
                                            "error": "mission runner unavailable"})
                return self._json(200, runner.execute(mid))

            if path.startswith("/api/missions/") and path.endswith("/pause"):
                mid = path.split("/")[3]
                from core.contracts import MissionState
                d.services["missions"].set_fields(mid, state=MissionState.PAUSED.value)
                agents = d.services.get("agents_service")
                if agents is not None:
                    try:
                        agents.pause(mid, reason="owner")
                    except Exception:
                        pass
                return self._json(200, {"ok": True, "mission_id": mid, "state": "PAUSED"})

            if path.startswith("/api/missions/") and path.endswith("/resume"):
                mid = path.split("/")[3]
                from core.contracts import MissionState
                d.services["missions"].set_fields(mid, state=MissionState.RUNNING.value)
                runner = d.services.get("mission_runner")
                out = runner.execute(mid) if runner is not None else {}
                return self._json(200, {"ok": True, "mission_id": mid, **out})

            if path == "/api/memory/write":
                from core.contracts import CallContext
                rid = d.services["memory"].write(
                    CallContext(), type=body.get("type", "semantic"),
                    entity=body.get("entity", ""), value=body.get("value", ""),
                    confidence=float(body.get("confidence", 0.7)))
                return self._json(200, {"record_id": rid})

            # --- competition workflow (§16-§18) ---
            # A competition runs candidates against ONE task and commits the
            # verified winner only. Side effects are impossible for a loser
            # because `commit` is invoked at most once, for the winner.
            if path == "/api/competition/run":
                return self._json(200, self._agents().compete_task(
                    str(body.get("mission_id") or ""), str(body.get("task_id") or ""),
                    requested=body.get("requested")))

            if path == "/api/knowledge/add":
                svc = self._knowledge_service()
                if svc is None:
                    return self._json(200, {"ok": False,
                                            "error": "knowledge service is not registered"})
                return self._json(200, svc.add_source(
                    str(body.get("path") or ""),
                    added_by=str(body.get("added_by") or "owner")))

            if path == "/api/knowledge/remove":
                # Removes the SOURCE and its index entries. The owner's files
                # are never touched - removing knowledge is not deleting data.
                svc = self._knowledge_service()
                if svc is None:
                    return self._json(200, {"ok": False,
                                            "error": "knowledge service is not registered"})
                return self._json(200, svc.remove_source(
                    str(body.get("source_id") or "")))

            if path == "/api/competition/cancel":
                mid = str(body.get("mission_id") or "")
                orchestrator = self._agents().team(mid)
                if orchestrator is None:
                    return self._json(200, {"ok": False, "cancelled": False,
                                            "error": f"no team for mission {mid}"})
                orchestrator.cancel_event.set()
                return self._json(200, {"ok": True, "cancelled": True,
                                        "mission_id": mid})

            if path == "/api/action/cancel":
                # Stop a running BOUNDED multi-step action between its steps.
                orch = getattr(d, "orchestrator", None)
                stopped = bool(orch and orch.cancel_request(body.get("request_id")))
                return self._json(200, {"ok": stopped, "cancelled": stopped,
                                        "request_id": body.get("request_id"),
                                        "error": "" if stopped else "No unambiguous cancellable request."})

            if path == "/api/providers":
                prov = d.services["registry"].add_provider(body)
                return self._json(200, {"provider": prov})

            if path == "/api/providers/test":
                # Accept both spellings: the native client posts {"id": ...}
                # while other callers use {"provider_id": ...}. Matching only one
                # of them silently tested the empty provider.
                pid = body.get("id") or body.get("provider_id") or ""
                out = d.services["gateway"].test_connection(pid,
                                                            body.get("model_id"))
                # Record validation state so canonical status can move to
                # ready / unreachable. Never stores the secret itself.
                try:
                    d.services["registry"].record_test_result(
                        pid,
                        bool(out.get("ok")),
                        str(out.get("error_code") or out.get("error") or ""))
                except Exception:
                    pass
                return self._json(200, out)

            # Model auto-discovery for the Add Provider flow. The key supplied
            # here is used for the single outbound discovery request and is NOT
            # persisted by this route - storage happens only through the Vault
            # when the provider is actually saved.
            if path == "/api/providers/discover":
                # Point 2 — true custom provider discovery. Accepts optional
                # custom headers, timeout, and auth scheme for arbitrary
                # OpenAI-compatible (or non-standard) endpoints.
                out = d.services["gateway"].discover_models(
                    provider_id=str(body.get("provider_id") or ""),
                    base_url=str(body.get("base_url") or ""),
                    secret=str(body.get("api_key") or ""),
                    protocol=str(body.get("protocol") or "openai_chat"),
                    discovery_url=str(body.get("discovery_url") or ""),
                    headers=body.get("headers"),
                    timeout=float(body.get("timeout") or 20.0),
                    auth_scheme=str(body.get("auth_scheme") or "bearer"))
                return self._json(200, out)

            if path == "/api/models/roles":
                roles = d.services["registry"].set_role(
                    body.get("role", ""),
                    body.get("provider_id", ""),
                    body.get("model_id", ""))
                return self._json(200, {"roles": roles})

            # Provider update / removal. POST matches the existing mutation
            # style in this server (add_provider, test are POST too).
            if path == "/api/providers/update":
                pid = body.get("id") or body.get("provider_id") or ""
                patch = body.get("patch") or {
                    k: v for k, v in body.items()
                    if k not in ("id", "provider_id")
                }
                prov = d.services["registry"].update_provider(pid, patch)
                return self._json(200, {"provider": prov})

            if path == "/api/providers/remove":
                pid = body.get("id") or body.get("provider_id") or ""
                ok = d.services["registry"].remove_provider(pid)
                return self._json(200, {"removed": bool(ok)})

            if path == "/api/providers/models/remove":
                pid = body.get("provider_id", "")
                mid = body.get("model_id", "")
                ok = d.services["registry"].remove_model(pid, mid)
                return self._json(200, {"removed": bool(ok)})

            if path.endswith("/models"):
                pid = path.split("/")[3]
                m = d.services["registry"].add_model(pid, body)
                return self._json(200, {"model": m})

            if path.endswith("/key/remove"):
                pid = path.split("/")[3]
                ref = f"secret://provider/{pid}/key"
                d.services["vault"].delete(ref)
                # clearing a credential invalidates any prior validation
                try:
                    d.services["registry"].record_test_result(pid, False, "credential_removed")
                except Exception:
                    pass
                return self._json(200, {"removed": True, "ref": ref})

            if path.endswith("/key"):
                pid = path.split("/")[3]
                ref = f"secret://provider/{pid}/key"
                key = body.get("key", "")
                if key == "":
                    # An empty value is a REMOVE request, not "store a blank
                    # secret" - storing empties kept credential_present true.
                    d.services["vault"].delete(ref)
                    try:
                        d.services["registry"].record_test_result(pid, False, "credential_removed")
                    except Exception:
                        pass
                    return self._json(200, {"removed": True, "ref": ref})
                d.services["vault"].store(ref, key)
                return self._json(200, {"stored": True, "ref": ref})

            if path == "/api/plugins/install":
                return self._json(200, self._plugins().install(
                    body.get("path", ""), grant=body.get("grant")))
            if path == "/api/plugins/grant":
                return self._json(200, self._plugins().grant(
                    body.get("plugin_id", ""), body.get("permissions") or []))
            if path == "/api/plugins/revoke":
                return self._json(200, self._plugins().revoke(
                    body.get("plugin_id", ""), body.get("permissions")))
            if path == "/api/plugins/enable":
                return self._json(200, self._plugins().set_enabled(
                    body.get("plugin_id", ""), bool(body.get("enabled", True))))
            if path == "/api/plugins/restart":
                return self._json(200, self._plugins().client(
                    body.get("plugin_id", "")).restart())
            if path == "/api/plugins/invoke":
                from core.contracts import CallContext
                invocation = self._plugins().execute(
                    CallContext(person_id=body.get("person_id", "owner")),
                    body.get("capability", ""), body.get("params") or {})
                return self._json(200, invocation.to_dict())

            if path == "/api/voice/listen":
                return self._json(200, self._voice().push_to_talk(
                    float(body.get("seconds", 5.0))))
            if path == "/api/owner-policy":
                computer = d.services.get("computer")
                if computer is None:
                    return self._json(503, {"ok": False, "error": "computer service unavailable"})
                try:
                    if (self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site")
                            or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                            or self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost")):
                        return self._json(403, {"ok": False, "error": "Use native desktop owner settings."})
                    policy = computer.owner_policy.configure(body)
                except (ValueError, TypeError) as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                if computer.audit:
                    computer.audit.record(who="owner", device="pc_main",
                                          action="owner_policy.configure", why="local desktop settings request",
                                          result="saved")
                return self._json(200, {"ok": True, "policy": policy})
            if path in ("/api/desktop/settings", "/api/desktop/pause", "/api/desktop/resume"):
                from core.contracts import CallContext
                computer = d.services.get("computer")
                if computer is None:
                    return self._json(503, {"ok": False, "error": "computer service unavailable"})
                action = path.rsplit("/", 1)[1]
                cap = "desktop.settings.set" if action == "settings" else "desktop." + action
                result = computer.execute(CallContext(person_id="owner"), cap,
                    {"settings": body.get("settings", {})} if action == "settings" else {})
                return self._json(200, {"ok": result.ok, "detail": result.detail,
                                        "status": computer.desktop_awareness.status,
                                        "settings": computer.desktop_awareness.settings.to_dict()})
            if path == "/api/voice/start":
                return self._json(200, self._voice().start_listening(body.get("mode")))
            if path == "/api/voice/settings":
                return self._json(200, self._voice().configure_live(body))
            if path == "/api/voice/setup":
                return self._json(200, self._voice().live.complete_setup(body.get("key", "")))
            if path == "/api/voice/test":
                return self._json(200, self._voice().live.test_connection())
            if path == "/api/local-awareness/settings":
                return self._json(200, d.services["local_monitor"].configure(body))
            if path == "/api/local-awareness/stop":
                return self._json(200, d.services["local_monitor"].stop())
            if path == "/api/local-awareness/clear":
                monitor = d.services["local_monitor"]
                if not monitor.stop().get("ok"):
                    return self._json(200, {"ok": False, "error": "Monitoring is still stopping."})
                monitor.observations.clear()
                return self._json(200, {"ok": True})
            if path == "/api/voice/stop":
                return self._json(200, self._voice().stop_listening())
            if path == "/api/voice/say":
                return self._json(200, self._voice().say(body.get("text", "")))
            if path == "/api/voice/barge-in":
                return self._json(200, self._voice().barge_in())
            if path == "/api/voice/selftest":
                return self._json(200, self._voice().selftest(
                    body.get("command", "volume 30")))
            if path == "/api/voice/profiles/active":
                return self._json(200, self._voice().profiles.set_active(
                    body.get("profile_id", "")))
            if path == "/api/voice/profiles/consent":
                return self._json(200, self._voice().profiles.grant_consent(
                    body.get("profile_id", ""), body.get("person_id", "owner"),
                    body.get("purpose", ""), body.get("evidence", "")))

            # ---- skills + teaching (Phase 5) -----------------------------------
            if path == "/api/skills/execute":
                from core.contracts import CallContext
                return self._json(200, self._skills().execute(
                    CallContext(person_id=body.get("person_id", "owner")),
                    body.get("goal", ""), body.get("params") or {},
                    dry_run=bool(body.get("dry_run", False))))
            if path == "/api/skills/learn-mission":
                from core.contracts import CallContext
                return self._json(200, self._skills().learn_from_mission(
                    CallContext(person_id=body.get("person_id", "owner")),
                    body.get("mission_id", ""), auto_save=bool(body.get("auto_save", False)),
                    activate=bool(body.get("activate", False))))
            if path == "/api/skills/rollback":
                return self._json(200, self._skills().rollback(
                    body.get("skill_id", ""), body.get("version")))
            if path == "/api/skills/status":
                return self._json(200, self._skills().set_status(
                    body.get("skill_id", ""), body.get("status", ""),
                    reason=body.get("reason", "ui")))
            if path == "/api/skills/correction":
                return self._json(200, self._skills().record_correction(
                    skill_id=body.get("skill_id", ""), version=int(body.get("version", 1)),
                    step_id=body.get("step_id", ""), failed_step=body.get("failed_step", ""),
                    user_action=body.get("user_action", ""),
                    previous_state=body.get("previous_state", ""),
                    resulting_state=body.get("resulting_state", ""),
                    detail=body.get("detail", "")))
            if path == "/api/teaching/start":
                return self._json(200, self._skills().start_teaching(
                    goal=body.get("goal", ""), application=body.get("application", ""),
                    scope=body.get("scope", "user")))
            if path == "/api/teaching/stop":
                return self._json(200, self._skills().stop_teaching())
            if path == "/api/teaching/learn":
                return self._json(200, self._skills().learn_from_demonstration(
                    name=body.get("name", ""), auto_save=bool(body.get("auto_save", True)),
                    activate=bool(body.get("activate", False))))
            if path == "/api/teaching/discard":
                return self._json(200, self._skills().discard_teaching())
            if path == "/api/teaching/event":
                return self._json(200, {"event": self._skills().capture(
                    body.get("kind", "note"), **{k: v for k, v in body.items() if k != "kind"})})

            # ---- device mesh (Phase 6) -----------------------------------------
            if path == "/api/devices/pair":
                return self._json(200, self._devices().pair(
                    body.get("device_id", ""), body.get("code", ""),
                    name=body.get("name", ""), type=body.get("type", ""),
                    capabilities=body.get("capabilities")))
            if path == "/api/devices/unpair":
                return self._json(200, self._devices().unpair(body.get("device_id", "")))
            if path == "/api/devices/trust":
                return self._json(200, self._devices().registry.set_trust(
                    body.get("device_id", ""), body.get("trust_tier", "")))
            if path == "/api/devices/invoke":
                from core.contracts import CallContext
                invocation = self._devices().execute(
                    CallContext(person_id=body.get("person_id", "owner")),
                    body.get("device_id", ""), body.get("capability", ""),
                    body.get("params") or {},
                    queue_if_offline=bool(body.get("queue_if_offline", True)))
                return self._json(200, invocation.to_dict())
            if path == "/api/devices/cancel":
                return self._json(200, self._devices().cancel(
                    body.get("device_id", ""), body.get("command_id", "")))
            if path == "/api/devices/expire":
                return self._json(200, {"expired": self._devices().expire_stale()})

            # ---- agent teams (Phase 10) ----------------------------------------
            if path == "/api/agents/plan":
                plan = self._agents().factory.plan_team(
                    objective=body.get("objective", ""),
                    complexity=float(body.get("complexity", 0.5)),
                    needs=body.get("needs") or [],
                    mission_id=body.get("mission_id", ""),
                    budget_usd=float(body.get("budget_usd", 0.0)))
                out = plan.to_dict()
                # Section 9: a real high-level plan, not just team composition.
                # plan_team chooses WHO; MissionPlanner decides WHAT, in what
                # order, with completion criteria. Callers that already supply
                # tasks keep them.
                if not out.get("tasks"):
                    from missions.planner import MissionPlanner
                    out.update(MissionPlanner().plan(
                        body.get("objective", ""),
                        complexity=float(body.get("complexity", 0.5)),
                        needs=body.get("needs") or []).to_dict())
                return self._json(200, out)
            if path == "/api/agents/start":
                from agents.contracts import TeamPlan as _TeamPlan
                raw = body.get("plan") or {}
                plan = _TeamPlan(objective=raw.get("objective", ""),
                                 team=raw.get("team") or [],
                                 tasks=raw.get("tasks") or [],
                                 mission_id=body.get("mission_id", ""))
                return self._json(200, self._agents().start_team(
                    mission_id=body.get("mission_id", ""),
                    objective=body.get("objective", ""), plan=plan))
            if path == "/api/agents/failover":
                return self._json(200, self._agents().failover(
                    body.get("mission_id", ""), from_provider=body.get("from_provider", ""),
                    reason=body.get("reason", ""), to_provider=body.get("to_provider", "")))
            if path == "/api/agents/cancel":
                return self._json(200, self._agents().cancel(
                    body.get("mission_id", ""), body.get("reason", "owner requested")))
            if path == "/api/agents/pause":
                return self._json(200, self._agents().pause(
                    body.get("mission_id", ""), body.get("reason", "owner requested")))
            if path == "/api/agents/resume":
                return self._json(200, self._agents().resume(body.get("mission_id", "")))

            # ---- operator control surface (Phase 11 §11) -----------------------
            if path == "/api/ops/control":
                action = str(body.get("action", ""))
                reserve = {"action", "mission_id", "actor"}
                params = {k: v for k, v in body.items() if k not in reserve}
                return self._json(200, self._agents().ops_control(
                    action, mission_id=str(body.get("mission_id", "")),
                    actor=str(body.get("actor", "operator")), **params))

            # ---- user control center (Phase 13) --------------------------------
            if path == "/api/control-center/forget":
                return self._json(200, self._control_center().forget(
                    str(body.get("record_id", ""))))

            # ---- proactivity (Phase 9) -----------------------------------------
            if path == "/api/proactive/quiet-hours":
                return self._json(200, self._proactive().set_quiet_hours(
                    start_hour=int(body.get("start_hour", 22)),
                    end_hour=int(body.get("end_hour", 7)),
                    person_id=body.get("person_id", ""), zone_id=body.get("zone_id", ""),
                    enabled=bool(body.get("enabled", True))))
            if path == "/api/proactive/preference":
                return self._json(200, self._proactive().set_preference(
                    body.get("event_class", ""), float(body.get("value", 0.5))))
            if path == "/api/proactive/risky":
                assessment = self._proactive().warn_risky_action(
                    body.get("capability", ""), body.get("params") or {})
                return self._json(200, assessment.to_dict())

            # ---- perception (Phase 8) ------------------------------------------
            if path == "/api/perception/policy":
                return self._json(200, self._perception().set_policy(
                    body.get("zone_id", ""), camera=body.get("camera"),
                    microphone=body.get("microphone"), screen=body.get("screen"),
                    motion=body.get("motion"), retention_s=body.get("retention_s")))
            if path == "/api/perception/camera/activate":
                return self._json(200, self._perception().activate_camera(
                    body.get("zone_id", ""), camera_id=body.get("camera_id", "")))
            if path == "/api/perception/camera/close":
                return self._json(200, self._perception().close_camera(
                    body.get("zone_id", "")))
            if path == "/api/perception/observe":
                return self._json(200, self._perception().observe(
                    body.get("zone_id", ""), classify=bool(body.get("classify", True))))
            if path == "/api/perception/purge":
                return self._json(200, self._perception().purge(body.get("zone_id", "")))
            if path == "/api/perception/register":
                return self._json(200, self._perception().register_zone(
                    body.get("zone_id", ""), body.get("name", ""), body.get("kind", "")))

            if path == "/api/peripherals/invoke":
                from core.contracts import CallContext
                invocation = self._devices().execute(
                    CallContext(person_id=body.get("person_id", "owner")), "pc_main",
                    body.get("capability", ""), body.get("params") or {})
                return self._json(200, invocation.to_dict())

            # ---- sync v1 (golden #17) ------------------------------------------
            if path == "/api/devices/sync":
                from core.contracts import CallContext
                return self._json(200, self._devices().sync.apply(
                    CallContext(person_id=body.get("person_id", "owner")),
                    body.get("changes") or [], policy=body.get("policy", "")))
            if path == "/api/devices/sync/resolve":
                return self._json(200, self._devices().sync.resolve_conflict(
                    body.get("conflict_id", ""), winner=body.get("winner", ""),
                    by=body.get("person_id", "owner")))
            if path == "/api/devices/sync/policy":
                return self._json(200, self._devices().sync.set_policy(
                    body.get("namespace", "default"), body.get("policy", "")))

            if path == "/api/permissions/grant":
                from core.contracts import GrantType
                gid = d.services["trust"].grant(
                    principal=body.get("principal", "owner"), scope=body.get("scope", ""),
                    grant_type=GrantType(body.get("grant_type", "standing")),
                    granted_by="owner", reason=body.get("reason", "ui"))
                return self._json(200, {"grant_id": gid})

            return self._json(404, {"error": "not found"})
        except Exception as exc:
            log.exception("POST %s failed", path)
            return self._json(500, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        d = self.daemon
        body = self._body()
        try:
            if path == "/api/memory":
                from core.contracts import CallContext
                n = d.services["memory"].forget(CallContext(),
                                                record_id=body.get("record_id"),
                                                entity=body.get("entity"))
                return self._json(200, {"forgotten": n})
            return self._json(404, {"error": "not found"})
        except Exception as exc:
            return self._json(500, {"error": str(exc)})

    # ------------------------------------------------------------ chat stream
    def _chat_stream(self, body: Dict[str, Any]) -> None:
        """Real token streaming over SSE.

        Emits `event: delta` for every piece of text the provider actually
        produces, then `event: done`. If the provider cannot stream, exactly
        ONE delta is emitted carrying the full reply — GENIE never splits a
        finished answer into fake chunks. `streamed: false` in the done event
        tells the UI which case happened.
        """
        d = self.daemon
        text = body.get("text", "")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def _emit(event: str, data: Dict[str, Any]) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            try:
                self.wfile.write(f"event: {event}\ndata: ".encode("utf-8") + payload + b"\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError):
                raise

        try:
            _emit("start", {"session_id": body.get("session_id", "owner")})
            count = 0
            tokens = 0
            # Daemon.stream_chat yields (text, kind). "token" deltas are real
            # streamed model output; "progress"/"final" are not token streams and
            # are reported as such rather than being presented as streaming.
            for delta, kind in d.stream_chat(text,
                                             session_id=body.get("session_id", "owner"),
                                             request_id=body.get("request_id")):
                count += 1
                if kind == "token":
                    tokens += 1
                if kind in ("token", "final", "progress"):
                    _emit("progress" if kind == "progress" else "delta", {"text": delta, "kind": kind})
            mode = "tokens" if tokens > 1 else ("single" if tokens == 1 else "actions")
            _emit("done", {"deltas": count, "token_deltas": tokens,
                           "mode": mode, "streamed": tokens > 1})
        except (BrokenPipeError, ConnectionAbortedError):
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("chat stream failed")
            try:
                _emit("error", {"error": str(exc),
                                "where": traceback.format_exc().strip().splitlines()[-3:]})
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------------- SSE
    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=100)

        def _handler(event):
            try:
                q.put_nowait(event.to_dict())
            except queue.Full:
                pass

        sub = get_bus().subscribe("*", _handler)
        try:
            self.wfile.write(b": connected\n\n")
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(ev, ensure_ascii=False).encode("utf-8")
                self.wfile.write(b"data: " + payload + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass
        finally:
            get_bus().unsubscribe(sub)


class IPCServer:
    def __init__(self, daemon, host: str = "127.0.0.1", port: int = 8787):
        self.daemon = daemon
        self.host = host
        self.port = port

        class _Handler(Handler):
            pass
        _Handler.daemon = daemon
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._thread: Optional[threading.Thread] = None

    def start(self, background: bool = True) -> None:
        log.info("IPC server on http://%s:%s", self.host, self.port)
        if background:
            self._thread = threading.Thread(target=self._httpd.serve_forever,
                                            name="ipc", daemon=True)
            self._thread.start()
        else:
            self._httpd.serve_forever()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
