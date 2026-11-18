"""Sanitized, durable child-listener startup failure evidence for local SLH status.

No exception messages, credentials or arbitrary traceback text are persisted.
The configured endpoint is safe to expose through the loopback status API.
"""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import time

_FILENAME = 'organization-listener-failure.json'
_SCHEMA = 'main-computer.slh.organization-listener-failure.v1'


def record_listener_bind_failure(directory: Path, config: dict, exc: OSError) -> dict:
    number = getattr(exc, 'winerror', None) or exc.errno
    unavailable = number in (10049, errno.EADDRNOTAVAIL)
    report = {
        'schema': _SCHEMA,
        'error_code': ('ORG_SLH_BIND_ADDRESS_UNAVAILABLE' if unavailable
                       else 'ORG_SLH_LISTENER_BIND_FAILED'),
        'stage': 'LISTENER_BIND',
        'bind_host': config['host'],
        'api_port': config['api_port'],
        'os_error': number if type(number) is int and 0 <= number <= 65535 else None,
        'explanation': ('The configured bind address is not assigned to this computer.' if unavailable
                        else 'The configured listener could not bind.'),
        'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    path = Path(directory) / _FILENAME
    temporary = path.with_name(path.name + f'.{os.getpid()}.{time.time_ns()}.tmp')
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, sort_keys=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def read_listener_bind_failure(directory: Path, config: dict) -> dict | None:
    path = Path(directory) / _FILENAME
    try:
        if path.stat().st_size > 2048:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None
    if (not isinstance(data, dict) or data.get('schema') != _SCHEMA or
        data.get('stage') != 'LISTENER_BIND' or
        data.get('error_code') not in ('ORG_SLH_BIND_ADDRESS_UNAVAILABLE', 'ORG_SLH_LISTENER_BIND_FAILED') or
        data.get('bind_host') != config.get('host') or data.get('api_port') != config.get('api_port') or
        not isinstance(data.get('observed_at'), str) or len(data['observed_at']) > 40):
        return None
    number = data.get('os_error')
    if number is not None and (type(number) is not int or not 0 <= number <= 65535):
        return None
    return {
        'error_code': data['error_code'], 'stage': 'LISTENER_BIND',
        'bind_host': data['bind_host'], 'api_port': data['api_port'],
        'os_error': number,
        'explanation': ('The configured bind address is not assigned to this computer.'
                        if data['error_code'] == 'ORG_SLH_BIND_ADDRESS_UNAVAILABLE'
                        else 'The configured listener could not bind.'),
        'observed_at': data['observed_at'],
    }


def clear_listener_bind_failure(directory: Path) -> None:
    (Path(directory) / _FILENAME).unlink(missing_ok=True)
