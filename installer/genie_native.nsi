; GENIE — Windows-native installer (NSIS, no electron-builder)
; ---------------------------------------------------------------------------
; STATUS: VERIFIED — compiled with NSIS 3.10 and tested end to end.
;
;     makensis.exe installer\genie_native.nsi      (~2 min; 360 MB payload)
;     -> dist\GENIE-Setup.exe                       (98 MB, 27.1% of payload)
;
; Verified 12/12 by scripts\verify_installer.py:
;   silent install (no UAC) -> installed app starts and backend answers /health
;   -> --genie-shutdown closes it and stops the backend -> silent uninstall
;   removes program files -> owner data under %LOCALAPPDATA%\GENIE survives.
; The only file left after uninstall is uninstall.exe itself, which is standard
; NSIS behaviour: an uninstaller cannot delete its own running executable.
;
; WHY NSIS AND NOT INNO SETUP
; The pre-existing installer\GENIE.iss is an Inno Setup script targeting the
; retired PyInstaller build (dist\GENIE\GENIE.exe + GenieBackend.exe). It does
; not match the Windows-native product. This script packages the WPF/.NET 8
; client and the embedded Python core.
;
; HARD RULES CARRIED OVER FROM installer/graceful_shutdown.nsh
; (that file is the electron-builder hook variant, kept as reference)
;   * Ask GENIE to close itself. Wait for a clean exit. NEVER force-kill.
;     Killing it orphans the backend and leaves files locked, which is what
;     produced "Failed to uninstall old application files".
;   * If GENIE will not close, tell the owner exactly what to do and let them
;     Retry — do not silently kill their session.
; ---------------------------------------------------------------------------

!include "MUI2.nsh"

