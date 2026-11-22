"""Opt-in HTTPS MC proof login and /graphical redirect, without production cutover.

Run from MC repository root (ordinary, non-elevated Windows PowerShell):
    python -m tools.mcel_auth_bootstrap provision
    python -m tools.mcel_auth_bootstrap run
    python -m tools.mcel_auth_bootstrap status
    python -m tools.mcel_auth_bootstrap revoke-all

`run` opens a dedicated Chromium/Edge profile for https://mc.test:8875/auth/login.
No hosts-file edit, administrator permission or unsafe browser TLS flags.
The original http://127.0.0.1:8765 MC remains unchanged and UNSECURED.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from main_computer.auth_bootstrap import (
    BootstrapAuthority, BootstrapServer, DEFAULT_PORT, HOST, _load_json, _private_folder,
    ensure_windows_user_trust, generate_tls_certificate, provision, state_directory, proof_file_path,
)


def _find_chromium() -> str:
    candidates = [os.environ.get('MC_AUTH_BOOTSTRAP_BROWSER'), shutil.which('msedge'),
                  shutil.which('chrome'), shutil.which('chromium')]
    for parent in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'),
                   os.environ.get('LOCALAPPDATA')):
        if parent:
            for relative in ('Microsoft/Edge/Application/msedge.exe',
                             'Google/Chrome/Application/chrome.exe'):
                candidates.append(str(Path(parent) / relative))
    for item in candidates:
        if item and Path(item).is_file():
            return item
    raise RuntimeError('CHROMIUM_OR_EDGE_NOT_FOUND (set MC_AUTH_BOOTSTRAP_BROWSER)')


def _launch_browser(root: Path, port: int) -> None:
    browser = _find_chromium()
    profile = _private_folder(root / 'browser-profile')
    argv = [browser, f'--user-data-dir={profile}',
            '--host-resolver-rules=MAP mc.test 127.0.0.1, EXCLUDE localhost',
            '--no-first-run', '--no-default-browser-check',
            f'https://{HOST}:{port}/auth/login?next=%2Fgraphical']
    subprocess.Popen(argv, cwd=str(root), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('provision', 'run', 'status', 'revoke-all'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--state-dir', type=Path, default=None, help='Explicit private state directory (tests only)')
        if name == 'run':
            cmd.add_argument('--port', type=int, default=DEFAULT_PORT)
            cmd.add_argument('--backend-port', type=int, default=8765,
                             help='Legacy Viewport port for the login-gated dashboard proxy (default 8765)')
            cmd.add_argument('--no-browser', action='store_true',
                             help='Keep browser launch disabled (use a browser already mapping mc.test)')
    opts = parser.parse_args(argv)
    try:
        if os.name != 'nt' and opts.state_dir is None:
            raise RuntimeError('Windows required for real provisioning; Linux smoke uses --state-dir')
        root = opts.state_dir or state_directory()
        if opts.command == 'provision':
            path = provision(root)
            print('MC_AUTH_BOOTSTRAP provision PASS')
            print('MC_AUTH_BOOTSTRAP proof-file=' + str(path))
            print('MC_AUTH_BOOTSTRAP select-this-file-on-the-https-login-page')
        elif opts.command == 'status':
            if not (root / 'authority.json').is_file():
                print('MC_AUTH_BOOTSTRAP status NOT_PROVISIONED')
                return 1
            authority = BootstrapAuthority(root)
            print('MC_AUTH_BOOTSTRAP status PROVISIONED session-records=' +
                  str(len(authority.sessions.data)))
        elif opts.command == 'revoke-all':
            authority = BootstrapAuthority(root)
            authority.sessions.revoke_all()
            print('MC_AUTH_BOOTSTRAP sessions-revoked PASS')
        elif opts.command == 'run':
            if not 1024 <= opts.port <= 65535 or not 1 <= opts.backend_port <= 65535:
                raise ValueError('INVALID_PORT')
            authority = BootstrapAuthority(root)  # explicit provision required
            cert, key, thumb = generate_tls_certificate(root)
            ensure_windows_user_trust(cert, thumb)
            with BootstrapServer(('127.0.0.1', opts.port), authority, backend_port=opts.backend_port,
                                 cert=cert, key=key) as server:
                print(f'MC_AUTH_BOOTSTRAP READY https://{HOST}:{opts.port}/auth/login?next=%2Fgraphical', flush=True)
                print('MC_AUTH_BOOTSTRAP bootstrap-only; existing production HTTP routes are unchanged', flush=True)
                print('MC_AUTH_BOOTSTRAP proof-file=' + str(proof_file_path(root)), flush=True)
                if not opts.no_browser:
                    _launch_browser(root, opts.port)
                try:
                    server.serve_forever(poll_interval=0.2)
                except KeyboardInterrupt:
                    print('MC_AUTH_BOOTSTRAP stopped; sessions and trusted certificate retained')
        return 0
    except Exception as exc:
        print(f'MC_AUTH_BOOTSTRAP ERROR {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
