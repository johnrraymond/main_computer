"""Disposable Scramjet sidecar proof-of-concept for the MCEL Browser.

Run from Main Computer checkout:
    python -m tools.mcel_browser_scramjet_smoke

Downloads Scramjet-App under runtime/vendor; never modifies MCEL Browser code.
Tests a controlled page, frontend DOM operations, and mock trusted-origin isolation.
The test-runner patches the disposable Scramjet-App to bind 127.0.0.2 and
allow ONLY the 127.0.0.3 fixture through Wisp. DO NOT treat a passing smoke as
a security audit or deployment approval.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler

ISOLATED_IP = "127.0.0.2"
FIXTURE_IP = "127.0.0.3"
TRUSTED_IP = "127.0.0.1"
REPO_URL = "https://github.com/MercuryWorkshop/Scramjet-App.git"


def port_for(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def fetch_status(url: str, timeout: float = 1.0) -> tuple[int, int]:
    # The isolated loopback addresses must not be routed through environment HTTP proxies.
    with build_opener(ProxyHandler({})).open(url, timeout=timeout) as response:
        data = response.read(1024 * 1024)
        return response.status, len(data)


def find_executable(name: str) -> str | None:
    """Resolve Windows npm/Corepack shims explicitly instead of invoking bare names.

    Python's CreateProcess lookup does not reliably apply PATHEXT to bare
    commands. Prefer .cmd over extensionless POSIX wrappers on Windows.
    """
    if os.name == "nt":
        for extension in (".cmd", ".exe", ".bat"):
            path = shutil.which(name + extension)
            if path:
                return path
        # Do not launch extensionless Unix shell wrappers on native Windows.
        return None
    return shutil.which(name)


def install_dependencies(vendor: Path, report_dir: Path) -> dict:
    pnpm = find_executable("pnpm")
    corepack = find_executable("corepack")
    if pnpm:
        command = [pnpm, "install", "--frozen-lockfile"]
        label = "pnpm"
    elif corepack:
        command = [corepack, "pnpm", "install", "--frozen-lockfile"]
        label = "corepack pnpm"
    else:
        raise RuntimeError("pnpm or Corepack is required. On Windows check `where.exe pnpm.cmd` or `where.exe corepack.cmd`.")
    log_path = report_dir / "install.log"
    print(f"SCRAMJET_SETUP installer={label} executable={command[0]}", flush=True)
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write("SCRAMJET_INSTALL_COMMAND " + json.dumps(command) + "\n")
        stream.flush()
        try:
            proc = subprocess.run(command, cwd=vendor, stdout=stream,
                                  stderr=subprocess.STDOUT, check=False, timeout=600)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot launch {label} at {command[0]!r}: {type(exc).__name__}: {exc}. "
                f"Full details: {log_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{label} installation exceeded 600 seconds. Full details: {log_path}") from exc
    if proc.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-16:])
        raise RuntimeError(
            f"{label} install exited {proc.returncode}. Full log: {log_path}\n"
            f"SCRAMJET_INSTALL_TAIL\n{tail}")
    return {"installer":label,"command":command,"install_log":str(log_path)}


def get_runtime(repo: Path, report_dir: Path, no_bootstrap: bool) -> tuple[Path, str]:
    vendor = repo / "runtime" / "vendor" / "scramjet-app-smoke"
    server = vendor / "src" / "index.js"
    if not server.is_file():
        if no_bootstrap:
            raise RuntimeError("Scramjet-App absent. Remove --no-bootstrap for initial setup.")
        if vendor.exists():
            raise RuntimeError(f"Incomplete checkout: {vendor}; remove that directory and retry")
        if not shutil.which("git"):
            raise RuntimeError("git is required; install git before the smoke")
        vendor.parent.mkdir(parents=True, exist_ok=True)
        print("SCRAMJET_SETUP cloning third-party sample under runtime/vendor", flush=True)
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(vendor)], check=True, timeout=120)
    node = find_executable("node")
    if node is None:
        raise RuntimeError("Node.js is required; install Node.js 20+ first")
    if not (vendor / "node_modules" / "@mercuryworkshop" / "scramjet").exists():
        if no_bootstrap:
            raise RuntimeError("Dependencies absent. Remove --no-bootstrap to install them")
        print("SCRAMJET_SETUP installing pinned third-party dependencies locally", flush=True)
        install_dependencies(vendor, report_dir)
    source = server.read_text(encoding="utf-8")
    if 'host: "0.0.0.0"' in source:
        source = source.replace('host: "0.0.0.0"', f'host: "{ISOLATED_IP}"', 1)
    elif f'host: "{ISOLATED_IP}"' not in source:
        raise RuntimeError("Unknown upstream listener: refusing to run an unbound/public proxy")
    # Wisp blocks all loopback IPs by default, including our local fixture.
    # For this disposable smoke ONLY, allow loopback but restrict the transport
    # to the controlled fixture. This is not a deployable general web proxy.
    # This deliberately preserves the public/private network deny default.
    policy_marker = r"hostname_whitelist: [/^127\.0\.0\.3$/],"
    old_marker = 'hostname_whitelist: ["127.0.0.3"],'
    if old_marker in source:
        source = source.replace(old_marker, policy_marker, 1)
    if policy_marker not in source:
        upstream = r'hostname_blacklist: [/example\.com/],'
        if upstream not in source:
            raise RuntimeError("Upstream Wisp config changed; refusing to guess at fixture allowlist")
        source = source.replace(upstream, upstream + "\n\tallow_loopback_ips: true,\n\t" + policy_marker, 1)
    server.write_text(source, encoding="utf-8")
    print("SCRAMJET_SETUP smoke-only Wisp allowlist=127.0.0.3 (not production policy)", flush=True)
    with suppress(Exception):
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=vendor, text=True).strip()
        return vendor, commit
    return vendor, "unknown"


class FixtureHandler(BaseHTTPRequestHandler):
    requested_paths: list[str] = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        self.requested_paths.append(route)
        if route == "/":
            content_type = "text/html"
            payload = b'''<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/fixture.css">
<script defer src="/fixture.js"></script></head><body>
<main id="fixture-root"><h1>MCEL Scramjet Smoke</h1>
<p id="select">native selectable fixture text</p>
<input id="typing" aria-label="Type here">
<button id="click" type="button">Click here</button>
<div id="clicked">not clicked</div>
<div id="data">not fetched</div>
<div id="fixture-ready">not ready</div>
<div id="color">css active</div>
<a id="next" href="/next">Next page</a></main>
</body></html>'''
        elif route == "/fixture.js":
            content_type = "application/javascript"
            payload = b'''window.__mcelFixtureScript = true;
document.querySelector('#click').addEventListener('click', () => {
  document.querySelector('#clicked').textContent = 'clicked natively';
});
fetch('/fixture-data.json').then(r => r.json()).then(j => {
  document.querySelector('#data').textContent = j.value;
  document.querySelector('#fixture-ready').textContent = 'ready';
}).catch(e => {
  document.querySelector('#fixture-ready').textContent = 'fetch failed: ' + e;
});'''
        elif route == "/fixture-data.json":
            content_type = "application/json"
            payload = b'{"value":"rewritten fetch succeeded"}'
        elif route == "/fixture.css":
            content_type = "text/css"
            payload = b"#color { color: rgb(17, 101, 207); }"
        elif route == "/next":
            content_type = "text/html"
            payload = b'<html><body><h1 id="next-title">Native navigation succeeded</h1></body></html>'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MockTrustedHandler(BaseHTTPRequestHandler):
    secret = ""
    sidecar = ""

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/mcel-secret":
            data = self.secret.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        elif self.path == "/":
            data = (f'<html><body><h1>MOCK MCEL</h1><iframe id="sidecar" '
                    f'src="{self.sidecar}"></iframe></body></html>').encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", f"MOCK_MCEL_CANARY={self.secret}; Path=/; SameSite=Lax")
        else:
            self.send_error(404)
            return
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def check(report: dict, name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    report["checks"][name] = {"status": status, "detail": detail}
    print(f"SCRAMJET_SMOKE {name} {status}" + (f" {detail}" if detail else ""), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-bootstrap", action="store_true", help="Use already cloned/installed Scramjet-App")
    parser.add_argument("--headful", action="store_true", help="Show Edge/Chromium for debugging")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    if not (repo / "main_computer" / "mcel_backend_browser.py").is_file():
        print("SCRAMJET_SMOKE ERROR Run this from the Main Computer repository root", file=sys.stderr)
        return 2
    report_dir = repo / "runtime" / "reports" / "mcel-browser-scramjet-0001"
    report_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "checks": {},
                    "source": REPO_URL, "status": "ERROR"}
    servers = []
    process = None
    log = None
    stage = "baseline"
    try:
        baseline = (repo / "main_computer" / "mcel_backend_browser.py").read_text(encoding="utf-8")
        check(report, "existing-browser-is-screenshot-bridge", "screenshot" in baseline or "JPEG" in baseline)
        stage = "bootstrap-or-dependency-install"
        vendor, commit = get_runtime(repo, report_dir, args.no_bootstrap)
        stage = "start-isolated-sidecar"
        report["scramjet_app_git_commit"] = commit
        check(report, "smoke-only-fixture-allowlist", r"hostname_whitelist: [/^127\.0\.0\.3$/]," in
              (vendor / "src" / "index.js").read_text(encoding="utf-8"))
        port = port_for(ISOLATED_IP)
        FixtureHandler.requested_paths = []
        fixture = ThreadingHTTPServer((FIXTURE_IP, 0), FixtureHandler)
        servers.append(fixture)
        trusted_port = port_for(TRUSTED_IP)
        sidecar_url = f"http://{ISOLATED_IP}:{port}/"
        MockTrustedHandler.secret = secrets.token_hex(16)
        MockTrustedHandler.sidecar = sidecar_url
        trusted = ThreadingHTTPServer((TRUSTED_IP, trusted_port), MockTrustedHandler)
        servers.append(trusted)
        from threading import Thread
        for server in servers:
            Thread(target=server.serve_forever, daemon=True).start()
        env = {**os.environ, "PORT": str(port)}
        log = (report_dir / "sidecar.log").open("w", encoding="utf-8")
        process = subprocess.Popen([find_executable("node"), "src/index.js"], cwd=vendor, env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Scramjet server exited with code {process.returncode}; inspect sidecar.log")
            try:
                status, _ = fetch_status(sidecar_url, 0.5)
                if status == 200:
                    break
            except (URLError, TimeoutError, ConnectionError, OSError):
                time.sleep(0.3)
        else:
            raise RuntimeError("Scramjet server did not respond; inspect sidecar.log")
        report["sidecar_url"] = sidecar_url
        check(report, "isolated-ip-http-server", True, sidecar_url)
        try:
            fetch_status(f"http://{TRUSTED_IP}:{port}/", 1)
            check(report, "not-bound-on-trusted-ip", False, "Sidecar accepts 127.0.0.1 traffic")
        except (URLError, TimeoutError, ConnectionError, OSError):
            check(report, "not-bound-on-trusted-ip", True)
        for path, name in (("scram/scramjet.all.js", "scramjet-client-bundle"),
                           ("scram/scramjet.wasm.wasm", "scramjet-ast-wasm"),
                           ("libcurl/index.mjs", "network-transport"),
                           ("baremux/index.js", "bare-mux"),
                           ("sw.js", "service-worker-script")):
            try:
                status, length = fetch_status(sidecar_url + path, 5)
                check(report, name, status == 200 and length > 10, f"HTTP {status}, read {length} bytes")
            except Exception as e:
                check(report, name, False, f"{type(e).__name__}: {e}")
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError as exc:
            raise RuntimeError("Install Python Playwright in this venv: python -m pip install playwright") from exc
        stage = "frontend-and-isolation-checks"
        with sync_playwright() as pw:
            browser = None
            errors = []
            for channel in ("msedge", None):
                try:
                    kw = {"channel": channel} if channel else {}
                    browser = pw.chromium.launch(headless=not args.headful, **kw)
                    break
                except Exception as e:
                    errors.append(str(e).splitlines()[0])
            if browser is None:
                raise RuntimeError("No Chromium or Edge available; " + "; ".join(errors))
            try:
                context = browser.new_context()
                page = context.new_page()
                browser_trace = []

                def trace(message: str) -> None:
                    # Short bounded diagnostic trail; no page body or cookie contents.
                    browser_trace.append(str(message)[:450])
                    if len(browser_trace) > 250:
                        del browser_trace[:len(browser_trace) - 250]

                page.on("console", lambda msg: trace("console " + msg.type + ": " + msg.text))
                page.on("pageerror", lambda err: trace("pageerror " + str(err)))
                page.on("requestfailed", lambda req: trace("requestfailed " + req.method + " " + req.url + " " + str(req.failure)))
                page.on("response", lambda resp: trace("response " + str(resp.status) + " " + resp.url) if resp.status >= 400 else None)
                page.on("frameattached", lambda f: trace("frameattached " + f.url))
                page.on("framenavigated", lambda f: trace("framenavigated " + f.url))
                page.on("framedetached", lambda f: trace("framedetached " + f.url))
                page.goto(f"http://{TRUSTED_IP}:{trusted_port}/", wait_until="domcontentloaded")
                canary = MockTrustedHandler.secret
                check(report, "mock-trusted-cookie-created", canary in page.evaluate("document.cookie"))
                page.goto(sidecar_url, wait_until="domcontentloaded")
                check(report, "is-trustworthy-serviceworker-context",
                      page.evaluate("!!navigator.serviceWorker && isSecureContext"))
                check(report, "trusted-cookie-not-on-sidecar-origin",
                      canary not in page.evaluate("document.cookie"))
                page.locator("#sj-address").fill(f"http://{FIXTURE_IP}:{fixture.server_port}/")
                page.locator("#sj-address").press("Enter")
                try:
                    page.locator("#sj-frame").wait_for(timeout=25000)
                    frame = page.frame_locator("#sj-frame")
                    expect(frame.locator("#fixture-ready")).to_have_text("ready", timeout=35000)
                    check(report, "frontend-dom-rendered", True)
                    check(report, "script-ast-transformation-and-fetch",
                          frame.locator("#fixture-ready").inner_text() == "ready" and
                          frame.locator("#data").inner_text() == "rewritten fetch succeeded")
                    check(report, "css-resource-rewrite",
                          frame.locator("#color").evaluate("el => getComputedStyle(el).color") == "rgb(17, 101, 207)")
                    frame.locator("#click").click()
                    check(report, "native-pointer-event", frame.locator("#clicked").inner_text() == "clicked natively")
                    frame.locator("#typing").fill("typed on frontend")
                    check(report, "native-keyboard-input",
                          frame.locator("#typing").input_value() == "typed on frontend")
                    frame.locator("#select").select_text()
                    check(report, "native-text-selection", "selectable fixture text" in
                          frame.locator("#select").evaluate("el => getSelection().toString()"))
                    # This is a controlled canary endpoint, not a real MCEL route.
                    # A successful read means the stock proxy must not be trusted with MCEL access.
                    result = frame.locator("#fixture-root").evaluate("""async (el, opts) => {
                      try {
                        const response = await Promise.race([
                          fetch(opts.url), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 7000))
                        ]);
                        return {read: (await response.text()).includes(opts.secret), status: response.status};
                      } catch (err) { return {read:false, error:String(err)}; }
                    }""", {"url":f"http://{TRUSTED_IP}:{trusted_port}/mcel-secret", "secret": canary})
                    check(report, "proxied-page-cannot-read-trusted-mock",
                          not result.get("read", False), str(result)[:180])
                    frame.locator("#next").click()
                    expect(frame.locator("#next-title")).to_have_text("Native navigation succeeded", timeout=20000)
                    check(report, "native-navigation", True)
                except Exception as exc:
                    check(report, "frontend-fixture-pipeline", False, f"{type(exc).__name__}: {str(exc)[:200]}")
                    with suppress(Exception):
                        page.screenshot(path=str(report_dir / "frontend-failure.png"), full_page=True)
                    with suppress(Exception):
                        report["sidecar_frontend_error"] = page.locator("#sj-error").inner_text()
                        report["sidecar_frontend_error_code"] = page.locator("#sj-error-code").inner_text()[:500]
                    with suppress(Exception):
                        report["browser_frames_at_failure"] = [f.url for f in page.frames]
                    with suppress(Exception):
                        report["service_worker_at_failure"] = page.evaluate("""async () => ({
                          controller: navigator.serviceWorker?.controller?.scriptURL || null,
                          registrations: (await navigator.serviceWorker?.getRegistrations() || []).map(
                            r => ({scope:r.scope, active:r.active?.scriptURL || null, state:r.active?.state || null})),
                          iframe: (() => { const f = document.querySelector('#sj-frame');
                            return f ? {src:f.getAttribute('src'), resolvedSrc:f.src} : null; })(),
                          error: document.querySelector('#sj-error')?.textContent || ''
                        })""")
                    report["browser_event_trace"] = browser_trace[-120:]
                    report["fixture_http_paths_at_failure"] = FixtureHandler.requested_paths[-60:]
                    print("SCRAMJET_DIAG fixture_http_paths=" + json.dumps(report["fixture_http_paths_at_failure"]), flush=True)
                    print("SCRAMJET_DIAG frames=" + json.dumps(report.get("browser_frames_at_failure", [])), flush=True)
                    print("SCRAMJET_DIAG service_worker=" + json.dumps(report.get("service_worker_at_failure", {}))[:1000], flush=True)
                    for line in browser_trace[-15:]:
                        print("SCRAMJET_DIAG " + line, flush=True)
                # Separate-origin iframe test (MCEL mock 127.0.0.1 embeds Scramjet 127.0.0.2).
                boundary_page = context.new_page()
                boundary_page.goto(f"http://{TRUSTED_IP}:{trusted_port}/", wait_until="domcontentloaded")
                boundary_page.locator("#sidecar").wait_for()
                embedded = boundary_page.locator("#sidecar").element_handle()
                embedded_frame = embedded.content_frame() if embedded else None
                embedded_url = ""
                if embedded_frame is not None:
                    with suppress(Exception):
                        embedded_frame.wait_for_url(lambda u: u.startswith(sidecar_url), timeout=10000)
                    embedded_url = embedded_frame.url
                barrier_result = boundary_page.evaluate("""() => {
                  const f = document.querySelector('#sidecar');
                  try {
                    const value = f.contentWindow.document.body;
                    return {blocked: false, frameSrc:f.src, readable: value?.tagName || null};
                  } catch (e) {
                    return {blocked:e.name === 'SecurityError', frameSrc:f.src,
                            exceptionName:e.name, exceptionMessage:String(e.message).slice(0, 200)};
                  }
                }""")
                report["embedded_sidecar_url"] = embedded_url
                report["cross_origin_barrier_detail"] = barrier_result
                check(report, "cross-origin-dom-barrier",
                      embedded_url.startswith(sidecar_url) and barrier_result.get("blocked") is True,
                      f"actual_frame={embedded_url!r} exception={barrier_result.get('exceptionName', 'none')} "
                      f"readable={barrier_result.get('readable', 'none')}")
                context.close()
            finally:
                browser.close()
        failed = [k for k, v in report["checks"].items() if v["status"] != "PASS"]
        required = ("smoke-only-fixture-allowlist", "isolated-ip-http-server", "not-bound-on-trusted-ip", "scramjet-client-bundle",
                    "scramjet-ast-wasm", "network-transport", "bare-mux",
                    "service-worker-script", "is-trustworthy-serviceworker-context",
                    "frontend-dom-rendered", "script-ast-transformation-and-fetch",
                    "css-resource-rewrite", "native-pointer-event", "native-keyboard-input",
                    "native-text-selection", "native-navigation", "trusted-cookie-not-on-sidecar-origin",
                    "cross-origin-dom-barrier", "proxied-page-cannot-read-trusted-mock")
        missing = [name for name in required if report["checks"].get(name, {}).get("status") != "PASS"]
        report["status"] = "PASS_SMOKE_NOT_SECURITY_CERTIFIED" if not missing else "BLOCKED"
        print(f"SCRAMJET_SMOKE RESULT {report['status']}" + (" missing=" + ",".join(missing) if missing else ""), flush=True)
        return 0 if not missing else 1
    except Exception as exc:
        report["status"] = "ERROR"
        report["stage"] = stage
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"SCRAMJET_SMOKE ERROR stage={stage} {report['error']}", file=sys.stderr, flush=True)
        return 2
    finally:
        if process is not None and process.poll() is None:
            with suppress(Exception):
                process.terminate()
                process.wait(timeout=5)
            if process.poll() is None:
                with suppress(Exception):
                    process.kill()
        if log is not None:
            log.close()
            if report.get("status") != "PASS_SMOKE_NOT_SECURITY_CERTIFIED":
                sidecar_log = report_dir / "sidecar.log"
                with suppress(Exception):
                    tail = sidecar_log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
                    report["sidecar_log_tail"] = tail
                    for line in tail[-10:]:
                        print("SCRAMJET_DIAG sidecar " + line[:250], flush=True)
        for server in servers:
            server.shutdown()
            server.server_close()
        path = report_dir / "report.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"SCRAMJET_SMOKE REPORT {path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
