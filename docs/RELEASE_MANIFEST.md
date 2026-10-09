GENIE — VISUAL IDENTITY AND NATIVE UI REDESIGN PASS

Continue from the current verified Windows-native GENIE baseline.

Current verified candidate:
  
rc23
  
source: 3a77d7c
  
installer:
  
E:\G3\GENIE\dist\GENIE-Setup.exe
  
SHA-256:
  
3d1f5effba101c0a92aeaf9f594647b3c57d1233b99c58dcfead75e700107c95

rc23 is historical once product UI code changes.
  
Do NOT move or retag rc23.

The next verified product candidate must receive a NEW RC identity.

============================================================

1. DESIGN DIRECTION
     
   ============================================================

The owner has supplied two visual references:

A. A futuristic glowing blue/violet AI waveform image.
  
B. The supplied GENIE character / lamp artwork.

Use them as visual identity references for the new GENIE experience.

Do NOT simply paste the waveform image as a giant background everywhere.

Interpret the visual language:

```
deep midnight blue
sapphire
electric cyan
violet accents
subtle warm gold
soft glow
controlled translucency
intelligent energy / waveform language
```

The product should feel:

```
premium
modern
intelligent
alive
unmistakably GENIE
```

But still remain:

```
clean
readable
fast
professional
practical
```

============================================================
  
2\. USE THE PROVIDED GENIE ARTWORK
==================================

The newly supplied GENIE character/lamp image is the canonical visual branding
  
reference for this pass.

Use it for suitable branding assets such as:

```
application icon
window icon
installer icon where technically appropriate
Start Menu icon
Desktop shortcut icon
small Home branding / companion presence
```

Generate the required Windows icon sizes properly from the supplied artwork.

Do not stretch or distort the artwork.

Keep its aspect ratio.

Do not replace it with a generic AI icon.

Do not recreate a different mascot.

============================================================
  
3\. HOME PAGE — MAIN VISUAL REDESIGN
====================================

Home should receive the strongest visual treatment.

The current blank white Home page is functionally acceptable but visually too
  
empty and generic.

Create a premium GENIE Home experience.

Suggested structure:

```
branded header
    GENIE
    current greeting
    compact connection/status indicator

hero / intelligence area
    elegant AI waveform / energy visualization
    inspired by the supplied waveform reference

GENIE character artwork
    integrated tastefully into the hero
    not oversized
    not obstructing functionality

central interaction
    command/chat input
    voice action
    clear Send/action state

useful live context
    current mission
    next step
    recent activity
    system/backend state
    quick actions
```

Do not turn Home into a decorative poster.

The visual hero must coexist with useful information.

============================================================
  
4\. WAVEFORM BEHAVIOUR
======================

Create the visual language of the supplied waveform image using native WPF
  
drawing/animation where practical.

Preferred:

```
lightweight native vector/gradient rendering
subtle glow
smooth low-cost movement
optional response to voice/audio state
```

Avoid:

```
embedded Chromium
WebView
video backgrounds
giant GIFs
heavy GPU shaders
high-frequency animation
excessive CPU/GPU use
```

When GENIE is idle:

```
waveform should be calm
```

When listening:

```
waveform may become active
```

When thinking/working:

```
slow intelligent pulse
```

When speaking:

```
waveform may react to actual available audio level where supported
```

If real state is not available, do NOT fake activity.

============================================================
  
5\. PERFORMANCE REQUIREMENT
===========================

The owner has experienced system instability previously.

The visual redesign must remain lightweight.

Measure:

```
idle CPU
idle GPU
memory
animation cost
```

No visual effect is worth causing unnecessary heat or resource consumption.

Animations must:

```
pause/reduce when the window is not visible
avoid busy loops
avoid continuous high-frequency timers
```

============================================================
  
6\. GLOBAL APP THEME
====================

Apply a coherent GENIE visual language across the native application.

Recommended palette:

```
background:
    near-black / midnight navy

surfaces:
    dark blue-neutral panels

primary:
    sapphire / electric blue

secondary:
    cyan / violet

premium accent:
    restrained antique/warm gold

text:
    high-contrast off-white
```

Do not use bright blue everywhere.

Gold should remain an accent, not dominate the product.

============================================================
  
7\. OPERATIONAL PAGES SHOULD STAY CALMER
========================================

Home may be visually expressive.

Operational pages should be calmer and highly readable:

```
Chat
Missions
Agents
Computer
Skills
Devices
Memory
Forecast
Security
Competition
Experience
Knowledge
Media
Settings
```

Use the GENIE palette and components consistently, but do not put large
  
waveform artwork or mascot illustrations behind data tables.

Think:

```
branded Home
professional workspace everywhere else
```

============================================================
  
