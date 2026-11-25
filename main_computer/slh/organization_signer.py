"""Persistent ORG-owned EVM signer, independent of Hub/network authorization.

Like the Hub admin wallet, generate once and load thereafter. Private material
lives under protected installation state, never in configuration or API output.
The wallet database anchors the address even before the first successful MSK.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
from datetime import datetime, timezone

from main_computer.multisession_key_signing import private_key_to_address
from .authority import SLHError
from .storage import secure_machine_state_dir, secure_private_state_file
from .wallet_acl import ensure_windows_wallet_directory, ensure_windows_wallet_file

SIGNER_ENV = 'SLH_PINKY_SWEEP_SIGNER_FILE'
SCHEMA = 'main-computer.slh-organization-signer.v1'
DIRECTORY = 'wallet-private'
FILENAME = 'organization-wallet.json'


def _exists(path):
    # A broken symlink is a damaged authority, not a missing account.
    return os.path.lexists(path)


def _unsafe(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) &
                                     getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _address(secret):
    if not isinstance(secret, str) or not re.fullmatch(r'(?:0x)?[0-9a-fA-F]{64}', secret):
        raise ValueError('invalid signer format')
    return private_key_to_address(secret).lower()


def load_legacy_signer():
    """Explicit old signer path, retained for migration and disposable labs."""
    name = os.environ.get(SIGNER_ENV, '').strip()
    if not name:
        return None
    path = Path(name)
    try:
        if _unsafe(path) or not path.is_file():
            raise SLHError('PINKY_SWEEP_SIGNER_UNAVAILABLE', 503)
        if os.name == 'posix' and path.stat().st_mode & 0o077:
            raise SLHError('PINKY_SWEEP_SIGNER_PERMISSIONS', 503)
        secret = path.read_text(encoding='ascii').strip()
        return _address(secret), secret
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise SLHError('PINKY_SWEEP_SIGNER_INVALID', 503) from exc


def _load(path, *, repair_directory=False):
    try:
        directory = path.parent
        if (_unsafe(directory) or not directory.is_dir() or _unsafe(path) or not path.is_file()):
            raise SLHError('ORG_SIGNER_UNSAFE_STORAGE', 503)
        if os.name == 'posix' and (directory.stat().st_mode & 0o077 or path.stat().st_mode & 0o077):
            raise SLHError('ORG_SIGNER_PERMISSIONS', 503)
        if os.name == 'nt':
            # Startup may repair the directory DACL, but never rewrite the key
            # or silently take ownership. Ordinary signing only verifies.
            try:
                ensure_windows_wallet_directory(directory, repair=repair_directory)
                ensure_windows_wallet_file(path)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise SLHError('ORG_SIGNER_ACL_UNVERIFIED', 503) from exc
        if path.stat().st_size > 4096:
            raise ValueError('invalid signer record')
        record = json.loads(path.read_text(encoding='ascii'))
        if not isinstance(record, dict) or record.get('schema') != SCHEMA:
            raise ValueError('invalid signer record')
        secret = record['private_key']
        address = _address(secret)
        if record.get('address') != address:
            raise ValueError('invalid signer address')
        return address, secret
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise SLHError('ORG_SIGNER_INVALID', 503) from exc


def _generate():
    # CSPRNG, with rejection sampling for the secp256k1 scalar range. Do not
    # invoke cast or put private keys on a subprocess command line.
    while True:
        secret = '0x' + secrets.token_hex(32)
        try:
            return _address(secret), secret
        except ValueError:
            continue


def _persist(path, signer, source):
    directory = path.parent
    if _exists(directory) and _unsafe(directory):
        raise SLHError('ORG_SIGNER_UNSAFE_STORAGE', 503)
    secure_machine_state_dir(directory)
    if os.name == 'nt':
        ensure_windows_wallet_directory(directory, repair=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=directory, prefix='.org-wallet-',
                                         suffix='.tmp', encoding='ascii', delete=False) as stream:
            tmp = Path(stream.name)
            if os.name == 'posix':
                os.fchmod(stream.fileno(), 0o600)
            json.dump({'schema': SCHEMA, 'address': signer[0], 'private_key': signer[1],
                       'source': source, 'created_at': datetime.now(timezone.utc).isoformat()}, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        secure_private_state_file(tmp)
        if os.name == 'nt':
            ensure_windows_wallet_file(tmp)
        # The wallet DB write transaction serializes provisioning across
        # threads/processes. Never replace a preexisting signer.
        if _exists(path):
            raise SLHError('ORG_SIGNER_ALREADY_EXISTS', 409)
        os.replace(tmp, path)
        if os.name == 'posix':
            fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return _load(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def resolve_organization_signer(wallet, *, provision=False):
    """Load canonical signer, or provision once in the resident startup lane.

    Existing wallet binding or MSK without recoverable signer is not a new
    organization. An explicitly supplied legacy signer is imported only if
    consistent with durable authority. An isolated lab can still use its own
    legacy signer without generating persistent production credentials.
    """
    from .marketplace_auto import load_authorization
    path = wallet.path.parent / DIRECTORY / FILENAME
    with wallet._lock, wallet._db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS signer_identity (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), address TEXT NOT NULL)''')
        row = db.execute('SELECT address FROM signer_identity WHERE singleton=1').fetchone()
        anchor = row['address'] if row else None
        binding = wallet._binding(db)
        if _exists(path.parent) and _unsafe(path.parent):
            raise SLHError('ORG_SIGNER_UNSAFE_STORAGE', 503)
        if _exists(path):
            signer = _load(path, repair_directory=provision)
            if anchor and signer[0] != anchor:
                raise SLHError('ORG_SIGNER_IDENTITY_MISMATCH', 409)
            if binding and binding['wallet_address'] != signer[0]:
                raise SLHError('WALLET_MIGRATION_REQUIRED', 409)
            # Canonical authority wins over obsolete environment settings.
        elif anchor:
            raise SLHError('ORG_SIGNER_RECOVERY_REQUIRED', 409)
        else:
            legacy = load_legacy_signer()
            if not provision:
                if legacy is None:
                    raise SLHError('PINKY_SWEEP_CONFIGURATION_REQUIRED', 409)
                return legacy
            if binding and (legacy is None or binding['wallet_address'] != legacy[0]):
                raise SLHError('ORG_SIGNER_RECOVERY_REQUIRED', 409)
            if not binding and load_authorization(wallet.path.parent) is not None:
                # An unbound existing authorization has no verified local
                # address. Do not provision a replacement behind its back.
                raise SLHError('ORG_SIGNER_RECOVERY_REQUIRED', 409)
            signer = _persist(path, legacy or _generate(), 'legacy-import' if legacy else 'generated')
        if anchor is None:
            db.execute('INSERT INTO signer_identity VALUES (1, ?)', (signer[0],))
        return signer
