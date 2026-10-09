' GENIE Preview — owner-preview launch path.
'
' The frozen rc26 install is never touched. This creates a clearly named
' "GENIE Preview" shortcut that launches EXACTLY the latest source build, so the
' owner can never again mistake a stale installed binary for current source.
'
' Target: E:\G3\GENIE\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe
' Icon:   the real GENIE icon.
' No console: the target is a Windows (GUI) subsystem executable.

Option Explicit

Dim ws, exe, ico, desktop, startMenu, lnkPath, sc

Set ws = CreateObject("WScript.Shell")

Dim fso, root
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
exe = fso.BuildPath(root, "ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe")
ico = fso.BuildPath(root, "assets\Genie.ico")

If Not fso.FileExists(exe) Then
    WScript.Echo "MISSING EXE: " & exe
    WScript.Quit 1
End If
If Not fso.FileExists(ico) Then
    WScript.Echo "MISSING ICON: " & ico
    WScript.Quit 1
End If

desktop   = ws.SpecialFolders("Desktop")
startMenu = ws.SpecialFolders("StartMenu")

' Desktop shortcut.
lnkPath = desktop & "\GENIE Preview.lnk"
Set sc = ws.CreateShortcut(lnkPath)
sc.TargetPath       = exe
sc.WorkingDirectory = fso.GetParentFolderName(exe)
sc.IconLocation     = ico & ", 0"
sc.Description      = "GENIE Preview — latest source build (not the frozen rc26 install)"
sc.WindowStyle      = 1
sc.Save
WScript.Echo "DESKTOP: " & lnkPath

' Start Menu shortcut so it is reachable without the Desktop.
lnkPath = startMenu & "\Programs\GENIE Preview.lnk"
Set sc = ws.CreateShortcut(lnkPath)
sc.TargetPath       = exe
sc.WorkingDirectory = fso.GetParentFolderName(exe)
sc.IconLocation     = ico & ", 0"
sc.Description      = "GENIE Preview — latest source build (not the frozen rc26 install)"
sc.WindowStyle      = 1
sc.Save
WScript.Echo "STARTMENU: " & lnkPath

WScript.Echo "OK"
