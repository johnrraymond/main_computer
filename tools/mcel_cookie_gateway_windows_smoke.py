"""Opt-in, no-admin Windows integration smoke for the MC HTTPS cookie gateway.

One-command Windows smoke (ordinary, non-elevated PowerShell)::

    python -m tools.mcel_cookie_gateway_windows_smoke run

Chromium resolves mc.test -> 127.0.0.1 *inside the test browser only* via
--host-resolver-rules. It does not modify Windows DNS, hosts, registry, or
system certificate settings. The test uses actual Chromium certificate
verification: no --ignore-certificate-errors, --ignore-certificate-errors-spki-
list, ignoreHTTPSErrors, or browser-root override is permitted.

For the TLS acceptance test, a short-lived, self-signed non-CA server leaf is
installed into the invoking Windows user's CurrentUser\\Root store.
It is removed in a finally block. If interrupted, run 'cleanup-trust' from
the SAME Windows user to remove only the recorded fixture certificate.
The fixture keys, persistent sessions, and browser profiles live in Alice's
ACL-protected local user directory. The existing MC application is untouched.

Requires the original tools.mcel_localhost_cookie_gateway_smoke module,
Python cryptography and Playwright with Chromium/Edge. This test does not
simulate a real Windows reboot or a second Windows security principal. It
validates browser-profile separation, not two separate OS user sessions.
Normal browsers do not inherit this test's private hostname mapping; production
hostname handling remains an independent architectural decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import mcel_localhost_cookie_gateway_smoke as base

HOST = "mc.test"
SUBJECT_PREFIX = "MC Cookie Gateway Windows Smoke "
PENDING_FILENAME = "pending-trust.json"
TRUST_STORE = "Root"  # CurrentUser/Root is consumed by Chromium; CurrentUser/TrustedPeople is not


def require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("WINDOWS_REQUIRED: run this integration smoke in Windows PowerShell")


def non_elevated() -> bool:
    """Do not certify a Windows ACL isolation test from an elevated token."""
    require_windows()
    import ctypes
    return not bool(ctypes.windll.shell32.IsUserAnAdmin())


def _run(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, capture_output=True, text=True, check=False, timeout=25)


def result(name: str, ok: bool, detail: str = "") -> bool:
    print(f"COOKIE_GATEWAY_WINDOWS {name} {'PASS' if ok else 'FAIL'}" + (f" {detail}" if detail else ""), flush=True)
    return ok


def caller_sid() -> str:
    # Reuse the SLH's token-based SID/ACL authority rather than USERNAME strings.
    from main_computer.slh.storage import _windows_caller_sid
    return _windows_caller_sid()


def state_dir() -> Path:
    require_windows()
    appdata = os.environ.get("LOCALAPPDATA")
    if not appdata:
        raise RuntimeError("LOCALAPPDATA_MISSING")
    return Path(appdata) / "MainComputer" / "CookieGatewayWindowsSmoke"


def _icacls_dacl(path: Path, *options: str) -> None:
    """Change only the DACL of this test-owned path; never request SACL rights.

    Unlike Set-Acl on a Get-Acl security descriptor, icacls edits the
    discretionary access list without asking for SeSecurityPrivilege.
    It requires only the caller's normal owner/WRITE_DAC authority.
    """
    completed = _run(["icacls.exe", str(path), *options])
    if completed.returncode:
        raise PermissionError("DIRECTORY_DACL_EDIT_FAILED: " +
                              (completed.stderr or completed.stdout).strip()[:350])


def secure_directory(path: Path) -> None:
    """Enforce an exact owner/SYSTEM/Administrators DACL without elevation.

    Operates only on the dedicated fixture directory and its descendants.
    Never changes owner, audit settings (SACL), or machine-wide permissions.
    Verify the resulting DACL and fail closed on any unexpected entry.
    """
    from main_computer.slh.storage import secure_machine_state_dir

    path = Path(path)
    root = state_dir()
    if path != root and root not in path.parents:
        raise PermissionError("REFUSE_ACL_CHANGE_OUTSIDE_SMOKE_DIRECTORY")
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise PermissionError("REFUSE_ACL_CHANGE_ON_REPARSE_DIRECTORY")

    # Existing SLH code creates the directory, disables inheritance, and
    # grants the three trusted SIDs. It can leave unrelated explicit grants;
    # those are removed below, rather than silently accepted.
    secure_machine_state_dir(path)
    owner_sid = caller_sid()
    permitted = {owner_sid, "S-1-5-18", "S-1-5-32-544"}
    report = acl_report(path)
    rules = report.get("rules") or []
    if isinstance(rules, dict):
        rules = [rules]
    if not report.get("protected"):
        raise PermissionError("DIRECTORY_INHERITANCE_NOT_DISABLED")

    # Remove any stale explicit ACE belonging to an unexpected SID, including
    # denies. Do not use /reset (which would re-enable parent permissions).
    by_sid = {}
    for rule in rules:
        sid = str(rule.get("sid", ""))
        if not re.fullmatch(r"S-1-\d+(?:-\d+)+", sid):
            raise PermissionError("UNRECOGNIZED_DIRECTORY_ACE_SID")
        by_sid.setdefault(sid, set()).add(str(rule.get("type", "")).lower())
    for sid, kinds in sorted(by_sid.items()):
        if sid not in permitted:
            _icacls_dacl(path, "/remove", "*" + sid)
        elif "deny" in kinds:
            _icacls_dacl(path, "/remove:d", "*" + sid)

    # Replace (not append) the three trusted explicit grants. OI/CI makes
    # newly created files and subdirectories inherit these permissions.
    _icacls_dacl(path, "/inheritance:r", "/grant:r",
                 f"*{owner_sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                 "*S-1-5-32-544:(OI)(CI)F")
    if not private_acl_ok(path, owner_sid):
        raise PermissionError("EXACT_DIRECTORY_DACL_UNVERIFIED: " + acl_diagnostic(path))


def secure_file(path: Path) -> None:
    from main_computer.slh.storage import secure_private_state_file
    secure_private_state_file(path)


class WindowsSecuredSessions(base.Sessions):
    """Harden the durable session record after *every* atomic replacement.

    Base Sessions._save replaces the destination with a new file. The parent
    directory has an owner-only ACL, but we also independently secure the new
    file so the revocation/restart ACL tests check the actual lasting record.
    """

    def _save(self) -> None:
        super()._save()
        secure_file(self.file)


def acl_report(path: Path) -> dict[str, object]:
    """Read explicit/inherited DACL entries and whether inheritance is blocked."""
    ps = r'''
$ErrorActionPreference='Stop'
$acl=Get-Acl -LiteralPath $env:MC_SMOKE_ACL_PATH
$rules=@($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]) |
  ForEach-Object { [PSCustomObject]@{
    sid=$_.IdentityReference.Value
    type=$_.AccessControlType.ToString()
    inherited=[bool]$_.IsInherited
  } })
ConvertTo-Json -InputObject ([PSCustomObject]@{
  protected=[bool]$acl.AreAccessRulesProtected
  rules=$rules
}) -Compress -Depth 4
'''
    env = os.environ.copy()
    env["MC_SMOKE_ACL_PATH"] = str(path)
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                               env=env, capture_output=True, text=True, check=False, timeout=25)
    if completed.returncode:
        raise RuntimeError("WINDOWS_ACL_INSPECTION_FAILED: " +
                           (completed.stderr or completed.stdout).strip()[:220])
    return json.loads(completed.stdout)


def acl_diagnostic(path: Path) -> str:
    try:
        return json.dumps(acl_report(path), sort_keys=True)[:600]
    except Exception as exc:
        return f"INSPECTION_FAILED {type(exc).__name__}: {str(exc)[:120]}"


def private_acl_ok(path: Path, owner_sid: str) -> bool:
    if not path.exists() or path.is_symlink():
        return False
    permitted = {owner_sid, "S-1-5-18", "S-1-5-32-544"}
    report = acl_report(path)
    rules = report.get("rules") or []
    if isinstance(rules, dict):
        rules = [rules]
    return (report.get("protected") is True
            and len(rules) == 3
            and all(str(r["type"]).lower() == "allow" for r in rules)
            and {str(r["sid"]) for r in rules} == permitted
            and all(not r["inherited"] for r in rules))


def certificate(folder: Path, hostname: str, stem: str, *, fixture_leaf: bool) -> tuple[Path, Path, str]:
    """Generate an ephemeral RSA leaf with CA=False, SAN and serverAuth EKU."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cn = SUBJECT_PREFIX + secrets.token_hex(8) if fixture_leaf else "MC Cookie Rogue Untrusted"
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(hours=12))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(private, hashes.SHA256()))
    cert_path = folder / f"{stem}.pem"
    private_path = folder / f"{stem}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    secure_file(private_path)
    digest = hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return cert_path, private_path, digest


