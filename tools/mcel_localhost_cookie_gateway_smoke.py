"""Isolated real-browser smoke for MC's proposed HTTPS cookie gateway.

No MC services are started, stopped, patched, or accessed. The fixture starts:
  * a TLS gateway holding a revocable persistent cookie;
  * an HTTP-only private backend rejecting direct requests;
  * a hostile HTTP listener on the SAME hostname but a DIFFERENT port;
  * a hostile HTTPS listener with a certificate not trusted by the browser.

The browser uses an ephemeral, explicitly SPKI-pinned fixture certificate; it
never disables certificate validation globally or edits the Windows trust store.
The browser resolves the candidate host to 127.0.0.1 internally. This is only a
smoke-test mapping, NOT a production DNS/hosts configuration.

Run from the repository root:
    python -m tools.mcel_localhost_cookie_gateway_smoke run
Optional:
    python -m tools.mcel_localhost_cookie_gateway_smoke run --server-only
    python -m tools.mcel_localhost_cookie_gateway_smoke run --host mc.test
    python -m tools.mcel_localhost_cookie_gateway_smoke run --host mc.localhost

Requires: cryptography, playwright and Chromium (managed or system installed).
The JSON summary contains NO secrets. Results are for the tested browser build.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import secrets
import shutil
import ssl
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COOKIE_NAME = "__Host-MC-Smoke-Session"
CONTROL_COOKIE = "MC-Smoke-Control"  # intentionally public, non-authentication positive control
COOKIE_AGE = 180 * 24 * 60 * 60


def certificate(folder: Path, hostname: str, stem: str) -> tuple[Path, Path, str]:
    """Generate a throwaway self-signed leaf and Chromium SPKI pin."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(private, hashes.SHA256())
    )
    cert_path, key_path = folder / f"{stem}.pem", folder / f"{stem}.key"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    if os.name != "nt":
        key_path.chmod(0o600)
    spki = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pin = base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")
    return cert_path, key_path, pin


class ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


@contextmanager
def running_server(handler, *, cert: Path | None = None, key: Path | None = None):
    server = ReusableServer(("127.0.0.1", 0), handler)
    if cert is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def respond(self, status: int, data: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        extra = headers or {}
        self.send_header("Content-Type", extra.get("Content-Type", "text/plain; charset=utf-8"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra.items():
            if name.lower() != "content-type":
                self.send_header(name, value)
        self.end_headers()
        if data:
            self.wfile.write(data)


class Sessions:
    def __init__(self, file: Path):
        self.file = file
        self.lock = threading.RLock()
        if file.exists():
            self.data = json.loads(file.read_text(encoding="utf-8"))
        else:
            self.data = {}

    def _save(self):
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data), encoding="utf-8")
        if os.name != "nt":
            temp.chmod(0o600)
        os.replace(temp, self.file)

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.data[hashlib.sha256(token.encode()).hexdigest()] = int(time.time()) + COOKIE_AGE
            self._save()
        return token

    def valid(self, token: str) -> bool:
        with self.lock:
            return int(self.data.get(hashlib.sha256(token.encode()).hexdigest(), 0)) > time.time()

    def revoke(self, token: str) -> None:
        with self.lock:
            self.data.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            self._save()


def make_backend(secret: str):
    class Backend(QuietHandler):
        def do_GET(self):
            if self.path != "/private":
                self.respond(404)
            elif not secrets.compare_digest(self.headers.get("X-MC-Fixture-Capability", ""), secret):
                self.respond(403, b"DIRECT_BACKEND_DENIED")
            else:
                self.respond(200, b"PRIVATE_BACKEND_FIXTURE")
    return Backend


def make_gateway(backend_port: int, capability: str, sessions: Sessions, permanent_key: bytes):
    html = b'''<!doctype html><meta charset="utf-8"><title>MC Gateway Smoke</title>
<input type="file" id="key"><output id="state">LOGGED_OUT</output>
<script>
  document.getElementById('key').addEventListener('change', async e => {
    const f = e.target.files[0]; if (!f) return;
    const response = await fetch('/login', {method:'POST', body:f, headers:{'Content-Type':'application/octet-stream'}});
    document.getElementById('state').textContent = response.ok ? 'LOGGED_IN' : 'DENIED';
    e.target.value = '';
  });
</script>'''

    class Gateway(QuietHandler):
        def _cookie(self):
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE_NAME:
                    return v
            return ""

        def do_GET(self):
            if self.path == "/":
                self.respond(200, html, {"Content-Type": "text/html; charset=utf-8"})
                return
            if self.path == "/control":
                # Proves a same-host/different-port HTTP observer can receive
                # ordinary cookies. Contains no credential or private data.
                self.respond(200, b"PUBLIC_CONTROL_SET", {"Set-Cookie": f"{CONTROL_COOKIE}=public; Path=/; SameSite=Lax; Max-Age=300"})
                return
            if self.path != "/private":
                self.respond(404)
                return
            if not sessions.valid(self._cookie()):
                self.respond(401, b"AUTH_REQUIRED")
                return
            conn = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=5)
            try:
                conn.request("GET", "/private", headers={"X-MC-Fixture-Capability": capability})
                response = conn.getresponse()
                data = response.read()
                self.respond(response.status, data)
            finally:
                conn.close()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                self.respond(413)
                return
            body = self.rfile.read(length)
            if self.path == "/login":
                if length and secrets.compare_digest(body, permanent_key):
                    token = sessions.issue()
                    self.respond(200, b"LOGGED_IN", {"Set-Cookie": f"{COOKIE_NAME}={token}; Max-Age={COOKIE_AGE}; Path=/; Secure; HttpOnly; SameSite=Strict"})
                else:
                    self.respond(401, b"INVALID_KEY")
            elif self.path == "/logout":
                token = self._cookie()
                if not sessions.valid(token):
                    self.respond(401, b"AUTH_REQUIRED")
                    return
                sessions.revoke(token)
                self.respond(200, b"LOGGED_OUT", {"Set-Cookie": f"{COOKIE_NAME}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict"})
            else:
                self.respond(404)
    return Gateway


