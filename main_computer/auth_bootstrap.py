"""Opt-in MC authentication bootstrap. NOT the production ingress cutover.

A dedicated TLS listener proves the real /graphical file-picker -> HMAC challenge
-> durable, revocable Secure/HttpOnly browser session. Other existing MC services
are deliberately unchanged. Never expose this authentication flow over HTTP.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import ssl
from contextlib import contextmanager
from functools import lru_cache
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

# The incumbent mc.test origin remains unchanged until candidate activation.
HARDENED = os.environ.get('MAIN_COMPUTER_HARDENED_CANDIDATE') == '1'
PRIMARY_OWNER = os.environ.get('MAIN_COMPUTER_PRIMARY_OWNER', 'dev').strip().lower()
if HARDENED and PRIMARY_OWNER not in {'dev', 'unleashed'}:
    raise RuntimeError('HARDENED_PRIMARY_OWNER_MUST_BE_DEV_OR_UNLEASHED')
HOST = (PRIMARY_OWNER + '.main-computer.localhost') if HARDENED else 'mc.test'

DEFAULT_PORT = 8875
COOKIE = '__Host-MC-Session'
SESSION_AGE = 180 * 24 * 60 * 60
CHALLENGE_AGE = 120
PREFIX = b'MC-AUTH-BOOTSTRAP-V1\n'
PROOF_NAME = 'AAA_AUTH_MAIN_COMPUTER.json'
PROOF_TYPE = 'mc-proof-v2'
PROOF_LINK_VERSION = 2
AUTHORITY_NAME = 'authority.json'
SESSION_NAME = 'sessions.json'
PROOF_LINK_NAME = 'proof-link.json'
PROOF_LOCK_NAME = '.proof-link.lock'


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > 512 or not re.fullmatch('[A-Za-z0-9_-]+', text):
        raise ValueError('invalid proof encoding')
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def state_directory() -> Path:
    """Production state must be owned by the Windows account running MC."""
    if os.name != 'nt' or not os.getenv('LOCALAPPDATA'):
        raise RuntimeError('Windows LOCALAPPDATA is required; tests pass an explicit temporary state directory')
    return Path(os.environ['LOCALAPPDATA']) / 'MainComputer' / 'AuthBootstrap'


def proof_file_path(root: Path, *, profile_root: Path | None = None) -> Path:
    """Put the MC authentication proof in the Windows folder shown by file pickers.

    The permanent authority, sessions and TLS secrets remain in LOCALAPPDATA.
    Explicit test-state directories NEVER write into the real Windows profile.
    """
    root = Path(root)
    if profile_root is not None:
        return Path(profile_root) / PROOF_NAME
    if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
        if root.resolve(strict=False) == state_directory().resolve(strict=False):
            home = Path.home()
            if not home.is_dir():
                raise RuntimeError('MC_WINDOWS_PROFILE_DIRECTORY_NOT_FOUND')
            return home / PROOF_NAME
    return root / PROOF_NAME


def _verify_proof_matches_authority(proof: Path, authority: dict) -> None:
    """Never replace an existing proof with different key material."""
    record = _load_json(proof)
    if record.get('type') != PROOF_TYPE:
        raise RuntimeError('MC_PROOF_TYPE_MISMATCH')
    try:
        observed = _unb64(record['key_b64'])
        expected = _unb64(authority['hmac_key'])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('MC_PROOF_INVALID') from exc
    if len(expected) != 32 or not hmac.compare_digest(observed, expected):
        raise RuntimeError('MC_PROOF_AUTHORITY_MISMATCH')


def _discard_superseded_proofs(proof: Path, authority_key: bytes) -> None:
    """Discard obsolete copies of *this* authority's proof from the picker folder.

    Only a regular file with an obsolete schema and the exact same key qualifies.
    Never act on another user's files, a link, or an unrelated JSON document.
    """
    for candidate in proof.parent.glob('AAA_AUTH_*_MAIN_COMPUTER.json'):
        if candidate == proof or candidate.is_symlink() or (
                hasattr(candidate, 'is_junction') and candidate.is_junction()):
            continue
        try:
            record = _load_json(candidate)
            if record.get('type') == PROOF_TYPE:
                continue
            old = _unb64(record.get('key_b64'))
            if len(old) == 32 and hmac.compare_digest(old, authority_key):
                candidate.unlink()
        except (OSError, ValueError, KeyError, TypeError, PermissionError):
            # Refuse to remove anything not positively identified as ours.
            continue


@lru_cache(maxsize=1)
def _sid() -> str:
    from main_computer.slh.storage import _windows_caller_sid
    return _windows_caller_sid()


def _command(args: list[str], *, timeout: int = 25) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def _protect(path: Path, *, directory: bool) -> None:
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise PermissionError('AUTH_BOOTSTRAP_REFUSES_REPARSE_POINT')
    if os.name != 'nt':
        path.chmod(0o700 if directory else 0o600)
        return
    owner = _sid()
    # Read the actual current DACL every time. Do not cache file ACL decisions:
    # permissions can change while MC is running. Unknown descriptors fall
    # through to the existing full repair + independent verification path.
    from main_computer.auth_windows_acl import already_private
    try:
        if already_private(path, owner, directory=directory):
            return
    except (OSError, ValueError, AttributeError, ImportError):
        pass
    # Never inherit a broad parent DACL. Remove *all* extraneous explicit ACEs,
    # including arbitrary individual Windows accounts (not only Everyone/Users).
    ps = ("$ErrorActionPreference='Stop';" 
          "$a=Get-Acl -LiteralPath $env:MC_BOOTSTRAP_ACL_TARGET;" 
          "$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]) "
          "| ForEach-Object {$_.IdentityReference.Value}")
    env = dict(os.environ, MC_BOOTSTRAP_ACL_TARGET=str(path))
    report = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps],
                            env=env, capture_output=True, text=True, check=False, timeout=25)
    if report.returncode:
        raise PermissionError('AUTH_BOOTSTRAP_ACL_INSPECTION_FAILED')
    allowed = {owner, 'S-1-5-18', 'S-1-5-32-544'}
    for sid in set(report.stdout.splitlines()) - allowed:
        sid = sid.strip()
        if not re.fullmatch(r'S-1-\d+(?:-\d+)+', sid):
            raise PermissionError('AUTH_BOOTSTRAP_UNKNOWN_ACL_PRINCIPAL')
        removed = _command(['icacls.exe', str(path), '/remove', '*' + sid])
        if removed.returncode:
            raise PermissionError('AUTH_BOOTSTRAP_ACL_REMOVE_FAILED')
    suffix = '(OI)(CI)F' if directory else 'F'
    changed = _command(['icacls.exe', str(path), '/inheritance:r', '/grant:r',
                        f'*{owner}:{suffix}', f'*S-1-5-18:{suffix}', f'*S-1-5-32-544:{suffix}'])
    if changed.returncode:
        raise PermissionError('AUTH_BOOTSTRAP_ACL_APPLY_FAILED')
    # Inspect the final DACL; never certify a file with another account's ACE.
    report = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "$ErrorActionPreference='Stop';$a=Get-Acl -LiteralPath $env:MC_BOOTSTRAP_ACL_TARGET;"
        "if (-not $a.AreAccessRulesProtected) {exit 5};"
        "$rules=@($a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]));"
        "foreach($r in $rules){if($r.IsInherited -or $r.AccessControlType -ne 'Allow'){exit 6};"
        "Write-Output $r.IdentityReference.Value}"],
        env=env, capture_output=True, text=True, check=False, timeout=25)
    if report.returncode or set(report.stdout.splitlines()) != allowed:
        raise PermissionError('AUTH_BOOTSTRAP_ACL_NOT_PRIVATE')


def _private_folder(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        raise PermissionError('AUTH_BOOTSTRAP_REFUSES_REPARSE_POINT')
    root = root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _protect(root, directory=True)
    return root


def _write_new_private(path: Path, data: bytes) -> None:
    with path.open('xb') as output:
        output.write(data)
    _protect(path, directory=False)


def _replace_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + '.tmp-' + secrets.token_hex(6))
    try:
        _write_new_private(tmp, data)
        os.replace(tmp, path)
        _protect(path, directory=False)
    finally:
        if tmp.exists():
            tmp.unlink()


def _load_json(path: Path) -> dict:
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise PermissionError('AUTH_BOOTSTRAP_REFUSES_REPARSE_POINT')
    record = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(record, dict):
        raise ValueError('invalid bootstrap state')
    return record


def provision(root: Path) -> Path:
    """Provision or verify the current MC proof; never preserve older sessions.

    A prior proof filename/schema is not migrated. Existing private authority
    material can issue the new proof, but the versioned link clears all prior
    sessions before the proof becomes usable. Normal subsequent startups are
    idempotent and retain the current authenticated sessions.
    """
    root = _private_folder(root)
    authority_path = root / AUTHORITY_NAME
    proof = proof_file_path(root)
    if not authority_path.exists():
        if proof.exists() or proof.is_symlink() or (root / PROOF_LINK_NAME).exists():
            raise RuntimeError('INCOMPLETE_BOOTSTRAP_PROVISION; authority absent')
        secret = secrets.token_bytes(32)
        _write_new_private(authority_path, json.dumps({
            'version': 1, 'hmac_key': _b64(secret)
        }).encode())
        _write_new_private(proof, json.dumps({
            'type': PROOF_TYPE, 'key_b64': _b64(secret)
        }, indent=2).encode())
        # First-install link creation happens when the authority starts,
        # before it begins accepting HTTPS requests.
        return proof
    else:
        if not authority_path.is_file():
            raise RuntimeError('AUTH_BOOTSTRAP_AUTHORITY_NOT_A_FILE')
        _protect(authority_path, directory=False)
        authority_record = _load_json(authority_path)
        if authority_record.get('version') != 1:
            raise RuntimeError('INVALID_EXISTING_AUTHORITY')
        try:
            secret = _unb64(authority_record['hmac_key'])
        except (KeyError, ValueError, TypeError) as exc:
            raise RuntimeError('INVALID_EXISTING_AUTHORITY') from exc
        if len(secret) != 32:
            raise RuntimeError('INVALID_EXISTING_AUTHORITY')
        if proof.exists():
            if not proof.is_file():
                raise RuntimeError('MC_PROOF_PATH_NOT_A_FILE')
            _protect(proof, directory=False)
            _verify_proof_matches_authority(proof, authority_record)
    store = SessionStore(root, secret)
    if not store.proof_current() or not proof.is_file():
        raise RuntimeError('MC_PROOF_UNAVAILABLE')
    _verify_proof_matches_authority(proof, _load_json(authority_path))
    _discard_superseded_proofs(proof, secret)
    # Refresh the picker sorting timestamp, never the permanent key.
    os.utime(proof, None)
    return proof


def _proof_fingerprint(path: Path, authority_key: bytes) -> str | None:
    """Return a stable proof identity only for the current permanent key.

    Modification time is excluded because provisioning intentionally refreshes
    it for date-sorted file pickers. Device/inode and contents detect replacement
    even when a copied file contains the same secret.
    """
    try:
        if not path.is_file():
            return None
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            return None
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            return None
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get('type') != PROOF_TYPE:
            return None
        key = _unb64(record['key_b64'])
        if len(key) != 32 or not hmac.compare_digest(key, authority_key):
            return None
        identity = f'{after.st_dev}:{after.st_ino}:'.encode('ascii')
        return hashlib.sha256(identity + raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, PermissionError):
        return None


def _acquire_windows_proof_lock(fd: int, *, lock_module=None,
                                wait_seconds: float = 60.0,
                                retry_seconds: float = 0.01) -> None:
    """Wait for the *real* Windows cross-process lock without CRT's 10-try limit.

    The C runtime's LK_LOCK reports EDEADLOCK after ten unsuccessful attempts,
    even when a different supervised MC process is simply committing private
    ACL-protected session state. LK_NBLCK plus our own bounded retry retains
    exclusive locking; a stuck holder fails closed with an explicit diagnostic.
    No state is read or modified unless the lock has actually been acquired.
    """
    if lock_module is None:
        import msvcrt
        lock_module = msvcrt
    busy_errnos = {
        errno.EACCES, errno.EAGAIN, errno.EDEADLK,
        getattr(errno, 'EDEADLOCK', errno.EDEADLK),
        36,  # Windows CRT EDEADLOCK, even when tests run on another platform.
    }
    started = time.monotonic()
    next_notice = 5.0
    had_contention = False
    while True:
        try:
            # Nonblocking system lock. NEVER proceed unless this succeeds.
            lock_module.locking(fd, lock_module.LK_NBLCK, 1)
            if had_contention:
                print(f'MC_AUTH_BOOTSTRAP proof-session-lock ACQUIRED '
                      f'waited={time.monotonic() - started:.1f}s', flush=True)
            return
        except OSError as exc:
            if exc.errno not in busy_errnos and getattr(exc, 'winerror', None) not in (32, 33):
                raise  # Invalid descriptor/argument, not lock contention.
            had_contention = True
            elapsed = time.monotonic() - started
            if elapsed >= wait_seconds:
                raise TimeoutError(
                    f'AUTH_BOOTSTRAP_PROOF_LOCK_TIMEOUT waited={elapsed:.1f}s '
                    f'original_errno={exc.errno}; another MC process may still hold '
                    'the proof/session lock') from exc
            if elapsed >= next_notice:
                print(f'MC_AUTH_BOOTSTRAP proof-session-lock WAITING '
                      f'waited={elapsed:.1f}s; authentication remains locked', flush=True)
                next_notice += 5.0
            time.sleep(min(retry_seconds, max(0.0, wait_seconds - elapsed)))


class SessionStore:
    """Durable sessions bound to the *same physical proof* and its generation.

    A missing/replaced/invalid AAA file rotates the proof generation and clears
    sessions. Its restoration does not resurrect earlier cookies. A format
    upgrade never imports previous session tokens; a fresh login is required.
    """

    def __init__(self, root: Path, authority_key: bytes):
        self.root = Path(root)
        self.path = self.root / SESSION_NAME
        self.link_path = self.root / PROOF_LINK_NAME
        self.proof = proof_file_path(self.root)
        self.key = authority_key
        self.lock = threading.RLock()
        self.lock_path = self.root / PROOF_LOCK_NAME
        try:
            _write_new_private(self.lock_path, b'0')
        except FileExistsError:
            pass
        _protect(self.lock_path, directory=False)
        if self.path.exists():
            _protect(self.path, directory=False)
        if self.link_path.exists():
            _protect(self.link_path, directory=False)
        self.data = _load_json(self.path) if self.path.exists() else {}

    @contextmanager
    def _exclusive(self):
        """Serialize proof reconciliation and session changes across CLI/server."""
        if self.lock_path.is_symlink():
            raise PermissionError('AUTH_BOOTSTRAP_REFUSES_REPARSE_POINT')
        with self.lock_path.open('r+b') as handle:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                _acquire_windows_proof_lock(handle.fileno(), lock_module=msvcrt)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _refresh(self) -> None:
        # A CLI revocation runs in a DIFFERENT process from the resident HTTPS
        # service. Always reread before authorizing or updating sessions.
        if not self.path.is_file():
            self.data = {}
            return
        try:
            self.data = _load_json(self.path)
        except (OSError, ValueError):
            self.data = {}  # inaccessible/corrupt state must fail closed

    def _save(self) -> None:
        _replace_private(self.path, json.dumps(self.data, separators=(',', ':')).encode())

    def _reissue_missing_proof_locked(self) -> str | None:
        """Reissue a deleted picker file from the existing private MC key.

        Caller must hold the session lock and the cross-process proof lock.
        Revocation has already been committed before this is called. No HTTP
        caller can provide a key, path, or identity to this operation; the
        file can only be created by the MC-owned MC process.
        """
        if (self.proof.exists() or self.proof.is_symlink()
                or (hasattr(self.proof, 'is_junction') and self.proof.is_junction())):
            return None  # Never overwrite an existing or suspicious proof.
        try:
            authority_path = self.root / AUTHORITY_NAME
            record = _load_json(authority_path)
            if record.get('version') != 1:
                return None
            private_key = _unb64(record['hmac_key'])
            if len(private_key) != 32 or not hmac.compare_digest(private_key, self.key):
                return None
            payload = json.dumps({'type': PROOF_TYPE,
                                  'key_b64': _b64(private_key)}, indent=2).encode()
            _write_new_private(self.proof, payload)
            observed = _proof_fingerprint(self.proof, self.key)
            if observed is None:
                raise RuntimeError('REISSUED_PROOF_VERIFICATION_FAILED')
            generation = secrets.token_hex(16)
            _replace_private(self.link_path, json.dumps({
                'version': PROOF_LINK_VERSION, 'generation': generation,
                'status': 'active', 'fingerprint': observed,
                'reissued_reauth_required': True,
            }, separators=(',', ':')).encode())
            self.data = {}
            self._save()
            print('MC_AUTH_BOOTSTRAP AAA_AUTO_REISSUED old-sessions-revoked '
                  'reauthentication-required', flush=True)
            return generation
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            # Do not create a new identity, restore a saved session or accept
            # a corrupted existing proof. The service remains fail-closed.
            print(f'MC_AUTH_BOOTSTRAP WARNING AAA_AUTO_REISSUE_FAILED '
                  f'reason={type(exc).__name__}', flush=True)
            return None

    def _sync_proof(self) -> str | None:
        """Called under the cross-process lock; return the active generation."""
        observed = _proof_fingerprint(self.proof, self.key)
        try:
            link = _load_json(self.link_path) if self.link_path.exists() else None
        except (OSError, ValueError):
            # Corrupt state must not silently recreate a valid authority.
            return None
        if link is not None and (link.get('version') != PROOF_LINK_VERSION or not isinstance(link.get('generation'), str)):
            if link.get('version') != 1 or not isinstance(link.get('generation'), str):
                return None  # Unknown or corrupt state: never reset it silently.
            # Intentional breaking rename: refuse every previous session.
            # Reconcile the new proof filename using the unchanged private key.
            link = {'version': PROOF_LINK_VERSION, 'generation': secrets.token_hex(16),
                    'status': 'active' if observed is not None else
                              ('missing' if not self.proof.exists() else 'invalid'),
                    'fingerprint': observed}
            _replace_private(self.link_path, json.dumps(link, separators=(',', ':')).encode())
            self.data = {}
            self._save()
            if link['status'] == 'missing':
                return self._reissue_missing_proof_locked()
            return link['generation'] if link['status'] == 'active' else None
        if link is None:
            if observed is None:
                # No linked generation yet: do not migrate any legacy sessions
                # after the visible proof has been deleted.
                if (not self.proof.exists() and not self.proof.is_symlink()
                        and not (hasattr(self.proof, 'is_junction') and self.proof.is_junction())):
                    self.data = {}
                    self._save()
                    return self._reissue_missing_proof_locked()
                return None
            generation = secrets.token_hex(16)
            _replace_private(self.link_path, json.dumps({
                'version': PROOF_LINK_VERSION, 'generation': generation,
                'status': 'active', 'fingerprint': observed,
            }, separators=(',', ':')).encode())
            # The renamed identity requires a new login. Never import old
            # timestamp-only sessions into a new proof generation.
            self.data = {}
            self._save()
            return generation
        if link.get('status') == 'active' and observed is not None and link.get('fingerprint') == observed:
            return link['generation']
        # Missing, changed, or corrupt proof: invalidate ALL earlier sessions.
        # Even restoring identical secret bytes later creates a new generation.
        status = 'missing' if not self.proof.exists() else 'invalid'
        if observed is not None:
            status = 'active'
        if link.get('status') != status or link.get('fingerprint') != observed:
            updated = {'version': PROOF_LINK_VERSION, 'generation': secrets.token_hex(16),
                       'status': status, 'fingerprint': observed}
            _replace_private(self.link_path, json.dumps(updated, separators=(',', ':')).encode())
            self.data = {}
            self._save()
            if status == 'missing':
                return self._reissue_missing_proof_locked()
            return updated['generation'] if status == 'active' else None
        if status == 'missing':
            # A previous automatic recovery may have failed temporarily.
            # Retry on the next observed request without resurrecting sessions.
            return self._reissue_missing_proof_locked()
        return None

    def reissue_pending(self) -> bool:
        """Indicate why a new login is required, without exposing any key."""
        with self.lock, self._exclusive():
            try:
                link = _load_json(self.link_path)
            except (OSError, ValueError):
                return False
            return (link.get('status') == 'active'
                    and link.get('reissued_reauth_required') is True)

    def current_generation(self) -> str | None:
        with self.lock, self._exclusive():
            return self._sync_proof()

    def proof_current(self) -> bool:
        return self.current_generation() is not None

    def issue(self, *, expected_generation: str | None = None) -> str:
        with self.lock, self._exclusive():
            generation = self._sync_proof()
            if generation is None:
                raise RuntimeError('MC_PROOF_UNAVAILABLE')
            # A challenge signed before deletion must not issue a session
            # after automatic reissue of the *same* permanent secret.
            if expected_generation is not None and generation != expected_generation:
                raise RuntimeError('CHALLENGE_PROOF_GENERATION_CHANGED')
            token = secrets.token_urlsafe(32)
            self._refresh()
            now = int(time.time())
            self.data = {
                key: entry for key, entry in self.data.items()
                if isinstance(entry, dict) and int(entry.get('until', 0)) > now
                and entry.get('generation') == generation
            }
            self.data[hashlib.sha256(token.encode()).hexdigest()] = {
                'until': now + SESSION_AGE, 'generation': generation,
            }
            self._save()
            # The required reauthentication has now succeeded. Do not keep
            # claiming that a new, authenticated browser needs to log in.
            link = _load_json(self.link_path)
            if link.get('generation') == generation and link.get('reissued_reauth_required'):
                link['reissued_reauth_required'] = False
                _replace_private(self.link_path, json.dumps(link, separators=(',', ':')).encode())
            return token

    def valid(self, token: str) -> bool:
        if not isinstance(token, str) or not (32 <= len(token) <= 128):
            # Still reconcile deletion, including when Bob submits no cookie.
            self.proof_current()
            return False
        with self.lock, self._exclusive():
            generation = self._sync_proof()
            if generation is None:
                return False
            self._refresh()
            entry = self.data.get(hashlib.sha256(token.encode()).hexdigest())
            return (isinstance(entry, dict)
                    and entry.get('generation') == generation
                    and isinstance(entry.get('until'), int)
                    and entry['until'] > time.time())

    def revoke(self, token: str) -> None:
        with self.lock, self._exclusive():
            self._sync_proof()
            self._refresh()
            self.data.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            self._save()

    def revoke_all(self) -> None:
        with self.lock, self._exclusive():
            self._sync_proof()
            self.data = {}
            self._save()


def recover_deleted_proof_locally(root: Path) -> tuple[Path, bool]:
    """Trusted local startup/open reconciliation; live HTTPS uses the same rule.

    Only a genuinely missing proof can be reissued, from the unchanged
    private authority. The shared proof lock commits session revocation before
    the replacement file is created. Existing, invalid proofs are never reset.
    """
    root = _private_folder(root)
    proof = proof_file_path(root)
    if proof.is_symlink() or (hasattr(proof, 'is_junction') and proof.is_junction()):
        raise PermissionError('AUTH_BOOTSTRAP_REFUSES_REPARSE_POINT')
    if proof.exists():
        authority = _load_json(root / AUTHORITY_NAME)
        _verify_proof_matches_authority(proof, authority)
        return proof, False
    authority = _load_json(root / AUTHORITY_NAME)
    if authority.get('version') != 1 or len(_unb64(authority['hmac_key'])) != 32:
        raise RuntimeError('AUTH_BOOTSTRAP_AUTHORITY_INVALID')
    store = SessionStore(root, _unb64(authority['hmac_key']))
    with store.lock, store._exclusive():
        if proof.is_file():
            _verify_proof_matches_authority(proof, authority)
            return proof, False
        if store._sync_proof() is None or not proof.is_file():
            raise RuntimeError('AUTH_BOOTSTRAP_PROOF_REISSUE_FAILED')
        return proof, True

class BootstrapAuthority:
    def __init__(self, root: Path):
        self.root = _private_folder(root)
        _protect(self.root / AUTHORITY_NAME, directory=False)
        key = _unb64(_load_json(self.root / AUTHORITY_NAME)['hmac_key'])
        if len(key) != 32:
            raise ValueError('INVALID_AUTHORITY_KEY')
        self.key = key
        self.sessions = SessionStore(self.root, self.key)
        # Reconcile the proof generation before the HTTPS listener accepts its
        # first request. On Windows, initial reconciliation writes ACL-protected
        # state via external utilities; doing that inside an HTTP handler can
        # exceed client deadlines and leave its file lock open during teardown.
        # A genuinely missing proof is reissued from the original protected
        # authority after sessions are revoked. An existing invalid proof is
        # never overwritten; both cases keep the login page available.
        self.sessions.proof_current()
        self.challenges: dict[str, tuple[str, float, str]] = {}
        self.lock = threading.RLock()

    @staticmethod
    def message(challenge_id: str, nonce: str) -> bytes:
        return PREFIX + challenge_id.encode('ascii') + b'\n' + nonce.encode('ascii') + b'\n'

    def challenge(self) -> dict:
        generation = self.sessions.current_generation()
        if generation is None:
            raise RuntimeError('MC_PROOF_UNAVAILABLE')
        now = time.time()
        with self.lock:
            self.challenges = {cid: value for cid, value in self.challenges.items() if value[1] > now}
            if len(self.challenges) >= 128:
                raise RuntimeError('TOO_MANY_PENDING_CHALLENGES')
            cid, nonce = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
            self.challenges[cid] = nonce, now + CHALLENGE_AGE, generation
        return {'challenge_id': cid, 'nonce': nonce, 'algorithm': 'HMAC-SHA256',
                'expires_in': CHALLENGE_AGE}

    def verify(self, cid: str, signature: str) -> str | None:
        if not isinstance(cid, str) or not isinstance(signature, str):
            return None
        with self.lock:
            item = self.challenges.pop(cid, None)  # single-use, including failed attempts
        if not item or item[1] <= time.time():
            return None
        try:
            received = _unb64(signature)
        except (ValueError, TypeError):
            return None
        expected = hmac.new(self.key, self.message(cid, item[0]), hashlib.sha256).digest()
        if not hmac.compare_digest(received, expected):
            return None
        try:
            return self.sessions.issue(expected_generation=item[2])
        except RuntimeError:
            return None


def generate_tls_certificate(root: Path, hostname: str = HOST) -> tuple[Path, Path, str]:
    """Private self-signed non-CA TLS leaf; persistent until explicit replacement."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    root = _private_folder(root)
    certfile, keyfile = (root / 'tls-cert-localhost.pem', root / 'tls-key-localhost.pem') if HARDENED else (root / 'tls-cert.pem', root / 'tls-key.pem')
    if certfile.exists() and keyfile.exists():
        _protect(keyfile, directory=False)
        existing = x509.load_pem_x509_certificate(certfile.read_bytes())
        if HARDENED:
            from main_computer.auth_hostnames import HOSTS
            observed = set(existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
            if observed != set(HOSTS.values()):
                raise RuntimeError('HARDENED_TLS_SAN_MISMATCH_REFUSE_STALE_CERT')
        der = existing.public_bytes(serialization.Encoding.DER)
        return certfile, keyfile, hashlib.sha1(der).hexdigest().upper()
    if certfile.exists() or keyfile.exists():
        raise RuntimeError('INCOMPLETE_TLS_CERT_PAIR')
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Main Computer Authentication Bootstrap')])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=730))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in (
            __import__('main_computer.auth_hostnames', fromlist=['HOSTS']).HOSTS.values() if HARDENED else [hostname])]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(private, hashes.SHA256()))
    _write_new_private(keyfile, private.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    _write_new_private(certfile, cert.public_bytes(serialization.Encoding.PEM))
    thumb = hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return certfile, keyfile, thumb


def ensure_windows_user_trust(certfile: Path, thumb: str) -> None:
    if os.name != 'nt':
        return  # Linux smoke supplies the certificate explicitly to ssl.create_default_context.
    check = _command(['certutil.exe', '-user', '-store', 'Root', thumb])
    if check.returncode == 0 and thumb.lower() in (check.stdout + check.stderr).lower():
        return
    result = _command(['certutil.exe', '-user', '-addstore', 'Root', str(certfile)])
    if result.returncode:
        raise RuntimeError('CURRENT_USER_CERT_TRUST_FAILED: ' + (result.stderr or result.stdout)[:220])
    check = _command(['certutil.exe', '-user', '-store', 'Root', thumb])
    if check.returncode or thumb.lower() not in (check.stdout + check.stderr).lower():
        raise RuntimeError('CURRENT_USER_CERT_TRUST_NOT_VERIFIED')


class BootstrapServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], authority: BootstrapAuthority,
                 *, backend_port: int | None = None, cert: Path | None = None, key: Path | None = None,
                 isolated_backend_ports: dict[str, int] | None = None):
        if address[0] != '127.0.0.1':
            raise ValueError('BOOTSTRAP_MUST_BIND_NUMERIC_LOOPBACK_ONLY')
        self.authority = authority
        self.backend_port = backend_port
        self.isolated_backend_ports = isolated_backend_ports
        self.handoff_lock = threading.RLock()
        self.handoff_tickets = {}
        super().__init__(address, BootstrapHandler)
        if cert is not None and key is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(cert), str(key))
            self.socket = context.wrap_socket(self.socket, server_side=True)
        elif os.environ.get('MC_AUTH_BOOTSTRAP_TEST_HTTP') != '1':
            self.server_close()
            raise RuntimeError('BOOTSTRAP_REQUIRES_HTTPS')


