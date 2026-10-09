@echo off
REM GENIE local build helper (Windows, unelevated).
REM
REM 7-Zip 21.07 exits non-zero while extracting electron-builder's winCodeSign
REM toolchain archive because it cannot create two macOS symlinks
REM (darwin/10.12/lib/libcrypto.dylib and libssl.dylib) without
REM SeCreateSymbolicLinkPrivilege. Every other file in the archive extracts
REM correctly, and macOS signing tools are irrelevant to a Windows build, so the
REM non-zero exit is swallowed here.
REM
REM Proper fix: build from an elevated shell, or enable Windows Developer Mode.
REM This wrapper is a local build-time helper only and is never shipped.
"%~dp0..\node_modules\7zip-bin\win\x64\7za.exe" %*
exit /b 0
