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
        windows=Desktop(backend="uia").windows(); genie=[w for w in windows if w.window_text().strip()=="GENIE" and w.is_visible()]
        if not genie: print("GENIE_WINDOW=NOT_FOUND\nGUI_PREFLIGHT=BLOCKED_GENIE_NOT_RUNNING"); return 2
        if len(genie)!=1: print("GENIE_WINDOW=AMBIGUOUS\nGUI_PREFLIGHT=BLOCKED_MULTIPLE_GENIE_WINDOWS"); return 2
        observed=genie[0]; print(f"GENIE_WINDOW=FOUND\nGENIE_HWND={observed.handle}")
        nav=Desktop(backend="uia").window(handle=observed.handle).child_window(auto_id="NavList",control_type="List")
        if not nav.exists(timeout=5): print("NAV_LIST=NOT_FOUND\nGUI_PREFLIGHT=FAIL_WPF_ACCESSIBILITY"); return 1
        print("NAV_LIST=FOUND\nGUI_PREFLIGHT=PASS_READONLY_UIA"); return 0
    except Exception as e: print("GUI_PREFLIGHT_ERROR_TYPE="+type(e).__name__); return 1
    finally: u.CloseDesktop(h)
if __name__=="__main__": raise SystemExit(main())