class RogueObserver:
    def __init__(self):
        self.lock = threading.Lock()
        self.observations: list[bool] = []
        self.control_observations: list[bool] = []

    def handler(self):
        obs = self

        class Rogue(QuietHandler):
            def do_GET(self):
                with obs.lock:
                    seen = self.headers.get("Cookie", "")
                    obs.observations.append(COOKIE_NAME in seen)
                    obs.control_observations.append(CONTROL_COOKIE in seen)
                self.respond(200, b"ROGUE_RECEIVED_REQUEST")
        return Rogue


def chromium_executable():
    import os
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if Path(p.chromium.executable_path).is_file():
            return None
    values = [os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"), os.environ.get("CHROMIUM_EXECUTABLE")]
    for name in ["chromium", "chromium-browser", "chrome", "msedge", "google-chrome"]:
        values.append(shutil.which(name))
    if sys.platform == "win32":
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                for rel in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
                    values.append(str(Path(root) / rel))
    for value in values:
        if value and Path(value).is_file():
            return value
    raise RuntimeError("Chromium not found; run python -m playwright install chromium or supply PLAYWRIGHT_CHROMIUM_EXECUTABLE")


def raw_get(port: int, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/private", headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def report_result(name: str, condition: bool, details: str = "") -> bool:
    print(f"COOKIE_GATEWAY_SMOKE {name} {'PASS' if condition else 'FAIL'}" + (f" {details}" if details else ""), flush=True)
    return bool(condition)


def smoke(host: str) -> bool:
    if host.lower() != host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host) or not host or host.startswith("."):
        raise ValueError("host must be a lower-case DNS hostname")

    from playwright.sync_api import Error as BrowserError, sync_playwright
    results: dict[str, bool] = {}
    def check(name: str, ok: bool, detail: str = ""):
        results[name] = report_result(name, ok, detail)

    with tempfile.TemporaryDirectory(prefix="mc-cookie-smoke-") as temporary:
        directory = Path(temporary)
        if os.name != "nt":
            directory.chmod(0o700)
        good_cert, good_key, good_pin = certificate(directory, host, "gateway")
        rogue_cert, rogue_key, _ = certificate(directory, host, "rogue")
        keyfile = directory / "alice.key"
        permanent_key = secrets.token_bytes(32)
        keyfile.write_bytes(permanent_key)
        if os.name != "nt":
            keyfile.chmod(0o600)
        sessions_file = directory / "sessions.json"
        capability = secrets.token_urlsafe(32)
        profile = str(directory / "alice-profile")
        bob_profile = str(directory / "bob-profile")
        rogue_http_obs = RogueObserver()
        rogue_tls_obs = RogueObserver()
        browser_args = [
            "--no-proxy-server", "--proxy-bypass-list=*",
            f"--host-resolver-rules=MAP {host} 127.0.0.1",
            f"--ignore-certificate-errors-spki-list={good_pin}",
        ]
        if sys.platform.startswith("linux") and os.geteuid() == 0:
            browser_args.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        browser_path = chromium_executable()
        browser_kw = {"headless": True, "args": browser_args}
        if browser_path:
            browser_kw["executable_path"] = browser_path
        with running_server(make_backend(capability)) as backend, \
             running_server(rogue_http_obs.handler()) as rogue_http, \
             running_server(rogue_tls_obs.handler(), cert=rogue_cert, key=rogue_key) as rogue_tls:
            check("backend-direct-denied", raw_get(backend.server_port)[0] == 403)
            check("backend-forged-capability-denied", raw_get(backend.server_port, {"X-MC-Fixture-Capability": "forged"})[0] == 403)
            sessions = Sessions(sessions_file)
            with running_server(make_gateway(backend.server_port, capability, sessions, permanent_key), cert=good_cert, key=good_key) as gateway:
                port = gateway.server_port
                https_url = f"https://{host}:{port}"
                with sync_playwright() as p:
                    bob = p.chromium.launch_persistent_context(bob_profile, **browser_kw)
                    try:
                        bob_page = bob.new_page()
                        r = bob_page.goto(f"{https_url}/private", timeout=15000)
                        check("bob-unauthenticated-denied", r is not None and r.status == 401)
                        bob_page.goto(https_url, timeout=15000)
                        status = bob_page.evaluate("async () => (await fetch('/login', {method:'POST', body:'not-alices-key'})).status")
                        check("bob-forged-key-denied", status == 401)
                    finally:
                        bob.close()
                    alice = p.chromium.launch_persistent_context(profile, **browser_kw)
                    try:
                        page = alice.new_page()
                        page.goto(https_url, wait_until="domcontentloaded", timeout=15000)
                        page.locator("#key").set_input_files(str(keyfile))
                        page.wait_for_function("document.querySelector('#state').textContent === 'LOGGED_IN'", timeout=10000)
                        check("alice-file-login", page.evaluate("async () => (await fetch('/private')).status") == 200)
                        cookies = [c for c in alice.cookies() if c.get("name") == COOKIE_NAME]
                        check("cookie-secure-httponly-persistent", len(cookies) == 1 and cookies[0].get("secure") is True and cookies[0].get("httpOnly") is True and cookies[0].get("expires", -1) > time.time() + 86400)
                        check("js-cannot-read-cookie", COOKIE_NAME not in page.evaluate("document.cookie"))
                        check("localstorage-has-no-credential", page.evaluate("localStorage.length") == 0)
                        check("positive-control-cookie-set", page.evaluate("async () => (await fetch('/control')).status") == 200)
                        # Threat test: same hostname, different port, HTTP. Inspect
                        # Cookie at the rogue server, NEVER echo the token to stdout.
                        page.goto(f"http://{host}:{rogue_http.server_port}/steal", timeout=15000)
                        check("rogue-http-request-observed", len(rogue_http_obs.observations) >= 1)
                        check("rogue-http-positive-control-received", bool(rogue_http_obs.control_observations) and any(rogue_http_obs.control_observations))
                        check("rogue-http-cookie-not-leaked", bool(rogue_http_obs.observations) and not any(rogue_http_obs.observations))
                        # Threat test: the other HTTPS port has an untrusted leaf.
                        try:
                            page.goto(f"https://{host}:{rogue_tls.server_port}/steal", timeout=12000)
                            rejected = False
                        except BrowserError:
                            rejected = True
                        check("rogue-https-certificate-rejected", rejected and not rogue_tls_obs.observations)
                    finally:
                        alice.close()
            # Gateway/backend process lifecycle simulated by destroying and
            # reconstructing the gateway + session-store objects from disk.
            reloaded_sessions = Sessions(sessions_file)
            with running_server(make_gateway(backend.server_port, capability, reloaded_sessions, permanent_key), cert=good_cert, key=good_key) as reopened_gateway:
                # New port: cookies are not port-scoped. This exercises the
                # strongest persistence property while keeping fixture isolated.
                https_url = f"https://{host}:{reopened_gateway.server_port}"
                with sync_playwright() as p:
                    alice = p.chromium.launch_persistent_context(profile, **browser_kw)
                    try:
                        page = alice.new_page()
                        status = page.goto(f"{https_url}/private", timeout=15000)
                        check("browser-and-gateway-restart-persists", status is not None and status.status == 200)
                        page.goto(https_url, timeout=15000)
                        logout = page.evaluate("async () => (await fetch('/logout', {method:'POST'})).status")
                        denied = page.evaluate("async () => (await fetch('/private')).status")
                        check("explicit-session-revocation", logout == 200 and denied == 401)
                    finally:
                        alice.close()

    failed = [name for name, ok in results.items() if not ok]
    print("COOKIE_GATEWAY_SMOKE SUMMARY " + json.dumps({
        "host": host, "status": "PASS" if not failed else "FAIL", "tests": len(results), "failed": failed,
        "note": "Synthetic gateway only; no production MC authentication or Windows ACL verification",
    }, sort_keys=True), flush=True)
    return not failed


def server_only_smoke(host: str) -> bool:
    """Validate fixture server semantics without asserting browser cookie safety."""
    checks: dict[str, bool] = {}

    def check(name: str, ok: bool) -> None:
        checks[name] = report_result(name, ok)

    with tempfile.TemporaryDirectory(prefix="mc-cookie-server-smoke-") as tmp:
        directory = Path(tmp)
        cert, key, _ = certificate(directory, host, "gateway")
        permanent_key = secrets.token_bytes(32)
        capability = secrets.token_urlsafe(32)
        file = directory / "sessions.json"
        sessions = Sessions(file)

        def request(port: int, method: str, path: str, body: bytes | None = None, cookie: str = ""):
            # Test only fixture HTTP semantics; NEVER count this as a browser
            # certificate verification test (that belongs to smoke()).
            context = ssl._create_unverified_context()
            conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=context)
            try:
                conn.request(method, path, body=body, headers={"Host": host, "Cookie": cookie})
                r = conn.getresponse()
                result = (r.status, dict(r.getheaders()), r.read())
                return result
            finally:
                conn.close()

        with running_server(make_backend(capability)) as backend:
            check("backend-direct-denied", raw_get(backend.server_port)[0] == 403)
            check("backend-forged-capability-denied", raw_get(backend.server_port, {"X-MC-Fixture-Capability": "invalid"})[0] == 403)
            with running_server(make_gateway(backend.server_port, capability, sessions, permanent_key), cert=cert, key=key) as gateway:
                port = gateway.server_port
                check("unauthenticated-gateway-denied", request(port, "GET", "/private")[0] == 401)
                check("incorrect-key-denied", request(port, "POST", "/login", b"invalid")[0] == 401)
                status, headers, _ = request(port, "POST", "/login", permanent_key)
                cookie_header = headers.get("Set-Cookie", "")
                check("valid-key-issued-cookie", status == 200 and COOKIE_NAME in cookie_header and all(flag in cookie_header for flag in ("HttpOnly", "Secure", "SameSite=Strict", "Max-Age=")))
                cookie = cookie_header.partition(";")[0]
                check("gateway-proxies-authenticated", request(port, "GET", "/private", cookie=cookie)[0] == 200)
            # Reconstruct disk-backed session state and TLS gateway.
            persisted = Sessions(file)
            with running_server(make_gateway(backend.server_port, capability, persisted, permanent_key), cert=cert, key=key) as restarted:
                port = restarted.server_port
                check("durable-session-after-server-restart", request(port, "GET", "/private", cookie=cookie)[0] == 200)
                check("logout-revokes", request(port, "POST", "/logout", cookie=cookie)[0] == 200 and request(port, "GET", "/private", cookie=cookie)[0] == 401)

    failed = [name for name, ok in checks.items() if not ok]
    print("COOKIE_GATEWAY_SMOKE SUMMARY " + json.dumps({"mode": "server-only", "host": host,
        "status": "PASS" if not failed else "FAIL", "tests": len(checks), "failed": failed,
        "note": "No browser; no claim about cross-port cookie leakage, certificate trust, or Windows ACLs",
    }, sort_keys=True), flush=True)
    return not failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run"])
    parser.add_argument("--host", default="mc.localhost", help="Fixture hostname resolved only inside Chromium (default: mc.localhost)")
    parser.add_argument("--server-only", action="store_true", help="Validate fixture HTTP behavior without a browser (NOT a cookie-isolation test)")
    args = parser.parse_args(argv)
    try:
        result = server_only_smoke(args.host) if args.server_only else smoke(args.host)
        return 0 if result else 1
    except Exception as exc:
        print(f"COOKIE_GATEWAY_SMOKE ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
