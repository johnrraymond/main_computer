"""Credentialless Bob adversarial HTTP sweep: ONLY disposable correction targets.

This file is mounted into a distinct Docker container with no target source
mounts, no MC authentication secrets, no host ports and no production network.
Its tests apply to isolated selected-handler fixtures, NOT live MC, Windows
account isolation, TLS, Docker daemon privileges or WebSocket coverage.

Run via: python -m tools.mcel_security_correction_lab run
Local test: python -m tools.mcel_security_correction_lab run --mode local
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
from dataclasses import dataclass

BASE = int(os.environ.get("MC_BOB_PORT_BASE", "32410"))


@dataclass(frozen=True)
class Surface:
    name: str
    offset: int
    verb: str
    path: str
    body: str | None = None


# Every endpoint here has a real privileged handler or a protected fixture
# dispatch in the source-backed correction target. No nonexistent endpoint is
# counted as proof of authorization.
SURFACES = (
    Surface("viewport-read", 1, "GET", "/api/local/authenticator/identities"),
    Surface("viewport-file", 1, "GET", "/api/applications/onlyoffice/file?path=fixture.xlsx"),
    Surface("viewport-control", 1, "POST", "/api/applications/browser/control", '{}'),
    Surface("viewport-callback", 1, "POST", "/api/applications/onlyoffice/callback?path=fixture.xlsx", '{}'),
    Surface("heartbeat-authority", 3, "GET", "/api/heartbeat/authority/session"),
    Surface("heartbeat-control", 3, "POST", "/api/heartbeat/control", '{"action":"status"}'),
    Surface("heartbeat-private-status", 3, "GET", "/api/heartbeat/status"),
    Surface("heartbeat-private-health", 3, "GET", "/healthz"),
    Surface("heartbeat-control-get", 3, "GET", "/api/heartbeat/control"),
    Surface("mainlog-read", 5, "GET", "/v1/log/recent?limit=1"),
    Surface("mainlog-write", 5, "POST", "/v1/log/events", '{"events":[{"message":"attacker"}]}'),
    Surface("mainlog-health", 5, "GET", "/health"),
    Surface("openclaw-no-token", 7, "GET", "/v1/projects"),
    Surface("openclaw-auth-read", 8, "GET", "/v1/projects"),
    Surface("openclaw-auth-chat", 8, "POST", "/v1/chat", '{"prompt":"attacker"}'),
    Surface("browser-control", 10, "POST", "/api/applications/browser/control", '{"operation":"close","session":"XXXXXXXXXXXXXXXXXXXXXXXX"}'),

    Surface("direct-onlyoffice-status", 12, "POST", "/api/applications/onlyoffice/callback?path=fixture.xlsx", '{"status":1}'),
    Surface("session-viewport", 11, "GET", "/mc/viewport/private"),
    Surface("session-heartbeat", 11, "GET", "/mc/heartbeat/status"),
    Surface("session-mainlog", 11, "GET", "/mc/mainlog/recent"),
    Surface("session-openclaw", 11, "GET", "/mc/openclaw/projects"),
    Surface("session-browser", 11, "POST", "/mc/browser/control", '{"operation":"close","session":"XXXXXXXXXXXXXXXXXXXXXXXX"}'),
    Surface("session-onlyoffice", 11, "POST", "/mc/onlyoffice/callback", '{"status":1}'),
    Surface("session-issuance-check", 11, "GET", "/auth/check"),
    Surface("session-login-forgery", 11, "POST", "/auth/login", "wrong-key"),)


def header_variants(port: int):
    # Bob cannot know Alice's password, cookie or service capabilities. These
    # test identity-spoofing and proxy/route normalization tricks without one.
    origin = f"http://127.0.0.1:{port}"
    return (
        ("plain", {}),
        ("forged-gateway", {"X-MC-Gateway-Capability":"forged"}),
        ("forged-service", {"X-MC-Internal-Authority":"forged"}),
        ("forged-bearer", {"Authorization":"Bearer forged"}),
        ("forged-all", {"X-MC-Gateway-Capability":"forged", "X-MC-Internal-Authority":"forged", "Authorization":"Bearer forged"}),
        ("fake-mc-cookie", {"Cookie":"__Host-MC-Session=forged; MC-Session=forged"}),
        ("spoof-origin", {"Origin":origin, "Referer":origin+"/", "Sec-Fetch-Site":"same-origin"}),
        ("spoof-local-ip", {"X-Forwarded-For":"127.0.0.1", "X-Real-IP":"127.0.0.1", "Forwarded":"for=127.0.0.1;proto=https"}),
        ("spoof-proxy", {"X-Forwarded-Host":f"mc.test:{port}", "X-Forwarded-Proto":"https", "X-Forwarded-Port":str(port)}),
        ("spoof-host", {"Host":f"mc.test:{port}", "Origin":f"https://mc.test:{port}"}),
        ("host-localhost", {"Host":f"localhost:{port}", "Origin":f"http://localhost:{port}"}),
        ("host-hex", {"Host":f"0x7f000001:{port}"}),
        ("host-ipv6", {"Host":f"[::1]:{port}"}),
        ("spoof-middleware", {"X-Original-URL":"/api/local/authenticator/identities", "X-Rewrite-URL":"/api/heartbeat/authority/session"}),
        ("verb-override", {"X-HTTP-Method-Override":"GET", "X-Method-Override":"GET"}),
        ("all-spoofs", {"Origin":origin, "Sec-Fetch-Site":"same-origin", "X-Forwarded-For":"127.0.0.1", "X-MC-Gateway-Capability":"forged", "X-MC-Internal-Authority":"forged", "Authorization":"Bearer forged", "Cookie":"MC-Session=forged"}),
    )


def req(port: int, verb: str, path: str, body: str | None = None, headers: dict | None = None,
        duplicate: str | None = None) -> tuple[int, bytes, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.putrequest(verb, path, skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        for key, value in (headers or {}).items():
            if key.lower() == "host":
                # Test a single forged Host value rather than adding two.
                continue
            conn.putheader(key, value)
        if headers and "Host" in headers:
            # HTTP parser must refuse duplicate Host ambiguity.
            conn.putheader("Host", headers["Host"])
        if duplicate:
            conn.putheader(duplicate, "forged-one")
            conn.putheader(duplicate, "forged-two")
        if body is not None:
            raw = body.encode("utf-8")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(raw)))
        else:
            raw = None
        conn.endheaders(raw)
        response = conn.getresponse()
        payload = response.read(32768)
        return response.status, payload, {k.lower():v for k,v in response.getheaders()}
    finally:
        conn.close()


def denied(status: int, payload: bytes, headers: dict, *, verb: str) -> bool:
    # An unsupported method's 405/501 is safe, but a 500 is not a PASS.
    # A 2xx/3xx is a direct fail on the protected endpoints. 401/403/404
    # mean this attempt could not access the operation in this fixture.
    allowed = {401, 403, 404, 405, 501}
    if verb == "OPTIONS" and status == 204 and payload == b"" and "set-cookie" not in headers:
        # Uncredentialed CORS preflight carries no session or private payload.
        # A separate hostile-Origin preflight test below checks origin policy.
        return True
    if status not in allowed:
        return False
    if b"PRIVATE-LOG-SENTINEL" in payload or b"PRIVATE-LOG" in payload:
        return False
    if "set-cookie" in headers:
        return False
    return True


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", default="run", choices=["run"])
    args=parser.parse_args()
    if os.environ.get("MC_CORRECTION_CAPABILITY"):
        print("MC_BOB_REDTEAM FAIL bob-process-has-service-secret", flush=True)
        return 2
    if os.path.exists("/lab/candidate") or os.path.exists("/lab/baseline"):
        print("MC_BOB_REDTEAM FAIL bob-process-has-candidate-source-mount", flush=True)
        return 2
    failures = []
    count = 0
    groups: dict[str, tuple[int,int]] = {}
    for surface in SURFACES:
        n=0; bad=0
        port=BASE+surface.offset
        attempts = [(f"headers:{name}", surface.verb, surface.path, surface.body, headers, None)
                    for name, headers in header_variants(port)]
        # Alternate verbs and methods tunneled through two common headers.
        for verb in ("HEAD", "OPTIONS", "PUT", "PATCH", "DELETE", "TRACE", "get", "PoSt"):
            attempts.append(("verb:"+verb, verb, surface.path, surface.body, {}, None))
        for key in ("X-MC-Gateway-Capability", "X-MC-Internal-Authority", "Authorization"):
            attempts.append(("duplicate:"+key, surface.verb, surface.path, surface.body, {}, key))
        for path in (surface.path+"&_method=GET" if "?" in surface.path else surface.path+"?_method=GET",
                     surface.path+"#fragment"):
            attempts.append(("path-variation", surface.verb, path, surface.body, {}, None))
        # Cross-site preflight is allowed to have 204 status but must not
        # authorize a hostile origin or set a browser credential.
        attempts.append(("cross-origin-options", "OPTIONS", surface.path, None,
                         {"Origin":"https://evil.example", "Access-Control-Request-Method":"POST",
                          "Access-Control-Request-Headers":"Authorization,X-MC-Internal-Authority"}, None))
        for label,verb,path,body,headers,duplicate in attempts:
            count += 1; n += 1
            try:
                status, data, returned = req(port,verb,path,body,headers,duplicate)
                is_denied = denied(status,data,returned,verb=verb)
                if label == "cross-origin-options":
                    is_denied = is_denied and returned.get("access-control-allow-origin", "") != "https://evil.example" \
                            and returned.get("access-control-allow-credentials", "").lower() != "true"
                detail=f"HTTP {status}"
            except (OSError,http.client.HTTPException,ValueError) as exc:
                is_denied = False
                detail=f"ERROR {type(exc).__name__}: {str(exc)[:120]}"
            if not is_denied:
                bad += 1
                failures.append((surface.name,label,detail))
                if len(failures) <= 12:
                    print(f"MC_BOB_REDTEAM BYPASS_OR_ERROR surface={surface.name} vector={label} {detail}",flush=True)
        groups[surface.name] = (n,bad)
        print(f"MC_BOB_REDTEAM {surface.name} {'PASS' if bad==0 else 'FAIL'} probes={n} blocked={n-bad}",flush=True)
    print("MC_BOB_REDTEAM SUMMARY "+json.dumps({"status":"PASS" if not failures else "FAIL",
        "attempts":count,"failed":len(failures),"failed_vectors":failures[:25],
        "groups":{k:{"attempts":v[0],"failed":v[1]} for k,v in groups.items()},
        "scope":"isolated selected handler methods; no Windows user, TLS, WS, Docker privileges or production integration",
        "bob_secret_provided":False,"production_modified":False},sort_keys=True),flush=True)
    return 1 if failures else 0


if __name__=="__main__":
    raise SystemExit(main())