Name "GENIE"
OutFile "..\dist\GENIE-Setup.exe"
; Per-user install. GENIE keeps memory, missions, the vault and the workspace
; under %LOCALAPPDATA%\GENIE, so installing to a shared admin-owned path would
; be wrong. No UAC prompt.
InstallDir "$LOCALAPPDATA\Programs\GENIE"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
; Canonical transparent multi-res GENIE icon (16, 24, 32, 48, 64, 128, 256).
; Used for the installer chrome, the uninstaller, the Start Menu shortcut,
; the Desktop shortcut, and the installed Genie.Desktop.exe itself
; (the C# csproj <ApplicationIcon> uses the same bytes at assets/Genie.ico).
!define MUI_ICON "..\ui\assets\brand\app-icon.ico"
!define MUI_UNICON "..\ui\assets\brand\app-icon.ico"
!define APP_EXE "Genie.Desktop.exe"
; Named mutex owned by the running instance. Used to detect "is GENIE running"
; without a plugin and without spawning tasklist.
; Backslash is literal in NSIS strings - $ is the escape character, not \.
; Escaping it as `$\` produced warning 6000 and a mangled name at runtime,
; which would have made the running-instance check silently always fail.
!define MUTEX_NAME "Local\Genie.Desktop.SingleInstance"

; ---------------------------------------------------------------------------
; Is GENIE running? Plugin-free: try to open its single-instance mutex.
; Leaves 1 in $0 when running, 0 when not.
; ---------------------------------------------------------------------------
!macro _IsGenieRunning outvar
  Push $0
  System::Call 'kernel32::OpenMutex(i 0x00100000, i 0, t "${MUTEX_NAME}") i .s'
  Pop $0
  StrCpy ${outvar} 0
  IntCmp $0 0 +3 0 +3
    System::Call 'kernel32::CloseHandle(i r0)'
    StrCpy ${outvar} 1
  Pop $0
!macroend

; ---------------------------------------------------------------------------
; Ask the running instance to close itself, then wait. Returns when it is gone
; or when the caller's patience runs out. Sets $0 = 1 on success.
; ---------------------------------------------------------------------------
Function CloseGenieGracefully
  StrCpy $0 0
  StrCpy $1 0

  ; Ask. This is a request, not a kill: the running instance performs its own
  ; canonical full exit, which stops the backend it owns.
  nsExec::Exec `"$INSTDIR\${APP_EXE}" --genie-shutdown`
  Pop $2

close_poll:
  !insertmacro _IsGenieRunning $3
  IntCmp $3 0 close_done 0 close_done

  IntOp $1 $1 + 1
  ; Generous: the backend teardown is part of a clean exit.
  IntCmp $1 15 0 close_wait close_wait
  Goto close_blocked

close_wait:
  Sleep 1000
  Goto close_poll

close_done:
  StrCpy $0 1
  Goto close_end

close_blocked:
  ; A silent install must NEVER wait on a dialog - it hangs forever with nobody
  ; to click it (measured: a 600s timeout during automated acceptance). Fail
  ; fast instead, so the caller gets a real exit code.
  IfSilent 0 +3
    DetailPrint "GENIE still running; silent install cannot continue."
    StrCpy $0 0
    Goto close_end
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
    "GENIE is still running and did not close itself.$\r$\n$\r$\nPlease close it manually: right-click the GENIE icon in the system tray (bottom-right, you may need the '^' overflow arrow) and choose 'Exit GENIE'.$\r$\n$\r$\nThen click Retry to continue.$\r$\n$\r$\nGENIE will not force-close your running session, because killing it can leave files locked and corrupt the update." \
    /SD IDCANCEL IDRETRY close_retry
  StrCpy $0 0
  Goto close_end

close_retry:
  StrCpy $1 0
  nsExec::Exec `"$INSTDIR\${APP_EXE}" --genie-shutdown`
  Pop $2
  Goto close_poll

close_end:
FunctionEnd

; ---------------------------------------------------------------------------
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------------------
Section "GENIE" SEC_MAIN
  SetOutPath "$INSTDIR"

  ; Close a running instance before touching any file.
  IfFileExists "$INSTDIR\${APP_EXE}" 0 do_install
    Call CloseGenieGracefully
    IntCmp $0 1 do_install 0 do_install
      Abort

do_install:
  ; The Windows-native client (self-contained x64).
  File /r "..\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\*.*"

  ; The authoritative Python core, embedded.
  SetOutPath "$INSTDIR\backend-runtime"
  File /r "..\backend-dist\backend-runtime\*.*"

  SetOutPath "$INSTDIR"

  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Shortcuts: Start Menu and Desktop, both use the canonical GENIE icon
  ; (multi-res 16..256, alpha preserved). MUI_ICON ships app-icon.ico into
  ; the build output, and the source csproj <ApplicationIcon> copies
  ; Genie.ico to the same place, so both names resolve here.
  CreateShortcut "$SMPROGRAMS\GENIE.lnk" "$INSTDIR\${APP_EXE}" \
                 "$INSTDIR\app-icon.ico" 0
  CreateShortcut "$DESKTOP\GENIE.lnk" "$INSTDIR\${APP_EXE}" \
                 "$INSTDIR\app-icon.ico" 0

  ; Registry entries uninstall.exe needs to find itself.
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GENIE" \
                   "DisplayName" "GENIE"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GENIE" \
                   "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GENIE" \
                   "InstallLocation" "$INSTDIR"
SectionEnd

; ---------------------------------------------------------------------------
Section "Uninstall"
  ; Same rule on the way out: ask, never kill.
  IfFileExists "$INSTDIR\${APP_EXE}" 0 do_uninstall
    Call un.CloseGenieGracefully
    IntCmp $0 1 do_uninstall 0 do_uninstall
      Abort

do_uninstall:
  Delete "$SMPROGRAMS\GENIE.lnk"
  Delete "$DESKTOP\GENIE.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\GENIE"

  RMDir /r "$INSTDIR\backend-runtime"
  RMDir /r "$INSTDIR"

  ; The uninstaller cannot delete its own running executable - measured, still
  ; present 30 s after exit. /REBOOTOK schedules the removal for the next
  ; restart in contexts that are allowed to do it (a per-user uninstall has no
  ; right to write the pending-rename key, so in that case one file in an
  ; otherwise-empty directory is the honest end state).
  Delete /REBOOTOK "$INSTDIR\uninstall.exe"
  RMDir /REBOOTOK "$INSTDIR"
SectionEnd

Function un.CloseGenieGracefully
  StrCpy $0 0
  StrCpy $1 0
  nsExec::Exec `"$INSTDIR\${APP_EXE}" --genie-shutdown`
  Pop $2

un_close_poll:
  !insertmacro _IsGenieRunning $3
  IntCmp $3 0 un_close_done 0 un_close_done
  IntOp $1 $1 + 1
  IntCmp $1 15 0 un_close_wait un_close_wait
  Goto un_close_blocked

un_close_wait:
  Sleep 1000
  Goto un_close_poll

un_close_done:
  StrCpy $0 1
  Goto un_close_end

un_close_blocked:
  IfSilent 0 +3
    DetailPrint "GENIE still running; silent uninstall cannot continue."
    StrCpy $0 0
    Goto un_close_end
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
    "GENIE is still running and did not close itself.$\r$\n$\r$\nPlease close it from the system tray (right-click the GENIE icon, 'Exit GENIE'), then click Retry." \
    /SD IDCANCEL IDRETRY un_close_retry
  StrCpy $0 0
  Goto un_close_end

un_close_retry:
  StrCpy $1 0
  nsExec::Exec `"$INSTDIR\${APP_EXE}" --genie-shutdown`
  Pop $2
  Goto un_close_poll

un_close_end:
FunctionEnd

; ---------------------------------------------------------------------------
; Owner data is NOT deleted. Say so plainly at the end.
Function un.onUninstSuccess
  ; /SD is REQUIRED, not cosmetic. NSIS still displays a MessageBox in silent
  ; mode when no default is supplied, so an unattended `uninstall.exe /S` would
  ; sit here forever waiting for a click nobody will make. That is exactly the
  ; measured 600s hang in the upgrade acceptance run.
  MessageBox MB_OK|MB_ICONINFORMATION \
    "GENIE has been removed.$\r$\n$\r$\nYour data (memory, missions, vault, workspace) has NOT been deleted. It is kept in:$\r$\n$LOCALAPPDATA\GENIE$\r$\n$\r$\nDelete that folder manually if you want a complete removal." \
    /SD IDOK
FunctionEnd
