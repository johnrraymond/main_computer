#!/usr/bin/env python3
"""Opt-in real Chromium + MetaMask acceptance of the Worker application.

Generates its own disposable wallet if needed. Production code is untouched;
no mocked wallets, intercepted RPC, test funding, or personal wallet input.
Requires Node 20+, npm, a running local Main Computer web server.
"""
from __future__ import annotations

import argparse
import json
import io
import hashlib
import re
import tempfile
import zipfile
import stat
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen, Request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'tools' / 'worker_browser_acceptance'
DEFAULT_URL = 'http://127.0.0.1:8765/applications/worker'
REPORTS = ROOT / 'runtime' / 'reports' / 'worker-browser-acceptance'
WALLET_STATE = ROOT / 'runtime' / 'state' / 'worker-browser-acceptance'
WALLET_FILE = WALLET_STATE / 'wallet.json'
WALLET_LAST_RUN = WALLET_STATE / 'wallet-last-run.json'
PROFILE_DIR = WALLET_STATE / 'chromium-profile'
PROFILE_MARKER = WALLET_STATE / 'profile-wallet-address.txt'
METAMASK_DIR = WALLET_STATE / 'metamask-extension'
METAMASK_RELEASE_PROOF_NAME = '.main-computer-release-proof.json'
METAMASK_VERSION = '13.53.0'  # Pinned official MetaMask release; upgrades are explicit
METAMASK_RELEASE_API = f'https://api.github.com/repos/MetaMask/metamask-extension/releases/tags/v{METAMASK_VERSION}'


class AcceptanceError(RuntimeError):
    pass


def valid_worker_url(url: str) -> str:
    """Never hand test wallet access to a remote page by accident."""
    p = urlparse(url)
    if p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost') or p.username or p.password:
        raise AcceptanceError('Worker URL must be a local HTTP origin (127.0.0.1 or localhost)')
    if p.path.rstrip('/') != '/applications/worker' or p.query or p.fragment:
        raise AcceptanceError('Worker URL must refer to /applications/worker with no query or fragment')
    if p.port is None:
        raise AcceptanceError('Worker URL requires an explicit local port')
    return url


def commands(action: str, mode: str = 'wallet') -> list[list[str]]:
    if action == 'install':
        return [['npm', 'install', '--no-audit', '--no-fund'],
                ['npm', 'exec', '--', 'playwright', 'install', 'chromium']]
    if action == 'cache-wallet':
        return [['node', 'native_acceptance.mjs']]
    if action == 'rpc-probe':
        return [['node', 'native_acceptance.mjs']]
    if action == 'run':
        return [['node', 'native_acceptance.mjs']]
    raise AcceptanceError(f'Unsupported action: {action}')


def checked_env(action: str, env: dict[str, str]) -> None:
    if action in ('cache-wallet', 'run', 'rpc-probe') and not (PROJECT / 'node_modules').exists():
        raise AcceptanceError('Dependencies missing. Run: python tools/worker_browser_acceptance.py install')
    # Importing user-supplied seed phrases or passwords is intentionally unsupported.
    if env.get('MC_WORKER_TEST_MNEMONIC') or env.get('MC_WORKER_TEST_PASSWORD'):
        raise AcceptanceError('Remove legacy test-wallet secret environment variables; this harness creates its own wallet')


def execute(command: list[str], env: dict[str, str]) -> int:
    print('Running:', ' '.join(command), flush=True)
    executable = shutil.which(command[0]) or command[0]
    result = subprocess.run([executable, *command[1:]], cwd=PROJECT, env=env, check=False)
    if result.returncode:
        print(f'FAIL ({result.returncode}): {command[0]} exited nonzero', file=sys.stderr)
    return result.returncode


def wallet_address() -> str:
    """Only the public address is consumed by Python; Node owns the secrets."""
    try:
        address = json.loads(WALLET_FILE.read_text(encoding='utf-8'))['address']
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise AcceptanceError('Generated wallet is unreadable or incomplete') from exc
    if not isinstance(address, str) or len(address) != 42 or not address.startswith('0x'):
        raise AcceptanceError('Generated wallet has invalid address')
    return address.lower()


def prepare_profile(address: str) -> None:
    """Invalidate only the test-owned profile when the disposable wallet rotates."""
    if PROFILE_DIR.is_symlink():
        raise AcceptanceError('Refusing symlinked Chromium test profile')
    marker = PROFILE_MARKER.read_text(encoding='utf-8').strip().lower() if PROFILE_MARKER.exists() else ''
    if marker != address:
        if PROFILE_DIR.exists():
            shutil.rmtree(PROFILE_DIR)
        PROFILE_MARKER.unlink(missing_ok=True)


