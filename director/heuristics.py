"""Deterministic director (director/heuristics) — the NEDLE2 fast path.

Design goal (user requirement): "volume 30" must reach system.volume without any cloud
LLM call, and "phone ka next song" must route straight to phone_main -> media.next.

This is also the safety net when the Needle runtime is not installed yet, so GENIE is
never dead on a fresh machine.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.contracts import CallContext, TaskType

from browser.mode import decide_browser_mode, _OWN_BROWSER_RE

from .base import DirectorDecision, DirectorProvider, DirectorTask

APP_ALIASES: Dict[str, str] = {
    "chrome": "chrome", "google chrome": "chrome", "browser": "chrome",
    "notepad": "notepad", "vscode": "code", "vs code": "code", "code": "code",
    "blender": "blender", "spotify": "spotify", "obs": "obs",
    "explorer": "explorer", "file explorer": "explorer",
    "terminal": "cmd", "cmd": "cmd", "powershell": "powershell",
    "calculator": "calc", "calc": "calc", "paint": "mspaint",
    "steam": "steam", "discord": "discord", "vlc": "vlc", "word": "winword",
    "excel": "excel", "photoshop": "photoshop", "premiere": "premiere",
    "davinci": "resolve", "da vinci": "resolve",
}

DEVICE_ALIASES: Dict[str, str] = {
    "phone": "phone_main", "mobile": "phone_main", "android": "phone_main",
    "laptop": "laptop_main", "second pc": "pc_secondary", "dusra pc": "pc_secondary",
    "pi": "rpi_main", "raspberry": "rpi_main",
}

# --- web targets -------------------------------------------------------------
# A website is NOT an installed application: the installed-app resolver cannot
# resolve it, so "application.open youtube" fails with "not installed". Named web
# services therefore route to the BROWSER authority instead (browser.navigate /
# browser.media.play). This is general: any name here gets the same treatment.
WEB_SERVICES: Dict[str, str] = {
    "youtube music": "https://music.youtube.com",
    "youtube": "https://www.youtube.com",
    "google maps": "https://www.google.com/maps",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "wikipedia": "https://www.wikipedia.org",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "openai": "https://chatgpt.com",
    "whatsapp web": "https://web.whatsapp.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
}

# An explicitly requested browser must be respected, never silently substituted.
BROWSER_ALIASES: Dict[str, str] = {
    "brave browser": "brave", "brave": "brave",
    "google chrome": "chrome", "chrome": "chrome",
    "microsoft edge": "edge", "ms edge": "edge", "edge": "edge",
    "mozilla firefox": "firefox", "firefox": "firefox",
    "chromium": "chromium", "opera": "opera", "vivaldi": "vivaldi",
}

_OPEN_WEB_RE = re.compile(
    r"\b(open|kholo|khol|kholiye|kholna|launch|go\s+to|navigate|browse|visit|"
    r"shuru\s+karo|start)\b", re.I)
_PLAY_MEDIA_RE = re.compile(r"\b(play|bajao|bajado|chalao|chala|sunao|lagao)\b", re.I)
_MEDIA_NOUN_RE = re.compile(r"\b(song|gaana|gana|music|track|playlist|audio|video)\b", re.I)

# Device enumeration / system inspection wording. These are read-only lookups and
# must never be mistaken for playback, volume or a media control.
# Deterministic folder creation. Real acceptance found this common, safe,
# side-effecting request had NO fast route, so it fell to conversation and the
# durable "Execute" phase could never satisfy its receipt requirement.
_FOLDER_INTENT_RE = re.compile(
    r"\b(?:create|make|new|banao|bana\s*do|banade)\b[\s\S]{0,28}?\b(?:folder|directory)\b",
    re.IGNORECASE)
_FOLDER_NAME_RE = re.compile(
    r"\b(?:named|called|naam\s*(?:ka|ki|se)?)\s*[\"']?([A-Za-z0-9_\- .]{1,60}?)[\"']?"
    r"(?:\s+(?:in|on|inside|mein|me|ke\s*andar)\b|$)", re.IGNORECASE)
_FOLDER_LOCATION_RE = re.compile(
    r"\b(desktop|documents|downloads|pictures|music|videos)\b", re.IGNORECASE)

_DEVICE_ENUM_RE = re.compile(
    r"\b(?:audio|sound|mic|microphone|speaker|output|input|device|devices)\b",
    re.I)
_AUDIO_DEVICES_RE = re.compile(
    r"\b(?:audio|sound|mic|microphone|speaker)s?\s*(?:devices?|outputs?|inputs?)\b"
    r"|\b(?:devices?|outputs?|inputs?)\s*(?:of\s*)?(?:audio|sound)\b", re.I)
_STORAGE_INFO_RE = re.compile(
    r"\b(?:how\s+much\s+(?:storage|space)|(?:disk|drive|volume)\s+(?:space|storage|usage)|"
    r"(?:free|available)\s+(?:disk\s+)?space|storage\s+(?:on|in|of)|"
    r"(?:total|used|free)\s+(?:disk\s+)?space)\b|"
    r"\b(?:kitna|kitni)\b[\s\S]{0,35}\b(?:space|storage|jagah)\b|"
    r"\b(?:space|storage|jagah)\b[\s\S]{0,35}\b(?:kitna|kitni|bacha|khali)\b", re.I)
_NETWORK_STATE_RE = re.compile(
    r"\b(?:network|internet|wi-?fi|connection)\b[\s\S]*?"
    r"\b(?:status|state|check|dikhao|batao|kaisa|kaisi|how|is)\b"
    r"|\b(?:status|state)\b[\s\S]*?\b(?:network|internet|wi-?fi)\b", re.I)
_DESKTOP_INSPECT_RE = re.compile(
    r"\b(?:inspect|list|show|dikhao|batao)\b[\s\S]*?\b(?:desktop|screen|windows?)\b"
    r"|\b(?:desktop|screen)\b[\s\S]*?\b(?:inspect|dikhao|batao|observe|state)\b", re.I)
_FULLSCREEN_RE = re.compile(r"\b(full\s?screen|fullscreen|poori\s+screen|maximi[sz]e)\b", re.I)
_URL_LIKE_RE = re.compile(
    r"\b((?:https?://)?(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|ai|in|co|edu|gov|me|tv|app)"
    r"(?:/[^\s]*)?)\b", re.I)
_DOWNLOAD_RE = re.compile(r"\b(download|डाउनलोड|save\s+this|neeche\s+download)\b", re.I)

_MEDIA_STOPWORDS = {
    "open", "kholo", "khol", "kholiye", "kholna", "karo", "kar", "karke", "karwao",
    "karwa", "play", "bajao", "bajado", "chalao", "chala", "sunao", "lagao", "aur",
    "and", "then", "ko", "mein", "me", "par", "on", "the", "a", "an", "please",
    "youtube", "fullscreen", "full", "screen", "poori", "browser", "new", "chat",
    "shuru", "start", "generate", "image", "video", "song", "gaana", "gana", "music",
    "track", "playlist", "audio", "na", "se", "ke", "ki", "ka", "de", "do", "dena",
    "chahiye", "karna", "hai", "ho",
}


def find_web_service(text: str) -> Tuple[str, str]:
    """Return (service_name, url) for a named web service, else ("", "")."""
    low = (text or "").lower()
    for name in sorted(WEB_SERVICES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return name, WEB_SERVICES[name]
    return "", ""


def find_browser(text: str) -> str:
    """Return an explicitly named browser id (brave/chrome/edge/...), else ""."""
    low = (text or "").lower()
    for name in sorted(BROWSER_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return BROWSER_ALIASES[name]
    return ""


_MEDIA_CONTROL_RE = re.compile(
    r"\b(pause|ruko|rok|next|agla|skip|previous|prev|pichhla|peeche|stop|band|"
    r"mute|unmute|volume|awaz)\b", re.I)


def find_media_query(text: str, service: str) -> str:
    """Extract a media search query when the request is a song/media request.

    Only fires for a media service (or a bare song request), and never for media
    CONTROL (pause/next/previous/stop/volume) — that stays with the media
    authority. So "ChatGPT kholo" is not a media search and "pause the song" is
    not a play request.
    """
    low = (text or "").lower()
    # Store/application operations are not playback merely because they say Play.
    if (re.search(r"\b(?:install|uninstall|download|update)\b", low)
            and re.search(r"\b(?:app|software|program|game|store)\b", low)):
        return ""
    if _MEDIA_CONTROL_RE.search(low):
        return ""
    # Real acceptance finding: "audio devices dikhao" matched the media noun
    # "audio" and was routed to browser.media.play. Enumerating devices is a
    # read-only system inspection, never playback.
    if _DEVICE_ENUM_RE.search(low):
        return ""
    if not (_PLAY_MEDIA_RE.search(low) or _MEDIA_NOUN_RE.search(low)):
        return ""
    if service and service not in ("youtube", "youtube music"):
        return ""
    words = re.findall(r"[A-Za-z0-9']+", text or "")
    q = [w for w in words if w.lower() not in _MEDIA_STOPWORDS]
    return " ".join(q).strip()


# A clause the deterministic planner cannot execute yet. It is carried as a step
# so the run reports it as BLOCKED instead of silently dropping it (and never
# narrates it as done).
UNSUPPORTED_CAPABILITY = "plan.unsupported"

_CLAUSE_SPLIT_RE = re.compile(r"\s*(?:,|;|\baur\b|\band\b|\bthen\b|\bphir\b|\biske\s+baad\b)\s*",
                              re.I)


def split_clauses(text: str) -> List[str]:
    """Split a request into sequential clauses (best-effort, deterministic)."""
    parts = [p.strip(" .") for p in _CLAUSE_SPLIT_RE.split(text or "") if p.strip(" .")]
    return parts or [(text or "").strip()]


def _unsupported(clause: str) -> Dict[str, Any]:
    return {"capability": UNSUPPORTED_CAPABILITY, "params": {"clause": clause}}


def _clause_step(clause: str, browser: str) -> Dict[str, Any]:
    """Map ONE clause to a browser step (or an honest unsupported marker)."""
    cl = clause.lower()
    base: Dict[str, Any] = {"browser": browser} if browser else {}
    cs, cu = find_web_service(clause)
    explicit_url = _URL_LIKE_RE.search(clause)
    if explicit_url and _DOWNLOAD_RE.search(clause):
        target = explicit_url.group(1)
        if not target.lower().startswith(("http://", "https://")):
            target = "https://" + target
        return {"capability": "browser.download", "params": {**base, "url": target}}
    if explicit_url and _OPEN_WEB_RE.search(cl):
        target = explicit_url.group(1)
        return {"capability": "browser.navigate", "params": {**base, "url": target}}
    if _FULLSCREEN_RE.search(cl) and (cs or _BROWSER_CTX_RE.search(cl)):
        return {"capability": "browser.fullscreen", "params": {**base}}
    q = find_media_query(clause, cs)
    if q:
        return {"capability": "browser.media.play",
                "params": {**base, "service": cs or "youtube", "query": q}}
    if cs and (_OPEN_WEB_RE.search(cl) or _BROWSER_CTX_RE.search(cl)):
        return {"capability": "browser.navigate", "params": {**base, "url": cu}}
    return _unsupported(clause)


_IMAGE_INTENT_RE = re.compile(
    r"\b(image|photo|picture|tasveer|illustration|generate|banao|banado|create|draw)\b",
    re.I)


def extract_image_prompt(text: str) -> str:
    """The clause describing the image, with command words trimmed."""
    for clause in split_clauses(text):
        if _IMAGE_INTENT_RE.search(clause):
            p = re.sub(r"\b(generate|banao|banado|create|draw|karwao|karwa|kar\s+do)\b",
                       " ", clause, flags=re.I)
            return re.sub(r"\s+", " ", p).strip(" .")
    return (text or "").strip()


# Thin site adapters: a site task becomes ONE verified adapter step that composes
# the general browser primitives. Add a site here only when it needs special
# navigation/verification — never a second browser system or planner.
SITE_ADAPTERS = {
    "chatgpt": "browser.chatgpt.image",
    "openai": "browser.chatgpt.image",
}


def _host_of(url: str) -> str:
    u = (url or "").strip()
    for scheme in ("https://", "http://"):
        if u.lower().startswith(scheme):
            u = u[len(scheme):]
            break
    return u.split("/")[0].split("?")[0].strip().lower()


def annotate_web_plan(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach identity, dependencies, expected observation and verification.

    The executor needs more than a capability name: each step must say what it
    expects to observe and what proves it worked, so the run can be verified
    instead of narrated. Steps default to depending on the previous one, which is
    what makes a failed step stop the steps that need it.
    """
    out: List[Dict[str, Any]] = []
    prev = ""
    for i, step in enumerate(steps or [], start=1):
        s = dict(step or {})
        s.setdefault("id", f"s{i}")
        s.setdefault("depends_on", [prev] if prev else [])
        cap = str(s.get("capability", ""))
        params = dict(s.get("params") or {})
        if "expect" not in s:
            if cap == "browser.session":
                s["expect"] = "the selected existing browser and exact tab are attached"
                s["verify"] = {"attached": True, "mode": "owner_existing"}
            elif cap in ("browser.navigate", "browser.open_named", "browser.open_default"):
                # A handoff (open_named/open_default) carries the same page
                # expectation as navigate, plus the attach requirement: the OS
                # accepted the URL, but later steps may only control the page
                # once the session is actually attached (E36/E37, B05).
                s["expect"] = f"the page at {params.get('url', '')} is loaded"
                host = _host_of(str(params.get("url", "")))
                if host:
                    s["verify"] = {"url_contains": host}
                if cap != "browser.navigate":
                    s.setdefault("requires_attach", True)
                    s.setdefault("expect_attach",
                                 "the opened URL becomes the attached, controllable session")
            elif cap == "browser.media.play":
                s["expect"] = ("a media result for the query is open and the media "
                               "element reports advancing playback")
            elif cap == "browser.fullscreen":
                s["expect"] = "the browser/video is fullscreen"
            elif cap == "browser.chatgpt.image":
                s["expect"] = "a NEW generated image artifact exists on the page"
            elif cap == "browser.act":
                s["expect"] = "the target control is activated and the effect appears"
                if params.get("expect_selector"):
                    s["verify"] = {"selector": str(params["expect_selector"])}
            elif cap == "browser.fill":
                s["expect"] = "the editable field holds the entered text"
            elif cap == "plan.unsupported":
                s["expect"] = "no executor support — must be reported, not narrated"
        out.append(s)
        prev = s["id"]
    return out


