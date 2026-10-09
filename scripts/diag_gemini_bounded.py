from __future__ import annotations
import subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/"backend-dist"/"backend-runtime"/"python"/"python.exe"
DIAGNOSTIC=ROOT/"scripts"/"diag_gemini_authenticated.py"
ALLOWED={"DIAG_STARTED","DIAG_PACKAGED_IDENTITY","DIAG_CONFIG_LOADED","DIAG_VAULT_READABLE","DIAG_GEMINI_CREDENTIAL_PRESENT","DIAG_ENDPOINT_VALID","DIAG_HTTP_REQUEST_STARTED","DIAG_HTTP_REQUEST_FINISHED","DIAG_HTTP_STATUS","DIAG_HTTP_ERROR_CODE","DIAG_HTTP_ELAPSED_MS","DIAG_AUTH_GET_STATUS","DIAG_AUTH_GET_ERROR","DIAG_AUTH_GET_ELAPSED_MS"}
def main():
    if not PYTHON.is_file(): print("BLOCKED=PACKAGED_PYTHON_MISSING"); return 2
    started=time.monotonic()
    try: result=subprocess.run([str(PYTHON),"-u",str(DIAGNOSTIC)],cwd=str(ROOT),capture_output=True,text=True,timeout=50,check=False)
    except subprocess.TimeoutExpired as exc:
        print("RESULT=PROCESS_HARD_TIMEOUT"); print("LIMIT_SECONDS=50")
        for line in (exc.stdout or "").splitlines():
            k,sep,v=line.partition("=")
            if sep and k in ALLOWED: print(f"{k}={v}")
        return 124
    for line in result.stdout.splitlines():
        k,sep,v=line.partition("=")
        if sep and k in ALLOWED: print(f"{k}={v}")
    print("EXIT_CODE=",result.returncode); print("PROCESS_ELAPSED_MS=",round((time.monotonic()-started)*1000,1))
    print("RESULT=DIAGNOSTIC_COMPLETED" if result.returncode==0 else "RESULT=DIAGNOSTIC_FAILED")
    return result.returncode
if __name__=="__main__": raise SystemExit(main())