def provision_wallet(env: dict[str, str], *, fresh: bool = False) -> str:
    WALLET_STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    command = ['node', 'create_wallet.mjs'] + (['--rotate'] if fresh else [])
    if execute(command, env):
        raise AcceptanceError('Disposable wallet generation/validation failed')
    address = wallet_address()
    prepare_profile(address)
    return address


def _release_asset_url(asset: dict, expected_name: str) -> str:
    """Only accept the exact pinned-release download path on the official repo."""
    expected = (f'https://github.com/MetaMask/metamask-extension/releases/download/'
                f'v{METAMASK_VERSION}/{expected_name}')
    if asset.get('browser_download_url') != expected:
        raise AcceptanceError(f'MetaMask release asset {expected_name} URL did not match the pinned official repository')
    return expected


def _release_asset(release: dict, name: str) -> dict:
    matches = [a for a in release.get('assets', []) if a.get('name') == name]
    if len(matches) != 1:
        raise AcceptanceError(f'Official MetaMask release lacks unique expected asset {name}')
    _release_asset_url(matches[0], name)
    return matches[0]


def _published_sha256(checksums: bytes, filename: str) -> str:
    """Read the exact ZIP checksum from the official release's SHA256SUMS."""
    try:
        lines = checksums.decode('utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise AcceptanceError('Official MetaMask SHA256SUMS is not UTF-8') from exc
    matches = []
    for line in lines:
        match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+?)\s*', line)
        if match and match.group(2) == filename:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise AcceptanceError(f'Official MetaMask SHA256SUMS lacks unique checksum for {filename}')
    return matches[0]


def _validated_manifest(data: dict) -> str:
    """Chrome compatibility check; exact archive identity is verified by SHA256."""
    manifest_version = data.get('manifest_version')
    if manifest_version not in (2, 3) or isinstance(manifest_version, bool):
        raise AcceptanceError(f'Unsupported MetaMask Chrome manifest_version: {manifest_version!r}')
    version = data.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'\d+(?:\.\d+){1,3}', version):
        raise AcceptanceError(f'Invalid MetaMask Chrome manifest version: {version!r}')
    return version


def _check_cached_extension(manifest: Path) -> bool:
    """Only reuse a test installation with its original verified manifest."""
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
        version = _validated_manifest(data)
        proof = json.loads((METAMASK_DIR / METAMASK_RELEASE_PROOF_NAME).read_text(encoding='utf-8'))
        manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if (proof.get('releaseVersion') == METAMASK_VERSION and
            proof.get('manifestVersion') == version and
            proof.get('manifestSha256') == manifest_digest and
            re.fullmatch(r'[0-9a-f]{64}', proof.get('archiveSha256', ''))):
            return True
    except (OSError, ValueError, TypeError, AcceptanceError):
        pass
    raise AcceptanceError('Installed test MetaMask extension has no valid release proof or its manifest changed; refusing silent replacement')