def trusted_subject(thumb: str, store: str = TRUST_STORE) -> str | None:
    """Query the named CurrentUser store. Missing certificate is not an error."""
    if not re.fullmatch(r"[0-9A-F]{40}", thumb) or store not in {"Root", "TrustedPeople"}:
        raise ValueError("invalid certificate lookup")
    ps = r'''
$ErrorActionPreference = 'Stop'
$store = 'Cert:\CurrentUser\' + $env:MC_SMOKE_TRUST_STORE
if (-not (Test-Path -LiteralPath $store)) { throw 'CERT_STORE_UNAVAILABLE' }
$matches = @(Get-ChildItem -LiteralPath $store -ErrorAction Stop |
  Where-Object { $_.Thumbprint -eq $env:MC_SMOKE_THUMBPRINT })
if ($matches.Count -gt 1) { throw 'AMBIGUOUS_THUMBPRINT' }
if ($matches.Count -eq 1) { Write-Output $matches[0].Subject }
'''
    env = os.environ.copy()
    env["MC_SMOKE_TRUST_STORE"] = store
    env["MC_SMOKE_THUMBPRINT"] = thumb
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                          env=env, capture_output=True, text=True, check=False, timeout=25)
    if proc.returncode:
        raise RuntimeError("CERT_STORE_QUERY_FAILED: " + (proc.stderr or proc.stdout).strip()[:240])
    return proc.stdout.strip() or None


