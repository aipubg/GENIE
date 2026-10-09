# Capture every owner-facing surface so the UI can be reviewed as rendered,
# not as source. Reading XAML cannot show clipping, spacing or wrapping.
param(
    [string]$Exe = (Join-Path $PSScriptRoot "..\ui\windows\Genie.Desktop\bin\Release\net8.0-windows\win-x64\Genie.Desktop.exe"),
    [string]$Out = (Join-Path $PSScriptRoot "..\artifacts\ui"),
    [string[]]$Surfaces = @("Home","Chat","Missions","Agents","Computer","Skills",
                            "Devices","Memory","Forecast","Security","Competition",
                            "Experience","Knowledge","Media","Settings")
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
# AppActivate lives in Microsoft.VisualBasic, which is not auto-loaded in
# PowerShell Core on some hosts - load it explicitly so the focus call works.
try { Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop } catch {}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@

New-Item -ItemType Directory -Force -Path $Out | Out-Null

# Kill any prior GENIE so we capture a fresh run with our current binary.
Get-Process -Name "Genie.Desktop" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$p = Start-Process -FilePath $Exe -PassThru
Start-Sleep -Seconds 30     # backend + first render

$proc = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
if (-not $proc -or -not $proc.MainWindowHandle) {
    Write-Output "NO_WINDOW"
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

$h = $proc.MainWindowHandle
[Win]::ShowWindow($h, 9) | Out-Null   # SW_RESTORE
[Win]::ShowWindow($h, 3) | Out-Null   # SW_MAXIMIZE
# AppActivate (via the shell) is the only reliable cross-process way to steal
# focus from a browser. Without it SendKeys goes to whichever window is on top.
[Microsoft.VisualBasic.Interaction]::AppActivate($p.Id) | Out-Null
Start-Sleep -Seconds 3

for ($i = 0; $i -lt $Surfaces.Count; $i++) {
    $name = $Surfaces[$i]
    Start-Sleep -Milliseconds 1200

    $r = New-Object Win+RECT
    [Win]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.R - $r.L
    $ht = $r.B - $r.T
    if ($w -le 0 -or $ht -le 0) { Write-Output "BAD_RECT $name"; continue }

    $bmp = New-Object System.Drawing.Bitmap($w, $ht)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    # PW_RENDERFULLCONTENT (2) is required for WPF: it asks DWM to render
    # the window even when it is not foreground, so we no longer capture
    # whatever browser is on top.
    $hdc = $g.GetHdc()
    [Win]::PrintWindow($h, $hdc, 2) | Out-Null
    $g.ReleaseHdc($hdc)
    $file = Join-Path $Out ("{0:D2}_{1}.png" -f $i, $name)
    $bmp.Save($file, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Write-Output "captured $file"

    if ($i -lt ($Surfaces.Count - 1)) {
        [System.Windows.Forms.SendKeys]::SendWait("{DOWN}")
    }
}

Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
# Give Windows a moment to release the exe handle - otherwise a concurrent
# smoke test can see the file as still locked and report a 4/6 instead of 6/6.
Start-Sleep -Seconds 6
Write-Output "DONE"
