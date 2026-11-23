"""Fail-closed MC ingress security regression smoke (never changes production).

Usage:
  python -m tools.mcel_security_ingress_smoke run
  python -m tools.mcel_security_ingress_smoke run --with-cookie

This is an isolated negative-probe suite, not a claim of completed production
security. It starts ephemeral servers only on 127.0.0.1, mocks side-effecting
operations, never touches a running MC process, and writes no user documents.
Each confirmed bypass is FAIL; an unexercised dependency is UNVERIFIED, never PASS.
The optional --with-cookie executes the installed Windows gateway suite once.
It does not run destructive lifecycle operations or alter Docker services.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import http.client
import io
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request


DENIED = {401, 403}


class Results:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, status: str, detail: str = "") -> None:
        if status not in {"PASS", "FAIL", "UNVERIFIED"}:
            raise ValueError(status)
        self.rows.append((name, status, detail))
        print(f"MC_SECURITY_INGRESS {name} {status}" + (f" detail={detail[:240]}" if detail else ""), flush=True)

    def test(self, name: str, value: bool, detail: str = "") -> None:
        self.add(name, "PASS" if value else "FAIL", detail)

    def run(self, name: str, fn) -> None:
        before = len(self.rows)
        try:
            fn()
            if len(self.rows) == before:
                self.add(name, "UNVERIFIED", "test returned no assertions")
        except Exception as exc:
            self.add(name, "UNVERIFIED", f"{type(exc).__name__}: {str(exc)[:180]}")

    def finish(self) -> int:
        counts = {s: sum(1 for _, status, _ in self.rows if status == s)
                  for s in ("PASS", "FAIL", "UNVERIFIED")}
        print("MC_SECURITY_INGRESS SUMMARY " + json.dumps({"status": "PASS" if counts["FAIL"] == counts["UNVERIFIED"] == 0 else "BLOCKED",
            "tests": len(self.rows), "counts": counts,
            "failed": [n for n,s,_ in self.rows if s == "FAIL"],
            "unverified": [n for n,s,_ in self.rows if s == "UNVERIFIED"],
            "production_modified": False}, sort_keys=True), flush=True)
        return 0 if counts["FAIL"] == counts["UNVERIFIED"] == 0 else 1


@contextlib.contextmanager
def fixture_server(handler, setup=None):
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    srv = Server(("127.0.0.1", 0), handler)
    if setup is not None:
        setup(srv)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def request(port: int, path: str, method="GET", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read(8192), dict(response.getheaders())
    finally:
        conn.close()


def viewport_http_tests(r: Results) -> None:
    """Runtime entry-point negative probes; never execute a real MC operation.

    Dispatch is replaced with a harmless 200 sentinel. If the request reaches
    dispatch, the security boundary failed. A guarded Viewport must issue
    401/403 *before* the sentinel runs. Import/bootstrap failures are not PASS.
    """
    from main_computer import viewport_server as vs
    for method, path in (("GET", "/api/applications/onlyoffice/file?path=fixture.xlsx"),
                         ("GET", "/api/local/authenticator/identities"),
                         ("POST", "/api/applications/browser/control"),
                         ("POST", "/api/applications/onlyoffice/callback?path=fixture.xlsx")):
        seen = []
        def sentinel(handler):
            seen.append((method, handler.path))
            handler.send_response(200)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
        with mock.patch.object(vs, "dispatch_get", sentinel), mock.patch.object(vs, "dispatch_post", sentinel):
            with fixture_server(vs.ViewportHandler, lambda srv: setattr(srv,"signal",lambda *a,**k:None)) as srv:
                try:
                    status,_,_=request(srv.server_port,path,method=method,
                                       body=b'{}' if method=="POST" else None,
                                       headers={"Content-Type":"application/json"} if method=="POST" else None)
                except (http.client.HTTPException, OSError) as exc:
                    r.add("viewport-http-deny:"+method+":"+path,"UNVERIFIED",f"connection failed: {type(exc).__name__}")
                    continue
                r.test("viewport-http-deny:"+method+":"+path,status in DENIED and not seen,
                       f"HTTP {status}; dispatch_invoked={bool(seen)}")


def heartbeat_tests(r: Results) -> None:
    from main_computer.heartbeat import HeartbeatHandler
    def setup(srv):
        srv.config = SimpleNamespace(actual_heartbeat_port=srv.server_port, server_port=8765)
        srv.signal = lambda *a, **k: None
        srv.request_shutdown = lambda **kwargs: (_ for _ in ()).throw(AssertionError("lifecycle invoked"))
    with fixture_server(HeartbeatHandler, setup) as srv:
        p = srv.server_port
        hdr = {"Host":f"127.0.0.1:{p}"}
        status, body, _ = request(p, "/api/heartbeat/authority/session", headers=hdr)
        r.test("heartbeat-uncredentialed-session-issuance-denied", status in DENIED,
               f"HTTP {status}; issuing an anonymous session would authorize control actions")
        status, _, _ = request(p, "/api/heartbeat/authority/session", headers={**hdr,"Origin":"https://evil.example"})
        r.test("heartbeat-cross-origin-session-denied", status in DENIED, f"HTTP {status}")
        status, _, _ = request(p, "/api/heartbeat/control", method="POST", body=b'{"action":"status"}',
                               headers={**hdr,"Content-Type":"application/json"})
        r.test("heartbeat-control-without-credential-denied",status in DENIED, f"HTTP {status}")
        # Never POST start/stop/restart: even a bad guard must not mutate a real process.


def main_log_tests(r: Results) -> None:
    from main_computer.main_log_service import MainLogRequestHandler
    class Store:
        def __init__(self): self.appended = []
        def recent(self, *, limit): return [{"private_marker":"DO_NOT_DISCLOSE"}]
        def append_many(self,events): self.appended.extend(events);return {"ok":True,"count":len(events)}
        root = Path("/private-test-workspace")
        log_path = root / "private.log"
        raw_log_path = root / "private.raw"
        surprise_path = root / "private.surprise"
        raw_mirror = False
    store=Store()
    def setup(srv):srv.store=store
    with fixture_server(MainLogRequestHandler,setup) as srv:
        p=srv.server_port
        status, data, _=request(p,"/v1/log/recent?limit=1")
        r.test("main-log-anonymous-read-denied",status in DENIED and b"DO_NOT_DISCLOSE" not in data,f"HTTP {status}")
        payload=json.dumps({"event":"mc-security-smoke", "synthetic":True}).encode()
        status, _, _=request(p,"/v1/log/events",method="POST",body=payload,headers={"Content-Type":"application/json"})
        r.test("main-log-anonymous-event-injection-denied",status in DENIED and not store.appended,f"HTTP {status}; synthetic in-memory log only")
        status, data, _=request(p,"/health")
        r.test("main-log-health-does-not-leak-private-paths",b"/private-test-workspace" not in data,
               f"HTTP {status}; a public health check should not reveal user paths")


def openclaw_tests(r: Results) -> None:
    from main_computer.openclaw_bridge import OpenClawBridgeHandler
    class Catalog:
        def list_projects(self):return []
    computer=SimpleNamespace(catalog=Catalog())
    cfg=SimpleNamespace(workspace=Path("/private-test-workspace"),patch_level="smoke",provider="fixture",model="fixture")
    def setup(srv):
        srv.config=cfg; srv.computer=computer; srv.token=None; srv.signal=lambda *args,**kwargs:None
    with fixture_server(OpenClawBridgeHandler,setup) as srv:
        status, _, _=request(srv.server_port,"/v1/projects")
        r.test("openclaw-no-token-configuration-must-deny",status in DENIED,
               f"HTTP {status}; no-token bridge must fail closed")
    def secured(srv):setup(srv);srv.token="fixture-test-token"
    with fixture_server(OpenClawBridgeHandler,secured) as srv:
        status, _, _=request(srv.server_port,"/v1/projects")
        r.test("openclaw-configured-token-rejects-anonymous",status in DENIED,f"HTTP {status}")
        status, _, _=request(srv.server_port,"/v1/projects",headers={"Authorization":"Bearer forged"})
        r.test("openclaw-configured-token-rejects-forgery",status in DENIED,f"HTTP {status}")


def browser_route_tests(r: Results) -> None:
    from main_computer.mcel_backend_browser_route import handle_browser_post, browser_request_authorized
    class Browser:
        calls=[]
        def call(self,op,**kwargs):self.calls.append((op,kwargs));return {"ok":True}
    backend=Browser()
    class Handler:
        client_address=("127.0.0.1",56123)
        path="/api/applications/browser/control"
        def __init__(self,origin):
            self.server=SimpleNamespace(server_port=8123,backend_browser=backend)
            self.headers={"Host":"127.0.0.1:8123","Origin":origin,"Sec-Fetch-Site":"same-origin",
                "Content-Type":"application/json","X-MCEL-Browser-Operation":"browser-control"}
            body=json.dumps({"operation":"close","session":"X"*24}).encode()
            self.headers["Content-Length"]=str(len(body))
            self.rfile=io.BytesIO(body);self.response=None
        def _send_json(self,data,status=HTTPStatus.OK):self.response=int(status)
    h=Handler("http://127.0.0.1:8123")
    handle_browser_post(h)
    r.test("browser-control-spoofed-local-origin-no-session-denied",h.response in DENIED and not backend.calls,
           f"HTTP {h.response}, commands_executed={len(backend.calls)}")
    prior=len(backend.calls)
    h=Handler("https://evil.example")
    handle_browser_post(h)
    r.test("browser-control-cross-origin-denied",h.response in DENIED and len(backend.calls)==prior,f"HTTP {h.response}")


def slh_seh_tests(r: Results) -> None:
    """Real SLH API with a temporary authority; no live organization touched."""
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.api import SLHAPI
    from main_computer.slh.organization_service import seh_identity_operation_allowed
    with tempfile.TemporaryDirectory(prefix="mcel-slh-security-smoke-") as root:
        api = SLHAPI(SLHAuthority(Path(root)))
        for method, path in (("GET", "/v1/admin/devices"),
                             ("GET", "/v1/admin/policies"),
                             ("GET", "/v1/admin/audit"),
                             ("POST", "/v1/local/organization/stop")):
            code, _ = api.call(method, path, {}, token="", loopback=False)
            r.test("slh-unauthorized:" + method + ":" + path, code in DENIED,
                   f"HTTP {code}; actual SLH API, disposable authority")
        code, _ = api.call("GET", "/v1/admin/devices", {}, token="forged", loopback=False)
        r.test("slh-forged-token-denied", code in DENIED, f"HTTP {code}")
    # SEH-specific admission allowlist is distinct from generic SLH auth.
    for method, path in (("POST", "/v1/local/organization/stop"),
                         ("GET", "/v1/bootstrap/status"),
                         ("GET", "/v1/admin/audit"),
                         ("POST", "/v1/admin/seh/deploy"),
                         ("GET", "/v1/org/identity?other=1")):
        r.test("seh-identity-allowlist:" + method + ":" + path,
               not seh_identity_operation_allowed(method, path),
               "SEH-only listener must reject this operation")


def onlyoffice_tests(r: Results) -> None:
    """Execute the unmodified callback method from source with all I/O mocked.

    The large Viewport imports many native MCEL assets; isolated AST extraction
    permits testing the actual callback implementation without launching MC.
    Malicious URLs are trapped before any network action. A mere 400 caused by
    a missing helper is NOT accepted as proof of authentication.
    """
    path=Path(__file__).resolve().parents[1]/"main_computer"/"viewport_routes_onlyoffice.py"
    source=ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
    cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=="ViewportOnlyOfficeRoutesMixin")
    callback=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="_handle_onlyoffice_callback")
    # Keep source location for tracebacks; don't rewrite handler behavior.
    snippet=ast.Module(body=[ast.ClassDef(name="CallbackUnderTest",bases=[],keywords=[],body=[callback],decorator_list=[])],type_ignores=[])
    ast.fix_missing_locations(snippet)
    calls=[]; writes=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b"fixture-xlsx-not-a-real-file"
    def trap_urlopen(req,**kwargs):calls.append(req.full_url);return Response()
    scope={"parse_qs":parse_qs,"urlsplit":urlsplit,"Request":Request,"urlopen":trap_urlopen,"HTTPStatus":HTTPStatus}
    exec(compile(snippet,str(path),"exec"),scope)
    Handler=scope["CallbackUnderTest"]
    def attempt(body):
        h=Handler()
        h.path="/api/applications/onlyoffice/callback?path=fixture.xlsx"
        h.headers={"Host":"127.0.0.1:8765"}
        h.server=SimpleNamespace(signal=lambda *a,**k:None)
        h._onlyoffice_safe_path=lambda name,**k:Path("fixture.xlsx")
        h._read_json=lambda:body
        h._onlyoffice_looks_like_xlsx=lambda data:True
        h._onlyoffice_atomic_write=lambda path,data:writes.append((path,len(data)))
        h._send_json=lambda data,status=HTTPStatus.OK:setattr(h,"response",int(status))
        h.response=None
        h._handle_onlyoffice_callback()
        return h.response
    status=attempt({"status":2,"url":"http://127.0.0.1:65432/private"})
    r.test("onlyoffice-anonymous-callback-denied-before-ssrf",status in DENIED and not calls and not writes,
           f"HTTP {status}; outbound_fetches={len(calls)} workbook_writes={len(writes)}")
    calls.clear();writes.clear()
    status=attempt({"status":1})
    r.test("onlyoffice-status-only-callback-requires-auth",status in DENIED and not calls and not writes,f"HTTP {status}")


def viewport_entry_tests(r: Results) -> None:
    """Assert the Viewport entry gate precedes dispatch on all HTTP verbs.

    This is an AST guardrail, not an adequate substitute for real gateway-to-
    backend functional acceptance: accordingly name the test as static.
    """
    path=Path(__file__).resolve().parents[1]/"main_computer"/"viewport_server.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="ViewportHandler")
    for verb, dispatch in (("GET","dispatch_get"),("POST","dispatch_post")):
        method=next((n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="do_"+verb),None)
        if method is None:
            r.add("viewport-entry-"+verb.lower()+"-gate-static","UNVERIFIED","HTTP handler missing or moved")
            continue
        # Two-step proxy test: there must be a guard before dispatch. A bare
        # dispatch call (the pre-security snapshot) is a definite FAILURE.
        direct=(len(method.body)==1 and isinstance(method.body[0],ast.Expr) and isinstance(method.body[0].value,ast.Call)
                and isinstance(method.body[0].value.func,ast.Name) and method.body[0].value.func.id==dispatch)
        if direct:
            r.add("viewport-entry-"+verb.lower()+"-gate-static","FAIL","unconditional dispatch without authentication")
        else:
            r.add("viewport-entry-"+verb.lower()+"-gate-static","PASS",
                  "nontrivial entry handling present; separate real HTTP denial test still mandatory")
    # Force all privileged family names into the test's route inventory.
    dispatch_path=Path(__file__).resolve().parents[1]/"main_computer"/"viewport_route_dispatch.py"
    strings={n.value for n in ast.walk(ast.parse(dispatch_path.read_text(encoding="utf-8")))
             if isinstance(n,ast.Constant) and isinstance(n.value,str)}
    required=["/api/applications/browser/control","/api/applications/onlyoffice/callback",
              "/api/applications/onlyoffice/file","/api/applications/slh/seh/deployment",
              "/api/applications/slh/seh/lifecycle","/api/local/authenticator/"]
    for route in required:
        present=route in strings or any(s.startswith(route) for s in strings)
        r.test("viewport-sensitive-route-inventory:"+route,present,
               "route inventory checked; if missing, update inventory before any cutover")


def compose_tests(r: Results) -> None:
    """Report untrusted network exposure from the checked-in Docker port map."""
    try:import yaml
    except ImportError:
        r.add("docker-compose-parse","UNVERIFIED","PyYAML missing: cannot inventory Docker port bindings")
        return
    root=Path(__file__).resolve().parents[1]
    files=sorted(root.glob("docker-compose*.yml"))
    if not files:r.add("docker-compose-inventory","UNVERIFIED","no Compose files")
    risky=[];ports=0
    for f in files:
        data=yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not isinstance(data,dict):continue
        for name, svc in (data.get("services") or {}).items():
            for spec in (svc or {}).get("ports") or []:
                ports+=1
                if isinstance(spec,dict):
                    host=str(spec.get("host_ip","") or "")
                else:
                    text=str(spec).strip().strip('"\'')
                    # Expand ${VAR:-default} using the actual environment when
                    # present, or the declared default. This is a SOURCE check;
                    # installed containers are still separately UNVERIFIED.
                    def subst(match):
                        key, _, fallback = match.group(1).partition(":-")
                        return os.environ.get(key, fallback)
                    expanded=re.sub(r"\$\{([^}]+)\}",subst,text)
                    host=expanded.split(":")[0] if expanded.count(":")>=2 else ""
                if host not in {"127.0.0.1","::1"}:
                    risky.append(f"{f.name}/{name}:{str(spec)[:80]}")
    r.test("docker-compose-ports-inventory-present",ports>0,f"port_mappings={ports}")
    if risky:
        r.add("docker-public-port-policy","UNVERIFIED",f"{len(risky)} bindings require explicit security policy; examples: {'; '.join(risky[:3])}")
    else:
        r.add("docker-public-port-policy","PASS","all inspected Compose host-port bindings restrict loopback")


def additional_independent_services(r: Results) -> None:
    """Do not silently mark SLH/SEH, proxy/WebSockets, or production as tested."""
    for name, why in (
        ("slh-seh-independent-listeners", "isolated SLH API passes separately; native ORG-SLH TLS/device-identity listener remains untested"),
        ("websocket-and-sse-authentication", "requires real upgraded connection and streaming authorization probes"),
        ("docker-runtime-published-port-audit", "Compose source does not prove actual running Docker network bindings"),
        ("cross-windows-account-alice-bob", "requires separate Windows security principals"),
        ("full-mc-start-stop-reboot-persistence", "requires real production lifecycle acceptance"),
        ("onlyoffice-valid-signed-callback-and-url-allowlist", "requires an authorized document-scoped callback fixture after implementation"),
        ("gateway-to-backend-capability-forgery", "requires production gateway/backend joint startup"),
    ):
        r.add(name,"UNVERIFIED",why)


def run_cookie(r: Results) -> None:
    if os.name != "nt":
        r.add("windows-cookie-smoke","UNVERIFIED","requires Windows CurrentUser certificate store")
        return
    call=[sys.executable,"-m","tools.mcel_cookie_gateway_windows_smoke","run"]
    completed=subprocess.run(call,timeout=180,check=False)
    r.test("windows-cookie-smoke",completed.returncode==0,f"exit={completed.returncode}")


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",nargs="?",default="run",choices=["run"])
    parser.add_argument("--hostname-only",action="store_true",help="Run only the new .local hostname, shared-session, side-by-side and ONLYOFFICE contract")
    parser.add_argument("--with-host-browser",action="store_true",help="Run a real isolated Chromium DNS/namespace smoke (never system DNS)")
    parser.add_argument("--with-host-docker-runtime",action="store_true",help="Read-only check of running shared ONLYOFFICE container")
    parser.add_argument("--with-cookie",action="store_true",help="Also run the Windows 32-test certificate/cookie suite")
    parser.add_argument("--docker-onlyoffice",action="store_true",help="Run disposable ONLYOFFICE Docker exploit/correction lab (requires Docker)")
    parser.add_argument("--docker-real-onlyoffice",action="store_true",help="Run genuine isolated ONLYOFFICE browser edit/save/reopen test with a disposable .local HTTPS gateway (Docker + Playwright)")
    parser.add_argument("--docker-correct-all",action="store_true",help="Autonomously run isolated Docker baseline/candidate security probes for all five remaining vulnerable components AND ONLYOFFICE")
    args=parser.parse_args(argv)
    results=Results()
    from tools.mcel_security_hostname_smoke import run as hostname_contract
    results.run("hostname-session-contract", lambda: hostname_contract(results, with_browser=args.with_host_browser, with_docker_runtime=args.with_host_docker_runtime))
    if args.hostname_only:
        return results.finish()
    tests=[("viewport-entry",viewport_entry_tests),("viewport-http",viewport_http_tests),("heartbeat",heartbeat_tests),("main-log",main_log_tests),
           ("openclaw",openclaw_tests),("browser",browser_route_tests),("slh-seh",slh_seh_tests),
           ("onlyoffice",onlyoffice_tests),("docker",compose_tests)]
    for name,fn in tests:results.run(name,lambda fn=fn: fn(results))
    additional_independent_services(results)
    if args.with_cookie:results.run("cookie",lambda:run_cookie(results))
    if args.docker_real_onlyoffice:
        from tools.mcel_onlyoffice_real_editor_lab import experiment as real_editor_experiment
        def real_onlyoffice_lab():
            rc=real_editor_experiment(os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG','latest'))
            results.add('onlyoffice-real-editor-save-reopen-docker',
                        'PASS' if rc==0 else ('FAIL' if rc==1 else 'UNVERIFIED'),
                        f'lab-exit={rc}; disposable real editor session only, production unchanged; browser fixture TLS trust tested separately')
        results.run('onlyoffice-real-editor',real_onlyoffice_lab)
    if args.docker_onlyoffice or args.docker_correct_all:
        from tools.mcel_onlyoffice_docker_lab import experiment
        def docker_lab():
            rc = experiment(os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG','latest'))
            results.add('onlyoffice-disposable-docker-correction',
                        'PASS' if rc == 0 else 'UNVERIFIED',f'lab-exit={rc}; production callback remains unchanged')
        results.run('onlyoffice-docker',docker_lab)
    if args.docker_correct_all:
        from tools.mcel_security_correction_lab import experiment as correction_experiment
        def correction_lab():
            rc = correction_experiment(mode='docker')
            results.add('five-component-disposable-docker-corrections',
                        'PASS' if rc==0 else 'UNVERIFIED',
                        f'lab-exit={rc}; isolated candidate only; production still has the baseline failures')
            results.add('one-mc-session-all-ingress-candidates',
                        'PASS' if rc==0 else 'UNVERIFIED',
                        f'lab-exit={rc}; one login-session authority across Viewport, Heartbeat, Main Log, OpenClaw, Browser control, ONLYOFFICE status callback; separate Windows/TLS and native Document Server callbacks not implied')
        results.run('five-component-docker',correction_lab)
    return results.finish()


if __name__=="__main__":
    raise SystemExit(main())
