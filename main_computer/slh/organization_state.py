"""Persisted Organization Service policy; never enabled by initial bootstrap.

The Local Service alone mutates this configuration.  The organization-facing
process reads it and refuses to launch unless explicitly enabled and initialized.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import tempfile

from .ports import DEFAULT_SLH_API_PORT, DEFAULT_SLH_MANAGED_PORT

FILENAME = 'organization-service.json'
SCHEMA = 'main-computer.slh-organization-config.v1'
DEFAULTS = {
    'schema': SCHEMA,
    'configured': False,
    'enabled': False,
    'activation_confirmed': False,
    'host': '0.0.0.0',
    'worker_port': DEFAULT_SLH_MANAGED_PORT,
    'api_port': DEFAULT_SLH_MANAGED_PORT + 1,
    'workers_enabled': False,
    'seh_enabled': False,
}


class OrganizationConfigError(ValueError):
    pass


def validate_config(value: dict) -> dict:
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise OrganizationConfigError('invalid organization configuration schema')
    data = {**DEFAULTS, **value}
    if set(data) != set(DEFAULTS):
        raise OrganizationConfigError('unexpected organization configuration key')
    for key in ('configured', 'enabled', 'activation_confirmed', 'workers_enabled', 'seh_enabled'):
        if type(data[key]) is not bool:
            raise OrganizationConfigError('invalid ' + key)
    if data['enabled'] and not data['configured']:
        raise OrganizationConfigError('organization cannot be enabled before configuration')
    if not isinstance(data['host'], str):
        raise OrganizationConfigError('invalid network bind address')
    try:
        host = ipaddress.ip_address(data['host'])
    except ValueError as exc:
        raise OrganizationConfigError('bind host must be a literal IPv4 address') from exc
    if host.version != 4 or host.is_multicast or host.is_unspecified and str(host) != '0.0.0.0':
        raise OrganizationConfigError('invalid network bind address')
    if str(host) != data['host']:
        raise OrganizationConfigError('noncanonical network bind address')
    for key in ('worker_port', 'api_port'):
        value = data[key]
        if type(value) is not int or not 1 <= value <= 65535 or value == DEFAULT_SLH_API_PORT:
            raise OrganizationConfigError('invalid ' + key)
    if data['worker_port'] == data['api_port']:
        raise OrganizationConfigError('Organization API and Worker ports must differ')
    return data


def read_config(state_dir: Path) -> dict:
    path = Path(state_dir) / FILENAME
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return validate_config(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError, UnicodeError) as exc:
        raise OrganizationConfigError('organization configuration is invalid') from exc


def save_config(state_dir: Path, config: dict) -> dict:
    data = validate_config(config)
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.organization-',
                                         suffix='.tmp', encoding='utf-8', delete=False) as f:
            tmp_path = Path(f.name)
            if os.name == 'posix':
                os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(data, sort_keys=True) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, directory / FILENAME)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return data


def initialize_disabled(state_dir: Path) -> Path:
    path = Path(state_dir) / FILENAME
    if not path.exists():
        raw = json.dumps(DEFAULTS, sort_keys=True) + '\n'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(raw)
    return path


def organization_status(state_dir: Path) -> dict:
    try:
        data = read_config(state_dir)
    except OrganizationConfigError:
        return {'configured': False, 'enabled': False,
                'state': 'configuration_error', 'listening': False,
                'bind_host': None, 'api_port': None, 'network_access': 'UNVERIFIED'}
    return {'configured': data['configured'], 'enabled': data['enabled'],
            'state': 'not_started' if data['enabled'] else
                     ('disabled' if data['configured'] else 'not_configured'),
            'listening': False,
            'bind_host': data['host'] if data['configured'] else None,
            'api_port': data['api_port'] if data['configured'] else None,
            'network_access': ('NOT_CONFIGURED' if not data['configured'] else
                               'LOOPBACK_ONLY' if data['host'].startswith('127.') else
                               'BIND_CONFIGURED_REACHABILITY_UNVERIFIED')}
