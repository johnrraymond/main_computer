"""Canonical, browser-only Main Computer origin policy for the hardened candidate.

The authenticated host and ONLYOFFICE share one HTTPS listener.  The dev and
unleashed installation modes are mutually exclusive owners of the 8765 lane.
No OS-wide DNS or hosts-file changes are made.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

HOSTS = {
    'dev': 'dev.main-computer.local',
    'unleashed': 'unleashed.main-computer.local',
    'debug': 'debug.main-computer.local',
    'safe': 'safe.main-computer.local',
    'onlyoffice': 'onlyoffice.main-computer.local',
}
APP_PORTS = {'dev': 8765, 'unleashed': 8765, 'debug': 28865, 'safe': 38865}
TLS_PORT = 8875


def canonical_host(mode: str) -> str:
    if mode not in HOSTS:
        raise ValueError('UNRECOGNIZED_MAIN_COMPUTER_MODE')
    return HOSTS[mode]


def auth_origin(mode: str, port: int = TLS_PORT) -> str:
    return f'https://{canonical_host(mode)}:{port}'


def managed_browser_resolver_rules() -> str:
    maps = ', '.join(f'MAP {name} 127.0.0.1' for name in HOSTS.values())
    # Reject unknown members of the private .local namespace *inside Chromium*.
    return maps + ', MAP *.main-computer.local ^NOTFOUND, EXCLUDE localhost'


def validate_origin(value: str, *, port: int = TLS_PORT) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and parsed.hostname in HOSTS.values()
                and parsed.port == port and not parsed.username and not parsed.password
                and not parsed.path.rstrip('/') and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def active_auth_hosts(primary_owner: str) -> frozenset[str]:
    """Only one of Dev and Unleashed may own the primary backend lane."""
    if primary_owner not in {'dev', 'unleashed'}:
        raise ValueError('PRIMARY_LANE_OWNER_REQUIRED')
    return frozenset((HOSTS[primary_owner], HOSTS['debug'], HOSTS['safe']))
