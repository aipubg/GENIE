; ===========================================================================
; SUPERSEDED for the Windows-native build - retained as reference.
;
; This is the electron-builder hook variant (it relies on electron-builder's
; customCheckAppRunning macro and ${APP_EXECUTABLE_FILENAME}). The native
; installer implements the same rule natively in genie_native.nsi:
;   * ask GENIE to close itself, wait, never force-kill
;   * if it refuses, tell the owner what to do and offer Retry
;
; Moved out of ui/electron when Electron was retired so the generic logic is
; not lost with the shell it happened to live in.
; ===========================================================================

; GENIE — graceful application shutdown for install / uninstall / upgrade.
;
; WHY THIS FILE EXISTS
; --------------------
; The stock electron-builder macro (_CHECK_APP_RUNNING) reaches for
; `taskkill /f` almost immediately. Force-killing an Electron app kills the
; browser process but can orphan renderer / GPU / utility child processes that
; still hold open handles to files inside the install directory. That is exactly
; what produced the two messages the owner saw:
;
;     "GENIE cannot be closed. Please close it manually and click Retry."
;     "Failed to uninstall old application files ... :2"
;
; Instead of killing anything, GENIE asks the already-running instance to close
; itself. `GENIE.exe --genie-shutdown` is relayed through the single-instance
; lock to the running GENIE, which then performs its own canonical full exit:
; close every window, destroy the tray, release handles, exit the process.
;
; HARD RULES
; ----------
;   * Ask first. Wait for a clean exit. Never force-kill by default.
;   * If GENIE genuinely refuses to close, tell the owner exactly what to do
;     (which action to take) and let them Retry — do not silently kill.
;
; This macro is defined in the common installer header, so it is used by BOTH
; the installer and the uninstaller (no `un.` prefix needed — it is inline).

!macro customCheckAppRunning

  StrCpy $R2 0

  IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0 genie_wait
    DetailPrint "Asking the running ${PRODUCT_NAME} to close itself (graceful request)..."
    nsExec::Exec `"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --genie-shutdown`
    Pop $R0
    StrCpy $R2 1

genie_wait:
  StrCpy $R1 0

genie_poll:
  IntOp $R1 $R1 + 1
  !insertmacro FIND_PROCESS "${APP_EXECUTABLE_FILENAME}" $R0
  ${If} $R0 != 0
    DetailPrint "${PRODUCT_NAME} has closed."
    Goto genie_done
  ${EndIf}

  ; We issued a real shutdown request -> give GENIE a generous window to exit
  ; cleanly (12 s). We could not find an executable to ask -> fail fast (3 s)
  ; rather than stalling the owner on a request that was never made.
  ${If} $R2 == 1
    ${If} $R1 >= 12
      Goto genie_blocked
    ${EndIf}
  ${Else}
    ${If} $R1 >= 3
      Goto genie_blocked
    ${EndIf}
  ${EndIf}

  Sleep 1000
  Goto genie_poll

genie_blocked:
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
    "${PRODUCT_NAME} is still running and did not close itself.$\r$\n$\r$\nPlease close it manually: right-click the GENIE icon in the system tray (bottom-right, you may need the '^' overflow arrow) and choose 'Exit GENIE'.$\r$\n$\r$\nThen click Retry to continue.$\r$\n$\r$\nGENIE will not force-close your running session, because killing it can leave files locked and corrupt the update." \
    /SD IDCANCEL IDRETRY genie_retry
  Quit

genie_retry:
  IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0 genie_wait
    nsExec::Exec `"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --genie-shutdown`
    Pop $R0
    StrCpy $R2 1
  Goto genie_wait

genie_done:

!macroend