def pending_trust() -> Path:
    return state_dir() / PENDING_FILENAME


def cleanup_trust() -> bool:
    require_windows()
    journal = pending_trust()
    if not journal.is_file():
        return result("trust-cleanup-no-pending-cert", True)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    thumb = str(payload.get("thumbprint", ""))
    expected_subject = str(payload.get("subject", ""))
    # Backward compatibility: older smoke-test journals used TrustedPeople.
    store = str(payload.get("store", "TrustedPeople"))
    if (not re.fullmatch(r"[0-9A-F]{40}", thumb)
            or not expected_subject.startswith("CN=" + SUBJECT_PREFIX)
            or store not in {"TrustedPeople", "Root"}):
        raise RuntimeError("INVALID_TRUST_JOURNAL: refuse removing unknown certificate")
    subject = trusted_subject(thumb, store)
    if subject is not None:
        if subject != expected_subject:
            raise RuntimeError("TRUST_STORE_SUBJECT_MISMATCH: refuse removing unrelated certificate")
        proc = _run(["certutil.exe", "-user", "-delstore", store, thumb])
        if proc.returncode:
            raise RuntimeError("TRUST_CLEANUP_FAILED: " + (proc.stderr or proc.stdout).strip()[:220])
    if trusted_subject(thumb, store) is not None:
        raise RuntimeError("TRUST_CLEANUP_UNVERIFIED: certificate remains installed")
    journal.unlink()
    return result("trust-cleanup-verified", True, f"store=CurrentUser/{store}")


def trust_leaf(cert_path: Path, thumbprint: str) -> None:
    if pending_trust().exists():
        raise RuntimeError("PENDING_TRUST_JOURNAL: clean prior fixture before new test")
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import NameOID
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    subject = "CN=" + cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    if not subject.startswith("CN=" + SUBJECT_PREFIX):
        raise RuntimeError("REFUSE_TRUST_NON_FIXTURE_CERT")
    if hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper() != thumbprint:
        raise RuntimeError("CERT_THUMBPRINT_MISMATCH")
    if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise RuntimeError("REFUSE_TO_TRUST_CA_CERTIFICATE")
    pending = pending_trust()
    pending.write_text(json.dumps({"thumbprint": thumbprint, "subject": subject,
                                   "store": TRUST_STORE}) + "\n", encoding="utf-8")
    secure_file(pending)
    proc = _run(["certutil.exe", "-user", "-addstore", TRUST_STORE, str(cert_path)])
    if proc.returncode:
        raise RuntimeError("TRUST_IMPORT_FAILED: " + (proc.stderr or proc.stdout).strip()[:220])
    if trusted_subject(thumbprint, TRUST_STORE) != subject:
        raise RuntimeError("TRUST_IMPORT_UNVERIFIED")


