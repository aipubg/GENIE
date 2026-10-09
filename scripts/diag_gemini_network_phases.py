from __future__ import annotations
import socket, ssl, subprocess, time, urllib.request, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; APP=(ROOT/"backend-dist"/"backend-runtime"/"app").resolve(); sys.path.insert(0,str(APP))
HOST="generativelanguage.googleapis.com"; PORT=443; URL="https://generativelanguage.googleapis.com/v1beta/models?pageSize=1"
def emit(k,v): print(f"PHASE_{k}={v}",flush=True)
def phase(name,fn):
    emit(name+"_START",True); t=time.monotonic()
    try:
        status, detail = fn(); emit(name+"_STATUS", status); emit(name+"_RESULT", detail)
    except Exception as e: emit(name+"_STATUS","ERROR"); emit(name+"_RESULT",type(e).__name__)
    finally: emit(name+"_ELAPSED_MS",round((time.monotonic()-t)*1000,1))
def dns(): return ("PASS","RESOLVED") if socket.getaddrinfo(HOST,PORT,type=socket.SOCK_STREAM) else ("FAIL","NO_ADDRESSES")
def tcp():
    with socket.create_connection((HOST,PORT),timeout=6): return "PASS","CONNECTED"
def tls():
    with socket.create_connection((HOST,PORT),timeout=6) as raw:
        raw.settimeout(6)
        with ssl.create_default_context().wrap_socket(raw,server_hostname=HOST) as s: return "PASS",s.version() or "TLS_CONNECTED"
def proxy(): return ("PASS", "CONFIGURED" if urllib.request.getproxies() else "NOT_CONFIGURED")
def urllib_probe():
    from models import provider_http
    r=provider_http.request(URL,method="GET",timeout=10); return ("PASS_HTTP_REACHABILITY",f"HTTP_{r['status']}") if r.get("status") else ("FAIL_TRANSPORT",r.get("error_code") or "NO_HTTP_RESPONSE")
def curl():
    p=subprocess.run(["curl.exe","--silent","--show-error","--output","NUL","--connect-timeout","6","--max-time","12","--write-out","http=%{http_code};tcp=%{time_connect};tls=%{time_appconnect};first_byte=%{time_starttransfer}",URL],capture_output=True,text=True,timeout=15,check=False)
    text=p.stdout.strip()[:200]; code=""; 
    for part in text.split(';'):
        if part.startswith('http='): code=part.split('=',1)[1]
    return ("PASS_HTTP_REACHABILITY" if code and code != "000" else "FAIL_TRANSPORT",f"exit={p.returncode};{text}")
def main():
    emit("DIAGNOSTIC_STARTED",True)
    for n,f in (("PROXY",proxy),("DNS",dns),("DIRECT_TCP",tcp),("DIRECT_TLS",tls),("URLLIB_HTTPS",urllib_probe),("CURL_HTTPS",curl)): phase(n,f)
    emit("DIAGNOSTIC_FINISHED",True)
if __name__=="__main__": main()