8\. NAVIGATION
==============

Improve the left navigation visually.

Keep the existing simple structure.

Add:

```
proper iconography
clear active state
better spacing
subtle hover state
compact GENIE branding at the top
```

The supplied GENIE artwork may be represented by a small icon/avatar in the
  
navigation header.

Do not make the sidebar visually noisy.

============================================================
  
9\. CHAT
========

Chat should become a polished AI workspace.

Improve:

```
message bubbles / message grouping
user vs GENIE distinction
streaming state
Stop action
input area
attachment/artifact affordance where supported
voice action where supported
errors/retry
```

Keep:

```
Enter = send
Shift+Enter = newline
```

The input should feel like the main command interface for GENIE.

============================================================
  
10\. MISSIONS
=============

The current mission table is too utilitarian.

Improve readability with:

```
clear state badges
goal text hierarchy
active/current mission distinction
contextual actions
```

Do not show Cancel for already completed missions.

Completed mission:
  
View / Open

Active mission:
  
Cancel where valid

Failed/revision:
  
Retry / Inspect where valid

Do not invent actions unsupported by the backend.

============================================================
  
11\. AGENTS
===========

The Agents screen currently appears almost empty when there is no active agent
  
state.

Provide a useful honest empty state:

```
"No agents are currently active."
```

Explain briefly what appears here when a mission launches workers.

When active, show:

```
agent
role
task
model/provider
state
artifact/output
error where applicable
```

Avoid raw internal dumps.

============================================================
  
12\. COMPUTER
=============

Computer must not expose raw JSON in normal mode.

Convert isolation/runtime state into readable UI.

Example groups:

```
Workspace
Browser
Desktop isolation
Microphone
Camera
Computer actions
```

Status values should be understandable:

```
Available
Active
Not configured
Unavailable
Unknown
```

Raw evidence/details belong in Advanced diagnostics only.

============================================================
  
13\. DEVICES
============

Do not display the current raw backend field dump as the primary UX.

Create readable device presentation:

```
device name
platform
trust
connection state
capabilities
last seen
```

Advanced details can reveal:

```
ids
fingerprints
transport internals
sync details
```

============================================================
  
14\. KNOWLEDGE
==============

Knowledge is now a real authority.

Its native UI should expose actual useful actions:

```
Import source
Search
source list
index status
indexed item count
provenance
remove from index
```

When empty:

```
"No knowledge sources imported yet."
```

Do not expose:
  
source_count
  
indexed_item_count
  
failing_sources

as raw key/value fields to the owner.

Present those as human-readable summary metrics.

============================================================
  
15\. MEDIA
==========

Media should become an artifact browser rather than a generic Item/Detail grid.

When items exist, display:

```
name
type
mission
agent
timestamp
availability
```

Allow opening the artifact where supported.

When empty:

```
"No generated artifacts yet."
```

Do not fabricate thumbnails when none exist.

============================================================
  
16\. SKILLS
===========

If the API returns unavailable/no response, provide a clear owner-facing state.

Do not make the owner interpret:

```
"no response from /api/skills"
```

unless Advanced diagnostics is enabled.

Normal wording:

```
"Skills are currently unavailable."
```

Advanced:
  
may show the endpoint/error.

Also investigate why `/api/skills` was unavailable in the rendered screenshot
  
before calling the surface complete.

============================================================
  
17\. SECURITY
=============

Turn Security into a readable status surface.

Present:

```
overall state
findings
severity
unresolved count
```

No raw dictionaries / backend summaries in normal mode.

============================================================
  
18\. COMPETITION
================

Preserve the existing real Competition workflow.

Improve presentation only.

Make:

```
objective
task
candidate count
commit policy
current state
verifier
result
```

easy to understand.

Keep the important safety explanation:

```
only the verified winner commits
external side effects happen once
```

============================================================
  
19\. EXPERIENCE
===============

Preserve the current dedicated Experience surface.

Improve visual hierarchy for:

```
strategies that worked
approaches that failed
verifier lessons
learning by agent
reusable configuration
```

Do not add decorative content that makes an empty experience bank look
  
populated.

============================================================
  
20\. SETTINGS
=============

The current Settings page still looks like a developer form.

Reorganize it into professional sections.

Normal mode:

```
General
Voice
AI Models
Providers
Privacy
About
```

Advanced:

```
instance
NEDLE2
raw endpoint
STT/TTS internals
runtime diagnostics
test/developer providers
```

Important:

The owner screenshot showed:

```
everyday = final_stub
```

A stub/test model must NOT be presented as a normal production Everyday role.

Investigate this state.

If it is a test artifact:
  
remove/replace it from the normal owner configuration.

Do not silently choose a paid/cloud provider without owner configuration.

