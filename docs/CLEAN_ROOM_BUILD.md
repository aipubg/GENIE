# Clean-room Windows build after owner purge

Run from a fresh checkout after `scripts/verify_final_workspace.py` passes.
Use the provisioned user-local build tools, not a toolchain inside the checkout.

```powershell
$env:GENIE_BUILD_ROOT = "$env:LOCALAPPDATA\GENIE\build"
$dotnet = "$env:LOCALAPPDATA\GENIE\build\dotnet-sdk-8.0.425\dotnet.exe"
& $dotnet publish ui/windows/Genie.Desktop/Genie.Desktop.csproj -c Release -r win-x64 --self-contained true
& "$env:LOCALAPPDATA\GENIE\build\embed\python-embed\python.exe" scripts/build_backend_runtime.py
& "$env:LOCALAPPDATA\GENIE\build\embed\python-embed\python.exe" scripts/sync_backend_runtime.py
& "$env:LOCALAPPDATA\GENIE\build\embed\python-embed\python.exe" scripts/verify_build_match.py
& "$env:LOCALAPPDATA\GENIE\build\embed\python-embed\python.exe" -m pytest
makensis.exe installer/genie_native.nsi
& "$env:LOCALAPPDATA\GENIE\build\embed\python-embed\python.exe" scripts/verify_installer.py
```

The commands are instructions, not a claim that a clean-room build has run.
If NSIS 3.10 is not on `PATH`, install it from its official distribution in a
separate tool location before compiling. Then install the resulting package in
a disposable Windows account/VM and verify daemon health, graceful shutdown,
data preservation and uninstall. Record commit, runtime identity, date, size,
SHA-256 and notices in the new release manifest. Do not reuse the old installer
as evidence.