def _plan_web_action_raw(text: str) -> List[Dict[str, Any]]:
    """Deterministic BROWSER plan for a web request, or [] when it is not one.

    Reuses only the browser authority. Never returns an installed-application
    task for a website. A multi-clause request becomes an ordered, bounded step
    list; a clause with no executor support becomes an explicit unsupported step
    so it is reported, never dropped or narrated as done.
    """
    t = (text or "").strip()
    if not t:
        return []
    low = t.lower()
    service, url = find_web_service(t)
    browser = find_browser(t)
    base: Dict[str, Any] = {"browser": browser} if browser else {}

    # Same-page follow-ups must use the selected tab. Neither operation opens
    # a URL, creates a target, or changes browser identity.
    if re.fullmatch(r"(?:please\s+)?(?:reload|refresh)(?:\s+(?:this|the|current))?(?:\s+(?:page|tab|browser))?", low):
        return [{"capability": "browser.reload", "params": base}]
    scroll = re.fullmatch(r"(?:please\s+)?scroll(?:\s+(?:this|the|current)\s+(?:page|tab))?\s+(up|down)(?:\s+(?:a\s+bit|to\s+the\s+(?:top|bottom)))?", low)
    if scroll:
        direction = scroll.group(1)
        amount = -800 if direction == "up" else 800
        if "top" in low or "bottom" in low:
            amount *= 10
        return [{"capability": "browser.scroll", "params": {**base, "dy": amount}}]

    # An unknown named website needs a URL before any dependent action can run.
    # In particular, a song clause must not redirect a salon-site request to YouTube.
    clauses = split_clauses(t)
    if not service and _OPEN_WEB_RE.search(low) and re.search(r"\b(web\s?site|site)\b", low):
        return [_clause_step(c, browser) for c in clauses]
    if _URL_LIKE_RE.search(t) and _DOWNLOAD_RE.search(low):
        return [_clause_step(c, browser) for c in clauses]
    if _URL_LIKE_RE.search(t) and _OPEN_WEB_RE.search(low):
        return [_clause_step(c, browser) for c in clauses]

    # 1) media search + verified playback (a song request is ONE browser media
    #    action; media CONTROL is excluded inside find_media_query)
    query = find_media_query(t, service)
    if query:
        return [{"capability": "browser.media.play",
                 "params": {**base, "service": service or "youtube", "query": query}}]

    # 2) fullscreen against the current browser / video context
    if _FULLSCREEN_RE.search(low) and (service or _BROWSER_CTX_RE.search(low)):
        return [{"capability": "browser.fullscreen", "params": {**base}}]

    # 2b) a supported site task -> ONE verified adapter step
    adapter = SITE_ADAPTERS.get(service)
    if adapter and _IMAGE_INTENT_RE.search(low):
        return [{"capability": adapter,
                 "params": {**base, "prompt": extract_image_prompt(t)}}]

    is_web = bool(service) or bool(_URL_LIKE_RE.search(t)) or bool(_BROWSER_CTX_RE.search(t))
    if not is_web:
        return []

    # 3) multi-clause request -> bounded ordered plan
    clauses = split_clauses(t)
    if len(clauses) > 1:
        steps = [_clause_step(c, browser) for c in clauses]
        # only take over when at least one clause is a real browser action
        if any(s["capability"] != UNSUPPORTED_CAPABILITY for s in steps):
            return steps
        return []

    # 4) single clause -> navigate to the named service / URL on an open verb
    if service and (_OPEN_WEB_RE.search(low) or _BROWSER_CTX_RE.search(low)):
        return [{"capability": "browser.navigate", "params": {**base, "url": url}}]

    return []


