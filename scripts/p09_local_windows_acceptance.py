from __future__ import annotations
import argparse, os, platform, re, subprocess, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; PYTHON=ROOT/"backend-dist/backend-runtime/python/python.exe"; PREVIEW=ROOT/"ui/windows/Genie.Desktop/bin/Release/net8.0-windows/win-x64/Genie.Desktop.exe"; BROWSER_TEST=ROOT/"scripts/p1_browser_acceptance.py"; PROBE=ROOT/"scripts/p10_interactive_probe.py"
def out(k,v): print(f"{k}={v}",flush=True)
def fixture():
    if not PYTHON.is_file(): out("RESULT","BLOCKED_PACKAGED_PYTHON_MISSING"); return 2
    with tempfile.TemporaryDirectory(prefix="genie-p10-fixture-") as d:
        env=os.environ.copy(); env["GENIE_DATA_DIR"]=d; env["GENIE_ACCEPTANCE_HEADLESS"]="1"; started=time.monotonic()
        try: r=subprocess.run([str(PYTHON),"-u",str(BROWSER_TEST)],cwd=str(ROOT),env=env,text=True,capture_output=True,timeout=180,check=False)
        except subprocess.TimeoutExpired: out("RESULT","FAIL_FIXTURE_TIMEOUT"); return 124
        m=re.search(r"Browser/upload acceptance:\s*(\d+)/(\d+)\s+passed",r.stdout); out("FIXTURE_EXIT_CODE",r.returncode); out("FIXTURE_ELAPSED_SECONDS",round(time.monotonic()-started,2))
        if not m: out("RESULT","FAIL_FIXTURE_NO_ASSERTION_SUMMARY"); return 1
        passed,total=map(int,m.groups()); out("FIXTURE_ASSERTIONS_PASSED",passed); out("FIXTURE_ASSERTIONS_TOTAL",total); ok=r.returncode==0 and total>0 and passed==total; out("RESULT","PASS_FIXTURE_EXECUTED" if ok else "FAIL_FIXTURE"); return 0 if ok else 1
def interactive(attach=False, launch=False, test_chat=False):
    if not PREVIEW.is_file() or not PROBE.is_file(): out("RESULT","BLOCKED_INTERACTIVE_PROBE_MISSING"); return 2
    created=None
    if launch:
        if os.path.exists("E:\\G3\\GENIE\\artifacts\\p09-launch.lock"): out("RESULT","BLOCKED_FOREIGN_BACKEND"); return 2
        created=subprocess.Popen([str(PREVIEW)],cwd=str(PREVIEW.parent))
        time.sleep(5)
    try: r=subprocess.run([sys.executable,"-u",str(PROBE)],cwd=str(ROOT),capture_output=True,text=True,timeout=45,check=False)
    except subprocess.TimeoutExpired: out("RESULT","FAIL_INTERACTIVE_PROBE_TIMEOUT"); return 124
    observed_hwnd=None
    for line in r.stdout.splitlines():
        if line.startswith(("INPUT_DESKTOP=","GENIE_WINDOW=","GUI_PREFLIGHT=")): print(line,flush=True)
        if line.startswith("GENIE_HWND="):
            try: observed_hwnd=int(line.split("=",1)[1])
            except ValueError: observed_hwnd=None
    if r.returncode==0 and test_chat and observed_hwnd:
        chat=subprocess.run([sys.executable,"-u",str(ROOT/"scripts/p1_chat_gui_test.py"),"--attach-hwnd",str(observed_hwnd),"--expect","GENIE_GUI_OK","--no-external-messages"],cwd=str(ROOT),text=True,capture_output=True,timeout=180,check=False)
        out("CHAT_TEST_EXIT_CODE",chat.returncode); out("RESULT", "PASS_REAL_WPF_CHAT" if chat.returncode==0 else "FAIL_WPF_CHAT")
        if created: created.terminate()
        return chat.returncode
    out("RESULT","PASS_INTERACTIVE_PREFLIGHT" if r.returncode==0 else "BLOCKED_INTERACTIVE_GUI_PROBE")
    if created and not test_chat: created.terminate()
    return 0 if r.returncode==0 else 2
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=("fixture","interactive"),default="fixture"); ap.add_argument("--attach-existing",action="store_true"); ap.add_argument("--launch-if-absent",action="store_true"); ap.add_argument("--test-chat",action="store_true"); ap.add_argument("--no-external-messages",action="store_true"); a=ap.parse_args(); out("OS",platform.system())
    if platform.system()!="Windows": out("RESULT","BLOCKED_NOT_WINDOWS"); return 2
    if not a.no_external_messages: out("RESULT","BLOCKED_EXTERNAL_MESSAGE_SAFETY_FLAG_REQUIRED"); return 2
    out("EXTERNAL_MESSAGES","DISABLED"); return fixture() if a.mode=="fixture" else interactive(a.attach_existing,a.launch_if_absent,a.test_chat)
if __name__=="__main__": raise SystemExit(main())