def chromium_kwargs() -> dict:
    executable = base.chromium_executable()
    # An explicit browser-only mapping keeps all Windows DNS/hosts unchanged.
    # Only the test-managed Chromium receives this flag.
    args = [
        "--no-proxy-server", "--proxy-bypass-list=*",
        f"--host-resolver-rules=MAP {HOST} 127.0.0.1",
    ]
    if any(a.startswith(("--ignore-certificate-errors", "--allow-insecure-localhost"))
           for a in args):
        raise RuntimeError("BROWSER_CERTIFICATE_BYPASS_NOT_ALLOWED")
    if len([a for a in args if a.startswith("--host-resolver-rules=")]) != 1:
        raise RuntimeError("INVALID_BROWSER_HOST_MAPPING")
    kwargs: dict = {"headless": True, "args": args, "ignore_https_errors": False}
    if executable:
        kwargs["executable_path"] = executable
    return kwargs


def certificate_rejection(error: Exception) -> bool:
    """The negative fixture must fail *specifically* on unknown authority."""
    return "ERR_CERT_AUTHORITY_INVALID" in str(error)


def windows_native_smoke() -> bool:
    require_windows()
    # Protect the fixture state directory before reading any old journal.
    secure_directory(state_dir())
    # A prior run may have been interrupted; remove only its exact journaled
    # fixture certificate. Never remove any other trust-store entries.
    if pending_trust().exists():
        cleanup_trust()
    if not preflight():
        return False
    from playwright.sync_api import Error as BrowserError, sync_playwright

    results: dict[str, bool] = {}
    def check(name: str, condition: bool) -> None:
        detail = ""
        if not condition and name.endswith("-acl"):
            directory = run_root if name == "smoke-state-directory-acl" else folder
            if name in {"smoke-state-directory-acl", "fixture-directory-acl"}:
                detail = acl_diagnostic(directory)
        results[name] = result(name, condition, detail)
        if not condition and name.endswith("-acl"):
            raise PermissionError("PRIVATE_ACL_VERIFICATION_FAILED: " + name + " " + detail)

    # Verified launch configuration; runtime proof is browser TLS acceptance
    # using that mapping and a certificate signed for HOST.
    check("chromium-only-loopback-mapping", f"--host-resolver-rules=MAP {HOST} 127.0.0.1" in chromium_kwargs()["args"])
    check("process-not-elevated", non_elevated())
    user_sid = caller_sid()
    run_root = state_dir()
    secure_directory(run_root)
    check("smoke-state-directory-acl", private_acl_ok(run_root, user_sid))

    trusted = False
    trust_cleanup_ok = False
    try:
        with tempfile.TemporaryDirectory(prefix="fixture-", dir=run_root) as temp:
            folder = Path(temp)
            secure_directory(folder)
            check("fixture-directory-acl", private_acl_ok(folder, user_sid))
            # The security check must reject and repair a genuinely unsafe
            # explicit ACE, not merely report a passing default directory.
            _icacls_dacl(folder, "/grant", "*S-1-1-0:(OI)(CI)R")
            check("extra-everyone-grant-detected", not private_acl_ok(folder, user_sid))
            secure_directory(folder)
            check("extra-everyone-grant-removed", private_acl_ok(folder, user_sid))
            cert_path, tls_key, thumbprint = certificate(folder, HOST, "gateway", fixture_leaf=True)
            rogue_cert, rogue_key, _ = certificate(folder, HOST, "rogue", fixture_leaf=False)
            permanent_key = secrets.token_bytes(32)
            alice_key = folder / "alice.key"
            alice_key.write_bytes(permanent_key)
            secure_file(alice_key)
            check("alice-key-private-acl", private_acl_ok(alice_key, user_sid))
            check("tls-key-private-acl", private_acl_ok(tls_key, user_sid))
            check("rogue-tls-key-private-acl", private_acl_ok(rogue_key, user_sid))
            sessions_path = folder / "sessions.json"
            session_store = WindowsSecuredSessions(sessions_path)
            capability = secrets.token_urlsafe(32)
            rogue_http_observer = base.RogueObserver()
            rogue_https_observer = base.RogueObserver()
            kwargs = chromium_kwargs()
            with base.running_server(base.make_backend(capability)) as backend, \
                 base.running_server(rogue_http_observer.handler()) as rogue_http, \
                 base.running_server(rogue_https_observer.handler(), cert=rogue_cert, key=rogue_key) as rogue_https:
                check("direct-backend-denied", base.raw_get(backend.server_port)[0] == 403)
                check("forged-backend-capability-denied", base.raw_get(backend.server_port,
                      {"X-MC-Fixture-Capability": "forged"})[0] == 403)
                with base.running_server(base.make_gateway(backend.server_port, capability, session_store, permanent_key),
                                         cert=cert_path, key=tls_key) as gateway:
                    origin = f"https://{HOST}:{gateway.server_port}"
                    with sync_playwright() as p:
                        # Negative control: no SPKI pin, no ignoreHTTPSErrors, no
                        # Chromium host override. This leaf is not yet trusted.
                        context = p.chromium.launch_persistent_context(str(folder / "untrusted-profile"), **kwargs)
                        try:
                            page = context.new_page()
                            try:
                                page.goto(origin, timeout=15000)
                                blocked = False
                            except BrowserError as exc:
                                blocked = certificate_rejection(exc)
                            check("untrusted-windows-certificate-rejected", blocked)
                        finally:
                            context.close()

                    trust_leaf(cert_path, thumbprint)
                    trusted = True
                    check("current-user-root-leaf-trust-present", trusted_subject(thumbprint) is not None)
                    from cryptography import x509
                    from cryptography.x509.oid import ExtendedKeyUsageOID
                    leaf = x509.load_pem_x509_certificate(cert_path.read_bytes())
                    bc = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
                    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                    check("fixture-leaf-is-not-ca", bc.ca is False and ExtendedKeyUsageOID.SERVER_AUTH in eku)
                    with sync_playwright() as p:
                        bob = p.chromium.launch_persistent_context(str(folder / "bob-browser-profile"), **kwargs)
                        try:
                            page = bob.new_page()
                            response = page.goto(origin + "/private", timeout=15000)
                            check("bob-browser-unauthenticated-denied", response is not None and response.status == 401)
                            page.goto(origin, timeout=15000)
                            code = page.evaluate("async () => (await fetch('/login',{method:'POST',body:'wrong'})).status")
                            check("bob-browser-invalid-key-denied", code == 401)
                        finally:
                            bob.close()
                        alice = p.chromium.launch_persistent_context(str(folder / "alice-browser-profile"), **kwargs)
                        try:
                            page = alice.new_page()
                            first = page.goto(origin, timeout=15000)
                            check("windows-trusted-certificate-browser-accepted", first is not None and first.status == 200)
                            page.locator("#key").set_input_files(str(alice_key))
                            page.wait_for_function("document.querySelector('#state').textContent === 'LOGGED_IN'", timeout=10000)
                            check("alice-key-file-login", page.evaluate("async () => (await fetch('/private')).status") == 200)
                            secure_file(sessions_path)
                            check("session-store-private-acl", private_acl_ok(sessions_path, user_sid))
                            check("session-store-excludes-key", permanent_key.hex() not in sessions_path.read_text(encoding="utf-8")
                                  and permanent_key.decode("latin1") not in sessions_path.read_text(encoding="utf-8"))
                            cookies = [c for c in alice.cookies() if c.get("name") == base.COOKIE_NAME]
                            check("secure-httponly-persistent-cookie", len(cookies) == 1 and
                                  cookies[0].get("secure") is True and cookies[0].get("httpOnly") is True and
                                  cookies[0].get("expires", -1) > time.time() + 86400)
                            check("javascript-cannot-read-session-cookie", base.COOKIE_NAME not in page.evaluate("document.cookie"))
                            check("localstorage-has-no-session", page.evaluate("localStorage.length") == 0)
                            check("positive-control-cookie-installed", page.evaluate("async () => (await fetch('/control')).status") == 200)
                            page.goto(f"http://{HOST}:{rogue_http.server_port}/steal", timeout=15000)
                            check("rogue-http-request-observed", bool(rogue_http_observer.observations))
                            check("rogue-http-control-cookie-received", any(rogue_http_observer.control_observations))
                            check("rogue-http-session-cookie-not-leaked", bool(rogue_http_observer.observations)
                                  and not any(rogue_http_observer.observations))
                            try:
                                page.goto(f"https://{HOST}:{rogue_https.server_port}/steal", timeout=12000)
                                rejected = False
                            except BrowserError as exc:
                                rejected = certificate_rejection(exc)
                            check("rogue-https-certificate-rejected", rejected and not rogue_https_observer.observations)
                        finally:
                            alice.close()
                # Real socket/gateway teardown and real Chromium profile close.
                # Persist the session file, reconstruct Python objects, restart
                # the HTTPS listener and reopen the browser profile.
                recovered_sessions = WindowsSecuredSessions(sessions_path)
                with base.running_server(base.make_gateway(backend.server_port, capability,
                                          recovered_sessions, permanent_key), cert=cert_path, key=tls_key) as restarted:
                    with sync_playwright() as p:
                        alice = p.chromium.launch_persistent_context(str(folder / "alice-browser-profile"), **kwargs)
                        try:
                            page = alice.new_page()
                            response = page.goto(f"https://{HOST}:{restarted.server_port}/private", timeout=15000)
                            check("browser-and-gateway-restart-preserves-session", response is not None and response.status == 200)
                            page.goto(f"https://{HOST}:{restarted.server_port}/", timeout=15000)
                            logged_out = page.evaluate("async () => (await fetch('/logout',{method:'POST'})).status")
                            denied = page.evaluate("async () => (await fetch('/private')).status")
                            check("revoked-session-denied", logged_out == 200 and denied == 401)
                            check("session-acl-survives-revocation", private_acl_ok(sessions_path, user_sid))
                        finally:
                            alice.close()
    finally:
        if trusted or pending_trust().exists():
            try:
                trust_cleanup_ok = cleanup_trust()
            except Exception as exc:
                result("trust-cleanup-verified", False, f"{type(exc).__name__}: {str(exc)[:180]}")
                trust_cleanup_ok = False
        else:
            trust_cleanup_ok = True

    results["trust-cleanup-verified"] = trust_cleanup_ok
    failed = [key for key, value in results.items() if not value]
    print("COOKIE_GATEWAY_WINDOWS SUMMARY " + json.dumps({
        "status": "PASS" if not failed else "FAIL", "failed": failed,
        "tests": len(results), "host": HOST,
        "hostname_resolution": "chromium-only (not Windows DNS)",
        "admin_required": False,
        "certificate_store": "CurrentUser/Root",
        "windows_reboot_observed": False,
        "actual_other_windows_user_observed": False,
        "production_mc_touched": False,
    }, sort_keys=True), flush=True)
    return not failed