def plan_web_action(text: str, annotate: bool = True) -> List[str] | List[Dict[str, Any]]:
    """Public planner: the bounded ordered plan, annotated for verified execution.

    Each step also carries `browser_mode` so the executor can honour the owner's
    authorized browser mode (their existing signed-in browser vs a separate
    GENIE-owned profile) — never a silent substitution.
    """
    steps = _plan_web_action_raw(text)
    if not steps:
        return []
    service, _ = find_web_service(text)
    browser = find_browser(text)
    mode = decide_browser_mode(text, browser, service)
    for s in steps:
        params = dict(s.get("params") or {})
        if s.get("capability") == "browser.navigate" and re.search(r"\bdefault\s+browser\b", text, re.I):
            s["capability"] = "browser.open_default"
        elif s.get("capability") == "browser.navigate" and browser and mode == "owner_existing":
            # A URL handoff cannot bind the selected tab for the next clause.
            # Attach to the exact running browser first; if its tab is
            # ambiguous, return candidates rather than opening a new profile.
            s["capability"] = "browser.session"
            params["mode"] = "owner_existing"
        # An unqualified follow-up inherits the live task binding. Stamping
        # "genie_owned" here would override an earlier selected Brave session.
        if browser or mode == "owner_existing" or _OWN_BROWSER_RE.search(text):
            params.setdefault("browser_mode", mode)
        s["params"] = params
    return annotate_web_plan(steps) if annotate else steps

