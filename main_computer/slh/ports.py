"""Canonical SLH service endpoints.

Phase 2A is intentionally loopback-only; remote/LAN/SEH listeners are separate
future services and must not reuse the unsecured local development listener.
"""
DEFAULT_SLH_API_HOST = '127.0.0.1'
DEFAULT_SLH_API_PORT = 8897
DEFAULT_SLH_API_ORIGIN = f'http://{DEFAULT_SLH_API_HOST}:{DEFAULT_SLH_API_PORT}'

# Independent TLS-only LAN listener; never rebind the loopback admin port.
DEFAULT_SLH_MANAGED_PORT = 8898