class BootstrapHandler(BaseHTTPRequestHandler):
    server: BootstrapServer
    protocol_version = 'HTTP/1.1'

    def log_message(self, format: str, *args) -> None:
        # Never log full request headers, cookies, submitted signatures, or secrets.
        return

    def _send(self, code: int, payload: bytes = b'', *, kind: str = 'application/json; charset=utf-8',
              cookie: str | None = None, csp: str | None = None) -> None:
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.send_header('Cache-Control', 'no-store')
        timing = getattr(self, '_proxy_timing', None)
        if timing is not None:
            self.send_header('Server-Timing', timing)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if csp:
            self.send_header('Content-Security-Policy', csp)
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if payload:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
                # A client may close its TLS connection before receiving a
                # completed response. Do not emit a misleading server crash.
                pass

    def _json(self, code: int, obj: dict, *, cookie: str | None = None) -> None:
        self._send(code, json.dumps(obj, separators=(',', ':')).encode(), cookie=cookie)

    def _host_ok(self) -> bool:
        if HARDENED:
            from main_computer.auth_hostnames import HOSTS
            from main_computer.auth_hostnames import active_auth_hosts
            return self.headers.get('Host', '') in {f'{host}:{self.server.server_port}' for host in active_auth_hosts(PRIMARY_OWNER)}
        return self.headers.get('Host', '') == f'{HOST}:{self.server.server_port}' 

    def _origin_ok(self) -> bool:
        if len(self.headers.get_all('Origin', [])) != 1 or len(self.headers.get_all('Sec-Fetch-Site', [])) > 1:
            return False
        origin = self.headers.get('Origin', '')
        site = self.headers.get('Sec-Fetch-Site', '')
        return (origin == f"https://{self.headers.get('Host', '')}"
                and site in ('', 'same-origin', 'none'))

    def _session_cookie(self) -> str:
        # Multiple cookies with the same name can cause parser disagreement.
        raw = self.headers.get_all('Cookie', [])
        if len(raw) != 1:
            return ''
        found = []
        for item in raw[0].split(';'):
            name, sep, value = item.strip().partition('=')
            if sep and name == COOKIE:
                found.append(value)
        return found[0] if len(found) == 1 else ''

    def _authorized(self) -> bool:
        return self.server.authority.sessions.valid(self._session_cookie())

    def _read_json(self) -> dict | None:
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return None
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isdigit() or not (1 <= int(lengths[0]) <= 4096):
            return None
        try:
            record = json.loads(self.rfile.read(int(lengths[0])))
        except (ValueError, UnicodeDecodeError):
            return None
        return record if isinstance(record, dict) else None

    @staticmethod
    def _clean_return_path(value: str) -> str:
        # The login page is a local-only return target, never an open redirect.
        # Paths alone are sufficient for the bootstrap milestone; query strings
        # can be handled later with a separate, explicit allowlist.
        if (not isinstance(value, str) or len(value) > 256 or
                not re.fullmatch(r'/[a-zA-Z0-9_/-]*', value) or
                value.startswith('//') or '//' in value or '/..' in value):
            return '/graphical'
        return value

    def _login_return_path(self) -> str:
        queries = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        requested = queries.get('next', ['/graphical'])
        return self._clean_return_path(requested[0]) if len(requested) == 1 else '/graphical'

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header('Location', location)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()

    def _redirect_with_cookie(self, location: str, cookie: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header('Location', location)
        self.send_header('Set-Cookie', cookie)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()

    def _redirect_to_login(self, requested: str) -> None:
        # Redirect only top-level pages, not API requests or internal service
        # protocols. Those receive 401 and must handle authentication explicitly.
        safe_path = self._clean_return_path(urlsplit(requested).path)
        self._redirect('/auth/login?next=' + quote(safe_path, safe=''))

    def _page_navigation(self) -> bool:
        path = urlsplit(self.path).path
        if path.startswith(('/api/', '/v1/', '/ws/', '/sse/')):
            return False
        return (self.headers.get('Sec-Fetch-Mode') == 'navigate'
                or self.headers.get('Sec-Fetch-Dest') == 'document'
                or 'text/html' in self.headers.get('Accept', ''))

    def _proxy(self, method: str) -> None:
        """Bootstrap-only same-host dashboard bridge; not a production ingress gate."""
        started = time.perf_counter()
        if not self._authorized():
            if method == 'GET' and self._page_navigation():
                self._redirect_to_login(self.path)
            else:
                self._json(HTTPStatus.UNAUTHORIZED, {'ok': False, 'error': 'AUTH_REQUIRED'})
            return
        authorized_at = time.perf_counter()
        path = self.path
        if not path.startswith('/') or '://' in path or len(path) > 4096:
            self._json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': 'BAD_PATH'})
            return
        port = self.server.backend_port
        if HARDENED:
            from main_computer.auth_hostnames import HOSTS, APP_PORTS
            presented = self.headers.get('Host', '').split(':')[0]
            mode = next((name for name, host in HOSTS.items() if host == presented), None)
            if mode not in APP_PORTS:
                self._json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': 'INVALID_APPLICATION_ORIGIN'})
                return
            # The bootstrap remains one per-user HTTPS authority.  Each
            # hostname dispatches exclusively to its own MCEL installation.
            # Dev and Unleashed intentionally share the exclusive :8765 slot.
            routes = self.server.isolated_backend_ports
            port = APP_PORTS[mode] if routes is None else routes.get(mode)
            if port and path.startswith('/api/heartbeat/'):
                # The heartbeat service is its own listener; its candidate
                # handler now also requires this signed gateway assertion.
                port += 1
        if not port:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {'ok': False, 'error': 'MC_BACKEND_NOT_CONFIGURED'})
            return
        if HARDENED and method == 'POST' and not self._origin_ok():
            self._json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': 'CROSS_ORIGIN_WRITE_DENIED'})
            return
        size = 0
        if method == 'POST':
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > (
                    __import__('main_computer.hardened_ingress', fromlist=['MAX_BODY']).MAX_BODY if HARDENED else 2_000_000):
                self._json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': 'INVALID_BODY_LENGTH'})
                return
            size = int(lengths[0])
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=60 if HARDENED else 10)
        try:
            # The browser session is represented ONLY by our fresh signed
            # assertion. Never relay Cookie or client-supplied gateway headers.
            headers = {'Host': f'127.0.0.1:{port}', 'Accept': 'application/json,text/plain,*/*',
                       'Connection': 'close'}
            if method == 'POST' and not HARDENED:
                headers['Content-Type'] = self.headers.get('Content-Type', 'application/json')
            if HARDENED:
                # Preserve the native route contract behind the authenticated
                # gateway. Only translate an actual same-origin browser Origin;
                # do not invent one for requests that lack it, or relabel a
                # foreign origin. POST already passed _origin_ok above.
                route = urlsplit(path).path
                names = ['Origin', 'Sec-Fetch-Site', 'Sec-Fetch-Dest', 'Sec-Fetch-Mode', 'Accept']
                if method == 'POST':
                    names.append('Content-Type')
                if route.startswith('/api/applications/slh/'):
                    # These are SLH's own credentials, NOT the MC session.
                    # Preserve presence even for invalid credentials, so the
                    # native local/network target checks cannot be bypassed.
                    names += ['Authorization', 'X-MCEL-SLH-Operation']
                if route in ('/api/applications/wallet/org','/api/applications/wallet/portfolio'):
                    names.append('X-MCEL-Wallet-Operation')
                if route.startswith('/api/applications/browser/'):
                    names.append('X-MCEL-Browser-Operation')
                for name in names:
                    values = self.headers.get_all(name, [])
                    if len(values) > 1:
                        self._json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': 'AMBIGUOUS_REQUEST_HEADER'})
                        return
                    if values:
                        value = values[0]
                        if name == 'Origin':
                            if value != f"https://{self.headers.get('Host', '')}":
                                # A caller could otherwise name the private
                                # HTTP backend as its Origin and fool the
                                # native route's local-origin comparison.
                                self._json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': 'CROSS_ORIGIN_REQUEST_DENIED'})
                                return
                            value = 'http://' + headers['Host']
                        headers[name] = value
            payload = self.rfile.read(size) if size else b''
            if HARDENED:
                from main_computer.hardened_ingress import HEADER, issue_assertion
                headers[HEADER] = issue_assertion(self.server.authority.key,
                    session=self._session_cookie(), method=method, path=path,
                    body=payload, host=headers['Host'])
            upstream_started = time.perf_counter()
            conn.request(method, path, body=payload if method == 'POST' else None, headers=headers)
            response = conn.getresponse()
            headers_at = time.perf_counter()
            kind = response.getheader('Content-Type', 'application/octet-stream')
            # Legacy MCEL children embed their complete application bundle.
            # Increase the bounded response budget only for authenticated,
            # successful application HTML navigation, never API or file data.
            route = urlsplit(path).path
            large_html = (HARDENED and method == 'GET' and response.status == 200
                and (route == '/applications' or route.startswith('/applications/'))
                and kind.split(';', 1)[0].strip().lower() == 'text/html')
            limit = 32 * 1024 * 1024 if large_html else 2_000_000
            data = response.read(limit + 1)
            if len(data) > limit:
                self._json(HTTPStatus.BAD_GATEWAY, {'ok': False, 'error': 'BACKEND_RESPONSE_TOO_LARGE'})
                return
            received_at = time.perf_counter()
            self._proxy_timing = (f'auth;dur={(authorized_at-started)*1000:.2f}, '
                                  f'upstream;dur={(headers_at-upstream_started)*1000:.2f}, '
                                  f'body;dur={(received_at-headers_at)*1000:.2f}, '
                                  f'gateway;dur={(received_at-started)*1000:.2f}')
            self._send(response.status, data, kind=kind)
        except (OSError, TimeoutError, http.client.HTTPException):
            self._json(HTTPStatus.BAD_GATEWAY, {'ok': False, 'error': 'MC_BACKEND_UNAVAILABLE'})
        finally:
            self._proxy_timing = None
            conn.close()

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': 'INVALID_HOST'})
            return
        path = urlsplit(self.path).path
        if path == '/auth/login':
            if self._authorized():
                self._redirect(self._login_return_path())
                return
            data = (Path(__file__).resolve().parent / 'web' / 'auth_login.html').read_bytes()
            self._send(HTTPStatus.OK, data, kind='text/html; charset=utf-8',
                csp="default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                    "connect-src 'self'; img-src 'none'; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'none'; object-src 'none'")
        elif path == '/auth/login.js':
            data = (Path(__file__).resolve().parent / 'web' / 'auth_login.js').read_bytes()
            self._send(HTTPStatus.OK, data, kind='application/javascript; charset=utf-8',
                       csp="default-src 'none'")
        elif path in ('/graphical', '/widgets'):
            if not self._authorized():
                self._redirect_to_login(path)
                return
            html = (Path(__file__).resolve().parent / 'web' / 'graphical.html').read_bytes()
            # Preserve non-default isolated test ports in the button target.
            html = html.replace(b'https://mc.test:8875/auth/login?next=%2Fgraphical',
                f"https://{self.headers.get('Host', f'{HOST}:{self.server.server_port}')}/auth/login?next=%2Fgraphical".encode())
            self._send(HTTPStatus.OK, html, kind='text/html; charset=utf-8')
        elif HARDENED and path == '/auth/handoff/start':
            from main_computer.auth_hostnames import HOSTS
            target = parse_qs(urlsplit(self.path).query).get('target', [''])[0]
            source = self.headers.get('Host', '').split(':')[0]
            if not self._authorized() or target not in __import__('main_computer.auth_hostnames', fromlist=['active_auth_hosts']).active_auth_hosts(PRIMARY_OWNER) or target == source:
                self._json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': 'HANDOFF_UNAUTHORIZED'})
                return
            # A capability is not reusable after redeem and is never an
            # alternate means of creating a session or extending its lifetime.
            ticket = secrets.token_urlsafe(32)
            with self.server.handoff_lock:
                self.server.handoff_tickets = {k:v for k,v in self.server.handoff_tickets.items() if v[2] > time.time()}
                self.server.handoff_tickets[hashlib.sha256(ticket.encode()).hexdigest()] = (
                    target, self._session_cookie(), time.time() + 30)
            self._redirect(f'https://{target}:{self.server.server_port}/auth/handoff/redeem?ticket={quote(ticket, safe="")}')
        elif HARDENED and path == '/auth/handoff/redeem':
            token = parse_qs(urlsplit(self.path).query).get('ticket', [''])[0]
            if not isinstance(token, str) or len(token) > 128:
                self._json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': 'BAD_HANDOFF_TICKET'})
                return
            with self.server.handoff_lock:
                grant = self.server.handoff_tickets.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            if not grant or grant[0] != self.headers.get('Host', '').split(':')[0] or grant[2] < time.time() or not self.server.authority.sessions.valid(grant[1]):
                self._json(HTTPStatus.UNAUTHORIZED, {'ok': False, 'error': 'HANDOFF_EXPIRED_OR_REPLAYED'})
                return
            self._redirect_with_cookie('/graphical',
                f'{COOKIE}={grant[1]}; Max-Age={SESSION_AGE}; Path=/; Secure; HttpOnly; SameSite=Strict')
        elif path == '/api/auth/bootstrap/challenge':
            try:
                self._json(HTTPStatus.OK, self.server.authority.challenge())
            except RuntimeError as exc:
                if str(exc) == 'MC_PROOF_UNAVAILABLE':
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {'ok': False, 'error': 'MC_PROOF_UNAVAILABLE'})
                else:
                    self._json(HTTPStatus.TOO_MANY_REQUESTS, {'ok': False, 'error': 'CHALLENGE_LIMIT'})
        elif path == '/api/auth/bootstrap/session':
            # Report the actual canonical proof state, not just whether the
            # browser supplied a previously issued cookie. Reconciliation
            # revokes cookies if the user deleted or replaced the AAA file.
            authenticated = self._authorized()
            proof_available = self.server.authority.sessions.proof_current()
            self._json(HTTPStatus.OK, {'ok': True, 'authenticated': authenticated,
                                       'proof_available': proof_available,
                                       'proof_reissued': self.server.authority.sessions.reissue_pending()})
        elif path == '/':
            if self._authorized():
                self._redirect('/graphical')
            else:
                self._redirect_to_login('/graphical')
        elif path.startswith('/api/auth/bootstrap/'):
            self._json(HTTPStatus.NOT_FOUND, {'ok': False, 'error': 'NOT_FOUND'})
        else:
            self._proxy('GET')

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            self._json(HTTPStatus.FORBIDDEN, {'ok': False, 'error': 'ORIGIN_REQUIRED'})
            return
        path = urlsplit(self.path).path
        if path == '/api/auth/bootstrap/verify':
            record = self._read_json()
            if not record or set(record) != {'challenge_id', 'signature'}:
                self._json(HTTPStatus.BAD_REQUEST, {'ok': False, 'error': 'INVALID_PROOF'})
                return
            token = self.server.authority.verify(record['challenge_id'], record['signature'])
            if not token:
                self._json(HTTPStatus.UNAUTHORIZED, {'ok': False, 'error': 'INVALID_OR_EXPIRED_PROOF'})
                return
            self._json(HTTPStatus.OK, {'ok': True, 'authenticated': True},
                cookie=f'{COOKIE}={token}; Max-Age={SESSION_AGE}; Path=/; Secure; HttpOnly; SameSite=Strict')
        elif path == '/api/auth/bootstrap/logout':
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {'ok': False, 'error': 'AUTH_REQUIRED'})
                return
            self.server.authority.sessions.revoke(self._session_cookie())
            self._json(HTTPStatus.OK, {'ok': True, 'authenticated': False},
                cookie=f'{COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict')
        elif path.startswith('/api/auth/bootstrap/'):
            self._json(HTTPStatus.NOT_FOUND, {'ok': False, 'error': 'NOT_FOUND'})
        else:
            self._proxy('POST')
