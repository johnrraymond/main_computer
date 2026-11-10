"""Machine-local Main Computer P-256 identities (no organization dependency).

Patch A: unencrypted-at-rest, user-owned identity store.  This is deliberately
not a password/dongle vault.  The public API never returns private key bytes.
Device keys are NOT marketplace wallet keys.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import secrets
from pathlib import Path
import re
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .authenticator import generate_identity, public_key_fingerprint, sign_object
from .storage import legacy_authenticator_dir, user_identity_dir, secure_private_state_file

_DEVICE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')

# Public identity IDs are opaque, not role names. The region field is a
# randomly chosen US state abbreviation; it does not identify the key owner.
_US_STATE_CODES = (
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
)
_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def generate_identity_slug() -> str:
    """Return NNN-LLL-SS-NNN-LL-YY-MM-DD (all randomness from secrets)."""
    letters = lambda length: ''.join(secrets.choice(_ALPHABET) for _ in range(length))
    return (f'{secrets.randbelow(1000):03d}-{letters(3)}-'
            f'{secrets.choice(_US_STATE_CODES)}-{secrets.randbelow(1000):03d}-'
            f'{letters(2)}-{datetime.now(timezone.utc):%y-%m-%d}')


class IdentityStoreError(ValueError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def default_identity_directory() -> Path:
    """No silent per-user credential storage in the Windows service account."""
    if os.name == 'nt':
        profile = os.environ.get('USERPROFILE', '').replace('/', '\\').lower()
        username = os.environ.get('USERNAME', '').lower()
        if username in ('system', 'local service', 'network service') or profile.endswith((
            '\\systemprofile', '\\localservice', '\\networkservice')):
            raise IdentityStoreError('INTERACTIVE_USER_CONTEXT_REQUIRED', 503)
    canonical, legacy = user_identity_dir(), legacy_authenticator_dir()
    # Do not shadow existing keys or quietly merge different identity directories.
    canonical_has = canonical.is_dir() and any(canonical.glob('*.key.pem'))
    legacy_has = legacy.is_dir() and any(legacy.glob('*.key.pem'))
    if canonical_has and legacy_has:
        raise IdentityStoreError('IDENTITY_DIRECTORY_CONFLICT', 409)
    return legacy if legacy_has else canonical


def _identity_path(directory: Path, device_id: str) -> Path:
    if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
        raise IdentityStoreError('INVALID_IDENTITY_ID')
    return Path(directory) / (device_id + '.key.pem')


def _meta_path(directory: Path, device_id: str) -> Path:
    _identity_path(directory, device_id)
    return Path(directory) / (device_id + '.meta.json')


def _private_key(directory: Path, device_id: str):
    path = _identity_path(directory, device_id)
    if path.is_symlink():
        raise IdentityStoreError('IDENTITY_FILE_UNSAFE', 409)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise IdentityStoreError('IDENTITY_NOT_FOUND', 404) from None
    except OSError:
        raise IdentityStoreError('IDENTITY_UNAVAILABLE', 503) from None
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (ValueError, TypeError):
        raise IdentityStoreError('IDENTITY_CORRUPT_OR_LOCKED', 409) from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise IdentityStoreError('IDENTITY_INVALID_KEY_TYPE', 409)
    return key


def _public_pem(key) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')


def _identity_metadata(directory: Path, device_id: str, *, with_public: bool = False) -> dict:
    key = _private_key(directory, device_id)
    pem = _public_pem(key)
    metadata_path = _meta_path(directory, device_id)
    data = {}
    if metadata_path.is_file() and not metadata_path.is_symlink():
        try:
            value = json.loads(metadata_path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                data = value
        except (OSError, ValueError):
            pass  # Preserve key access even if noncritical display metadata is damaged.
    result = {
        'device_id': device_id,
        'label': data.get('label') if isinstance(data.get('label'), str) else device_id,
        'fingerprint': public_key_fingerprint(pem),
        'kind': 'p256_device',
        'protection': 'none',
        'locked': False,
        'usable': True,
        'created_at': data.get('created_at') if isinstance(data.get('created_at'), int) else None,
    }
    if with_public:
        result['public_key'] = pem
    return result


def _write_exclusive(path: Path, data: bytes) -> None:
    # An exclusive create never replaces an existing identity, including a symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if os.name == 'posix':
        path.chmod(0o600)


class LocalIdentityStore:
    _DEFAULT_FILE = '.default-identity.json'
    _DEFAULT_LOCK = '.default-identity.lock'

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory is not None else default_identity_directory()

    def _default_device_id(self) -> str | None:
        path = self.directory / self._DEFAULT_FILE
        if path.is_symlink():
            raise IdentityStoreError('DEFAULT_IDENTITY_UNSAFE', 409)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            raise IdentityStoreError('DEFAULT_IDENTITY_CORRUPT', 409) from None
        if (not isinstance(data, dict) or set(data) != {'schema', 'device_id', 'fingerprint'}
                or data.get('schema') != 'main-computer.local-default-identity.v1'
                or not isinstance(data.get('device_id'), str)
                or not _DEVICE_ID.fullmatch(data['device_id'])
                or not isinstance(data.get('fingerprint'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', data['fingerprint'])):
            raise IdentityStoreError('DEFAULT_IDENTITY_CORRUPT', 409)
        identity = self.get(data['device_id'])
        if identity['fingerprint'] != data['fingerprint']:
            raise IdentityStoreError('DEFAULT_IDENTITY_MISMATCH', 409)
        return data['device_id']

    def _persist_default(self, identity: dict) -> None:
        path = self.directory / self._DEFAULT_FILE
        temporary = self.directory / (self._DEFAULT_FILE + '.' + uuid.uuid4().hex + '.tmp')
        data = json.dumps({'schema': 'main-computer.local-default-identity.v1',
                           'device_id': identity['device_id'],
                           'fingerprint': identity['fingerprint']}, sort_keys=True).encode('utf-8')
        try:
            _write_exclusive(temporary, data)
            secure_private_state_file(temporary)
            # Only called under the exclusive default-selection lock.
            if path.exists() or path.is_symlink():
                raise IdentityStoreError('DEFAULT_IDENTITY_ALREADY_DESIGNATED', 409)
            os.replace(temporary, path)
            if os.name == 'posix':
                descriptor = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError:
            raise IdentityStoreError('IDENTITY_STORAGE_UNAVAILABLE', 503) from None
        finally:
            temporary.unlink(missing_ok=True)

    def ensure_default_identity(self, *, timeout_seconds: float = 8.0) -> dict:
        """Ensure >=1 ordinary identity with a stable default designation.

        Cross-process exclusive creation serializes competing LMS-SLH startups.
        Interrupted creation before the designation is safely reconciled by
        selecting the existing valid key on the following startup. A leftover
        lock or corrupted designation fails closed; neither is auto-replaced.
        """
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == 'posix':
                self.directory.chmod(0o700)
        except OSError:
            raise IdentityStoreError('IDENTITY_STORAGE_UNAVAILABLE', 503) from None
        lock = self.directory / self._DEFAULT_LOCK
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise IdentityStoreError('DEFAULT_IDENTITY_LOCKED', 503) from None
                time.sleep(0.025)
            except OSError:
                raise IdentityStoreError('IDENTITY_STORAGE_UNAVAILABLE', 503) from None
        try:
            designated = self._default_device_id()
            if designated is not None:
                return self.get(designated)
            listed = self.list()
            if listed['identities']:
                identity = self.get(listed['identities'][0]['device_id'])
            elif listed['invalid_identities']:
                raise IdentityStoreError('IDENTITY_CORRUPT_OR_LOCKED', 409)
            else:
                # The random ID is an ordinary identity identifier, not a type.
                identity = self.create_generated(label='Default identity')
            self._persist_default(identity)
            return identity
        finally:
            lock.unlink(missing_ok=True)

    def create_generated(self, *, label: str | None = None) -> dict:
        """Persist an ordinary identity with an opaque randomly generated slug.

        Collision retries never overwrite an existing key or default selection.
        """
        for _ in range(16):
            try:
                return self.create(generate_identity_slug(), label=label)
            except IdentityStoreError as exc:
                if exc.code != 'IDENTITY_ALREADY_EXISTS':
                    raise
        raise IdentityStoreError('IDENTITY_ID_COLLISION_EXHAUSTED', 409)

    def create(self, device_id: str, *, label: str | None = None) -> dict:
        path = _identity_path(self.directory, device_id)
        if label is not None and (not isinstance(label, str) or not 1 <= len(label.strip()) <= 120):
            raise IdentityStoreError('INVALID_IDENTITY_LABEL')
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if os.name == 'posix':
                self.directory.chmod(0o700)
            key, _ = generate_identity()
            raw = key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            _write_exclusive(path, raw)
            # Windows ignores POSIX create modes; enforce a token-SID-only ACL
            # using the same fail-closed policy as machine-owned service keys.
            try:
                secure_private_state_file(path)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        except FileExistsError:
            raise IdentityStoreError('IDENTITY_ALREADY_EXISTS', 409) from None
        except OSError:
            raise IdentityStoreError('IDENTITY_STORAGE_UNAVAILABLE', 503) from None
        # Metadata is optional and never carries secrets. A failed write does not
        # invalidate a successfully persisted key; list/detail recover the key.
        metadata = {'schema': 'main-computer.local-identity.v1',
                    'label': label.strip() if label is not None else device_id,
                    'created_at': int(time.time())}
        temporary = self.directory / (device_id + '.' + uuid.uuid4().hex + '.tmp')
        try:
            _write_exclusive(temporary, json.dumps(metadata).encode('utf-8'))
            os.replace(temporary, _meta_path(self.directory, device_id))
        except OSError:
            temporary.unlink(missing_ok=True)
        return _identity_metadata(self.directory, device_id, with_public=True)

    def load_private_key(self, device_id: str):
        return _private_key(self.directory, device_id)

    def get(self, device_id: str) -> dict:
        return _identity_metadata(self.directory, device_id, with_public=True)

    def list(self) -> dict:
        if not self.directory.exists():
            return {'identities': [], 'invalid_identities': [], 'default_device_id': None}
        identities, invalid = [], []
        try:
            files = sorted(self.directory.glob('*.key.pem'))
        except OSError:
            raise IdentityStoreError('IDENTITY_STORAGE_UNAVAILABLE', 503) from None
        for path in files:
            device_id = path.name[:-len('.key.pem')]
            if not _DEVICE_ID.fullmatch(device_id):
                continue
            try:
                identities.append(_identity_metadata(self.directory, device_id))
            except IdentityStoreError as exc:
                invalid.append({'device_id': device_id, 'error': exc.code})
        return {'identities': identities, 'invalid_identities': invalid,
                'default_device_id': self._default_device_id()}

    def sign(self, device_id: str, challenge: dict) -> dict:
        if not isinstance(challenge, dict) or len(json.dumps(challenge, ensure_ascii=True)) > 8192:
            raise IdentityStoreError('INVALID_CHALLENGE')
        identity = self.get(device_id)
        purpose = challenge.get('purpose')
        action = challenge.get('action')
        if purpose == 'slh.known_device.login':
            expected = {'version','purpose','audience','device_id',
                        'challenge_id','nonce','issued_at','expires_at'}
            if (set(challenge) != expected or type(challenge.get('version')) is not int
                    or challenge['version'] != 1
                    or not isinstance(challenge.get('audience'), str)
                    or not re.fullmatch(r'slh://[0-9a-f]{64}',challenge['audience'])
                    or not isinstance(challenge.get('challenge_id'), str)
                    or not re.fullmatch(r'[a-f0-9]{32}', challenge['challenge_id'])
                    or not isinstance(challenge.get('nonce'), str)
                    or not 24 <= len(challenge['nonce']) <= 128
                    or type(challenge.get('issued_at')) is not int
                    or type(challenge.get('expires_at')) is not int
                    or not 0 < challenge['expires_at'] - challenge['issued_at'] <= 120
                    or not challenge['issued_at'] - 30 <= int(time.time()) < challenge['expires_at']):
                raise IdentityStoreError('INVALID_CHALLENGE')
        elif action == 'slh.admin.root.register':
            # Exact V6-A grant statement. A local identity signs only its own
            # public key, for a short window, bound to one ORG service.
            expected = {'version', 'action', 'audience', 'device_id',
                        'public_key', 'client_nonce', 'issued_at'}
            if 'bootstrap_only' in challenge:
                if challenge['bootstrap_only'] is not True:
                    raise IdentityStoreError('INVALID_CHALLENGE')
                expected.add('bootstrap_only')
            nonce = challenge.get('client_nonce')
            issued = challenge.get('issued_at')
            if (set(challenge) != expected or type(challenge.get('version')) is not int
                    or challenge['version'] != 1
                    or not isinstance(challenge.get('audience'), str)
                    or not re.fullmatch(r'slh://[0-9a-f]{64}', challenge['audience'])
                    or challenge.get('public_key') != identity['public_key']
                    or not isinstance(nonce, str)
                    or not 24 <= len(nonce) <= 128
                    or not re.fullmatch(r'[A-Za-z0-9_-]+', nonce)
                    or type(issued) is not int
                    or abs(int(time.time()) - issued) > 120):
                raise IdentityStoreError('INVALID_CHALLENGE')
        elif action == 'slh.admin.bootstrap':
            expected = {'version','action','admin_id','device_id','public_key'}
            if (set(challenge) != expected or type(challenge.get('version')) is not int
                    or challenge['version'] != 1
                    or not isinstance(challenge.get('admin_id'), str)
                    or not 1 <= len(challenge['admin_id']) <= 128
                    or challenge.get('public_key') != identity['public_key']):
                raise IdentityStoreError('INVALID_CHALLENGE')
        elif action == 'slh.admin.bootstrap.pending':
            # Bind the proof to one SLH-issued bootstrap proposal. The SLH
            # authority verifies the exact stored nonce and interval later.
            expected = {'version','action','transaction_id','nonce',
                        'issued_at','expires_at','admin_id','device_id','public_key'}
            now = int(time.time())
            if (set(challenge) != expected or type(challenge.get('version')) is not int
                    or challenge['version'] != 1
                    or not isinstance(challenge.get('admin_id'), str)
                    or not 1 <= len(challenge['admin_id']) <= 160
                    or not isinstance(challenge.get('transaction_id'), str)
                    or not re.fullmatch('[0-9a-f]{32}', challenge['transaction_id'])
                    or not isinstance(challenge.get('nonce'), str)
                    or not 24 <= len(challenge['nonce']) <= 128
                    or type(challenge.get('issued_at')) is not int
                    or type(challenge.get('expires_at')) is not int
                    or not 0 < challenge['expires_at'] - challenge['issued_at'] <= 1800
                    or not challenge['issued_at'] - 30 <= now < challenge['expires_at']
                    or challenge.get('public_key') != identity['public_key']):
                raise IdentityStoreError('INVALID_CHALLENGE')
        else:
            raise IdentityStoreError('UNSUPPORTED_SIGNING_PURPOSE', 400)
        if challenge.get('device_id') != device_id:
            raise IdentityStoreError('IDENTITY_CHALLENGE_MISMATCH', 400)
        return {'device_id': device_id, 'fingerprint': identity['fingerprint'],
                'signature': sign_object(_private_key(self.directory, device_id), challenge),
                'signature_format': 'ecdsa-p256-sha256-canonical-json-v1'}


def load_identity(directory: Path, device_id: str):
    """Load an existing device identity for packaged SLH Worker/Requester clients."""
    store = LocalIdentityStore(directory)
    return store.load_private_key(device_id), store.get(device_id)['public_key']


def save_identity(directory: Path, device_id: str) -> dict:
    """Create a local P-256 identity without depending on repository-only CLI tools."""
    return LocalIdentityStore(directory).create(device_id)