Show an honest configuration-needed state when appropriate.

============================================================
  
21\. BACKEND STATUS CONSISTENCY
===============================

Several supplied screenshots show:

```
"Backend unavailable"
```

while other pages contain live backend data.

Investigate whether this was:

```
stale capture state
external backend state
UI binding delay
actual health-state bug
```

The UI must not simultaneously show healthy live data and "Backend unavailable"
  
without a valid explanation.

Make connection state authoritative and consistent.

============================================================
  
22\. VISUAL COMPONENT SYSTEM
============================

Create reusable native WPF components/styles for:

```
buttons
text inputs
section headers
cards/panels
status badges
empty states
tables/lists
navigation items
dialogs
progress states
```

Do not hand-style every page independently.

One component system.

============================================================
  
23\. ACCESSIBILITY / READABILITY
================================

Maintain:

```
readable font sizes
high text contrast
keyboard navigation
visible focus
screen-reader-compatible labels where practical
sensible scaling at Windows DPI settings
```

Test at:

```
100%
125%
150%
```

Avoid text clipping.

============================================================
  
24\. DO NOT BREAK THE VERIFIED CORE
===================================

This is primarily a UI/visual identity pass.

Do not unnecessarily alter:

```
lifecycle
process ownership
backend bootstrap
memory
mission engine
provider gateway
NEDLE2
Competition engine
Knowledge authority
Media authority
installer shutdown protocol
```

Preserve all proven lifecycle/no-console behavior.

============================================================
  
25\. CURRENT CANDIDATE IMMUTABILITY
===================================

rc23 is already verified.

Do NOT move or mutate:

```
genie-v0.1.0-rc23
```

Any product-code/theme/UI changes require a new candidate.

Use the next clean RC identity after successful validation.

============================================================
  
26\. VISUAL ACCEPTANCE
======================

After implementing the redesign, capture real rendered screenshots of:

```
Home
Chat
Missions
Agents
Computer
Knowledge
Media
Settings
Competition
Experience
```

Do not judge UI only by XAML inspection.

Review the actual rendered application.

Check:

```
visual consistency
clipping
raw internal data
empty states
contrast
spacing
resize behavior
icon rendering
GENIE branding
```

============================================================
  
27\. FUNCTIONAL REGRESSION
==========================

After visual changes, rerun at minimum:

```
.NET tests
smoke
single instance
shutdown
persistence
base installer
upgrade installer
no-console
zero-orphan verification
```

Run Python tests if backend code changes.

Do not reuse stale binaries/results.

============================================================
  
28\. FINAL DESIGN PRINCIPLE
===========================

Do not make GENIE look like:

```
a database admin panel
an internal debug tool
a plain default WPF prototype
```

Do not make GENIE look like:

```
a game launcher
a fantasy poster
an over-animated concept demo
```

Target:

```
a premium personal AI workstation
with a distinctive GENIE identity
```

The supplied futuristic waveform image defines the AI-energy visual language.

The supplied GENIE character/lamp artwork defines the brand identity.

Blend them carefully.

Home should feel alive.

Operational pages should feel calm.

Everything should remain functional, fast and truthful.

============================================================
  
29\. REPORT
===========

At the next candidate boundary report:

A. CURRENT HEAD
  
B. PRODUCT SOURCE
  
C. NEW RC
  
D. GENIE ICON / BRAND ASSET IMPLEMENTATION
  
E. HOME REDESIGN
  
F. WAVEFORM IMPLEMENTATION
  
G. NAVIGATION
  
H. CHAT
  
I. MISSIONS
  
J. AGENTS
  
K. COMPUTER
  
L. SKILLS
  
M. DEVICES
  
N. KNOWLEDGE
  
O. MEDIA
  
P. SECURITY
  
Q. COMPETITION
  
R. EXPERIENCE
  
S. SETTINGS
  
T. BACKEND STATUS CONSISTENCY
  
U. DPI / RESIZE TEST
  
V. CPU/GPU/MEMORY IMPACT
  
W. RENDERED SCREENSHOTS REVIEW
  
X. .NET TESTS
  
Y. PYTHON TESTS IF TOUCHED
  
Z. SMOKE
  
AA. SINGLE INSTANCE
  
AB. SHUTDOWN
  
AC. PERSISTENCE
  
AD. INSTALLER
  
AE. UPGRADE
  
AF. NO-CONSOLE
  
AG. ZERO ORPHANS
  
AH. INSTALLER PATH
  
AI. BYTE SIZE
  
AJ. FULL SHA-256
  
AK. WINDOWS 11 STATUS
  
AL. WINDOWS 10 STATUS
  
AM. SIGNING
  
AN. REMAINING ISSUES

Use measured results only.

Continue autonomously.
