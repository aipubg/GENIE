> **Status: OPEN / NOT CLAIMED.** No Windows 10 host has been available, so
> nothing here has been observed. Windows 11 evidence must never be presented
> as Windows 10 evidence.
>
> Current candidate for when a Windows 10 host exists: **rc18**.

# Windows 10 x64 acceptance — OPEN (not claimed)

**Status: open. Windows 10 support is NOT claimed.**

This document is deliberately explicit because the difference matters. GENIE's
Windows-native client targets `net8.0-windows` and ships self-contained x64.
Every component in it is *believed* to run on Windows 10, but "believed" is not
"accepted", and no Windows 10 machine has been available in this workspace to
produce evidence. Nothing here should be read as a support statement.

## Why it is plausible but unverified

| Component | Windows 10 position |
|---|---|
| .NET 8 self-contained WPF | Supported by Microsoft on Windows 10 1809+. Self-contained, so no runtime install. |
| Embedded Python 3.12.6 | Supports Windows 10. |
| Win32 calls used (`NtQuerySystemInformation`, user32, named mutex) | Available on Windows 10. |
| NSIS installer | Runs on Windows 10. |

Plausible is not verified. The gap is evidence, not theory.

## What must actually be run

On a real Windows 10 x64 machine (1809 or later), with the built client:

```
python scripts\verify_win10_acceptance.py ^
    --exe ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe ^
    --out artifacts\win10_acceptance.json
```

Then the full lifecycle, which the pre-flight does not cover:

```
python scripts\verify_native_client.py     --exe <exe>    # 6 checks
python scripts\verify_state_persistence.py --exe <exe>    # 9 checks
python scripts\verify_single_instance.py                  # 6 checks
python scripts\verify_no_console_children.py --seconds 900
```

## Acceptance criteria

All of the following must be true on the Windows 10 host, with the result file
recording that host's actual OS build:

- [ ] the client launches; the backend answers `/health`
- [ ] `/api/competition`, `/api/experience`, `/api/knowledge`, `/api/media` respond
- [ ] a graceful close leaves **zero** GENIE-owned processes
- [ ] the backend stops with the client (no orphaned `pythonw.exe`)
- [ ] a durable item (model role) survives a full exit and relaunch
- [ ] a second launch activates the running instance instead of starting a second one
- [ ] a 15-minute watch shows **no** console child spawned by GENIE
      (no `tasklist.exe`, `conhost.exe`, `cmd.exe`, `powershell.exe`, `wmic.exe`)
- [ ] no visible terminal window at any point — this is the original incident

## Known risk specific to Windows 10

The flashing-terminal fix depends on `NtQuerySystemInformation`, which is
present on Windows 10 but is a native API surface rather than a documented
Win32 one. If it is ever unavailable, `list_processes()` falls back to
`tasklist` with `CREATE_NO_WINDOW` — hidden, but a subprocess. On Windows 10 the
15-minute watch is therefore the check that matters most: it distinguishes
"hidden" from "not created".

## Recording a result

`scripts/verify_win10_acceptance.py` writes the host's real OS release and
build into the result file next to every check, and stamps every result with
`"claim": "NONE"`. A result gathered on Windows 11 cannot be mistaken for
Windows 10 evidence.
