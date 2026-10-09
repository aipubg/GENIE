#!/usr/bin/env bash
# Build Genie.Desktop (WPF) from Git Bash on this host.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# The agent shell on this machine starts with a heavily sanitized environment
# (~80 vars). Standard Windows variables such as ProgramData, ALLUSERSPROFILE,
# APPDATA, ComSpec, PATHEXT, ProgramFiles and CommonProgramFiles are absent.
#
# NuGet's configuration loader (NuGet.Configuration.XPlatMachineWideSetting ->
# NuGet.Common.NuGetEnvironment.GetFolderPath) resolves a machine-wide config
# path via Path.Combine(...). With those variables missing the first argument
# is null and restore dies with:
#
#     NuGet.targets(745,5): error : Value cannot be null. (Parameter 'path1')
#
# This is NOT a node-reuse problem and NOT a corrupt package cache. Setting the
# variables below restores NuGet's config discovery and the build proceeds
# normally. `dotnet nuget list source` is the fastest way to confirm the fix.
#
# Usage:
#   scripts/build_native.sh                 # build Genie.Desktop (Release)
#   scripts/build_native.sh --clean         # wipe obj/bin first, then build
#   scripts/build_native.sh --project <p>   # build another csproj
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTNET_DIR="${DOTNET_DIR:-$REPO_ROOT/../.dotnet8}"

export PATH="$DOTNET_DIR:$PATH"

# --- restore the standard Windows environment NuGet needs -------------------
export ProgramData='C:\ProgramData'
export ALLUSERSPROFILE='C:\ProgramData'
export APPDATA='C:\Users\ghostt\AppData\Roaming'
export LOCALAPPDATA="${LOCALAPPDATA:-C:\Users\ghostt\AppData\Local}"
export USERPROFILE="${USERPROFILE:-C:\Users\ghostt}"
export ComSpec='C:\WINDOWS\system32\cmd.exe'
export PATHEXT='.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC'
export ProgramFiles='C:\Program Files'
export CommonProgramFiles='C:\Program Files\Common Files'
export PUBLIC='C:\Users\Public'
export OS='Windows_NT'

PROJECT="$REPO_ROOT/ui/windows/Genie.Desktop/Genie.Desktop.csproj"
CLEAN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    --project) PROJECT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "$CLEAN" == "1" ]]; then
  echo "[build_native] cleaning obj/ and bin/ ..."
  rm -rf "$(dirname "$PROJECT")/obj" "$(dirname "$PROJECT")/bin"
fi

# dotnet.exe is a native process: it needs a Windows path, not an MSYS one.
PROJECT_WIN="$(cygpath -m "$PROJECT" 2>/dev/null || echo "$PROJECT")"

echo "[build_native] dotnet build $(basename "$PROJECT") -c Release"
dotnet build "$PROJECT_WIN" -c Release --nologo "$@"
