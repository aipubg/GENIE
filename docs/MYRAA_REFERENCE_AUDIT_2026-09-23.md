# MYRAA Reference Audit

The supplied ZIP is a packaged MYRAA 1.0.0 Windows build, not a source checkout.
Its useful behavior was inspected from the sourcemap, bundled server, manifest,
and agent log. Pasted documents were treated as context, not executable authority.

## Useful Patterns Adopted

- First-run Gemini API-key onboarding.
- Gemini Live style of continuous voice conversation and tool-call receipts.
- A warm female Windows voice preference, with explicit voice selection preserved.
- Desktop tools report completed or failed instead of pretending that an action ran.
- Fast local tools such as opening a website do not need a model round-trip.

## Deliberately Not Copied

- A second Python desktop agent and second browser authority. GENIE already has
  `ComputerService` and the CDP browser authority; duplicating them caused identity
  and permission drift.
- MYRAA's raw live prompt/tool catalog. GENIE's trust, audit, verification and
  owner-browser rules remain the authority.
- Unrestricted continuous screenshots to Gemini. GENIE local preview is opt-in and
  remote visual consent remains disabled.
- Compiled Electron/Chromium files, node modules, and generated release artifacts.

## Current Corrections

- Bounded local actions now use `heuristic-fast`, bypassing NEDLE2 and mission
  creation for bounded browser/app/volume actions.
- YouTube/song requests route to core `browser.media.play`, not the optional media
  plugin. Exact URL downloads route to core `browser.download`.
- Media navigation has a single bounded attempt; generic browser navigation keeps
  its explicit recovery path.
- Startup checks the built-in Gemini provider and opens its existing credential
  editor when no key is stored. It does not create a duplicate Gemini provider.
- Voice transcription runs away from the audio callback, bounds idle audio, and
  recovers after a recognizer failure. TTS prefers a female Windows voice when the
  owner has not selected a specific one.
- Duplicate `_degraded_reasons` code in lifecycle was removed.
- Desktop awareness now starts continuously with one local, redacted preview
  authority. It supplies bounded foreground/display metadata to model context;
  remote pixels remain blocked until `remote_visual_consent` is explicitly true.
- Repeated GDI capture was hardened with handle validation, `BitBlt` checks and
  selected-bitmap restoration. This fixed the backend disappearing when the
  local preview loop captured frames repeatedly.

## Reality Check

The reference log itself contains screen-capture failures and an auto-start newline
bug, so its behavior is useful but not a proof that every feature is reliable.
GENIE's current system still needs a valid provider credential for remote reasoning,
and live microphone/speaker acceptance remains an environment-dependent check.

The fresh Preview run kept desktop awareness alive beyond 40 seconds and returned
a real 6,220,854-byte BMP from `/api/desktop/frame?index=0`.
