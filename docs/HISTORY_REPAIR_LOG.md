# Durable repair and release lessons

This is the retained engineering history for the native Windows product. Dated
audit snapshots and intermediate acceptance reports are not current release
authority; consult `FINAL_WORKSPACE_AUDIT.md` and the current release docs.

## Runtime and packaging

- The native WPF client is the product UI. The backend runs from the packaged
  embedded CPython tree. Source changes do not reach Preview until the runtime
  is rebuilt or synchronized and its file manifest is checked.
- Mutable state belongs under `%LOCALAPPDATA%\GENIE` in installed mode. Bundled
  `config/` is read-only defaults; per-user overrides, databases, vault, logs,
  browser state and models are user data.
- The sole installer authority is `installer/genie_native.nsi`. Do not use the
  retired Inno Setup script or Electron packaging path.
- Browser launch requires an absolute profile directory and an unowned free
  debugging port. Owner-existing sessions must be explicitly selected and may
  never be replaced silently with a GENIE-owned profile.
- WPF/UIA availability must be checked inside the packaged Python runtime,
  including pywin32 import and DLL search paths; source-interpreter success is
  not proof of packaged desktop control.
- Window close verification must observe that the target window/process is
  gone; hidden or minimized state is not close evidence.

## Routing, safety and acceptance

- Deterministic heuristics and NEDLE2 are the active production routing path.
  Laya is pinned and may be provisioned for shadow evaluation only; do not
  promote it from a small or in-sample evaluation.
- Consequential actions require the existing permission and confirmation flow.
  A successful capability unit test is not acceptance of the complete WPF
  Chat/Voice-to-action workflow.
- Mission recurrence needs evidence of persisted schedule state and a later
  scheduled occurrence, in addition to one completed run.
- Generic browser fixtures do not prove access to an owner's authenticated
  Brave session. WhatsApp delivery, physical audio and remote visual consent
  remain owner/session acceptance items until directly observed.
- Never report an owner acceptance result without fresh visible evidence and a
  receipt tied to the exact action.

## Workspace hygiene

- Runtime artifacts, model weights, browser profiles, logs and private state
  must be external to the source workspace. Keep only source, required tests,
  release inputs and current documentation in a distributable checkout.
- Workspace deletion is owner-run only when the execution policy blocks the
  agent. Keep an external checkpoint and a path-by-path manifest; do not use
  alternate deletion mechanisms to bypass policy.
