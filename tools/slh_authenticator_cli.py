"""Passphrase-free development authenticator for SLH Phase 2A.

The private device key is stored as UNENCRYPTED PEM in the local identity directory.
This is for local development only, not protected production key storage.
Do not use this key for a marketplace wallet.

  python -m tools.slh_authenticator_cli create --device-id admin-pc
  python -m tools.slh_authenticator_cli bootstrap-admin --device-id admin-pc --admin-id owner --admin-name "Owner"
  python -m tools.slh_authenticator_cli login --device-id admin-pc
  python -m tools.slh_authenticator_cli request-enrollment --new --label "Identity Test 001" --role worker
  python -m tools.slh_authenticator_cli request-enrollment --new --label "Identity Test 002" --role worker --admin owner
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
from http.client import HTTPConnection

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from main_computer.slh.authenticator import sign_object
from main_computer.slh.authenticator_store import (LocalIdentityStore, IdentityStoreError,
    default_identity_directory)
from main_computer.slh.ports import DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT

_DEVICE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')


def _default_auth_dir() -> Path:
    return default_identity_directory()


def _default_state_dir() -> Path:
    from main_computer.slh.storage import slh_state_dir
    return slh_state_dir()


def identity_file(directory: Path, device_id: str) -> Path:
    if not _DEVICE_ID.fullmatch(device_id):
        raise ValueError('Invalid device ID: use 1-64 alphanumeric, underscore, dot or dash characters')
    return Path(directory) / (device_id + '.key.pem')


def save_identity(directory: Path, device_id: str) -> dict:
    """Backward-compatible CLI wrapper over the shared local identity store."""
    store = LocalIdentityStore(directory)
    try:
        result = store.create(device_id)
    except IdentityStoreError as exc:
        if exc.code == 'IDENTITY_ALREADY_EXISTS':
            raise FileExistsError(str(identity_file(directory, device_id))) from None
        raise
    return {'device_id': device_id,
            'identity_file': str(identity_file(directory, device_id).resolve()),
            'fingerprint': result['fingerprint'], 'encrypted': False}


def load_identity(directory: Path, device_id: str):
    """Existing CLI and managed client retain their private-key loading API."""
    store = LocalIdentityStore(directory)
    key = store.load_private_key(device_id)
    return key, store.get(device_id)['public_key']


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
                    admin_id: str, admin_name: str):
    status = _request(port, 'GET', '/v1/bootstrap/status')
    if status.get('initialized'):
        raise RuntimeError('SLH already initialized: first-administrator bootstrap is closed')
    key, public_key = load_identity(identity_dir, device_id)
    secret_file = Path(state_dir) / 'bootstrap.secret'
    secret = secret_file.read_text(encoding='ascii').strip()
    statement = {'version':1, 'action':'slh.admin.bootstrap',
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



def choose_enrollment_admin(admins: list[dict]) -> str | None:
    """Present an explicit graphical choice; never infer the administrator.

    This is the enrollment-requester's local picker. The approval UI remains
    MCEL's Organization Identities > Pending notebook.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        raise RuntimeError('ADMIN_SELECTION_UI_UNAVAILABLE: Tkinter is required without --admin') from None
    try:
        root = tk.Tk()
    except tk.TclError:
        raise RuntimeError('ADMIN_SELECTION_UI_UNAVAILABLE: a graphical desktop is required without --admin') from None
    try:
        root.title('MCEL - Choose enrollment administrator')
        root.resizable(False, False)
        root.minsize(460, 220)
        chosen = {'id': None}
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Choose an administrator for this enrollment request',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(0, 6))
        ttk.Label(outer, text='Selection sends a pending request. It does not grant access.',
                  wraplength=420).pack(anchor='w', pady=(0, 12))
        rows = ttk.Treeview(outer, columns=('name', 'id'), show='headings',
                            selectmode='browse', height=min(8, max(3, len(admins))))
        rows.heading('name', text='Administrator')
        rows.heading('id', text='ID')
        rows.column('name', width=230, stretch=True)
        rows.column('id', width=180, stretch=True)
        for i, admin in enumerate(admins):
            rows.insert('', 'end', iid=str(i), values=(admin['name'], admin['id']))
        rows.pack(fill='both', expand=True)
        controls = ttk.Frame(outer)
        controls.pack(fill='x', pady=(12, 0))
        def confirm(*_):
            selection = rows.selection()
            if not selection:
                return
            chosen['id'] = admins[int(selection[0])]['id']
            root.destroy()
        def cancel(*_):
            root.destroy()
        ttk.Button(controls, text='Cancel', command=cancel).pack(side='right')
        ttk.Button(controls, text='Request enrollment', command=confirm).pack(side='right', padx=(0, 8))
        root.bind('<Escape>', cancel)
        root.bind('<Return>', confirm)
        rows.bind('<Double-1>', confirm)
        root.protocol('WM_DELETE_WINDOW', cancel)
        root.lift()
        root.mainloop()
        return chosen['id']
    finally:
        # destroy() in a callback is normal; also clean up on an unexpected exception.
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass


def request_enrollment(*, port: int, label: str, role: str, admin_id: str | None,
                       chooser=None) -> dict:
    """Create an LMS-owned local identity and ask ORG-SLH to enroll it.

    No admin bearer and no automatic approval. The chooser is invoked exactly
    when --admin is absent, before a new identity is created.
    """
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 120:
        raise ValueError('Identity label must contain 1-120 characters')
    if role not in ('worker', 'requester', 'admin'):
        raise ValueError('Unsupported enrollment role')
    status = _request(port, 'GET', '/v1/bootstrap/status')
    if status.get('initialized') is not True:
        raise RuntimeError('SLH_ORGANIZATION_NOT_INITIALIZED: enrollment is unavailable')
    fingerprint = status.get('service_fingerprint')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint):
        raise RuntimeError('SLH_SERVICE_IDENTITY_UNVERIFIED')
    admins_response = _request(port, 'GET', '/v1/enrollment/admins')
    admins = admins_response.get('admins')
    if (not isinstance(admins, list) or not admins or
        any(not isinstance(a, dict) or not isinstance(a.get('id'), str) or
            not a['id'] or not isinstance(a.get('name'), str) or
            not a['name'] for a in admins)):
        raise RuntimeError('NO_VERIFIED_ENROLLMENT_ADMINISTRATORS')
    ids = {a['id'] for a in admins}
    if admin_id is None:
        admin_id = (chooser or choose_enrollment_admin)(admins)
        if admin_id is None:
            return {'status': 'CANCELLED', 'identity_created': False, 'enrollment_submitted': False}
    if admin_id not in ids:
        raise RuntimeError('ADMIN_NOT_IN_ENROLLMENT_DIRECTORY')

    # The same local signer API serves MCEL's identities. The CLI never reads
    # private key material and does not create a second credential directory.
    saved = _request(port, 'POST', '/v1/local/authenticator/identities',
                     {'label': label.strip()})
    device_id, public_key, identity_fingerprint = (saved.get('device_id'),
        saved.get('public_key'), saved.get('fingerprint'))
    if (not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id) or
        not isinstance(public_key, str) or not isinstance(identity_fingerprint, str)):
        raise RuntimeError('LOCAL_IDENTITY_CREATION_UNVERIFIED')
    body = {'selected_admin': admin_id, 'device_id': device_id,
            'public_key': public_key,
            'kind': 'administrator' if role == 'admin' else role,
            'device_name': label.strip(), 'claimed_user': 'Main Computer operator',
            'requested_role': role, 'client_nonce': secrets.token_urlsafe(32)}
    statement = {**body, 'version': 1, 'action': 'slh.device.enrollment.request',
                 'audience': 'slh://' + fingerprint}
    try:
        signed = _request(port, 'POST',
                          f'/v1/local/authenticator/identities/{device_id}/sign',
                          {'challenge': statement})
        if (signed.get('device_id') != device_id or
            signed.get('fingerprint') != identity_fingerprint or
            not isinstance(signed.get('signature'), str)):
            raise RuntimeError('LOCAL_ENROLLMENT_SIGNATURE_UNVERIFIED')
        response = _request(port, 'POST', '/v1/enrollment/requests',
                            {**body, 'proof': signed['signature']})
        req_id = response.get('request_id')
        if (response.get('status') != 'PENDING_ADMIN' or
            response.get('selected_admin') != admin_id or
            not isinstance(req_id, str) or not re.fullmatch(r'[a-f0-9]{32}', req_id)):
            raise RuntimeError('ENROLLMENT_RECEIPT_UNVERIFIED')
        check = _request(port, 'GET', f'/v1/enrollment/requests/{req_id}/status')
        if str(check.get('status')).lower() not in ('pending', 'pending_admin'):
            raise RuntimeError('ENROLLMENT_STATUS_NOT_PENDING')
    except (RuntimeError, ValueError, OSError) as exc:
        raise RuntimeError(f'Identity {device_id} retained in LMS-SLH; '
                           f'enrollment outcome unverified: {exc}') from None
    return {'status': 'PENDING_ADMIN', 'request_id': req_id,
            'device_id': device_id, 'fingerprint': identity_fingerprint,
            'label': label.strip(), 'requested_role': role, 'selected_admin': admin_id,
            'identity_created': True, 'enrollment_submitted': True,
            'next_step': 'Approve or reject in MCEL > SLH > Organization Identities > Pending'}

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
            c.add_argument('--admin-id',required=True)
            c.add_argument('--admin-name',required=True)
    enrollment = sub.add_parser('request-enrollment',
        help='Create a new LMS-owned identity and request MCEL Pending approval')
    enrollment.add_argument('--new', action='store_true', required=True,
                            help='Explicitly authorize creation of one new local identity')
    enrollment.add_argument('--label', required=True)
    enrollment.add_argument('--role', choices=('worker', 'requester', 'admin'), default='worker')
    enrollment.add_argument('--admin', default=None,
                            help='Exact administrator ID; omit to open the graphical selector')
    enrollment.add_argument('--port', type=int, default=DEFAULT_SLH_API_PORT,
                            help='Canonical LMS-SLH loopback port (8897)')
    args=p.parse_args(argv)
    try:
        if args.command=='request-enrollment':
            result=request_enrollment(port=args.port,label=args.label,role=args.role,admin_id=args.admin)
        elif args.command=='create':
            result=save_identity(args.identity_dir,args.device_id)
        elif args.command=='bootstrap-admin':
            result=bootstrap_admin(port=args.port,state_dir=args.state_dir,identity_dir=args.identity_dir,
                                   device_id=args.device_id,
                                   admin_id=args.admin_id,admin_name=args.admin_name)
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