_VOLUME_RE = re.compile(r"(?:volume|awaz|awaj|sound)\s*(?:(?:ko|to|at)\s*)?(\d{1,3})", re.I)
_VOLUME_UP = re.compile(r"(volume|awaz|awaj|sound)\s*(badhao|increase|up|barhao|ज्यादा)", re.I)
_VOLUME_DOWN = re.compile(r"(volume|awaz|awaj|sound)\s*(kam|kum|decrease|down|ghatao)", re.I)
_MUTE = re.compile(r"\b(mute|unmute|chup|silent)\b", re.I)

_OPEN_RE = re.compile(r"\b(open|kholo|khol|kholo|launch|start|chalu karo|shuru karo)\b", re.I)
_CLOSE_RE = re.compile(r"\b(close|band karo|band|exit|kill)\b", re.I)

_NEXT_RE = re.compile(r"\b(next|agla|age|skip)\b", re.I)
_PREV_RE = re.compile(r"\b(previous|prev|pichhla|peeche)\b", re.I)
_PAUSE_RE = re.compile(r"\b(pause|ruko|rok)\b", re.I)
_PLAY_RE = re.compile(r"\b(play|chalao|bajao|chalu)\b", re.I)
_SONG_CTX = re.compile(r"\b(song|gana|gaana|music|track|media)\b", re.I)

