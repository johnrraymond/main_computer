"""Persistent, explicit per-Windows-user deployment selection. No secrets in launcher manifests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from main_computer.auth_hostnames import APP_PORTS, HOSTS

POLICY_NAME = 'onlyoffice-deployment.json'
SECRET_NAME = 'onlyoffice-deployment-secrets.json'
MANAGED = 'MAIN_COMPUTER_DEPLOYMENT_MANAGED'
PROJECT = 'main-computer-onlyoffice'
DOCS_PORT = 18086
DOCS_ORIGIN = f'https://{HOSTS["onlyoffice"]}:{DOCS_PORT}'


def state_path() -> Path:
    if os.name != 'nt' or not os.getenv('LOCALAPPDATA'):
        raise RuntimeError('WINDOWS_LOCALAPPDATA_REQUIRED')
    return Path(os.environ['LOCALAPPDATA']) / 'MainComputer' / 'AuthBootstrap'


def same_path(a, b) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def read_policy(state: Path) -> dict | None:
    path = state / POLICY_NAME
    if not path.exists():
        return None
    from main_computer.auth_bootstrap import _load_json
    value = _load_json(path)
    if value.get('schema') != 1 or value.get('mode') not in {'legacy', 'hardened'}:
        raise RuntimeError('INVALID_DEPLOYMENT_POLICY')
    if value['mode'] == 'hardened':
        owner, roots = value.get('primary'), value.get('roots')
        if (owner not in {'dev', 'unleashed'} or not isinstance(roots, dict)
                or owner not in roots or not set(roots) <= {owner, 'debug', 'safe'}
                or not all(isinstance(x, str) and Path(x).is_absolute() for x in roots.values())
                or len({os.path.normcase(str(Path(x).resolve())) for x in roots.values()}) != len(roots)):
            raise RuntimeError('INVALID_DEPLOYMENT_LANE_POLICY')
        locations=[Path(x).resolve() for x in roots.values()]
        if any(a in b.parents for a in locations for b in locations if a != b):
            raise RuntimeError('NESTED_DEPLOYMENT_ROOTS_NOT_ALLOWED')
        if not isinstance(value.get('fingerprint'), dict) or not value['fingerprint']:
            raise RuntimeError('MISSING_DEPLOYMENT_SOURCE_FINGERPRINT')
    return value


def lane_for(policy: dict, root: Path) -> str:
    for lane, location in policy['roots'].items():
        if same_path(location, root):
            return lane
    raise RuntimeError('UNREGISTERED_INSTALLATION_WHILE_HARDENED_DEPLOYMENT_ACTIVE')


def file_hashes(root: Path, files) -> dict:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}


def verify_source(policy: dict, root: Path) -> None:
    """Validate lane membership and installed release integrity.

    A registered development checkout is mutable. Its old cutover receipt is
    historical evidence, not a permission to execute only those exact bytes.
    Missing required files still fail, and installed lanes retain their pin.
    """
    lane = lane_for(policy, root)
    observed = file_hashes(root, policy['fingerprint'])
    if lane != 'dev' and observed != policy['fingerprint']:
        raise RuntimeError('DEPLOYMENT_SOURCE_CHANGED_RECERTIFICATION_REQUIRED')


def public_environment(state: Path, root: Path, *, check_source=True) -> dict[str, str]:
    policy = read_policy(state)
    if not policy or policy['mode'] == 'legacy':
        # Explicit rollback must defeat stale inherited candidate flags, but
        # absent policy leaves independent test environments unchanged.
        return ({MANAGED: '0', 'MAIN_COMPUTER_HARDENED_CANDIDATE': '0'} if policy else {})
    lane = lane_for(policy, root)
    if check_source:
        verify_source(policy, root)
    port = APP_PORTS[lane]
    return {MANAGED: '1', 'MAIN_COMPUTER_HARDENED_CANDIDATE': '1',
            'MAIN_COMPUTER_PRIMARY_OWNER': policy['primary'], 'MAIN_COMPUTER_INSTALLATION_MODE': lane,
            'MAIN_COMPUTER_AUTH_BOOTSTRAP_ENABLED': '1' if lane == policy['primary'] else '0',
            'MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT': '8875',
            'MAIN_COMPUTER_CONTROL_PORT': str(port),
            'MAIN_COMPUTER_HEARTBEAT_PORT': str(port+1),
            'MAIN_COMPUTER_MAIN_LOG_PORT': str(port+2),
            'MAIN_COMPUTER_MAIN_LOG_URL': f'http://127.0.0.1:{port+2}',
            'MAIN_COMPUTER_ONLYOFFICE_ENABLED': '1', 'MAIN_COMPUTER_ONLYOFFICE_MODE': 'docker',
            'MAIN_COMPUTER_ONLYOFFICE_PROJECT': PROJECT,
            'MAIN_COMPUTER_ONLYOFFICE_PORT': '18085',
            'MAIN_COMPUTER_ONLYOFFICE_CONTAINER_NAME': 'main-computer-onlyoffice-documentserver',
            'MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED': 'true',
            'MAIN_COMPUTER_ONLYOFFICE_ALLOW_PRIVATE_IP_ADDRESS': 'true',
            'MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS': 'false',
            'MAIN_COMPUTER_ONLYOFFICE_HTTPS_PORT': str(DOCS_PORT),
            'MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL': DOCS_ORIGIN,
            'MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL': DOCS_ORIGIN,
            'MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL': 'http://127.0.0.1:18085',
            'MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL': f'http://host.docker.internal:{port}',
            'MAIN_COMPUTER_ONLYOFFICE_STOP_ON_STOP': '0'}


def secret_environment(state: Path) -> dict[str, str]:
    from main_computer.auth_bootstrap import _load_json, _protect
    path = state / SECRET_NAME
    _protect(path, directory=False)
    value = _load_json(path)
    keys = ('MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET', 'MAIN_COMPUTER_MAIN_LOG_TOKEN')
    if set(value) != set(keys) or not all(isinstance(value[k], str) and re.fullmatch(r'[0-9a-f]{64}', value[k]) for k in keys):
        raise RuntimeError('INVALID_DEPLOYMENT_SECRETS')
    return value


def apply_runtime_environment(root: Path, *, include_jwt: bool = True) -> None:
    # Only normal Windows launchers set MANAGED. Isolated lab/test invocations
    # retain their own ephemeral authority and explicit test configuration.
    if os.name != 'nt':
        return
    state = state_path()
    policy = read_policy(state)
    if os.environ.get(MANAGED) != '1':
        if policy and policy['mode'] == 'hardened':
            raise RuntimeError('HARDENED_DEPLOYMENT_REQUIRES_NORMAL_MANAGED_LAUNCHER')
        return
    settings = public_environment(state, root)
    if settings.get(MANAGED) != '1':
        raise RuntimeError('DEPLOYMENT_CHANGED_DURING_START')
    os.environ.update(settings)
    private = secret_environment(state)
    if not include_jwt:
        private.pop("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET")
        os.environ.pop("MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET", None)
    os.environ.update(private)
