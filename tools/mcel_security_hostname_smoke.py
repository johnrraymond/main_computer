"""MC installation hostname, SSO, and shared ONLYOFFICE acceptance contract.

This is an intentionally RED pre-cutover test. It reads the production code and
runs independent, disposable authentication-state tests; it NEVER edits MC
production state, Windows hosts/DNS, certificates, or running Docker services.

The deployment contract is:
  dev.main-computer.local, unleashed.main-computer.local,
  debug.main-computer.local, safe.main-computer.local,
  onlyoffice.main-computer.local.

Raw checkout (dev) and installed Unleashed are MUTUALLY EXCLUSIVE choices
for the 8765 lane; do not demand a virtual-host multiplexer for them. Either
may run alongside Debug and Safe, which retain their separate ports. This
source-level contract does not itself prove live exclusive startup behavior.

The identity boundary is ONE authority per Windows owner (not one per checkout
and NOT one across different Windows users), one persistent underlying session
shared by that owner's installations, host-only browser cookies with a secure
SSO handoff, and a machine-wide ONLYOFFICE service using document-scoped grants.

Because .local is mDNS-special (RFC 6762), the managed browser must explicitly
resolve named MC origins to loopback AND reject unknown names in the MC namespace.
Static checks cannot prove browser runtime routing, TLS trust or real Windows
user isolation: those are separately UNVERIFIED until exercised.

Usage:
    python -m tools.mcel_security_hostname_smoke run
    python -m tools.mcel_security_hostname_smoke run --with-browser
    python -m tools.mcel_security_ingress_smoke run --hostname-only
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LANES = ('dev', 'unleashed', 'debug', 'safe')
EXCLUSIVE_8765 = frozenset(('dev', 'unleashed'))
HOSTS = {name: name + '.main-computer.local' for name in LANES}
DOCS_HOST = 'onlyoffice.main-computer.local'
ALL_HOSTS = (*HOSTS.values(), DOCS_HOST)
EXPECTED_PROOF = 'AAA_AUTH_MAIN_COMPUTER.json'
COOKIE_NAME = '__Host-MC-Session'


def concurrent_lanes_allowed(lanes) -> bool:
    """Deployment policy only; runtime exclusivity is tested separately."""
    proposed = tuple(lanes)
    return (len(proposed) == len(set(proposed))
            and set(proposed).issubset(LANES)
            and not EXCLUSIVE_8765.issubset(proposed))


class Results:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, status: str, detail: str = '') -> None:
        assert status in ('PASS', 'FAIL', 'UNVERIFIED')
        self.rows.append((name, status, detail))
        print('MC_HOSTNAME_AUTH ' + name + ' ' + status
              + (f' detail={detail[:220]}' if detail else ''), flush=True)

    def test(self, name: str, predicate: bool, detail: str = '') -> None:
        self.add(name, 'PASS' if predicate else 'FAIL', detail)

    def finish(self) -> int:
        counts = {s: sum(row[1] == s for row in self.rows) for s in ('PASS', 'FAIL', 'UNVERIFIED')}
        status = 'PASS' if not counts['FAIL'] and not counts['UNVERIFIED'] else 'BLOCKED'
        print('MC_HOSTNAME_AUTH SUMMARY ' + json.dumps({
            'status': status, 'counts': counts, 'tests': len(self.rows),
            'failed': [name for name, state, _ in self.rows if state == 'FAIL'],
            'unverified': [name for name, state, _ in self.rows if state == 'UNVERIFIED'],
            'production_modified': False,
            'boundary': 'one per-Windows-user shared authority; one shared ONLYOFFICE service',
        }, sort_keys=True), flush=True)
        return int(status != 'PASS')


def read(root: Path, name: str) -> str:
    file = root / name
    return file.read_text(encoding='utf-8-sig', errors='replace') if file.is_file() else ''


def _parse_mapping_rules(source: str) -> str:
    """Extract the literal Chromium resolver rules actually installed by launcher.

    This is a source-level audit, not a synthetic fixture-generated mapping.
    A later policy module may construct the rules dynamically; in that case the
    real browser acceptance run is authoritative, not this literal audit.
    """
    found = re.findall(r'--host-resolver-rules=([^\r\n\'\"]+)', source)
    return ' ; '.join(found)


def check_namespace(results: Results) -> None:
    results.test('hostnames-match-approved-namespace',
                 len(set(ALL_HOSTS)) == 5 and
                 all(re.fullmatch(r'[a-z][a-z0-9-]*\.main-computer\.local', x) for x in ALL_HOSTS),
                 ', '.join(ALL_HOSTS))
    results.test('no-underscore-in-browser-hostnames', all('_' not in host for host in ALL_HOSTS))
    results.test('one-shared-document-host-not-per-lane',
                 DOCS_HOST not in HOSTS.values() and
                 all('onlyoffice.' not in host for host in HOSTS.values()))
    results.test('dev-and-unleashed-mutually-exclusive-by-contract',
                 not concurrent_lanes_allowed(('dev', 'unleashed'))
                 and concurrent_lanes_allowed(('dev', 'debug', 'safe'))
                 and concurrent_lanes_allowed(('unleashed', 'debug', 'safe')),
                 'dev and unleashed are alternate owners of the 8765 lane; '
                 'debug and safe retain independent side-by-side ports')


def check_production_names(results: Results, root: Path) -> None:
    """Red until actual MC browser, TLS, and routing configuration are normalized."""
    policy = root / 'main_computer' / 'auth_hostnames.py'
    if not policy.is_file():
        results.add('production-canonical-hostname-registry', 'FAIL',
                    'main_computer/auth_hostnames.py not present; one authoritative registry is required')
    else:
        source = read(root, 'main_computer/auth_hostnames.py')
        absent = [h for h in ALL_HOSTS if h not in source]
        results.test('production-canonical-hostname-registry', not absent,
                     'missing=' + ','.join(absent) if absent else 'five origins declared')

    bootstrap = read(root, 'main_computer/auth_bootstrap.py')
    service = read(root, 'main_computer/auth_bootstrap_service.py')
    launcher = read(root, 'scripts/open-main-computer-browser.ps1')
    graphical = read(root, 'main_computer/web/graphical.html')
    targets = {'auth_bootstrap.py': bootstrap, 'auth_bootstrap_service.py': service,
               'open-main-computer-browser.ps1': launcher, 'graphical.html': graphical}
    stale = [name for name, src in targets.items() if 'mc.test' in src]
    results.test('legacy-mc-test-removed-from-active-paths', not stale,
                 'still references mc.test: ' + ', '.join(stale) if stale else 'none')

    # Source-level checks prevent a passing mock from certifying outdated code.
    # Preserve the established side-by-side MC lane ports; hostname changes
    # should not silently move the installations or launch extra Doc Servers.
    mode_ports = {'debug': 28865, 'safe': 38865}
    for lane, port in mode_ports.items():
        results.test('existing-' + lane + '-port-preserved',
                     bool(re.search(r'\"' + re.escape(lane) + r'\"\s*\{\s*return\s+\"' + str(port) + r'\"', launcher, re.I)),
                     f'expected={port} from installation browser launcher')
    results.test('existing-unleashed-port-preserved',
                 '8765' in launcher, 'Unleashed retains the exclusive primary :8765 slot (alternate to raw checkout)')

    results.test('auth-server-mode-aware-host-validation',
                 'auth_hostnames' in bootstrap and
                 'auth_hostnames' in service and
                 'mc.test' not in bootstrap,
                 'auth bootstrap and HTTPS service must both use the canonical per-mode policy')
    results.test('auth-login-canonical-per-lane',
                 'auth_hostnames' in service and 'mc.test' not in graphical,
                 'login and /graphical navigation must derive the correct installation URL')

    resolver_source = service + '\n' + launcher + '\n' + read(root, 'main_computer/auth_hostnames.py')
    literal_rules = _parse_mapping_rules(resolver_source)
    missing = [h for h in ALL_HOSTS if not re.search(
        r'MAP\s+' + re.escape(h) + r'\s+(?:127\.0\.0\.1|\[::1\])(?:\b|,|\s)', resolver_source, re.I)]
    results.test('managed-browser-explicit-loopback-mappings', not missing,
                 'unmapped=' + ','.join(missing) if missing else 'all five explicit mappings present')
    # Fail closed for misspelled or future unapproved names under this mDNS namespace.
    match = re.search(r'MAP\s+\*\.main-computer\.local\s+[~^]NOTFOUND\b',
                      resolver_source, re.I)
    results.test('managed-browser-unknown-namespace-deny', bool(match),
                 'require MAP *.main-computer.local ^NOTFOUND after explicit mappings')
    bad = any(pat in resolver_source for pat in ('--ignore-certificate-errors',
                                                 '--allow-insecure-localhost',
                                                 '--ignore-certificate-errors-spki-list'))
    results.test('managed-browser-does-not-disable-tls', not bad)
    results.test('managed-browser-source-has-resolver-rules',
                 bool(literal_rules) or ('host_resolver_rules' in resolver_source),
                 'browser launcher must actually use resolver rules, not Windows hosts/mDNS')
    results.test('no-insecure-http-canonical-addresses',
                 all('http://' + h not in (service + '\n' + launcher) for h in ALL_HOSTS),
                 'protected browser links must be HTTPS')

    # Raw checkout resolves the same 'unleashed' defaults as the installed
    # Unleashed lane. They are mutually exclusive alternatives on port 8765,
    # NOT simultaneous vhosts. Do not force a new mux or second port here.
    startup = read(root, 'scripts/main-computer-start-stop.ps1')
    source_uses_unleashed = (
        bool(re.search(r'Get-ModeDefaultEnvironment\s+\$RootPath\s+"unleashed"', startup))
        and bool(re.search(r'tree_kind\s*=\s*"source"\s+mode\s*=\s*"unleashed"', startup)))
    unleashed_port_8765 = bool(re.search(
        r'default\s*\{\s*\$normalized\s*=\s*"unleashed"'
        r'[\s\S]*?\$port\s*=\s*"8765"', startup))
    results.test('dev-unleashed-exclusive-8765-lane-source',
                 source_uses_unleashed and unleashed_port_8765,
                 'checkout uses installed Unleashed 8765 lane defaults; '
                 'no concurrent dev+Unleashed vhost requirement')
    results.add('real-dev-unleashed-mutual-exclusion', 'UNVERIFIED',
                'requires Windows startup acceptance: starting dev while Unleashed '
                'owns :8765 (and reverse) must refuse or safely hand off, never run both')
    supervisor = read(root, 'main_computer/service_supervisor.py')
    results.test('one-auth-https-owner-across-concurrent-lanes',
                 ('shared_auth_owner' in supervisor or 'auth_singleton_lease' in supervisor),
                 'one per-user auth owner across allowed concurrent lanes: '
                 '(dev OR unleashed) + debug + safe; no competing :8875 listeners')
    results.add('real-concurrent-mode-route-collision-check', 'UNVERIFIED',
                'requires live (dev OR unleashed) with debug/safe; dev and '
                'Unleashed must never be required to coexist on :8765')


def check_authority(results: Results, root: Path) -> None:
    try:
        from main_computer import auth_bootstrap as auth
    except Exception as exc:
        results.add('existing-auth-authority-import', 'UNVERIFIED', f'{type(exc).__name__}: {exc}')
        return
    results.test('one-neutral-aaa-proof', auth.PROOF_NAME == EXPECTED_PROOF,
                 f'observed={auth.PROOF_NAME}')
    results.test('session-cookie-host-only-name',
                 auth.COOKIE == COOKIE_NAME and auth.COOKIE.startswith('__Host-'))
    source = read(root, 'main_computer/auth_bootstrap.py')
    results.test('cookie-secure-httponly-host-restricted',
                 all(s in source for s in ('Secure; HttpOnly', 'SameSite=Strict', 'Path=/'))
                 and not re.search(r'cookie=f[^\n]*Domain=', source),
                 'no Domain= on MC user-session cookie')
    results.test('private-state-per-windows-account',
                 'LOCALAPPDATA' in source and 'MainComputer' in source and
                 'AuthBootstrap' in source and 'PROGRAMDATA' not in source.upper(),
                 'shared across MC checkouts of the same Windows user, not across Windows users')
    # Exercise actual durable authority from the supplied repository, never
    # production user's existing key, sessions, or AAA proof file.
    try:
        with tempfile.TemporaryDirectory(prefix='mc-hostname-auth-') as temp:
            state = Path(temp) / 'per-user-authority'
            auth.provision(state)
            a = auth.BootstrapAuthority(state)
            b = auth.BootstrapAuthority(state)  # second installed MC lane
            generation = a.sessions.current_generation()
            token = a.sessions.issue(expected_generation=generation)
            results.test('one-durable-session-reusable-by-all-lanes',
                         a.sessions.valid(token) and b.sessions.valid(token),
                         'two independent authority objects read same isolated session store')
            b.sessions.revoke(token)
            results.test('one-revocation-visible-to-all-lanes',
                         not a.sessions.valid(token) and not b.sessions.valid(token))
            old = a.sessions.issue(expected_generation=a.sessions.current_generation())
            saved_proof = auth.proof_file_path(state)
            saved_proof.unlink()
            # A missing proof automatically reissues and revokes the entire
            # generation. Reauthentication needs a new session.
            not_valid = not a.sessions.valid(old)
            results.test('aaa-deletion-revokes-all-mode-sessions',
                         not_valid and not b.sessions.valid(old)
                         and saved_proof.is_file(),
                         'same permanent key, new proof generation, no old session revival')
            fresh = b.sessions.issue(expected_generation=b.sessions.current_generation())
            results.test('fresh-authentication-possible-after-reissue',
                         b.sessions.valid(fresh) and not a.sessions.valid(old))
    except Exception as exc:
        results.add('shared-authority-disposable-runtime', 'UNVERIFIED',
                    f'failed to execute the real session store: {type(exc).__name__}: {str(exc)[:130]}')

    # Sessions can be shared across processes already. *Browser single sign-on*
    # across distinct host-only cookie origins is not established by that fact.
    handoff = read(root, 'main_computer/auth_hostnames.py') + read(root, 'main_computer/auth_bootstrap.py')
    results.test('cross-host-sso-handoff-implemented',
                 'sso_handoff' in handoff and 'one_time' in handoff and 'audience' in handoff,
                 'host-only cookie cannot travel from dev to debug; require one-use audience-bound handoff')
    results.add('cross-host-single-session-identity-runtime', 'UNVERIFIED',
                'needs two real canonical hostnames plus SSO handoff: same server-side session ID, distinct host-only cookies')
    results.add('sso-replay-and-cross-lane-binding-runtime', 'UNVERIFIED',
                'requires replay, cross-lane, session-revocation and cookie-scope tests of live SSO endpoints')


def check_tls(results: Results, root: Path) -> None:
    # A generated certificate for old mc.test cannot be reused for .local.
    # Test the actual certificate generator with TEMPORARY files only.
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID
        from main_computer.auth_bootstrap import generate_tls_certificate
        with tempfile.TemporaryDirectory(prefix='mc-hostname-san-') as t:
            cert, _key, _thumb = generate_tls_certificate(Path(t))
            certificate = x509.load_pem_x509_certificate(cert.read_bytes())
            sans = set(certificate.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName))
            results.test('tls-default-cert-uses-approved-mode-origin',
                         bool(sans.intersection(HOSTS.values())),
                         'observed=' + ','.join(sorted(sans)) + '; per-host SNI certificates are allowed')
            results.test('tls-leaf-not-certificate-authority',
                         not certificate.extensions.get_extension_for_oid(
                             ExtensionOID.BASIC_CONSTRAINTS).value.ca)
        # Existing installations already have an mc.test certificate. Test
        # the actual startup preflight with a disposable OLD certificate, rather
        # than presuming the generator itself must implement rotation. A correct
        # implementation may replace it, or refuse unsafe startup until an
        # explicitly managed certificate change is complete.
        with tempfile.TemporaryDirectory(prefix='mc-hostname-cert-upgrade-') as t:
            state = Path(t)
            from main_computer.auth_bootstrap import provision
            from main_computer.auth_bootstrap_service import preflight
            provision(state)
            generate_tls_certificate(state, hostname='mc.test')
            try:
                current_cert, _, _ = (None, None, None)
                _, current_cert, _ = preflight(state=state)
                current = x509.load_pem_x509_certificate(current_cert.read_bytes())
                actual_sans = set(current.extensions.get_extension_for_oid(
                    ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName))
                results.test('stale-mc-test-cert-not-accepted-by-startup',
                             bool(actual_sans.intersection(HOSTS.values())),
                             'preflight returned with SANs=' + ','.join(sorted(actual_sans)))
            except Exception as exc:
                results.add('stale-mc-test-cert-not-accepted-by-startup', 'UNVERIFIED',
                            'preflight rejected old certificate; must demonstrate authorized managed replacement: '
                            + type(exc).__name__)
    except ImportError:
        results.add('tls-certificate-san-check', 'UNVERIFIED', 'cryptography unavailable')
    except Exception as exc:
        results.add('tls-certificate-san-check', 'UNVERIFIED', f'{type(exc).__name__}: {str(exc)[:160]}')
    results.add('tls-all-mode-and-editor-sni-certificate-acceptance', 'UNVERIFIED',
                'each of the five approved HTTPS origins may use a separate valid cert; verify real SNI and CurrentUser trust')
    results.add('tls-live-sni-and-browser-trust', 'UNVERIFIED',
                'requires real configured HTTPS lane listeners and managed browser; self-signed temp fixture is not production trust')


def check_onlyoffice(results: Results, root: Path) -> None:
    compose = read(root, 'docker-compose.onlyoffice.yml')
    control = read(root, 'tools/onlyoffice/onlyoffice-control.ps1')
    source = read(root, 'main_computer/viewport_routes_onlyoffice.py')
    results.test('onlyoffice-single-shared-compose-identity',
                 'name: main-computer-onlyoffice' in compose
                 and 'main-computer-onlyoffice-documentserver' in compose
                 and 'MAIN_COMPUTER_ONLYOFFICE_PROJECT' in control,
                 'one machine-wide Document Server, not four per-mode containers')
    results.test('onlyoffice-docker-published-loopback-only',
                 bool(re.search(r'127\.0\.0\.1:\$\{MAIN_COMPUTER_ONLYOFFICE_PORT', compose)),
                 'legacy Docker port must not bind 0.0.0.0')
    results.test('onlyoffice-own-browser-origin-configured',
                 DOCS_HOST in (compose + control + source +
                               read(root, 'main_computer/auth_hostnames.py')),
                 'shared editor must use onlyoffice.main-computer.local')
    results.test('current-bootstrap-proxy-does-not-forward-browser-cookie',
                 'Do not forward an MC session' in read(root, 'main_computer/auth_bootstrap.py')
                 and COOKIE_NAME not in source,
                 'source-level proxy check; live editor isolation not implied')
    results.add('onlyoffice-live-editor-never-sees-user-session', 'UNVERIFIED',
                'requires an actual browser/editor request capture; no MC session Cookie forwarded to docs origin')
    results.test('onlyoffice-server-jwt-enabled-by-default',
                 'JWT_ENABLED: ${MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED:-true}' in compose,
                 'signed Document Server traffic rather than JWT disabled by default')
    results.test('onlyoffice-download-capability-enforced-in-real-handler',
                 '_handle_onlyoffice_file_get' in source and
                 ('document_capability' in source or 'authorize_document_download' in source),
                 'anonymous GET /api/applications/onlyoffice/file must be blocked')
    results.test('onlyoffice-callback-capability-enforced-in-real-handler',
                 '_handle_onlyoffice_callback' in source and
                 ('verify_callback_capability' in source or 'authorize_document_callback' in source),
                 'anonymous callbacks and SSRF must be blocked before downloads/writes')
    results.test('onlyoffice-per-mode-document-audience',
                 'document_audience' in source and 'installation' in source,
                 'document grants must bind originating MC installation and exact document')
    results.add('onlyoffice-live-edit-save-reopen-with-shared-auth', 'UNVERIFIED',
                'requires real Document Server editor callback and workbook round-trip')


def check_docker_runtime(results: Results) -> None:
    """Read-only daemon inspection; no Docker start/stop/mutation."""
    try:
        completed = subprocess.run(['docker', 'ps', '--format', '{{.Names}}|{{.Image}}|{{.Ports}}'],
                                   capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        results.add('docker-onlyoffice-single-running-server', 'UNVERIFIED',
                    f'Docker runtime unavailable: {type(exc).__name__}')
        return
    if completed.returncode:
        results.add('docker-onlyoffice-single-running-server', 'UNVERIFIED',
                    'Docker daemon unavailable or access denied; no container was changed')
        return
    records = [s.strip().split('|', 2) for s in completed.stdout.splitlines() if s.strip()]
    known = [fields for fields in records if len(fields) == 3
             and fields[1].startswith('onlyoffice/documentserver')]
    results.test('docker-onlyoffice-single-running-server', len(known) == 1,
                 'found=' + ','.join(fields[0] for fields in known) if known
                 else 'no running ONLYOFFICE Document Server')
    if known:
        public = [fields[0] for fields in known if
                  '0.0.0.0:' in fields[2] or ':::' in fields[2]
                  or re.search(r'(?<![\d.])(?:\*):\d+->', fields[2])]
        results.test('docker-onlyoffice-no-wildcard-host-publish', not public,
                     'wildcard-published=' + ','.join(public) if public else 'no wildcard host publishes observed')


def check_runtime(results: Results, *, with_browser: bool, root: Path) -> None:
    # Explicitly avoid calling socket.getaddrinfo(host) for *.local: that would
    # itself risk sending an mDNS lookup to the LAN. No packet leaves the host
    # during default contract tests.
    if not with_browser:
        results.add('managed-browser-real-loopback-dns-and-mdns-denial', 'UNVERIFIED',
                    'opt in with --with-browser after production resolver rules exist; no system mDNS queries made')
        return
    try:
        from main_computer.auth_hostnames import CHROMIUM_RESOLVER_RULES  # new production API
    except (ImportError, AttributeError):
        results.add('managed-browser-real-loopback-dns-and-mdns-denial', 'UNVERIFIED',
                    'production CHROMIUM_RESOLVER_RULES not installed; refusing to trigger .local resolution without guard')
        return
    rules = str(CHROMIUM_RESOLVER_RULES)
    if not all(re.search(r'MAP\s+' + re.escape(host) + r'\s+127\.0\.0\.1\b', rules, re.I)
               for host in ALL_HOSTS) or not re.search(
                r'MAP\s+\*\.main-computer\.local\s+[~^]NOTFOUND', rules, re.I):
        results.add('managed-browser-real-loopback-dns-and-mdns-denial', 'FAIL',
                    'refuse browser network test: explicit loopback and wildcard negative mapping incomplete')
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        results.add('managed-browser-real-loopback-dns-and-mdns-denial', 'UNVERIFIED',
                    'Playwright unavailable')
        return
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = self.headers.get('Host','').encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            # Isolated Playwright profile, no changes to user's browser/DNS.
            browser = p.chromium.launch(headless=True, args=[
                '--host-resolver-rules=' + rules, '--no-proxy-server'])
            try:
                page = browser.new_page()
                ok = []
                for host in ALL_HOSTS:
                    resp = page.goto(f'http://{host}:{server.server_port}/',
                                     wait_until='domcontentloaded', timeout=7000)
                    ok.append(resp is not None and resp.status == 200
                              and resp.text().strip() == f'{host}:{server.server_port}')
                results.test('managed-browser-five-origins-resolve-to-loopback', all(ok),
                             f'count={sum(ok)}/5; HTTP-only test, not TLS trust')
                try:
                    page.goto(f'http://unknown.main-computer.local:{server.server_port}/',
                              wait_until='domcontentloaded', timeout=5000)
                    results.add('managed-browser-unknown-mdns-origin-denied', 'FAIL',
                                'unrecognized MC hostname connected; resolution must fail closed')
                except Exception as exc:
                    if 'ERR_NAME_NOT_RESOLVED' in str(exc):
                        results.add('managed-browser-unknown-mdns-origin-denied', 'PASS',
                                    'unknown namespace refused by resolver rules')
                    else:
                        results.add('managed-browser-unknown-mdns-origin-denied', 'UNVERIFIED',
                                    f'unknown-host request failed for another reason: {type(exc).__name__}')
            finally:
                browser.close()
    except Exception as exc:
        results.add('managed-browser-real-loopback-dns-and-mdns-denial', 'UNVERIFIED',
                    f'{type(exc).__name__}: {str(exc)[:130]}')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def run(results: Results, *, root: Path | None = None, with_browser: bool = False,
        with_docker_runtime: bool = False) -> None:
    """Accept any results object offering .add(name, status, detail) and .test()."""
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    for label, fn in (
        ('namespace', lambda: check_namespace(results)),
        ('production', lambda: check_production_names(results, root)),
        ('authority', lambda: check_authority(results, root)),
        ('tls', lambda: check_tls(results, root)),
        ('onlyoffice', lambda: check_onlyoffice(results, root)),
        ('browser', lambda: check_runtime(results, root=root, with_browser=with_browser)),
        ('docker-runtime', lambda: check_docker_runtime(results) if with_docker_runtime
         else results.add('docker-onlyoffice-single-running-server', 'UNVERIFIED',
         'opt in with --with-docker-runtime for read-only live daemon inspection')), 
    ):
        try:
            fn()
        except Exception as exc:
            results.add('hostname-contract-' + label, 'UNVERIFIED',
                        f'probe failed: {type(exc).__name__}: {str(exc)[:160]}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='run', choices=['run'])
    parser.add_argument('--with-browser', action='store_true',
                        help='Use isolated Playwright Chromium to validate the production resolver rules')
    parser.add_argument('--with-docker-runtime', action='store_true',
                        help='Inspect running shared ONLYOFFICE container (read-only docker ps)')
    args = parser.parse_args(argv)
    results = Results()
    run(results, with_browser=args.with_browser, with_docker_runtime=args.with_docker_runtime)
    return results.finish()


if __name__ == '__main__':
    raise SystemExit(main())
