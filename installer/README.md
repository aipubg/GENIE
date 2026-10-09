# Windows installer

GENIE has one installer authority: `genie_native.nsi` (NSIS). It installs the source-built native WPF client and the synchronized embedded Python backend per user, without elevation.

## Build

From a clean checkout with the supported .NET 8 SDK and NSIS 3.10 available:

1. Build `ui/windows/Genie.Desktop/Genie.Desktop.csproj` in Release for `win-x64`.
2. Build/sync `backend-dist/backend-runtime` using `scripts/build_backend_runtime.py` and `scripts/sync_backend_runtime.py`.
3. Run `scripts/verify_build_match.py` and applicable tests.
4. Compile `installer/genie_native.nsi` with `makensis.exe`. Output: `dist/GENIE-Setup.exe`.
5. Run `scripts/verify_installer.py` on a disposable Windows account or VM.

The NSIS script packages `ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64` and `backend-dist/backend-runtime`. It requests per-user installation and preserves `%LOCALAPPDATA%\GENIE` on uninstall. The current distributable is `dist/GENIE-Setup.exe`; rebuild it from the cleaned tree before release. Never treat an old hash or previous verification result as evidence for a new artifact.

## Release verification

Verify fresh install, launch, packaged runtime identity, `/health`, graceful shutdown, uninstall, data preservation, and absence of non-native runtimes. Record build date, source commit, file size, SHA-256 and runtime manifest in the release manifest.
