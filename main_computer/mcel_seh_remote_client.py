"""Remote SEH administrative transport, retaining ORG-SLH authority and TLS pin.

The SEH never authenticates an administrator or reads decrypted HTTP. A
fixed-purpose WebSocket transports the SLH's HTTPS connection unchanged.
"""
from __future__ import annotations

import http.client
from urllib.parse import urlsplit

from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError
from main_computer.slh.seh_transport import SEHLocalClientAdapter
from main_computer.slh.storage import slh_app_user_dir


def _seh_endpoint(seh_url: str, *, allow_lab=False):
    if not isinstance(seh_url, str) or not 1 <= len(seh_url) <= 512:
        raise NetworkConnectionError('SEH_ENDPOINT_INVALID')
    try:
        parsed = urlsplit(seh_url)
        port = parsed.port
    except ValueError as exc:
        raise NetworkConnectionError('SEH_ENDPOINT_INVALID') from exc
    if (not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment
            or parsed.username or parsed.password
            or (parsed.scheme != 'wss' and not (allow_lab and parsed.scheme == 'ws'
                                                   and parsed.hostname in ('localhost','127.0.0.1')))):
        raise NetworkConnectionError('SEH_TLS_REQUIRED')
    if port is not None and not 1 <= port <= 65535:
        raise NetworkConnectionError('SEH_ENDPOINT_INVALID')
    return parsed._replace(path='', query='', fragment='').geturl().rstrip('/')


class SEHAdminHTTPSConnection(http.client.HTTPSConnection):
    """One loopback adapter per pinned HTTPS connection; closes it on failure."""
    def __init__(self, host, port, *, seh_url, allow_lab=False, **kwargs):
        super().__init__(host, port, **kwargs)
        self._seh_url = seh_url
        self._allow_lab = allow_lab
        self._adapter = None

    def connect(self):
        if self._adapter is not None:
            raise OSError('SEH connection already initialized')
        self._adapter = SEHLocalClientAdapter(self._seh_url,
                                              allow_lab=self._allow_lab, purpose='admin')
        self.host, self.port = self._adapter.server_address
        try:
            super().connect()  # TLS terminates only at the ORG-SLH.
        except BaseException:
            self.close()
            raise

    def close(self):
        super().close()
        adapter, self._adapter = self._adapter, None
        if adapter is not None:
            adapter.stop()


class RemoteSEHClient(NetworkSlhClient):
    """Same device signer, trust-on-confirmation, session and notebook allowlist."""
    def __init__(self, *, trust_path=None, signer=None):
        super().__init__(trust_path=(trust_path if trust_path is not None else
                         slh_app_user_dir() / 'trusted-remote-seh-slh.json'), signer=signer)
        self._session_scope = 'seh'

    def _admin_services_status(self, conn, token):
        # No operational service API is admitted on the SEH identity listener.
        return 'IDENTITY_ADMINISTRATION_ONLY'

    def seh_connection_request(self, ticket, *, action=None):
        # Lifecycle controls belong to This Computer / direct Network SLH.
        raise NetworkConnectionError('SEH_OPERATION_NOT_ALLOWED', 403)

    def connect(self, seh_url, admin_device_id, *, confirm_pin=None, allow_lab=False):
        origin = _seh_endpoint(seh_url, allow_lab=allow_lab)
        # The SEH serves exactly one pinned SLH. The relay origin indexes the
        # destination ORG-SLH's separately confirmed TLS certificate.
        label = f'seh:{origin}'
        if len(label) > 100:
            raise NetworkConnectionError('SEH_TRUST_KEY_TOO_LONG')
        transport = {'kind':'seh', 'seh_url':origin, 'allow_lab':allow_lab}
        return self._connect_validated('127.0.0.1', 443, admin_device_id,
                                       label=label, confirm_pin=confirm_pin,
                                       transport=transport)
