"""Read-only SEH hostname DNS diagnostics for the Coolify application status.

Checks the DNS view from the operator's machine (not authoritative zone data).
Cloudflare's published IP ranges are an indicator of edge proxying, not proof
of correct origin routing, TLS configuration, or functioning WebSockets.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

# Cloudflare publishes these proxy/edge ranges at
# https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6.
# Do not confuse addresses within these ranges with the Coolify origin IP.
_CLOUDFLARE_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22',
    '103.31.4.0/22', '141.101.64.0/18', '108.162.192.0/18',
    '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22',
    '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32',
    '2405:b500::/32', '2405:8100::/32', '2a06:98c0::/29',
    '2c0f:f248::/32',
))


def domain_hosts(application: dict) -> list[str]:
    """Parse Coolify's `domains` or `fqdn` forms, without exposing paths."""
    names: set[str] = set()
    for field in ('domains', 'fqdn'):
        value = application.get(field)
        if isinstance(value, str):
            raw = [value]
        elif isinstance(value, (tuple, list)):
            raw = [entry for entry in value if isinstance(entry, str)]
        else:
            continue
        for entry in raw:
            for token in re.split(r'[,;\s]+', entry):
                if not token:
                    continue
                parsed = urlsplit(token if '://' in token else '//' + token)
                if parsed.hostname and not parsed.username and not parsed.password:
                    names.add(parsed.hostname.lower().rstrip('.'))
    return sorted(names)


def _resolve(host: str) -> list[str]:
    # A and AAAA via the system resolver; handles DNS CNAME chains as usual.
    # SOCK_STREAM avoids duplicate UDP/TCP results on some Windows resolvers.
    answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({row[4][0] for row in answers})


def probe(public_url: str | None, application: dict | None = None, *, resolver=None) -> dict:
    """Return non-mutating DNS readiness evidence, never throw on DNS failures.

    `ready` requires both Cloudflare-range A/AAAA answers and an exact Coolify
    hostname match. DNS alone cannot establish HTTPS/WSS or Cloudflare's origin.
    """
    app = application if isinstance(application, dict) else {}
    configured = domain_hosts(app)
    result = {
        'status': 'DNS_TARGET_UNAVAILABLE', 'hostname': None,
        'resolver': 'system', 'addresses': [], 'cloudflare_edge_addresses': False,
        'coolify_domains': configured, 'coolify_domain_matches': None,
        'ready': False,
    }
    if not isinstance(public_url, str) or not public_url:
        return result
    try:
        parts = urlsplit(public_url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or parts.port):
            result['status'] = 'DNS_TARGET_INVALID'
            return result
        hostname = parts.hostname.lower().rstrip('.')
    except ValueError:
        result['status'] = 'DNS_TARGET_INVALID'
        return result
    result['hostname'] = hostname
    result['coolify_domain_matches'] = hostname in configured if configured else None
    try:
        observed = (resolver or _resolve)(hostname)
        addresses = sorted({str(ipaddress.ip_address(value)) for value in observed})
    except socket.gaierror as exc:
        missing = {getattr(socket, 'EAI_NONAME', None), getattr(socket, 'EAI_NODATA', None)}
        result['status'] = 'DNS_NOT_FOUND' if exc.errno in missing else 'DNS_LOOKUP_FAILED'
        return result
    except (OSError, ValueError, TypeError):
        result['status'] = 'DNS_LOOKUP_FAILED'
        return result
    result['addresses'] = addresses
    if not addresses:
        result['status'] = 'DNS_NOT_FOUND'
        return result
    ips = [ipaddress.ip_address(value) for value in addresses]
    if any(not ip.is_global for ip in ips):
        result['status'] = 'DNS_NONPUBLIC_ADDRESSES'
        return result
    cf_count = sum(any(ip in network for network in _CLOUDFLARE_NETWORKS) for ip in ips)
    if cf_count == len(ips):
        result['status'] = 'DNS_CLOUDFLARE_EDGE'
        result['cloudflare_edge_addresses'] = True
    elif cf_count:
        result['status'] = 'DNS_MIXED_CLOUDFLARE_AND_ORIGIN'
    else:
        result['status'] = 'DNS_NOT_CLOUDFLARE'
    result['ready'] = result['cloudflare_edge_addresses'] and result['coolify_domain_matches'] is True
    return result
