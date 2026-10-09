from __future__ import annotations
import ctypes, platform
from ctypes import wintypes
def main():
    if platform.system()!="Windows": print("GUI_PREFLIGHT=NOT_WINDOWS"); return 2
    u=ctypes.WinDLL("user32",use_last_error=True); u.OpenInputDesktop.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; u.OpenInputDesktop.restype=wintypes.HANDLE; u.CloseDesktop.argtypes=[wintypes.HANDLE]
    h=u.OpenInputDesktop(0,False,1)
    if not h: print("INPUT_DESKTOP=UNAVAILABLE\nGUI_PREFLIGHT=BLOCKED_DESKTOP_ACCESS"); return 2
    try:
        print("INPUT_DESKTOP=ACCESSIBLE",flush=True)
        from pywinauto import Desktop
        windows=Desktop(backend="uia").windows(); genie=[w for w in windows if w.window_text()=="GENIE" and w.is_visible()]
        if not genie: print("GENIE_WINDOW=NOT_FOUND\nGUI_PREFLIGHT=BLOCKED_GENIE_NOT_RUNNING"); return 2
        edits=genie[0].descendants(control_type="Edit"); print("GENIE_WINDOW=FOUND\nGUI_PREFLIGHT="+("PASS_READONLY_UIA" if isinstance(edits,list) else "FAIL_UIA")); return 0
    except Exception as e: print("GUI_PREFLIGHT_ERROR_TYPE="+type(e).__name__); return 1
    finally: u.CloseDesktop(h)
if __name__=="__main__": raise SystemExit(main())
