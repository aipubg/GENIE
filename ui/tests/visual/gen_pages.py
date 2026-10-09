"""Generate the operational GENIE pages.

Each page reuses the shared shell (sidebar + top bar) and binds to REAL backend
endpoints. Where a backend service is not registered on this daemon the page
shows an explicit honest state instead of invented data (item 37).

    python ui/tests/visual/gen_pages.py
"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "ui" / "web"

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>GENIE — {title}</title>
<link rel="stylesheet" href="/ui/tokens.css" />
<link rel="stylesheet" href="/ui/app-shell.css" />
<link rel="stylesheet" href="/ui/ops.css" />
</head>
<body data-page="{page}">
<div class="ops">
  <div id="genie-sidebar"></div>
  <header id="genie-topbar"></header>
  <main class="content">
{body}
  </main>
</div>
<script src="/ui/page-shell.js"></script>
<script src="/ui/pages.js"></script>
</body>
</html>
"""


def page(name: str, title: str, body: str) -> None:
    (WEB / name).write_text(HEAD.format(title=title, page=name[:-5], body=body),
                            encoding="utf-8")
    print("  wrote", name)


# --------------------------------------------------------------------- Chat
page("chat.html", "Chat", """
    <h1>Chat</h1>
    <p class="lede">Conversation with GENIE. Voice and text enter the same request path.</p>
    <div class="chat-wrap">
      <div class="chat-log" id="chat-log" aria-live="polite">
        <div class="msg sys">Ask anything. The reply comes from the real GENIE backend.</div>
      </div>
      <form class="commandbar" id="chat-form" autocomplete="off" style="padding:0">
        <div class="pill">
          <input id="chat-input" placeholder="Ask Genie anything..." aria-label="Ask Genie" />
          <button type="submit" class="send" aria-label="Send">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 20 21 12 3 4l3 8-3 8z"/></svg>
          </button>
        </div>
      </form>
    </div>
""")

# ----------------------------------------------------------------- Missions
page("missions.html", "Missions", """
    <h1>Missions</h1>
    <p class="lede">Real mission state from the GENIE mission service.</p>
    <div id="missions-body"></div>
""")

# ------------------------------------------------------------------- Agents
page("agents.html", "Agents", """
    <h1>Agents</h1>
    <p class="lede">Runtime, provider/model, task, health and cost — as reported by the backend.</p>
    <div id="agents-body"></div>
""")

# ----------------------------------------------------------------- Computer
page("computer.html", "Computer", """
    <h1>Computer</h1>
    <p class="lede">Two clearly separated surfaces: your machine and the GENIE workspace.</p>
    <div class="split">
      <section class="zone user">
        <h3>&#9635; USER COMPUTER</h3>
        <div id="user-computer"></div>
      </section>
      <section class="zone genie">
        <h3>&#9670; GENIE WORKSPACE</h3>
        <div id="genie-workspace"></div>
      </section>
    </div>
""")

# ------------------------------------------------------------------- Skills
page("skills.html", "Skills", """
    <h1>Skills</h1>
    <p class="lede">Provenance, scanner verdict, permissions and capabilities from the real skill service.</p>
    <div id="skills-body"></div>
""")

# ------------------------------------------------------------------ Devices
page("devices.html", "Devices", """
    <h1>Devices</h1>
    <p class="lede">GENIE Device Mesh — pairing, trust, capabilities and last seen.</p>
    <div id="devices-body"></div>
""")

# ------------------------------------------------------------------- Memory
page("memory.html", "Memory", """
    <h1>Memory</h1>
    <p class="lede">Personal persistent memory. Distinct from Knowledge — this is what GENIE remembers about you.</p>
    <div id="memory-body"></div>
""")

# ---------------------------------------------------------------- Knowledge
page("knowledge.html", "Knowledge", """
    <h1>Knowledge</h1>
    <p class="lede">Documents, corpora and indexed sources. Not personal memory.</p>
    <div id="knowledge-body"></div>
""")

# -------------------------------------------------------------------- Media
page("media.html", "Media", """
    <h1>Media</h1>
    <p class="lede">Artifacts produced by missions and agents.</p>
    <div id="media-body"></div>
""")

# ----------------------------------------------------------------- Forecast
page("forecast.html", "Forecast", """
    <h1>Forecast</h1>
    <p class="lede">Calibrated, evidence-aware forecasting. GENIE never invents a probability.</p>
    <div id="forecast-body"></div>
""")

# ----------------------------------------------------------------- Security
page("security.html", "Security", """
    <h1>Security</h1>
    <p class="lede">GENIE SecurityScope / PTE authority. Strix appears only when it actually executes.</p>
    <div id="security-body"></div>
""")

# ----------------------------------------------------------------- Settings
page("settings.html", "Settings", """
    <h1>Settings</h1>
    <p class="lede">Voice, companion, models, privacy, permissions and workspace.</p>
    <div class="set-grid" id="settings-body"></div>
""")

print("\\nGenerated", len(list(WEB.glob('*.html'))), "pages in", WEB)
