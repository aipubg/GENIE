# GENIE — PLUGINS

Authoritative: master spec §5.7–5.8.

## Purpose

Apps ke liye plugins: `Spotify · Blender · DaVinci · Premiere · Photoshop · VS Code · Steam · OBS · Chrome · Home Assistant`.
Core source modify kiye bina integration install ho.

## Contract (MUST)

`manifest · capabilities · actions · observations · permissions · events · tests`

Examples:
- Spotify: `play · pause · next · previous · search · play_item · current_track`
- Blender: `inspect_scene · open_project · save · run_script · render · export`

## Plugin SDK

```text
register capabilities · register actions · register events
expose observations · define permissions · define tests
```

## Sandbox defaults (deny-first)

| Resource | Default |
|---|---|
| Filesystem | declared paths only; workspace by default |
| Network | deny; per-plugin allowlist |
| Device / mouse / keyboard | deny unless capability declared |
| Process spawn | deny by default |
| Crash isolation | separate process; crash ≠ daemon crash; backoff restart; 3 fails → disable + notify |

Install par declared permissions clearly dikhne chahiye. Revoke bina uninstall ke possible.

## Runtime rules

- Plugin **daemon ke andar nahi** — `plugin-host` process mein.
- Har action PTE scope check + audit se guzarta hai.
- Plugin output = **untrusted** (SECURITY.md) jab tak plugin verified na ho.
- Version + compatibility range (app versions) manifest mein.

## Testing

Har plugin ke saath: unit tests + sandbox-violation tests + contract tests + a sandbox-run golden task.
See `plugins/installed/<id>/TESTS.md`.