# Questions about the conversation itself. These must be answered from history,
# never dispatched to a tool/device/plugin. Kept deliberately narrow so real
# action requests ("play the previous song") still route to media control.
_CONTEXT_QUESTION_RE = re.compile(
    r"\b(what\s+(did|was|were)\s+(i|we|you)|"
    r"what\s+did\s+i\s+(just\s+)?(ask|say)|"
    r"what\s+were\s+we\s+talking|"
    r"repeat\s+(your|the)\s+(last|previous)|"
    r"why\s+did\s+you\s+(choose|pick|say)|"
    r"remind\s+me\s+what\s+i)", re.I)

_MEMORY_WRITE = re.compile(
    r"(mujhe .*(pasand|pasand nahi)|yaad rakh|remember|mera naam|i (like|prefer|hate))", re.I)
_MEMORY_QUERY = re.compile(r"(kal wala|pichhli baar|last time|pehle wala|previous .*method|yaad hai)", re.I)

# --- Point 6: mission intent -------------------------------------------------
# A Mission is DURABLE autonomous work. Classification requires semantic
# EVIDENCE of durable/autonomous execution — never message length, a single
# keyword, or the mere presence of a verb.
_RECURRENCE_RE = re.compile(
    r"\b(every\s+(day|morning|evening|week|hour|night|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|time)|daily|weekly|hourly|nightly|monthly|"
    r"recurring|repeatedly|regularly|on\s+a\s+schedule|scheduled|roz|har\s+(din|roz|subah))\b",
    re.I)
_AUTONOMY_RE = re.compile(
    r"\b(keep\s+(running|going|doing|an?\s+eye|monitoring|it\s+updated)|continuously|"
    r"ongoing|long[- ]running|in\s+the\s+background|background|monitor|watch\s+for|"
    r"automate|automatically|autonomous|manage|handle|maintain|grow|"
    r"run\s+(it|this|them|for\s+me)|until\s+(it|the|this|complete|done|finished)|"
    r"over\s+time|from\s+now\s+on)\b", re.I)
_WORK_VERB_RE = re.compile(
    r"\b(research|check|monitor|scan|collect|gather|compile|prepare|generate|"
    r"create|build|grow|upload|post|publish|schedule|send|email|mail|report|"
    r"analyse|analyze|track|watch|find|hunt|apply|book|order|manage|handle|"
    r"browse|download|scrape|summari[sz]e|deliver|produce)\b", re.I)
_DELIVERABLE_RE = re.compile(
    r"\b(report|summary|findings|results?|deliverable|digest|roundup|"
    r"email\s+me|send\s+me|bring\s+me|give\s+me|for\s+me)\b", re.I)
_MULTISTEP_RE = re.compile(
    r"(\band\s+then\b|\bthen\b|\bafter\s+that\b|\bstep\s+by\s+step\b|"
    r"\bfollowed\s+by\b|\bpipeline\b)", re.I)
_BROWSER_CTX_RE = re.compile(
    r"\b(browser|web\s?site|web\s?page|chatgpt|gemini|claude|online|the\s+web)\b", re.I)
_GREETING_RE = re.compile(
    r"\W*(hi|hey|hello|yo|thanks|thank\s+you|ok|okay|good\s+(morning|evening|night)|"
    r"how\s+are\s+you|kaise\s+ho|namaste|kya\s+haal)\W*", re.I)


# Casual exchanges: greetings, thanks, acknowledgements, small talk.
_CASUAL_RE = re.compile(
    r"^\W*(hi+|hey+|hay+|hello+|yo+|sup+|hola+|namaste+|thanks|thank\s+you|thx|"
    r"ok+|okay|good\s+(morning|afternoon|evening|night)|how\s+are\s+you|how\s+r\s+u|"
    r"kaise\s+ho|kya\s+haal(\s+hai)?|shukriya|dhanyavad|bye|goodbye|"
    r"hm+|hmm+|achha|theek\s+hai)\W*$", re.I)