def preflight() -> bool:
    """Fail closed on real dependencies, never demand DNS/hosts elevation."""
    require_windows()
    checks: dict[str, bool] = {}

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = result(name, ok, detail)

    check("non-elevated-windows-user", non_elevated())
    check("browser-only-hostname-mapping", True, f"{HOST}->127.0.0.1 (no hosts-file edit)")
    check("trust-journal-clear", not pending_trust().exists())
    try:
        import playwright.sync_api  # noqa: F401
        import cryptography  # noqa: F401
        chromium_kwargs()
        check("chromium-and-crypto-available", True)
    except Exception as exc:
        check("chromium-and-crypto-available", False, f"{type(exc).__name__}: {str(exc)[:100]}")
    try:
        from main_computer.slh.storage import secure_machine_state_dir, secure_private_state_file  # noqa: F401
        sid = caller_sid()
        check("windows-acl-authority-available", bool(re.fullmatch(r"S-1-\d+(?:-\d+)+", sid)))
    except Exception as exc:
        check("windows-acl-authority-available", False, f"{type(exc).__name__}: {str(exc)[:100]}")
    import shutil
    for exe in ("certutil.exe", "powershell.exe", "icacls.exe"):
        check("command-" + exe, shutil.which(exe) is not None)
    return all(checks.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "cleanup-trust", "run"], nargs="?", default="run")
    args = parser.parse_args(argv)
    try:
        if args.action == "preflight":
            return 0 if preflight() else 1
        if args.action == "cleanup-trust":
            return 0 if cleanup_trust() else 1
        return 0 if windows_native_smoke() else 1
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:500]}"
        print(f"COOKIE_GATEWAY_WINDOWS ERROR {detail}", file=sys.stderr, flush=True)
        print("COOKIE_GATEWAY_WINDOWS SUMMARY " + json.dumps({
            "status": "FAIL", "failed": ["exception"], "error": detail,
            "host": HOST, "hostname_resolution": "chromium-only (not Windows DNS)",
            "production_mc_touched": False,
        }, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