def ensure_metamask_extension() -> Path:
    """Install the pinned official GitHub ZIP, verified against its SHA256SUMS.

    The internal Chrome manifest version need not exactly equal the release
    tag: the byte-for-byte published ZIP is the authority for release identity.
    Do not load unsigned substitutes, mirrors, personal extensions or profiles.
    """
    if METAMASK_DIR.is_symlink():
        raise AcceptanceError('Refusing symlinked MetaMask extension directory')
    manifest = METAMASK_DIR / 'manifest.json'
    if _check_cached_extension(manifest):
        return METAMASK_DIR
    if METAMASK_DIR.exists():
        raise AcceptanceError('Incomplete MetaMask extension directory; inspect before retrying')
    try:
        req = Request(METAMASK_RELEASE_API, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'main-computer-worker-browser-acceptance',
        })
        with urlopen(req, timeout=30) as response:
            release = json.load(response)
        if release.get('tag_name') != f'v{METAMASK_VERSION}':
            raise AcceptanceError('GitHub MetaMask release metadata tag differs from pinned version')
        asset_name = f'metamask-chrome-{METAMASK_VERSION}.zip'
        asset = _release_asset(release, asset_name)
        sums_asset = _release_asset(release, 'SHA256SUMS')
        with urlopen(Request(_release_asset_url(sums_asset, 'SHA256SUMS'),
                             headers={'User-Agent': 'main-computer-worker-browser-acceptance'}),
                     timeout=30) as response:
            checksums = response.read(131_073)
        if len(checksums) > 131_072:
            raise AcceptanceError('Official MetaMask SHA256SUMS exceeds size limit')
        pinned_sha = _published_sha256(checksums, asset_name)
        api_digest = asset.get('digest')
        if api_digest is not None and api_digest != f'sha256:{pinned_sha}':
            raise AcceptanceError('GitHub release asset digest disagrees with official SHA256SUMS')
        with urlopen(Request(_release_asset_url(asset, asset_name),
                             headers={'User-Agent': 'main-computer-worker-browser-acceptance'}),
                     timeout=120) as response:
            raw = response.read(120_000_001)
        if not raw or len(raw) > 120_000_000:
            raise AcceptanceError('Unexpected MetaMask extension archive size')
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != pinned_sha:
            raise AcceptanceError('Downloaded MetaMask archive SHA256 differs from official GitHub SHA256SUMS')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [x for x in archive.infolist() if not x.is_dir()]
            if len(members) > 25_000 or sum(x.file_size for x in members) > 350_000_000:
                raise AcceptanceError('MetaMask archive exceeds extraction limits')
            names = [Path(x.filename.replace('\\', '/')).as_posix() for x in members]
            manifest_names = [name for name in names if name == 'manifest.json' or name.endswith('/manifest.json')]
            if len(manifest_names) != 1:
                raise AcceptanceError('MetaMask archive has no unique extension manifest')
            prefix = manifest_names[0][:-len('manifest.json')]
            WALLET_STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix='metamask-stage-', dir=WALLET_STATE) as stage:
                base = Path(stage) / 'extension'
                base.mkdir()
                for member, name in zip(members, names):
                    if not name.startswith(prefix):
                        continue
                    relative = name[len(prefix):]
                    parts = Path(relative).parts
                    if not relative or any(part in ('', '..', '.') for part in parts) or Path(relative).is_absolute():
                        raise AcceptanceError('Unsafe MetaMask archive path')
                    if stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK:
                        raise AcceptanceError('MetaMask archive contains a symbolic link')
                    dest = base.joinpath(*parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, dest.open('wb') as target:
                        shutil.copyfileobj(source, target)
                real_manifest = base / 'manifest.json'
                data = json.loads(real_manifest.read_text(encoding='utf-8'))
                version = _validated_manifest(data)
                proof = {
                    'schema': 'main-computer.metamask-release-proof.v1',
                    'releaseVersion': METAMASK_VERSION,
                    'manifestVersion': version,
                    'archiveSha256': actual_sha,
                    'manifestSha256': hashlib.sha256(real_manifest.read_bytes()).hexdigest(),
                    'checksumSource': f'github:MetaMask/metamask-extension/releases/tag/v{METAMASK_VERSION}/SHA256SUMS',
                }
                (base / METAMASK_RELEASE_PROOF_NAME).write_text(json.dumps(proof, sort_keys=True) + '\n', encoding='utf-8')
                base.rename(METAMASK_DIR)
        if version != METAMASK_VERSION:
            print(f'Note: official MetaMask archive manifest version {version!r} differs from release tag '
                  f'{METAMASK_VERSION!r}; official SHA256 verified', flush=True)
        print('Installed official MetaMask test extension:', METAMASK_VERSION,
              'manifest:', version, 'sha256:', actual_sha, flush=True)
        return METAMASK_DIR
    except AcceptanceError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise AcceptanceError(f'Official MetaMask extension installation failed: {type(exc).__name__}: {exc}') from exc


def previous_wallet_run(address: str) -> bool:
    if not WALLET_LAST_RUN.exists():
        return False
    try:
        return json.loads(WALLET_LAST_RUN.read_text(encoding='utf-8')).get('address') == address
    except (ValueError, OSError):
        # Uncertain prior test state is not grounds for reusing a previously
        # authorized wallet in a fresh key-issuance test.
        return True


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'install', 'cache-wallet', 'rpc-probe', 'run'))
    parser.add_argument('--mode', choices=('wallet', 'worker'), default='wallet')
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--authorize-worker-offer', action='store_true',
                        help='Explicitly allow registering a real paid-worker offer on testnet')
    args = parser.parse_args(argv)
    url = valid_worker_url(args.url)
    if args.mode == 'worker' and args.action == 'run' and not args.authorize_worker_offer:
        raise AcceptanceError('Worker registration requires --authorize-worker-offer')
    if args.action == 'plan':
        print(json.dumps({'project': str(PROJECT), 'browser': 'native Playwright Chromium + MetaMask (no Synpress CLI)',
            'url': url, 'mode': args.mode, 'commands': {
              x: commands(x) for x in ('install', 'cache-wallet', 'rpc-probe', 'run')},
            'wallet_policy': 'generate disposable wallet if absent; fresh wallet for repeated signing tests',
            'production_code_changed': False, 'purchases_credits': False}, indent=2))
        return 0

    env = os.environ.copy()
    checked_env(args.action, env)
    if not shutil.which('node') or not shutil.which('npm'):
        raise AcceptanceError('Node.js >=20 and npm must be installed')
    version = subprocess.run(['node', '--version'], capture_output=True, text=True, check=True).stdout.strip()
    try:
        major = int(version.lstrip('v').split('.')[0])
    except ValueError as exc:
        raise AcceptanceError(f'Cannot parse Node version: {version}') from exc
    if major < 20:
        raise AcceptanceError(f'Node.js >=20 required; found {version}')

    if args.action == 'rpc-probe':
        # No new wallet or new profile. Reuse only the already verified disposable
        # MetaMask test profile. This is read-only and does not click Connect.
        if not WALLET_FILE.is_file() or not PROFILE_DIR.is_dir() or not PROFILE_MARKER.is_file():
            raise AcceptanceError('RPC probe needs an existing disposable wallet profile; run cache-wallet first')
        address = wallet_address()
        if PROFILE_MARKER.read_text(encoding='utf-8').strip().lower() != address:
            raise AcceptanceError('RPC probe refused mismatched disposable wallet profile')
    if args.action in ('run', 'rpc-probe'):
        try:
            with urlopen(url, timeout=8) as response:
                if response.status != 200:
                    raise AcceptanceError(f'Worker page returned HTTP {response.status}')
        except OSError as exc:
            raise AcceptanceError(f'Local Worker web server unavailable at {url}: {exc}') from exc

    REPORTS.mkdir(parents=True, exist_ok=True)
    env.update({
        'MC_WORKER_TEST_URL': url,
        'MC_WORKER_TEST_MODE': args.mode,
        'MC_WORKER_TEST_WALLET_FILE': str(WALLET_FILE),
        'MC_WORKER_TEST_REPORT_DIR': str(REPORTS),
        'MC_WORKER_TEST_OUTPUT_DIR': str(REPORTS / 'playwright'),
        'CI': '0',
    })
    if args.action == 'install':
        for command in commands('install'):
            if result := execute(command, env):
                return result
        return 0

    if args.action == 'rpc-probe':
        extension_path = ensure_metamask_extension()
        env.update({
            'MC_WORKER_TEST_EXTENSION_DIR': str(extension_path),
            'MC_WORKER_TEST_PROFILE_DIR': str(PROFILE_DIR),
            'MC_WORKER_TEST_PROFILE_MARKER': str(PROFILE_MARKER),
            'MC_WORKER_TEST_ACTION': 'rpc-probe',
        })
        return execute(commands('rpc-probe')[0], env)

    if args.action in ('cache-wallet', 'run'):
        # In wallet mode, repeat runs must exercise NEW key issuance, not
        # silently reuse a previously authorized multi-session key.
        fresh = (args.action == 'run' and args.mode == 'wallet' and
                 WALLET_FILE.exists() and previous_wallet_run(wallet_address()))
        address = provision_wallet(env, fresh=fresh)
        extension_path = ensure_metamask_extension()
        env.update({
            'MC_WORKER_TEST_EXTENSION_DIR': str(extension_path),
            'MC_WORKER_TEST_PROFILE_DIR': str(PROFILE_DIR),
            'MC_WORKER_TEST_PROFILE_MARKER': str(PROFILE_MARKER),
            'MC_WORKER_TEST_ACTION': args.action,
        })
        if args.action == 'cache-wallet':
            if result := execute(commands('cache-wallet')[0], env):
                return result
            print('Disposable MetaMask account ready:', address)
            return 0

        if args.mode == 'wallet':
            WALLET_LAST_RUN.write_text(json.dumps({'address': address}) + '\n', encoding='utf-8')
        # Never accept an old PASS report if the browser run crashed.
        (REPORTS / 'acceptance.json').unlink(missing_ok=True)
        result = execute(commands('run')[0], env)
        if result:
            return result
        report = REPORTS / 'acceptance.json'
        if not report.is_file():
            raise AcceptanceError('Playwright exited without writing acceptance evidence')
        summary = json.loads(report.read_text(encoding='utf-8'))
        print('Acceptance evidence:', report)
        print('Result:', 'PASS' if summary.get('ok') is True else 'FAIL')
        return 0 if summary.get('ok') is True else 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (AcceptanceError, OSError, subprocess.CalledProcessError) as exc:
        print(f'WORKER_BROWSER_ACCEPTANCE_REFUSED: {exc}', file=sys.stderr)
        raise SystemExit(2)