def is_conversational(text: str) -> bool:
    """Shared conversation authority.

    A Mission is durable work; a greeting or a short remark is never one. This
    is deliberately NOT a word list patch: an utterance is conversation when it
    is a known casual exchange, or when it is short and carries no work,
    recurrence, autonomy, deliverable or multi-step signal at all.

    Every director (heuristic, model, or future providers) must obey this, so
    the orchestrator applies it AFTER classification rather than relying on any
    single provider to get it right.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _CASUAL_RE.match(t):
        return True
    return False


def is_instruction_question(text: str) -> bool:
    """A request for instructions does not authorize the described action."""
    question = str(text or "")
    if re.search(r"\bkaise\s+(?:karte|karna|kar\s+sakte)\b|कैसे\s+(?:करते|करना)", question, re.I):
        return True
    return bool(re.match(
        r"\s*(?:how\s+(?:can|do|would|should)\s+(?:i|we)|how\s+to|"
        r"can\s+you\s+explain|what\s+does|(?:main|mein|mai)\s+.*?\bkaise\s+"
        r"(?:karun|karu|karoon)|मैं\s+.*?कैसे\s+(?:करूँ|करूं))\b",
        str(text or ""), re.I))


def is_control_interaction(text: str) -> bool:
    return not is_instruction_question(text) and bool(
        re.search(r"\b(button|control|notification|bell|composer|search\s+field|chat)\b|बटन|घंटी|नोटिफिकेशन", text, re.I)
        and re.search(r"\b(click|dabao|select|open|kholo|khol|search|type|likho|play)\b|क्लिक|दबाओ|खोल|खोज|लिख|चलाओ", text, re.I))


# Positive durable-intent signals. A Mission is durable, autonomous work, so
# creating one requires EVIDENCE of durability - never just a director's
# recommendation. Anything without one of these stays a conversation or a
# simple action, whatever a model provider claims.
_DURABLE_RE = re.compile(
    r"\b(every\s+(day|morning|evening|week|hour|night|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|time)|daily|weekly|hourly|nightly|monthly|"
    r"recurring|repeatedly|regularly|scheduled|schedule\s+this|on\s+a\s+schedule|"
    r"roz|har\s+(din|roz|subah)|"
    r"continuously|ongoing|long[- ]running|in\s+the\s+background|keep\s+(running|"
    r"going|doing|an?\s+eye|monitoring|it\s+updated)|monitor\s+(this|these|the)|"
    r"watch\s+for|automate|automatically|autonomous|maintain|from\s+now\s+on|"
    r"until\s+(it|the|this|complete|done|finished)|24\s*/\s*7|24x7|"
    r"manage\s+my|handle\s+my|grow\s+my|run\s+(it|this|them))\b", re.I)


def has_durable_intent(text: str) -> bool:
    """Positive gate for durable Mission creation.

    Returns True only when the request itself carries evidence of recurrence,
    scheduling, continuous monitoring, long-running responsibility, or
    persistence until completion. A director recommendation alone is never
    sufficient — that is exactly how "HAY" became a Mission.
    """
    t = (text or "").strip()
    if not t:
        return False
    if not _DURABLE_RE.search(t):
        return False
    # Durability wording must also be attached to actual work or an objective.
    return bool(_WORK_VERB_RE.search(t) or _DELIVERABLE_RE.search(t)
                or _MULTISTEP_RE.search(t) or _AUTONOMY_RE.search(t))


def detect_mission_intent(text: str) -> Tuple[bool, str]:
    """Return (mission_required, schedule) for a request.

    Deliberately a COMBINATION of signals, never one keyword:
      * recurrence/schedule + a work verb                      -> mission (scheduled)
      * autonomy/long-running + a work verb                    -> mission
      * multi-step autonomy (>=2 work verbs or "then"/"and then") -> mission
      * browser/tool work + a deliverable                      -> mission
    Everything else is a normal exchange and stays a conversation.
    """
    t = (text or "").strip()
    if not t:
        return False, ""
    if len(t) <= 28 and _GREETING_RE.fullmatch(t):
        return False, ""

    recurring = bool(_RECURRENCE_RE.search(t))
    autonomy = bool(_AUTONOMY_RE.search(t))
    work = bool(_WORK_VERB_RE.search(t))
    deliverable = bool(_DELIVERABLE_RE.search(t))
    multistep = bool(_MULTISTEP_RE.search(t))
    browser_ctx = bool(_BROWSER_CTX_RE.search(t))
    work_verbs = len(_WORK_VERB_RE.findall(t))

    if recurring and work:
        m = _RECURRENCE_RE.search(t)
        return True, (m.group(0).lower() if m else "recurring")
    if autonomy and work:
        return True, ""
    if (multistep or work_verbs >= 2) and work:
        return True, ""
    if browser_ctx and work and deliverable:
        return True, ""
    return False, ""


def _find_app(text: str) -> Optional[str]:
    low = text.lower()
    for alias, target in sorted(APP_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return target
    return None


def _find_device(text: str) -> Optional[str]:
    low = text.lower()
    for alias, device in sorted(DEVICE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in low:
            return device
    return None


class HeuristicDirector(DirectorProvider):
    name = "heuristic"

    def classify(self, text: str, ctx: CallContext,
                 context_hint: Dict[str, Any] | None = None) -> DirectorDecision:
        t = (text or "").strip()
        if not t:
            return DirectorDecision(source=self.name, confidence=0.1)

        # 0) CONVERSATION CONTEXT GUARD ------------------------------------
        # "what did I just ask you?" was being routed into a media plugin
        # because a later branch matched a stray keyword. A question about the
        # conversation is never an action: resolve it from history, never with
        # a tool. This runs BEFORE every action branch.
        if _CONTEXT_QUESTION_RE.search(t) or is_instruction_question(t):
            return DirectorDecision(source=self.name, confidence=0.95,
                                    intent="conversation")

        # Controls inside the current app are not app launches or media search
        # queries. Route them to the existing observation/tool loop before web
        # planning can replace the selected page with a service homepage.
        if is_control_interaction(t):
            return DirectorDecision(source=self.name, confidence=0.95,
                                    intent="simple_action", reasoning_required=True,
                                    raw={"reason": "observed-control-tool-loop"})

        d = DirectorDecision(source=self.name, confidence=0.9)
        # Point 6 — mission intent from semantic evidence. The action branches
        # below (volume/media/app) never trip this, so they stay simple actions.
        d.mission_required, d.schedule = detect_mission_intent(t)
        d.intent = "mission" if d.mission_required else "simple_action"

        # 1) volume / audio -------------------------------------------------
        m = _VOLUME_RE.search(t)
        if m:
            level = max(0, min(100, int(m.group(1))))
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "system.volume.set", params={"level": level}))
            d.reply_hint = f"Volume {level} set kar diya."
            return d
        if _VOLUME_UP.search(t):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.up"))
            d.reply_hint = "Volume badha diya."
            return d
        if _VOLUME_DOWN.search(t):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.down"))
            d.reply_hint = "Volume kam kar diya."
            return d
        if _MUTE.search(t):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main", "system.volume.mute"))
            d.reply_hint = "Mute toggle kar diya."
            return d

        # 1a) deterministic folder creation (safe, common, verifiable).
        _hinglish_folder = (re.search(r"\b([A-Za-z0-9_\-]{2,60})\s+(?:banao|bana\s*do|banade)\b", t, re.I)
                            and _FOLDER_LOCATION_RE.search(t))
        if _FOLDER_INTENT_RE.search(t) or _hinglish_folder:
            location = "desktop"
            loc_match = _FOLDER_LOCATION_RE.search(t)
            if loc_match:
                location = loc_match.group(1).lower()
            name = ""
            name_match = _FOLDER_NAME_RE.search(t)
            if name_match:
                name = name_match.group(1).strip()
            if not name:
                # The name may appear before OR after the location word:
                #   "create a folder daily_probe in downloads"
                #   "downloads mein test_folder banao"
                # Scan the whole clause and take the last non-stopword token.
                tokens = re.findall(r"[A-Za-z0-9_\-]{2,60}", t)
                stop = {"create", "make", "new", "a", "an", "the", "folder", "directory",
                        "banao", "bana", "do", "banade", "named", "called", "naam", "ka",
                        "ki", "se", "in", "on", "inside", "mein", "me", "execute", "run",
                        "every", "morning", "evening", "daily", "at", "am", "pm",
                        "desktop", "documents", "downloads", "pictures", "music", "videos"}
                candidates = [tok for tok in tokens if tok.lower() not in stop]
                name = candidates[-1] if candidates else ""
            name = name.strip().strip('"').strip("'")
            if name and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name) and name not in (".", ".."):
                try:
                    from computer.files import resolve_user_location
                    parent = resolve_user_location(location)
                    d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                                "files.mkdir",
                                                target=str(parent),
                                                params={"path": str(Path(parent) / name)}))
                    d.reply_hint = f"{name} folder {location} mein bana raha hoon."
                    return d
                except Exception:
                    pass

        # 1b) read-only system inspection. Real acceptance found "network status"
        # and "inspect desktop" fell through to conversation (no tool), and
        # "audio devices" was misrouted to media playback. These are exact,
        # bounded, read-only capabilities and must resolve deterministically.
        if _AUDIO_DEVICES_RE.search(t):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "system.audio.devices"))
            # Build the response from the enumerated devices, not a generic
            # pre-action acknowledgement.
            d.reply_hint = ""
            return d
        if _NETWORK_STATE_RE.search(t):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "system.network.state"))
            d.reply_hint = ""
            return d
        if _STORAGE_INFO_RE.search(t):
            drive_match = (re.search(r"(?<![A-Za-z0-9])([A-Za-z])\s*:(?:\\)?", t)
                           or re.search(r"\b(?:drive|disk|volume)\s+([A-Za-z])\b", t, re.I)
                           or re.search(r"\b([A-Za-z])\s+(?:drive|disk|volume)\b", t, re.I))
            wants_all = bool(re.search(r"\b(all|every)\s+(?:mounted\s+)?(?:drives?|volumes?)\b|"
                                       r"\b(?:sab|saare)\s+(?:drives?|volumes?)\b", t, re.I))
            drive = ("all" if wants_all else
                     (f"{drive_match.group(1).upper()}:\\" if drive_match else ""))
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "files.disk_usage", params={"path": drive}))
            d.reply_hint = ""
            return d
        if _DESKTOP_INSPECT_RE.search(t):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "desktop.observe"))
            d.reply_hint = "Desktop observe kar raha hoon."
            return d
        if re.search(r"\bminimize\b[\s\S]{0,24}\b(?:all|every)\b[\s\S]{0,16}\bwindows?\b|"
                     r"\b(?:all|every)\s+(?:the\s+)?windows?\b[\s\S]{0,20}\bminimize\b|"
                     r"\bsaari?\s+windows?\b[\s\S]{0,20}\bminimize\b", t, re.I):
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "window.minimize_all"))
            d.reply_hint = ""
            return d

        # A YouTube/song request is a browser action, not a device-media plugin
        # action. The old generic song branch converted it to `media.play`, which
        # made the answer depend on the optional media plugin and never opened the
        # requested browser page.
        web_service, _web_url = find_web_service(t)
        web_query = find_media_query(t, web_service)
        if web_query:
            d.tasks.append(DirectorTask(TaskType.COMPUTER_ACTION, "pc_main",
                                        "browser.media.play", params={
                                            "service": web_service or "youtube",
                                            "query": web_query,
                                        }))
            d.reply_hint = "Browser mein video kholkar play kar raha hoon."
            return d

        # 2) media control (device-aware) -----------------------------------
        if _SONG_CTX.search(t) or re.search(r"(media|player)", t, re.I):
            device = _find_device(t) or "pc_main"
            if _NEXT_RE.search(t):
                d.tasks.append(DirectorTask(TaskType.DEVICE_ACTION, device, "media.next"))
                d.reply_hint = f"{device} par agla track."
                return d
            if _PREV_RE.search(t):
                d.tasks.append(DirectorTask(TaskType.DEVICE_ACTION, device, "media.previous"))
                d.reply_hint = f"{device} par pichhla track."
                return d
            if _PAUSE_RE.search(t):
                d.tasks.append(DirectorTask(TaskType.DEVICE_ACTION, device, "media.pause"))
                d.reply_hint = "Media pause."
                return d
            if _PLAY_RE.search(t):
                d.tasks.append(DirectorTask(TaskType.DEVICE_ACTION, device, "media.play"))
                d.reply_hint = "Media play."
                return d

        # 3) application open / close ---------------------------------------
        if re.search(r"\b(uninstall|remove|delete)\b.{0,50}\b(app|application|software|program)\b|"
                     r"\b(app|application|software|program)\b.{0,50}\b(uninstall|remove|delete)\b", t, re.I):
            # Inspect the real installed-app UI and request owner confirmation.
            # Never misread "uninstall X" as "open X".
            d.reasoning_required = True
            d.provider_category = "reasoning"
            d.intent = "conversation"
            return d
        app = _find_app(t)
        if app:
            # Opening is only the first step when the owner also asks to edit
            # content. Route the whole request through the bounded tool loop so
            # it can observe the exact window/control, type into it, and verify
            # the resulting document instead of replying after launch alone.
            if re.search(r"\b(write|type|enter|draft|compose|edit|fill|likho|likh|bhar|daalo)\b",
                         t, re.I):
                d.tasks = []
                d.reasoning_required = True
                d.provider_category = "reasoning"
                d.intent = "application_interaction"
                d.reply_hint = ""
                return d
            if _CLOSE_RE.search(t) and not _OPEN_RE.search(t):
                d.tasks.append(DirectorTask(TaskType.APPLICATION_ACTION, "pc_main",
                                            "application.close", target=app))
                d.reply_hint = f"{app} band kiya."
                return d
            d.tasks.append(DirectorTask(TaskType.APPLICATION_ACTION, "pc_main",
                                        "application.open", target=app))
            d.reply_hint = f"{app} khol raha hoon."
            return d

        # 4) memory ----------------------------------------------------------
        if _MEMORY_QUERY.search(t):
            d.memory_query = t
            d.reasoning_required = True
            d.provider_category = "reasoning"
            d.confidence = 0.7
            d.intent = "conversation"
            return d
        if _MEMORY_WRITE.search(t):
            d.memory_writes.append({
                "type": "preference", "entity": "user statement",
                "value": t, "confidence": 0.6, "source": "conversation"})
            d.reply_hint = "Yaad kar liya."
            return d

        # 5) explicit complex categories -------------------------------------
        if re.search(r"(code|banao|banado|develop|app bana|refactor|fix bug|debug)", t, re.I):
            d.reasoning_required = True
            d.provider_category = "coding"
            return d
        if re.search(r"(search|research|dhundho|khojo|batao .*kaise|explain)", t, re.I):
            d.reasoning_required = True
            d.provider_category = "research"
            return d

        # 6) default: conversation (or a mission if durable work was detected) --
        d.reasoning_required = True
        d.provider_category = "reasoning"
        d.confidence = 0.6
        d.reply_hint = ""
        if not d.mission_required:
            d.intent = "conversation"
        return d

    def available(self) -> bool:
        return True
