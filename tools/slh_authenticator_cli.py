"""Passphrase-free development authenticator for SLH Phase 2A.

The private device key is stored as UNENCRYPTED PEM in the local identity directory.
This is for local development only, not protected production key storage.
Do not use this key for a marketplace wallet.

  python -m tools.slh_authenticator_cli create --device-id admin-pc
  python -m tools.slh_authenticator_cli bootstrap-admin --device-id admin-pc --org-id my-org --admin-id owner --admin-name "Owner"
  python -m tools.slh_authenticator_cli login --device-id admin-pc
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from http.client import HTTPConnection

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from main_computer.slh.authenticator import generate_identity, public_key_fingerprint, sign_object
from main_computer.slh.ports import DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT

_DEVICE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')


def _default_auth_dir() -> Path:
    root = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'share')
    return Path(root) / 'MainComputer' / 'authenticators'


def _default_state_dir() -> Path:
    root = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'share')
    return Path(root) / 'MainComputer' / 'slh'


def identity_file(directory: Path, device_id: str) -> Path:
    if not _DEVICE_ID.fullmatch(device_id):
        raise ValueError('Invalid device ID: use 1-64 alphanumeric, underscore, dot or dash characters')
    return Path(directory) / (device_id + '.key.pem')


def save_identity(directory: Path, device_id: str) -> dict:
    """Create unencrypted PKCS#8 key, refusing to overwrite an existing key."""
    destination = identity_file(directory, device_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'posix':
        os.chmod(destination.parent, 0o700)
    key, public = generate_identity()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(pem)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if os.name == 'posix':
        os.chmod(destination, 0o600)
    return {'device_id': device_id, 'identity_file': str(destination.resolve()),
            'fingerprint': public_key_fingerprint(public), 'encrypted': False}


def load_identity(directory: Path, device_id: str):
    destination = identity_file(directory, device_id)
    try:
        key = serialization.load_pem_private_key(destination.read_bytes(), password=None)
    except TypeError as exc:
        raise ValueError('Existing authenticator key is encrypted; this passphrase-free CLI cannot open it') from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('Not a Main Computer P-256 authenticator')
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')
    return key, public


def _request(port: int, method: str, path: str, body: dict | None = None, token: str | None = None):
    if not 1 <= port <= 65535:
        raise ValueError('Port must be 1-65535')
    if not path.startswith('/v1/'):
        raise ValueError('Only SLH v1 API paths are supported')
    payload = None if body is None else json.dumps(body).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    # Direct loopback HTTP: no proxies or redirects can receive bootstrap secrets.
    conn = HTTPConnection(DEFAULT_SLH_API_HOST, port, timeout=8)
    try:
        conn.request(method,path,body=payload,headers=headers)
        response = conn.getresponse()
        raw = response.read()
        if 300 <= response.status < 400:
            raise RuntimeError('SLH redirect refused')
        try:
            result = json.loads(raw)
        except (ValueError,UnicodeError):
            raise RuntimeError(f'SLH returned invalid JSON (HTTP {response.status})') from None
        if response.status >= 400:
            code = result.get('error',{}).get('code','HTTP_ERROR') if isinstance(result,dict) else 'HTTP_ERROR'
            raise RuntimeError(f'SLH returned {response.status} ({code})')
        if not isinstance(result,dict):
            raise RuntimeError('Invalid SLH response')
        return result
    except (OSError,TimeoutError) as exc:
        raise RuntimeError(f'SLH is unreachable at 127.0.0.1:{port}: {exc}') from None
    finally:
        conn.close()


def bootstrap_admin(*, port: int, state_dir: Path, identity_dir: Path, device_id: str,
                    org_id: str, admin_id: str, admin_name: str):
    status = _request(port, 'GET', '/v1/bootstrap/status')
    if status.get('initialized'):
        raise RuntimeError('SLH already initialized: first-administrator bootstrap is closed')
    key, public_key = load_identity(identity_dir, device_id)
    secret_file = Path(state_dir) / 'bootstrap.secret'
    secret = secret_file.read_text(encoding='ascii').strip()
    statement = {'version':1, 'action':'slh.admin.bootstrap', 'org_id':org_id,
                 'admin_id':admin_id, 'device_id':device_id, 'public_key':public_key}
    data = {**statement, 'admin_name':admin_name, 'bootstrap_secret':secret,
            'proof':sign_object(key, statement)}
    data.pop('version')
    data.pop('action')
    result = _request(port, 'POST', '/v1/bootstrap/admin', data)
    return result


def login(*, port: int, identity_dir: Path, device_id: str):
    key, _ = load_identity(identity_dir, device_id)
    challenge = _request(port, 'POST', '/v1/auth/challenge', {'device_id':device_id})
    result = _request(port, 'POST', '/v1/auth/verify', {
        'device_id':device_id,
        'challenge_id':challenge['challenge_id'],
        'signature':sign_object(key,challenge),
    })
    # Use the bearer for this one local request but never log or persist it.
    token = result.pop('access_token')
    session = _request(port, 'GET', '/v1/auth/session', token=token)
    _request(port, 'POST', '/v1/auth/logout', {}, token=token)
    return {'authenticated':session['active'], 'device_id':session['device_id'],
            'user_id':session['user_id'], 'role':session['role'],
            'session': 'validated_and_logged_out'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for cmd in ('create', 'bootstrap-admin', 'login'):
        c = sub.add_parser(cmd)
        c.add_argument('--device-id', required=True)
        c.add_argument('--identity-dir',type=Path,default=_default_auth_dir())
        if cmd != 'create':
            c.add_argument('--port',type=int,default=DEFAULT_SLH_API_PORT,
                           help='Lab override; default is the canonical SLH loopback API port')
        if cmd == 'bootstrap-admin':
            c.add_argument('--state-dir',type=Path,default=_default_state_dir())
            c.add_argument('--org-id',required=True)
            c.add_argument('--admin-id',required=True)
            c.add_argument('--admin-name',required=True)
    args=p.parse_args(argv)
    try:
        if args.command=='create':
            result=save_identity(args.identity_dir,args.device_id)
        elif args.command=='bootstrap-admin':
            result=bootstrap_admin(port=args.port,state_dir=args.state_dir,identity_dir=args.identity_dir,
                                   device_id=args.device_id,
                                   org_id=args.org_id,admin_id=args.admin_id,admin_name=args.admin_name)
        else:
            result=login(port=args.port,identity_dir=args.identity_dir,device_id=args.device_id)
    except (ValueError, RuntimeError, OSError) as exc:
        # Never print the bootstrap secret, bearer, or private key.
        print(json.dumps({'status':'ERROR','reason':str(exc)},sort_keys=True),file=sys.stderr)
        return 1
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
